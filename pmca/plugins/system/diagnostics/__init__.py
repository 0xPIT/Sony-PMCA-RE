"""System diagnostics for USB/camera connectivity issues."""

import sys
from collections import namedtuple

DiagResult = namedtuple('DiagResult', 'status, label, detail, solution')


def check_libusb_available():
 """Check if the libusb backend is importable and functional.

 On Windows, libusb is only required for service mode; normal MTP/Mass
 Storage operations use the native Windows driver. A missing libusb runtime
 is therefore a warning (not a failure) on Windows."""
 try:
  import usb.core
  import usb.backend.libusb1
  backend = usb.backend.libusb1.get_backend()
  if backend is None:
   if sys.platform == 'win32':
    return DiagResult('warn', 'libusb backend',
     'libusb runtime (libusb-1.0.dll) not found by PyUSB. Normal MTP/Mass Storage '
     'operations use the native Windows driver and still work; libusb is only needed '
     'for service mode.',
     'Optional (service mode only): place libusb-1.0.dll on your PATH or next to the '
     'executable, and bind the service-mode device with Zadig.')
   return DiagResult('fail', 'libusb backend',
    'libusb shared library not found by PyUSB.',
    'Install libusb: "brew install libusb" (macOS) or "apt install libusb-1.0-0-dev" (Linux).')
  return DiagResult('pass', 'libusb backend', 'libusb backend loaded successfully.', None)
 except ImportError as e:
  return DiagResult('fail', 'libusb backend',
   'PyUSB not installed: %s' % str(e),
   'Install PyUSB: pip install pyusb')
 except Exception as e:
  return DiagResult('fail', 'libusb backend',
   'Unexpected error: %s' % str(e),
   'Check your libusb installation.')


def check_pyusb_version():
 """Report PyUSB version."""
 try:
  import usb.core
  version = getattr(usb, '__version__', 'unknown')
  return DiagResult('pass', 'PyUSB version', 'PyUSB %s' % version, None)
 except ImportError:
  return DiagResult('fail', 'PyUSB version', 'PyUSB not installed.', 'pip install pyusb')


def check_sony_device_visible():
 """Check if any Sony USB device is visible to libusb (vendor ID 0x054c).

 On Windows, cameras in MTP/Mass Storage mode are handled by the native
 driver and are not visible to libusb; absence there is expected, not an error."""
 try:
  import usb.core
  devices = list(usb.core.find(find_all=True, idVendor=0x054c))
  if not devices:
   if sys.platform == 'win32':
    return DiagResult('warn', 'Sony USB device (libusb)',
     'No Sony device visible to libusb. On Windows this is expected for cameras in '
     'MTP/Mass Storage mode, which use the native Windows driver rather than libusb.',
     'Only relevant for service mode. For normal operations, ensure the camera is '
     'connected, powered on, and set to USB Mass Storage mode.')
   return DiagResult('warn', 'Sony USB device',
    'No Sony USB device detected (vendor 0x054c).',
    'Ensure camera is connected, powered on, and set to USB Mass Storage mode. Try a different USB cable or port.')
  names = []
  for d in devices:
   names.append('PID 0x%04x' % d.idProduct)
  return DiagResult('pass', 'Sony USB device',
   'Found %d Sony device(s): %s' % (len(devices), ', '.join(names)), None)
 except Exception as e:
  return DiagResult('fail', 'Sony USB device',
   'Error scanning USB: %s' % str(e),
   'Check libusb installation and USB permissions.')


def check_python_version():
 """Check Python version compatibility."""
 major, minor = sys.version_info[:2]
 version_str = '%d.%d.%d' % sys.version_info[:3]
 if major < 3 or (major == 3 and minor < 7):
  return DiagResult('warn', 'Python version',
   'Python %s detected. Python 3.7+ recommended.' % version_str,
   'Upgrade Python to 3.7 or later.')
 return DiagResult('pass', 'Python version', 'Python %s' % version_str, None)


def check_crypto_available():
 """Check if cryptography libraries are available (needed for service mode)."""
 try:
  try:
   from Cryptodome.Hash import SHA256
   return DiagResult('pass', 'Crypto library', 'PyCryptodome available.', None)
  except ImportError:
   from Crypto.Hash import SHA256
   return DiagResult('pass', 'Crypto library', 'PyCrypto available.', None)
 except ImportError:
  return DiagResult('warn', 'Crypto library',
   'Neither PyCryptodome nor PyCrypto found.',
   'Install with: pip install pycryptodome. Required for service mode operations.')


