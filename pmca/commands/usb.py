import contextlib
import io
import json
import os
import sys
import time
import struct
import zipfile

import config
from ..resources import get_bundle_resource_path
from ..apk import *
from .. import appstore
from .. import firmware
from .. import installer
from ..io import *
from ..marketserver.server import *
from ..platform import *
from ..platform.backend.senser import *
from ..platform.backend.usb import *
from ..usb import *
from ..usb.driver import *
from ..usb.driver.generic import *
from ..usb.sony import *
from ..util import http


def printStatus(status):
 """Print progress"""
 print('%s %d%%' % (status.message, status.percent))


appListCache = None
def listApps(enableCache=False):
 global appListCache
 appStoreRepo = appstore.GithubApi(config.githubAppListUser, config.githubAppListRepo)

 if not appListCache or not enableCache:
  print('Loading app list')
  apps = appstore.AppStore(appStoreRepo).apps
  print('Found %d apps' % len(apps))
  appListCache = apps
 return appListCache


def installApp(dev, apkFile=None, appPackage=None, outFile=None):
 """Installs an app on the specified device."""
 certFile = get_bundle_resource_path('certs/localtest.me.pem')
 with ServerContext(LocalMarketServer(certFile)) as server:
  apkData = None
  if apkFile:
   apkData = apkFile.read()
  elif appPackage:
   print('Downloading apk')
   apps = listApps(True)
   if appPackage not in apps:
    raise Exception('Unknown app: %s' % appPackage)
   apkData = apps[appPackage].release.asset

  if apkData:
   print('Analyzing apk')
   print('')
   checkApk(io.BytesIO(apkData))
   print('')
   server.setApk(apkData)

  print('Starting task')
  xpdData = server.getXpd()

  print('Starting communication')
  # Point the camera to the web api
  result = installer.install(SonyAppInstallCamera(dev), *server.server_address, xpdData, printStatus)
  if result.code != 0:
   raise Exception('Communication error %d: %s' % (result.code, result.message))

  result = server.getResult()

  print('Task completed successfully')

  if outFile:
   print('Writing to output file')
   json.dump(result, outFile, indent=2)

  return result


def checkApk(apkFile):
 try:
  apk = ApkParser(apkFile)

  props = [
   ('Package', apk.getPackageName()),
   ('Version', apk.getVersionName()),
  ]
  apk.getVersionCode()
  for k, v in props:
   print('%-9s%s' % (k + ': ', v))

  sdk = apk.getMinSdkVersion()
  if sdk > 10:
   print('Warning: This app might not be compatible with the device (minSdkVersion = %d)' % sdk)

  try:
   apk.getCert()
  except:
   print('Warning: Cannot read apk certificate')

 except:
  print('Warning: Invalid apk file')


class UsbDriverList(contextlib.AbstractContextManager):
 def __init__(self, *contexts, fallbackFactories=()):
  self._contexts = tuple(contexts)
  self._fallbackFactories = tuple(fallbackFactories)
  self._drivers = []
  self._fallbackDrivers = []
  self._fallbackDriverMap = {}
  self._exitStack = None

 def __enter__(self):
  self._exitStack = contextlib.ExitStack()
  try:
   for context in self._contexts:
    driver = self._exitStack.enter_context(context)
    self._drivers.append(driver)
  except BaseException:
   self._exitStack.__exit__(*sys.exc_info())
   self._reset()
   raise
  return self

 def __exit__(self, *ex):
  try:
   return self._exitStack.__exit__(*ex)
  finally:
   self._reset()

 def _reset(self):
  self._drivers = []
  self._fallbackDrivers = []
  self._fallbackDriverMap = {}
  self._exitStack = None

 def _getFallbackDrivers(self, classType):
  drivers = []
  for fallbackClass, name, factory in self._fallbackFactories:
   if fallbackClass != classType:
    continue
   key = id(factory)
   if key not in self._fallbackDriverMap:
    context = factory()
    driver = self._exitStack.enter_context(context)
    self._fallbackDriverMap[key] = driver
    self._fallbackDrivers.append(driver)
   drivers.append(self._fallbackDriverMap[key])
  return drivers

 @staticmethod
 def _listCandidates(drivers, vendor):
  for driver in drivers:
   for device in driver.listDevices(vendor):
    yield device, driver.classType, driver.openDevice(device)

 def listDevices(self, vendor):
  return self._listCandidates(self._drivers, vendor)

 def listRecognizedDevices(self, vendor, recognize):
  if not self._fallbackFactories:
   yield from recognize(self.listDevices(vendor))
   return

  classTypes = []
  for context in self._contexts:
   if context.classType not in classTypes:
    classTypes.append(context.classType)
  for classType, name, factory in self._fallbackFactories:
   if classType not in classTypes:
    classTypes.append(classType)

  for classType in classTypes:
   nativeDrivers = [
    driver for driver in self._drivers if driver.classType == classType
   ]
   fallbackAvailable = any(
    fallbackClass == classType
    for fallbackClass, name, factory in self._fallbackFactories
   )
   recognized = list(recognize(
    self._listCandidates(nativeDrivers, vendor)
   ))
   # Native Windows "driverless" detection yields UnimplementedUsbDriver —
   # that is not usable; try libusb once a real driver is bound.
   usable = [
    device for device in recognized
    if type(getattr(device, 'driver', None)).__name__ != 'UnimplementedUsbDriver'
   ]
   if usable:
    yield from usable
   elif fallbackAvailable:
    fallbackRecognized = list(recognize(
     self._listCandidates(self._getFallbackDrivers(classType), vendor)
    ))
    if fallbackRecognized:
     yield from fallbackRecognized
    elif recognized:
     # Expose driverless detections so callers can auto-install a driver.
     yield from recognized
   elif recognized:
    yield from recognized


