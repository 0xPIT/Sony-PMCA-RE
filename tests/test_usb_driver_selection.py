import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from pmca.commands import usb
from pmca.usb.driver import (
 USB_CLASS_MSC,
 USB_CLASS_PTP,
 USB_CLASS_VENDOR_SPECIFIC,
 UsbDeviceHandle,
)


class FakeDriver:
 def __init__(self, source, handle, info=None, probeError=None):
  self.source = source
  self.handle = handle
  self.info = info
  self.probeError = probeError

 def reset(self):
  pass


class FakeContext:
 def __init__(
  self, name, classType, devices=(), enterError=None, listError=None
 ):
  self.name = name
  self.classType = classType
  self.devices = list(devices)
  self.enterError = enterError
  self.listError = listError
  self.enterCalls = 0
  self.exitCalls = 0
  self.listCalls = 0
  self.opened = []

 def __enter__(self):
  self.enterCalls += 1
  if self.enterError:
   raise self.enterError
  return self

 def __exit__(self, *ex):
  self.exitCalls += 1

 def listDevices(self, vendor):
  self.listCalls += 1
  if self.listError:
   raise self.listError
  return (device for device in self.devices if device.idVendor == vendor)

 def openDevice(self, device):
  self.opened.append(device)
  return FakeDriver(self.name, device.handle)


class FallbackFactory:
 def __init__(self, context):
  self.context = context
  self.calls = 0

 def __call__(self):
  self.calls += 1
  return self.context


class ProbeDevice:
 def __init__(self, driver):
  self.driver = driver

 def getDeviceInfo(self):
  if self.driver.probeError:
   raise self.driver.probeError
  return self.driver.info


class RecognizedDevice:
 def __init__(self, driver):
  self.driver = driver

 def laterCommand(self):
  raise RuntimeError('later command failed')


def camera(handle, product=1):
 return UsbDeviceHandle(handle, 0x054c, product)


def recognized_info(source):
 return SimpleNamespace(
  recognized=True, source=source, manufacturer='Sony', model='Camera'
 )


def unsupported_info(source):
 return SimpleNamespace(
  recognized=False, source=source, manufacturer='Other', model='Device'
 )


class DriverSelectionTestCase(unittest.TestCase):
 def recognition_context(self, driverList):
  def open_device(device):
   if 'open-error' in device.handle:
    raise RuntimeError('opening device failed')
   driver = FakeDriver(device.handle.split('-')[0], device.handle)
   if 'unsupported' in device.handle:
    driver.info = unsupported_info(driver.source)
   else:
    driver.info = recognized_info(driver.source)
   if 'probe-error' in device.handle:
    driver.probeError = RuntimeError('identity probe failed')
   return driver

  for context in driverList._drivers:
   context.openDevice = open_device
  for factory in driverList._fallbackFactories:
   factory[2].context.openDevice = open_device

  stack = ExitStack()
  stack.enter_context(mock.patch.object(usb, 'MtpDevice', ProbeDevice))
  stack.enter_context(mock.patch.object(usb, 'MscDevice', ProbeDevice))
  stack.enter_context(mock.patch.object(
        usb, 'isSonyMtpCamera',
        side_effect=lambda info: info.recognized,
       ))
  stack.enter_context(mock.patch.object(
        usb, 'isSonyMtpAppInstallCamera', return_value=False
       ))
  stack.enter_context(mock.patch.object(
        usb, 'isSonyMscCamera',
        side_effect=lambda info: info.recognized,
       ))
  stack.enter_context(mock.patch.object(
        usb, 'isSonyMscUpdaterCamera', return_value=False
       ))
  stack.enter_context(mock.patch.object(
        usb, 'SonyMtpExtCmdDevice', RecognizedDevice
       ))
  stack.enter_context(mock.patch.object(
        usb, 'SonyMscExtCmdDevice', RecognizedDevice
       ))
  return stack

 def recognize(self, driverList):
  with self.recognition_context(driverList):
   return list(usb.listDevices(driverList, quiet=True))

 def make_tier(self, classType, nativeHandle, fallbackHandle):
  native = FakeContext(
   'Windows', classType,
   () if nativeHandle is None else (camera(nativeHandle),),
  )
  fallback = FakeContext(
   'libusb', classType,
   () if fallbackHandle is None else (camera(fallbackHandle),),
  )
  factory = FallbackFactory(fallback)
  drivers = usb.UsbDriverList(
   native,
   fallbackFactories=((classType, fallback.name, factory),),
  )
  return native, fallback, factory, drivers