def _camera_identity_result(device):
 """Read model/firmware over the ext-command interface (read-only, best effort)."""
 from pmca.usb.sony import SonyExtCmdCamera, SonyUpdaterCamera
 try:
  info = SonyExtCmdCamera(device).getCameraInfo()
  detail = '%s (product code %s, serial %s)' % (info.modelName, info.modelCode, info.serial)
 except Exception as e:
  return DiagResult('warn', 'Camera identity',
   'Camera detected but its identity could not be read: %s' % str(e),
   'Try another USB mode, cable or port, and quit any app that may be using the camera.')
 try:
  updater = SonyUpdaterCamera(device)
  updater.init()
  firmwareOld, _ = updater.getFirmwareVersion()
  if firmwareOld:
   detail += ', firmware %s' % firmwareOld
 except Exception:
  pass
 return DiagResult('pass', 'Camera identity', detail, None)


def check_camera_connectivity():
 """Detect the camera through the app's real native driver stack.

 This uses the same enumeration path the app itself uses, so it surfaces the
 real early-failure classes (no device, wrong USB mode, missing native driver,
 device claimed by another app) rather than only what libusb can see. Returns a
 list of DiagResults: a connection/mode result plus an identity result when the
 camera is reachable over the ext-command interface."""
 import contextlib
 import io as _io

 try:
  from pmca.commands.usb import importDriver, listDevices
  from pmca.usb.sony import (
   SonyMscExtCmdDevice, SonyMscUpdaterDevice, SonyMtpExtCmdDevice,
   SonyMtpAppInstallDevice, SonySenserDevice, SonyExtCmdDevice)
 except Exception as e:
  return [DiagResult('warn', 'Camera connection',
   'Could not load the USB driver stack: %s' % str(e), None)]

 buf = _io.StringIO()
 try:
  with contextlib.redirect_stdout(buf):
   with importDriver() as driver:
    devices = list(listDevices(driver, quiet=True))
    identity = None
    if len(devices) == 1 and isinstance(devices[0], SonyExtCmdDevice):
     identity = _camera_identity_result(devices[0])
 except Exception as e:
  return [DiagResult('warn', 'Camera connection',
   'Could not scan for a camera through the native driver: %s' % str(e),
   'Ensure the camera is connected and the required native driver is installed. '
   'On macOS run with sudo; on Windows install the Sony camera driver.')]

 if not devices:
  return [DiagResult('warn', 'Camera connection',
   'No Sony camera detected by the native driver stack.',
   'Connect the camera, power it on, unlock the screen, and set USB to Mass Storage mode. '
   'On macOS, install the Sony Camera Driver and run with sudo.')]
 if len(devices) > 1:
  return [DiagResult('warn', 'Camera connection',
   'Multiple Sony devices detected; only one camera is supported at a time.',
   'Disconnect the other Sony device(s) and re-run diagnostics.')]

 modes = [
  (SonyMscExtCmdDevice, 'pass', 'Camera connected in USB Mass Storage mode.', None),
  (SonyMtpExtCmdDevice, 'warn', 'Camera connected in MTP mode.',
   'Some operations require Mass Storage mode. If one fails, switch the camera USB '
   'connection to Mass Storage in its menu.'),
  (SonyMtpAppInstallDevice, 'pass', 'Camera connected in app-install (PlayMemories) mode.', None),
  (SonyMscUpdaterDevice, 'pass', 'Camera connected in firmware updater mode.', None),
  (SonySenserDevice, 'pass', 'Camera connected in service mode.', None),
 ]
 status, detail, solution = 'warn', 'Camera detected but its USB mode is unrecognized.', None
 for cls, st, dt, sol in modes:
  if isinstance(devices[0], cls):
   status, detail, solution = st, dt, sol
   break

 results = [DiagResult(status, 'Camera connection', detail, solution)]
 if identity is not None:
  results.append(identity)
 return results


def run_common_checks():
 """Run all platform-independent diagnostics."""
 return [
  check_python_version(),
  check_libusb_available(),
  check_pyusb_version(),
  check_crypto_available(),
  check_sony_device_visible(),
 ]


def run_all_checks():
 """Run common checks plus platform-specific checks, sorted by severity."""
 results = run_common_checks()
 results.extend(check_camera_connectivity())

 if sys.platform == 'darwin':
  from .diagnostics_macos import run_macos_checks
  results.extend(run_macos_checks())
 elif sys.platform == 'win32':
  from .diagnostics_windows import run_windows_checks
  results.extend(run_windows_checks())
 elif sys.platform.startswith('linux'):
  from .diagnostics_linux import run_linux_checks
  results.extend(run_linux_checks())

 order = {'fail': 0, 'warn': 1, 'pass': 2}
 results.sort(key=lambda r: order.get(r.status, 3))
 return results