def importDriver(driverName=None):
 """Imports the usb driver. Use in a with statement"""
 MscContext = None
 MtpContext = None
 VendorSpecificContext = None
 MscContext2 = None
 MtpContext2 = None
 VendorSpecificContext2 = None

 # Load native drivers
 if driverName == 'native' or driverName is None:
  if sys.platform == 'win32':
   try:
    from ..usb.driver.windows.msc import MscContext
   except (ImportError, OSError):
    if driverName == 'native':
     raise
   try:
    from ..usb.driver.windows.wpd import MtpContext
   except (ImportError, OSError):
    if driverName == 'native':
     raise
   # Detect driverless service-mode devices (also when selecting drivers automatically).
   if driverName in (None, 'native'):
    from ..usb.driver.windows.driverless import VendorSpecificContext
  elif sys.platform == 'darwin':
   from ..usb.driver.osx import isMscDriverAvailable
   if isMscDriverAvailable():
    from ..usb.driver.osx import MscContext
   else:
    print('Native driver not installed')
  else:
   print('No native drivers available')
 elif driverName == 'qemu':
  from ..usb.driver.generic.qemu import MscContext
  from ..usb.driver.generic.qemu import MtpContext
 elif driverName != 'libusb':
  raise Exception('Unknown driver')

 # Fallback to libusb
 if MscContext is None or (driverName is None and sys.platform == 'win32'):
  from ..usb.driver.generic.libusb import MscContext as MscContext2
 if MtpContext is None or (driverName is None and sys.platform == 'win32'):
  from ..usb.driver.generic.libusb import MtpContext as MtpContext2
 if (VendorSpecificContext is None and driverName != 'qemu') or (driverName is None and sys.platform == 'win32'):
  from ..usb.driver.generic.libusb import VendorSpecificContext as VendorSpecificContext2

 nativeContextTypes = [
  context for context in [MscContext, MtpContext, VendorSpecificContext]
  if context
 ]
 optionalNative = driverName is None and sys.platform == 'win32'
 drivers = [context() for context in nativeContextTypes]
 if optionalNative:
  fallbackFactories = [
   (classType, name, context)
   for classType, name, context in [
    (USB_CLASS_MSC, 'libusb-MSC', MscContext2),
    (USB_CLASS_PTP, 'libusb-MTP', MtpContext2),
    (USB_CLASS_VENDOR_SPECIFIC, 'libusb-vendor-specific', VendorSpecificContext2),
   ]
   if context
  ]
  print('Using drivers %s' % ', '.join(
   [driver.name for driver in drivers] +
   [name for classType, name, factory in fallbackFactories]
  ))
  return UsbDriverList(*drivers, fallbackFactories=fallbackFactories)
 fallbackContextTypes = [
  context for context in [MscContext2, MtpContext2, VendorSpecificContext2]
  if context
 ]
 fallbackDrivers = [context() for context in fallbackContextTypes]
 allDrivers = drivers + fallbackDrivers
 print('Using drivers %s' % ', '.join(d.name for d in allDrivers))
 return UsbDriverList(*allDrivers)


def _tagUsbIds(device, handle):
 """Attach USB ids from the enumeration handle for later driver install."""
 device.idVendor = handle.idVendor
 device.idProduct = handle.idProduct
 return device