class WindowsDriverSelectionTest(DriverSelectionTestCase):
 def test_recognized_native_mtp_suppresses_libusb_tier(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-mtp', 'fallback-mtp'
  )

  with drivers:
   devices = self.recognize(drivers)

  self.assertEqual(['native'], [device.driver.source for device in devices])
  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)
  self.assertEqual(0, fallback.listCalls)
  self.assertEqual([], fallback.opened)

 def test_unsupported_native_mtp_uses_libusb_tier(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-unsupported-mtp', 'fallback-mtp'
  )

  with drivers:
   devices = self.recognize(drivers)

  self.assertEqual(
   ['fallback'], [device.driver.source for device in devices]
  )
  self.assertEqual(1, factory.calls)
  self.assertEqual(1, fallback.listCalls)

 def test_recognized_native_msc_suppresses_libusb_tier(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_MSC, 'native-msc', 'fallback-msc'
  )

  with drivers:
   devices = self.recognize(drivers)

  self.assertEqual(['native'], [device.driver.source for device in devices])
  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)
  self.assertEqual(0, fallback.listCalls)
  self.assertEqual([], fallback.opened)

 def test_unsupported_native_msc_uses_libusb_tier(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_MSC, 'native-unsupported-msc', 'fallback-msc'
  )

  with drivers:
   devices = self.recognize(drivers)

  self.assertEqual(
   ['fallback'], [device.driver.source for device in devices]
  )
  self.assertEqual(1, factory.calls)
  self.assertEqual(1, fallback.listCalls)

 def test_ordinary_unsupported_identity_allows_fallback(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-unsupported-mtp', None
  )

  with drivers:
   self.assertEqual([], self.recognize(drivers))

  self.assertEqual(1, factory.calls)
  self.assertEqual(1, fallback.listCalls)

 def test_unexpected_identity_probe_failure_propagates(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-probe-error-mtp', 'fallback-mtp'
  )

  with drivers:
   with self.assertRaisesRegex(RuntimeError, 'identity probe failed'):
    self.recognize(drivers)

  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)

 def test_later_failure_after_native_selection_does_not_fallback(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-mtp', 'fallback-mtp'
  )

  with drivers:
   device = self.recognize(drivers)[0]
   with self.assertRaisesRegex(RuntimeError, 'later command failed'):
    device.laterCommand()

  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)

 def test_unexpected_native_context_failure_propagates(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, None, 'fallback-mtp'
  )
  native.enterError = RuntimeError('unexpected WPD failure')

  with self.assertRaisesRegex(RuntimeError, 'unexpected WPD failure'):
   with drivers:
    pass

  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)

 def test_unexpected_native_enumeration_failure_propagates(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, None, 'fallback-mtp'
  )
  native.listError = RuntimeError('unexpected enumeration failure')

  with drivers:
   with self.assertRaisesRegex(
    RuntimeError, 'unexpected enumeration failure'
   ):
    self.recognize(drivers)

  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)

 def test_unexpected_native_open_failure_propagates(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-open-error-mtp', 'fallback-mtp'
  )

  with drivers:
   with self.assertRaisesRegex(RuntimeError, 'opening device failed'):
    self.recognize(drivers)

  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)

 def test_mtp_and_msc_fallback_are_independent(self):
  nativeMtp = FakeContext(
   'Windows-MTP', USB_CLASS_PTP, [camera('native-mtp')]
  )
  nativeMsc = FakeContext(
   'Windows-MSC', USB_CLASS_MSC,
   [camera('native-unsupported-msc')],
  )
  fallbackMtp = FakeContext(
   'libusb-MTP', USB_CLASS_PTP, [camera('fallback-mtp')]
  )
  fallbackMsc = FakeContext(
   'libusb-MSC', USB_CLASS_MSC, [camera('fallback-msc')]
  )
  mtpFactory = FallbackFactory(fallbackMtp)
  mscFactory = FallbackFactory(fallbackMsc)
  drivers = usb.UsbDriverList(
   nativeMtp, nativeMsc,
   fallbackFactories=(
    (USB_CLASS_PTP, fallbackMtp.name, mtpFactory),
    (USB_CLASS_MSC, fallbackMsc.name, mscFactory),
   ),
  )

  with drivers:
   devices = self.recognize(drivers)

  self.assertEqual(
   {'native-mtp', 'fallback-msc'},
   {device.driver.handle for device in devices},
  )
  self.assertEqual(0, mtpFactory.calls)
  self.assertEqual(1, mscFactory.calls)

 def test_msc_and_mtp_fallback_are_independent(self):
  nativeMsc = FakeContext(
   'Windows-MSC', USB_CLASS_MSC, [camera('native-msc')]
  )
  nativeMtp = FakeContext(
   'Windows-MTP', USB_CLASS_PTP,
   [camera('native-unsupported-mtp')],
  )
  fallbackMsc = FakeContext(
   'libusb-MSC', USB_CLASS_MSC, [camera('fallback-msc')]
  )
  fallbackMtp = FakeContext(
   'libusb-MTP', USB_CLASS_PTP, [camera('fallback-mtp')]
  )
  mscFactory = FallbackFactory(fallbackMsc)
  mtpFactory = FallbackFactory(fallbackMtp)
  drivers = usb.UsbDriverList(
   nativeMsc, nativeMtp,
   fallbackFactories=(
    (USB_CLASS_MSC, fallbackMsc.name, mscFactory),
    (USB_CLASS_PTP, fallbackMtp.name, mtpFactory),
   ),
  )

  with drivers:
   devices = self.recognize(drivers)

  self.assertEqual(
   {'native-msc', 'fallback-mtp'},
   {device.driver.handle for device in devices},
  )
  self.assertEqual(0, mscFactory.calls)
  self.assertEqual(1, mtpFactory.calls)

 def test_multiple_native_devices_suppress_fallback_and_are_reported(self):
  native = FakeContext(
   'Windows-MTP', USB_CLASS_PTP,
   [camera('native-mtp-one'), camera('native-mtp-two')],
  )
  fallback = FakeContext(
   'libusb-MTP', USB_CLASS_PTP, [camera('fallback-mtp')]
  )
  factory = FallbackFactory(fallback)
  drivers = usb.UsbDriverList(
   native,
   fallbackFactories=((USB_CLASS_PTP, fallback.name, factory),),
  )

  output = StringIO()
  with drivers, self.recognition_context(drivers), \
       redirect_stdout(output):
   selected = usb.getDevice(drivers)

  self.assertIsNone(selected)
  self.assertIn('Too many Sony devices found', output.getvalue())
  self.assertEqual(0, factory.calls)
  self.assertEqual(0, fallback.enterCalls)

 def test_fallback_factory_is_entered_once_per_context_lifetime(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-unsupported-mtp', 'fallback-mtp'
  )

  with drivers:
   first = self.recognize(drivers)
   second = self.recognize(drivers)

  self.assertEqual(1, factory.calls)
  self.assertEqual(1, fallback.enterCalls)
  self.assertEqual(2, fallback.listCalls)
  self.assertEqual(
   ['fallback-mtp'], [device.driver.handle for device in first]
  )
  self.assertEqual(
   ['fallback-mtp'], [device.driver.handle for device in second]
  )

 def test_missing_native_modules_leave_default_windows_fallbacks(self):
  missing = {
   'pmca.usb.driver.windows.msc': None,
   'pmca.usb.driver.windows.wpd': None,
  }
  with mock.patch.object(usb.sys, 'platform', 'win32'), \
       mock.patch.dict(usb.sys.modules, missing), \
       redirect_stdout(StringIO()):
   drivers = usb.importDriver()

  self.assertEqual((), drivers._contexts)
  self.assertEqual(
   ['libusb-MSC', 'libusb-MTP', 'libusb-vendor-specific'],
   [name for classType, name, factory in drivers._fallbackFactories],
  )

 def test_default_windows_service_mode_uses_lazy_libusb_factory(self):
  with mock.patch.object(usb.sys, 'platform', 'win32'), \
       redirect_stdout(StringIO()):
   drivers = usb.importDriver()

  self.assertEqual(
   ['Windows-MSC', 'Windows-MTP'],
   [context.name for context in drivers._contexts],
  )
  self.assertEqual(
   ['libusb-MSC', 'libusb-MTP', 'libusb-vendor-specific'],
   [name for classType, name, factory in drivers._fallbackFactories],
  )
  self.assertEqual(
   [USB_CLASS_VENDOR_SPECIFIC],
   [classType for classType, name, factory in drivers._fallbackFactories
    if classType == USB_CLASS_VENDOR_SPECIFIC],
  )
  self.assertEqual({}, drivers._fallbackDriverMap)

 def test_non_windows_default_selection_remains_flat(self):
  with mock.patch.object(usb.sys, 'platform', 'linux'), \
       redirect_stdout(StringIO()):
   drivers = usb.importDriver()

  self.assertEqual((), drivers._fallbackFactories)
  self.assertEqual(
   ['libusb-MSC', 'libusb-MTP', 'libusb-vendor-specific'],
   [context.name for context in drivers._contexts],
  )

 def test_generic_driver_restriction_is_preserved(self):
  class NativeDriver:
   def reset(self):
    pass

  class DriverContext:
   def __enter__(self):
    return object()

   def __exit__(self, *ex):
    pass

  device = object.__new__(usb.SonyMscExtCmdDevice)
  device.driver = NativeDriver()
  with mock.patch.object(
   usb, 'importDriver', return_value=DriverContext()
  ), mock.patch.object(
   usb, 'getDevice', return_value=device
  ), mock.patch.object(
   usb, 'SonySenserAuthDevice'
  ) as senser, redirect_stdout(StringIO()):
   usb.senserShellCommand()

  senser.assert_not_called()