def _recognizeDevices(candidates, quiet=False):
 for dev, type, drv in candidates:
  if type == USB_CLASS_MSC:
   if not quiet:
    print('\nQuerying mass storage device')
   try:
    info = MscDevice(drv).getDeviceInfo()
   except GenericUsbException:
    continue

   if isSonyMscCamera(info):
    if isSonyMscUpdaterCamera(dev):
     if not quiet:
      print('%s %s is a camera in updater mode' % (info.manufacturer, info.model))
     yield _tagUsbIds(SonyMscUpdaterDevice(drv), dev)
    else:
     if not quiet:
      print('%s %s is a camera in mass storage mode' % (info.manufacturer, info.model))
     yield _tagUsbIds(SonyMscExtCmdDevice(drv), dev)

  elif type == USB_CLASS_PTP:
   if not quiet:
    print('\nQuerying MTP device')
   info = MtpDevice(drv).getDeviceInfo()

   if isSonyMtpCamera(info):
    if not quiet:
     print('%s %s is a camera in MTP mode' % (info.manufacturer, info.model))
    yield _tagUsbIds(SonyMtpExtCmdDevice(drv), dev)
   elif isSonyMtpAppInstallCamera(info):
    if not quiet:
     print('%s %s is a camera in app install mode' % (info.manufacturer, info.model))
    yield _tagUsbIds(SonyMtpAppInstallDevice(drv), dev)

  elif type == USB_CLASS_VENDOR_SPECIFIC:
   if isSonySenserCamera(dev):
    print('Found a camera in service mode')
    yield _tagUsbIds(SonySenserDevice(drv), dev)

  if not quiet:
   print('')


def listDevices(driverList, quiet=False):
 """List all Sony usb devices"""
 if not quiet:
  print('Looking for Sony devices')
 yield from driverList.listRecognizedDevices(
  SONY_ID_VENDOR,
  lambda candidates: _recognizeDevices(candidates, quiet),
 )


def getDevice(driver):
 """Check for exactly one Sony usb device"""
 devices = list(listDevices(driver))
 if not devices:
  print('No devices found. Please make sure that the camera is connected.')
 elif len(devices) != 1:
  print('Error: Too many Sony devices found. Only one camera is supported.')
 else:
  return devices[0]


def _driverInstallInteractive(interactive=None):
 """Whether to ask before installing a USB driver (False in the web GUI)."""
 if interactive is not None:
  return bool(interactive)
 try:
  return sys.stdin is not None and sys.stdin.isatty()
 except Exception:
  return False


def _promptInstallServiceDriver(pid, interactive=None, purpose='service mode'):
 """Install libusb-win32 for a Sony USB PID. Returns True on success.

 In non-interactive contexts (web GUI / redirected stdin), installs without a
 Y/n prompt after printing what will happen. UAC still requires user approval.
 """
 if sys.platform != 'win32' or pid is None:
  return False
 try:
  from ..usb.driver.windows.wdi import helper_available, install_libusb_driver
 except ImportError:
  return False
 if not helper_available():
  return False

 print('Sony USB device 054C:%04X needs a libusb-win32 driver for %s.' % (
  pid, purpose))
 if _driverInstallInteractive(interactive):
  try:
   resp = input('Install libusb-win32 driver automatically? [Y/n] ')
  except EOFError:
   resp = 'y'
  if resp.strip().lower() == 'n':
   print('Skipped. Use Zadig to install libusb-win32 for 054C:%04X.' % pid)
   return False
 else:
  print('Installing libusb-win32 driver automatically (UAC prompt may appear)...')

 print('Installing driver...')
 if install_libusb_driver(vid=SONY_ID_VENDOR, pid=pid):
  print('Driver installed successfully.')
  time.sleep(1.5)
  return True
 print('Driver installation failed. Use Zadig manually.')
 return False


def _restoreServiceDriver(pid):
 if sys.platform != 'win32' or pid is None:
  return
 try:
  from ..usb.driver.windows.wdi import restore_original_driver
 except ImportError:
  return
 print('Restoring original USB driver for 054C:%04X...' % pid)
 if restore_original_driver(vid=SONY_ID_VENDOR, pid=pid):
  print('Driver restored.')
 else:
  print('Warning: Could not restore driver automatically. '
   'Use Device Manager to roll back if needed.')


def _restoreInstalledDrivers(serviceDriverState):
 if not serviceDriverState:
  return
 # Restore service-mode first, then any MSC filter used to enter service mode.
 for key in ('installed_pid', 'installed_msc_pid'):
  pid = serviceDriverState.get(key)
  if pid is not None:
   _restoreServiceDriver(pid)
   serviceDriverState[key] = None


def _listDriverlessSenserPids():
 """Return PIDs of driverless Sony service-mode devices on Windows."""
 if sys.platform != 'win32':
  return []
 try:
  from ..usb.driver.windows.driverless import _listDevices as listDriverless
 except ImportError:
  return []
 return [
  d.idProduct for d in listDriverless()
  if d.idVendor == SONY_ID_VENDOR and d.idProduct in SONY_ID_PRODUCT_SENSER
 ]


def _waitForDevice(
 driverName, expectedType, attempts, delay, continuation,
 autoInstallServiceDriver=False, serviceDriverState=None, interactive=None,
):
 """Poll fresh driver contexts and consume the target inside its context.

 If autoInstallServiceDriver is True (Windows service-mode wait), detect a
 driverless 054C:02A9/0336 device and install libusb-win32 via wdi-helper.
 When installation succeeds, serviceDriverState['installed_pid'] is set so
 the caller can restore afterward. Prefer driverName='libusb' after install.
 """
 if serviceDriverState is None:
  serviceDriverState = {}
 installOffered = False
 pollDriverName = driverName

 for i in range(attempts):
  time.sleep(delay)

  if (autoInstallServiceDriver and sys.platform == 'win32'
    and not installOffered and serviceDriverState.get('installed_pid') is None):
   pids = _listDriverlessSenserPids()
   if pids:
    installOffered = True
    pid = pids[0]
    if _promptInstallServiceDriver(pid, interactive=interactive):
     serviceDriverState['installed_pid'] = pid
     pollDriverName = 'libusb'

  with importDriver(pollDriverName) as driver:
   devices = list(listDevices(driver, True))
   if len(devices) > 1:
    raise Exception(
     'Multiple Sony devices found while waiting for camera mode change.'
    )
   if len(devices) == 1 and isinstance(devices[0], expectedType):
    # Driverless native detections are not usable for the shell.
    if (type(getattr(devices[0], 'driver', None)).__name__
      == 'UnimplementedUsbDriver'):
     continue
    continuation(devices[0])
    return True
  del devices
 return False


def infoCommand(driverName=None):
 """Display information about the camera connected via usb"""
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device:
   if isinstance(device, SonyAppInstallDevice):
    info = installApp(device)
    print('')
    props = [
     ('Model', info['deviceinfo']['name']),
     ('Product code', info['deviceinfo']['productcode']),
     ('Serial number', info['deviceinfo']['deviceid']),
     ('Firmware version', info['deviceinfo']['fwversion']),
    ]
   elif isinstance(device, SonyExtCmdDevice):
    dev = SonyExtCmdCamera(device)
    info = dev.getCameraInfo()
    updater = SonyUpdaterCamera(device)
    updater.init()
    firmwareOld, firmwareNew = updater.getFirmwareVersion()
    props = [
     ('Model', info.modelName),
     ('Product code', info.modelCode),
     ('Serial number', info.serial),
     ('Firmware version', firmwareOld),
    ]
    try:
     lensInfo = dev.getLensInfo()
     if lensInfo.model != 0:
      props.append(('Lens', 'Model 0x%x (Firmware %s)' % (lensInfo.model, lensInfo.version)))
    except (InvalidCommandException, UnknownMscException):
     pass
    try:
     gpsInfo = dev.getGpsData()
     props.append(('GPS Data', '%s - %s' % gpsInfo))
    except (InvalidCommandException, UnknownMscException):
     pass
   else:
    print('Error: Cannot use camera in this mode. Please switch to MTP or mass storage mode.')
    return
   for k, v in props:
    print('%-20s%s' % (k + ': ', v))
   return props


def installCommand(driverName=None, apkFile=None, appPackage=None, outFile=None):
 """Install the given apk on the camera"""
 switched = False
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device and isinstance(device, SonyExtCmdDevice):
   print('Switching to app install mode')
   try:
    SonyExtCmdCamera(device).switchToAppInstaller()
   except InvalidCommandException:
    print('Error: This camera does not support apps. Please check the compatibility list.')
    return
   device = None
   switched = True
  elif device and isinstance(device, SonyAppInstallDevice):
   installApp(device, apkFile, appPackage, outFile)
  elif device:
   print('Error: Cannot use camera in this mode. Please switch to MTP or mass storage mode.')

 if switched:
  print('Waiting for camera to switch...')
  found = _waitForDevice(
   driverName, SonyAppInstallDevice, 10, .5,
   lambda device: installApp(device, apkFile, appPackage, outFile),
  )
  if not found:
   print('Operation timed out. Please run this command again when your camera has connected.')


def appSelectionCommand():
 apps = list(listApps().values())
 for i, app in enumerate(apps):
  print(' [%2d] %s' % (i+1, app.package))
 i = int(input('Enter number of app to install (0 to abort): '))
 if i != 0:
  pkg = apps[i - 1].package
  print('')
  print('Installing %s' % pkg)
  return pkg