class DriverContextCleanupTest(DriverSelectionTestCase):
 def test_native_recognition_closes_only_initialized_contexts(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-mtp', 'fallback-mtp'
  )

  with drivers:
   self.recognize(drivers)

  self.assertEqual(1, native.exitCalls)
  self.assertEqual(0, fallback.enterCalls)
  self.assertEqual(0, fallback.exitCalls)

 def test_unsupported_native_closes_native_and_fallback_contexts(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-unsupported-mtp', 'fallback-mtp'
  )

  with drivers:
   self.recognize(drivers)

  self.assertEqual(1, native.exitCalls)
  self.assertEqual(1, fallback.exitCalls)

 def test_probe_exception_closes_initialized_native_context(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, 'native-probe-error-mtp', 'fallback-mtp'
  )

  with self.assertRaisesRegex(RuntimeError, 'identity probe failed'):
   with drivers:
    self.recognize(drivers)

  self.assertEqual(1, native.exitCalls)
  self.assertEqual(0, fallback.enterCalls)
  self.assertEqual(0, fallback.exitCalls)

 def test_fallback_enumeration_exception_closes_all_entered_contexts(self):
  native, fallback, factory, drivers = self.make_tier(
   USB_CLASS_PTP, None, None
  )
  fallback.listError = RuntimeError('libusb discovery failed')

  with self.assertRaisesRegex(RuntimeError, 'libusb discovery failed'):
   with drivers:
    self.recognize(drivers)

  self.assertEqual(1, native.exitCalls)
  self.assertEqual(1, fallback.exitCalls)


if __name__ == '__main__':
 unittest.main()