def getFdats():
 fdatDir = get_bundle_resource_path('updatershell/fdat')
 for directory in os.listdir(fdatDir):
  directoryPath = os.path.join(fdatDir, directory)
  if os.path.isdir(directoryPath):
   payloadFile = os.path.join(fdatDir, directory + '.dat')
   if os.path.isfile(payloadFile):
    for model in os.listdir(directoryPath):
     hdrFile = os.path.join(directoryPath, model)
     if os.path.isfile(hdrFile) and hdrFile.endswith('.hdr'):
      yield model[:-4], (hdrFile, payloadFile)


def getFdat(device):
 fdats = dict(getFdats())
 while device != '' and not device[-1:].isdigit() and device not in fdats:
  device = device[:-1]
 if device in fdats:
  hdrFile, payloadFile = fdats[device]
  with open(hdrFile, 'rb') as hdr, open(payloadFile, 'rb') as payload:
   return hdr.read() + payload.read()


def firmwareUpdateCommand(file, driverName=None):
 offset, size = firmware.readDat(file)

 switched = False
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device:
   switched = firmwareUpdateCommandInternal(
    device, file, offset, size
   )
   device = None

 if switched:
  _waitForUpdaterDevice(
   driverName, file, offset, size, None
  )


def updaterShellCommand(model=None, fdatFile=None, driverName=None, complete=None):
 switched = False
 update = None
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device:
   if fdatFile:
    fdat = fdatFile.read()
   else:
    if not model:
     print('Getting device info')
     try:
      model = SonyExtCmdCamera(device).getCameraInfo().modelName
     except:
      print('Error: Cannot determine camera model')
      return
     print('Using firmware for model %s' % model)
     print('')

    fdat = getFdat(model)
    if not fdat:
     print('Error: Model "%s" does not support custom firmware updates. Please check the compatibility list.' % model)
     return

   if not complete:
    def complete(device):
     print('Starting updater shell...')
     print('')
     CameraShell(UsbPlatformBackend(device)).run()
   update = io.BytesIO(fdat)
   switched = firmwareUpdateCommandInternal(
    device, update, 0, len(fdat), complete
   )
   device = None

 if switched:
  _waitForUpdaterDevice(
   driverName, update, 0, len(fdat), complete
  )


def _waitForUpdaterDevice(
 driverName, file, offset, size, complete
):
 print('')
 print('Waiting for camera to switch...')
 print('Please follow the instructions on the camera screen.')
 found = _waitForDevice(
  driverName, SonyUpdaterDevice, 60, .5,
  lambda device: firmwareUpdateCommandInternal(
   device, file, offset, size, complete
  ),
 )
 if not found:
  print('Operation timed out. Please run this command again when your camera has connected.')


def firmwareUpdateCommandInternal(device, file, offset, size, complete=None):
 if not isinstance(device, SonyUpdaterDevice) and not isinstance(device, SonyExtCmdDevice):
  print('Error: Cannot use camera in this mode. Please switch to MTP or mass storage mode.')
  return False

 dev = SonyUpdaterCamera(device)

 print('Initializing firmware update')
 dev.init()
 file.seek(offset)
 dev.checkGuard(file, size)
 versions = dev.getFirmwareVersion()
 if versions[1] != '9.99':
  print('Updating from version %s to version %s' % versions)

 if not isinstance(device, SonyUpdaterDevice):
  print('Switching to updater mode')
  dev.switchMode()
  return True

 else:
  print('Writing firmware')
  file.seek(offset)
  dev.writeFirmware(ProgressFile(file, size), size, complete)
  dev.complete()
  print('Done')
  return False


def guessFirmwareCommand(file, driverName=None):
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device:
   if not isinstance(device, SonyExtCmdDevice):
    print('Error: Cannot use camera in this mode.')
    return

   print('Getting device info')
   model = SonyExtCmdCamera(device).getCameraInfo().modelName
   print('Model name: %s' % model)
   print('')

   dev = SonyUpdaterCamera(device)
   with zipfile.ZipFile(file) as zip:
    infos = zip.infolist()
    print('Trying %d firmware images' % len(infos))
    for info in infos:
     data = zip.read(info)
     try:
      dev.init()
      dev.checkGuard(io.BytesIO(data), len(data))
      break
     except Exception as e:
      if 'Invalid model' not in str(e):
       print(e)
       break
    else:
     print('Fail: No matching file found')
     return
    print('Success: Found matching file: %s' % info.filename)


def gpsUpdateCommand(file=None, driverName=None):
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device:
   if not isinstance(device, SonyExtCmdDevice):
    print('Error: Cannot use camera in this mode.')
    return

   if not file:
    print('Downloading GPS data')
    file = io.BytesIO(http.get('https://control.d-imaging.sony.co.jp/GPS/assistme.dat').raw_data)

   print('Writing GPS data')
   SonyExtCmdCamera(device).writeGpsData(file)
   print('Done')


def streamingCommand(write=None, file=None, driverName=None):
 """Read/Write Streaming information for the camera connected via usb"""
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device:
   if not isinstance(device, SonyExtCmdDevice):
    print('Error: Cannot use camera in this mode.')
   else:
    dev = SonyExtCmdCamera(device)

    if write:
     incoming = json.load(write)

     # assemble Social (first 9 items in file)
     mydict = {}
     for key in incoming[:9]:
      if key[0] in ['twitterEnabled', 'facebookEnabled']:
       mydict[key[0]] = key[1] # Integer
      else:
       mydict[key[0]] = key[1].encode('ascii')

     data = SonyExtCmdCamera.LiveStreamingSNSInfo.pack(
      twitterEnabled = mydict['twitterEnabled'],
      twitterConsumerKey = mydict['twitterConsumerKey'].ljust(1025, b'\x00'),
      twitterConsumerSecret = mydict['twitterConsumerSecret'].ljust(1025, b'\x00'),
      twitterAccessToken1 = mydict['twitterAccessToken1'].ljust(1025, b'\x00'),
      twitterAccessTokenSecret = mydict['twitterAccessTokenSecret'].ljust(1025, b'\x00'),
      twitterMessage = mydict['twitterMessage'].ljust(401, b'\x00'),
      facebookEnabled = mydict['facebookEnabled'],
      facebookAccessToken = mydict['facebookAccessToken'].ljust(1025, b'\x00'),
      facebookMessage = mydict['facebookMessage'].ljust(401, b'\x00'),
     )
     dev.setLiveStreamingSocialInfo(data)

     # assemble Streaming, file may contain multiple sets (of 14 items)
     data = b'\x01\x00\x00\x00'
     data += struct.pack('<i', int((len(incoming)-9)/14))
     mydict = {}
     count = 1
     for key in incoming[9:]:
      if key[0] in ['service', 'enabled', 'videoFormat', 'videoFormat', 'unknown', \
        'enableRecordMode', 'channels', 'supportedFormats']:
       mydict[key[0]] = key[1]
      elif key[0] == 'macIssueTime':
       mydict[key[0]] = binascii.a2b_hex(key[1])
      else:
       mydict[key[0]] = key[1].encode('ascii')

      if count == 14:
       # reassemble Structs
       data += SonyExtCmdCamera.LiveStreamingServiceInfo1.pack(
        service = mydict['service'],
        enabled = mydict['enabled'],
        macId = mydict['macId'].ljust(41, b'\x00'),
        macSecret = mydict['macSecret'].ljust(41, b'\x00'),
        macIssueTime = mydict['macIssueTime'],
        unknown = 0, # mydict['unknown'],
       )

       data += struct.pack('<i', len(mydict['channels']))
       for j in range(len(mydict['channels'])):
        data += struct.pack('<i', mydict['channels'][j])

       data += SonyExtCmdCamera.LiveStreamingServiceInfo2.pack(
        shortURL = mydict['shortURL'].ljust(101, b'\x00'),
        videoFormat = mydict['videoFormat'],
       )

       data += struct.pack('<i', len(mydict['supportedFormats']))
       for j in range(len(mydict['supportedFormats'])):
        data += struct.pack('<i', mydict['supportedFormats'][j])

       data += SonyExtCmdCamera.LiveStreamingServiceInfo3.pack(
        enableRecordMode = mydict['enableRecordMode'],
        videoTitle = mydict['videoTitle'].ljust(401, b'\x00'),
        videoDescription = mydict['videoDescription'].ljust(401, b'\x00'),
        videoTag = mydict['videoTag'].ljust(401, b'\x00'),
       )
       count = 1
      else:
       count += 1

     dev.setLiveStreamingServiceInfo(data)
     return

    # Read settings from camera (do this first so we know channels/supportedFormats)
    settings = dev.getLiveStreamingServiceInfo()
    social = dev.getLiveStreamingSocialInfo()

    data = []
    # Social settings
    for key in (social._asdict()).items():
     if key[0] in ['twitterEnabled', 'facebookEnabled']:
      data.append([key[0], key[1]])
     else:
      data.append([key[0], key[1].decode('ascii').split('\x00')[0]])

    # Streaming settings, file may contain muliple sets of data
    try:
     for key in next(settings).items():
      if key[0] in ['service', 'enabled', 'videoFormat', 'enableRecordMode', \
        'unknown', 'channels', 'supportedFormats']:
       data.append([key[0], key[1]])
      elif key[0] == 'macIssueTime':
       data.append([key[0], binascii.b2a_hex(key[1]).decode('ascii')])
      else:
       data.append([key[0], key[1].decode('ascii').split('\x00')[0]])
    except StopIteration:
     pass

    if file:
     file.write(json.dumps(data, indent=4))
    else:
     for k, v in data:
      print('%-20s%s' % (k + ': ', v))


def wifiCommand(write=None, file=None, multi=False, driverName=None):
 """Read/Write WiFi information for the camera connected via usb"""
 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device:
   if not isinstance(device, SonyExtCmdDevice):
    print('Error: Cannot use camera in this mode.')
   else:
    dev = SonyExtCmdCamera(device)

    if write:
     incoming = json.load(write)
     data = struct.pack('<i', int(len(incoming)/3))

     mydict = {}
     count = 1
     for key in incoming:
      if key[0] == 'keyType':
       mydict[key[0]] = key[1] # Integer
      else:
       mydict[key[0]] = key[1].encode('ascii')

      if count == 3:
       # reassemble Struct
       apinfo = SonyExtCmdCamera.APInfo.pack(
        keyType = mydict['keyType'],
        sid = mydict['sid'].ljust(33, b'\x00'),
        key = mydict['key'].ljust(65, b'\x00'),
       )
       data += apinfo
       count = 1
      else:
       count += 1

     if multi:
      dev.setMultiWifiAPInfo(data)
     else:
      dev.setWifiAPInfo(data)
     return

    # Read settings from camera
    if multi:
     settings = dev.getMultiWifiAPInfo()
    else:
     settings = dev.getWifiAPInfo()

    data = []
    try:
     for key in next(settings)._asdict().items():
      if key[0] == 'keyType':
       data.append([key[0], key[1]]) # Integer
      else:
       data.append([key[0],key[1].decode('ascii').split('\x00')[0]])
    except StopIteration:
     pass

    if file:
     file.write(json.dumps(data, indent=4))
    else:
     for k, v in data:
      print('%-20s%s' % (k + ': ', v))


def _switchCameraToSenser(device):
 """Send the service-mode switch over an already-open libusb MSC handle."""
 print('Switching to service mode')
 dev = SonySenserAuthDevice(device.driver)
 dev.start()
 dev.authenticate()


def senserShellCommand(driverName=None, complete=None, interactive=None):
 """Enter service mode and run complete()/shell.

 interactive=False (used by the web GUI) auto-installs Windows libusb-win32
 drivers without a Y/n prompt when a driverless Sony device is detected.
 """
 if driverName is None and sys.platform != 'win32':
  driverName = 'libusb'
 if interactive is None:
  interactive = _driverInstallInteractive()

 switched = False
 modelName = None
 serviceDriverState = {}
 needMscLibusb = False
 needSenserLibusb = False
 senserPid = None

 with importDriver(driverName) as driver:
  device = getDevice(driver)
  if device and isinstance(device, SonyMscExtCmdDevice):
   if not isinstance(device.driver, GenericUsbDriver):
    if sys.platform != 'win32':
     print('Error: Only libusb drivers are supported for switching to service mode.')
     return
    needMscLibusb = True
    serviceDriverState['installed_msc_pid'] = getattr(device, 'idProduct', None)
   else:
    try:
     modelName = SonyExtCmdCamera(device).getCameraInfo().modelName
    except Exception:
     pass
    _switchCameraToSenser(device)
    switched = True

  elif device and isinstance(device, SonySenserDevice):
   if isinstance(device.driver, GenericUsbDriver):
    _runSenserContinuation(
     device, modelName, complete, serviceDriverState, interactive=interactive)
    return
   # Driverless / unusable native detection — install then reopen via libusb.
   needSenserLibusb = True
   senserPid = getattr(device, 'idProduct', None)
  elif device:
   print('Error: Cannot use camera in this mode. Please switch to mass storage mode.')
   return
  elif sys.platform == 'win32':
   # Camera already in service mode but invisible to libusb until a driver is bound.
   pids = _listDriverlessSenserPids()
   if pids:
    needSenserLibusb = True
    senserPid = pids[0]

 if needMscLibusb:
  mscPid = serviceDriverState.get('installed_msc_pid')
  print('Switching to service mode requires libusb on the mass-storage interface.')
  if not mscPid or not _promptInstallServiceDriver(
    mscPid, interactive=interactive, purpose='entering service mode'):
   print('Use Zadig 2.8 to bind the "libusb-win32" driver to the mass storage device '
    '(verify VID 054C/PID/interface first), then roll it back via Device Manager '
    'afterwards.')
   serviceDriverState['installed_msc_pid'] = None
   return
  print('Reopening camera with libusb...')
  with importDriver('libusb') as driver:
   device = getDevice(driver)
   if (not device or not isinstance(device, SonyMscExtCmdDevice)
     or not isinstance(device.driver, GenericUsbDriver)):
    print('Error: Camera not accessible via libusb after driver install.')
    _restoreInstalledDrivers(serviceDriverState)
    return
   try:
    modelName = SonyExtCmdCamera(device).getCameraInfo().modelName
   except Exception:
    pass
   _switchCameraToSenser(device)
   switched = True

 if needSenserLibusb:
  if senserPid is None:
   pids = _listDriverlessSenserPids()
   senserPid = pids[0] if pids else 0x0336
  print('Service mode device has no usable libusb driver yet.')
  if not _promptInstallServiceDriver(
    senserPid, interactive=interactive, purpose='service mode'):
   print('Use Zadig 2.8 to bind the "libusb-win32" driver to the service-mode device '
    '(verify VID 054C/PID/interface first). Do not replace the normal MTP/MSC driver, '
    'and roll it back via Device Manager afterwards.')
   return
  serviceDriverState['installed_pid'] = senserPid
  print('Reopening service-mode device with libusb...')
  _runSenserWithLibusb(modelName, complete, serviceDriverState)
  return

 if switched:
  print('')
  print('Waiting for camera to switch...')
  # After switch, prefer libusb so the newly bound service-mode device is usable.
  waitDriver = 'libusb' if sys.platform == 'win32' else driverName
  found = _waitForDevice(
   waitDriver, SonySenserDevice, 20, .5,
   lambda device: _runSenserContinuation(
    device, modelName, complete, serviceDriverState, interactive=interactive,
   ),
   autoInstallServiceDriver=True,
   serviceDriverState=serviceDriverState,
   interactive=interactive,
  )
  if not found:
   print('Operation timed out. Please run this command again when your camera has connected.')
   _restoreInstalledDrivers(serviceDriverState)


def _runSenserContinuation(
 device, modelName, complete, serviceDriverState=None, interactive=None,
):
 if serviceDriverState is None:
  serviceDriverState = {}

 if not isinstance(device.driver, GenericUsbDriver):
  print('Service mode device has no usable libusb driver yet.')
  if sys.platform != 'win32':
   print('Error: Only libusb drivers are supported for service mode.')
   return
  pid = getattr(device, 'idProduct', None)
  if pid is None and hasattr(device.driver, 'getId'):
   try:
    pid = device.driver.getId()[1]
   except Exception:
    pass
  if pid is None:
   pids = _listDriverlessSenserPids()
   pid = pids[0] if pids else 0x0336
  try:
   from ..usb.driver.windows.wdi import helper_available
   canAuto = helper_available()
  except ImportError:
   canAuto = False
  if not (canAuto and _promptInstallServiceDriver(
    pid, interactive=interactive, purpose='service mode')):
   print('Use Zadig 2.8 to bind the "libusb-win32" driver to the service-mode device '
    '(verify VID 054C/PID/interface first). Do not replace the normal MTP/MSC driver, '
    'and roll it back via Device Manager afterwards.')
   return
  serviceDriverState['installed_pid'] = pid
  print('Reopening service-mode device with libusb...')
  # Must open a fresh libusb context; the current device handle is unusable.
  _runSenserWithLibusb(modelName, complete, serviceDriverState)
  return

 # If a previous run installed the filter, pick up the pending restore PID.
 if serviceDriverState.get('installed_pid') is None and sys.platform == 'win32':
  try:
   from ..usb.driver.windows.wdi import pending_restore_pid
   pending = pending_restore_pid()
   if pending is not None:
    serviceDriverState['installed_pid'] = pending
  except ImportError:
   pass

 _runSenserSession(device, modelName, complete, serviceDriverState)


def _runSenserWithLibusb(modelName, complete, serviceDriverState):
 """Open the service-mode device via libusb and run the session to completion."""
 with importDriver('libusb') as driver:
  device = getDevice(driver)
  if not device or not isinstance(device, SonySenserDevice):
   print('Error: Service-mode device not found via libusb.')
   _restoreInstalledDrivers(serviceDriverState)
   return
  if not isinstance(device.driver, GenericUsbDriver):
   print('Error: Service-mode device still not accessible via libusb.')
   _restoreInstalledDrivers(serviceDriverState)
   return
  _runSenserSession(device, modelName, complete, serviceDriverState)


def _runSenserSession(device, modelName, complete, serviceDriverState):
 print('Authenticating')
 dev = SonySenserAuthDevice(device.driver)
 started = False
 try:
  try:
   dev.start()
   started = True
   dev.authenticate()
   if complete:
    complete(SonySenserCamera(device), modelName)
   else:
    print('Starting service shell...')
    print('')
    CameraShell(SenserPlatformBackend(SonySenserCamera(device))).run()
  finally:
   if started:
    if sys.exc_info()[0] is None:
     dev.stop()
    else:
     try:
      dev.stop()
     except BaseException:
      pass
 finally:
  _restoreInstalledDrivers(serviceDriverState)
 print('Done')
