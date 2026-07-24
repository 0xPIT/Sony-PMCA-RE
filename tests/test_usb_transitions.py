import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from pmca.commands import usb
from pmca.usb.driver.generic import GenericUsbDriver


class FakeContext:
 def __init__(self, name, events, devices=(), enterError=None):
  self.name = name
  self.events = events
  self.devices = list(devices)
  self.enterError = enterError
  self.active = False
  self.enterCalls = 0
  self.exitCalls = 0

 def __enter__(self):
  self.events.append('enter:%s' % self.name)
  self.enterCalls += 1
  if self.enterError:
   raise self.enterError
  self.active = True
  return self

 def __exit__(self, *ex):
  self.events.append('exit:%s' % self.name)
  self.exitCalls += 1
  self.active = False


class OwnedDevice:
 def __init__(self, owner, mode='wrong'):
  self.owner = owner
  self.mode = mode

 def use(self):
  if not self.owner.active:
   raise RuntimeError('device used after context exit')


class WrongDevice:
 pass


class FakeGenericDriver(GenericUsbDriver):
 def __init__(self):
  pass

 def reset(self):
  pass

 def getId(self):
  return 0x054c, 0x0336


def sony_device(deviceType, driver=None):
 device = object.__new__(deviceType)
 device.driver = driver or SimpleNamespace(reset=lambda: None)
 return device


class FreshContextPollingTest(unittest.TestCase):
 def setUp(self):
  self.events = []

 def run_wait(
  self, contexts, expectedType=OwnedDevice, driverName='native',
  continuation=lambda device: device.use(), attempts=None
 ):
  if attempts is None:
   attempts = len(contexts)
  with mock.patch.object(
   usb, 'importDriver', side_effect=contexts
  ) as importDriver, mock.patch.object(
   usb, 'listDevices', side_effect=lambda driver, quiet: driver.devices
  ), mock.patch.object(usb.time, 'sleep'):
   result = usb._waitForDevice(
    driverName, expectedType, attempts, .5, continuation
   )
  return result, importDriver

 def test_each_unsuccessful_attempt_uses_a_fresh_context(self):
  empty = FakeContext('empty', self.events)
  wrong = FakeContext('wrong', self.events)
  target = FakeContext('target', self.events)
  wrong.devices = [WrongDevice()]
  target.devices = [OwnedDevice(target, 'target')]

  result, importDriver = self.run_wait([empty, wrong, target])

  self.assertTrue(result)
  self.assertEqual(3, importDriver.call_count)
  self.assertEqual(
   [
    'enter:empty', 'exit:empty',
    'enter:wrong', 'exit:wrong',
    'enter:target', 'exit:target',
   ],
   self.events,
  )
  self.assertEqual([1, 1, 1], [
   empty.enterCalls, wrong.enterCalls, target.enterCalls
  ])

 def test_target_is_consumed_only_while_context_is_active(self):
  targetContext = FakeContext('target', self.events)
  target = OwnedDevice(targetContext, 'target')
  targetContext.devices = [target]
  activeDuringContinuation = []

  result, unused = self.run_wait(
   [targetContext],
   continuation=lambda device: (
    activeDuringContinuation.append(device.owner.active), device.use()
   ),
  )

  self.assertTrue(result)
  self.assertEqual([True], activeDuringContinuation)
  self.assertFalse(targetContext.active)
  with self.assertRaisesRegex(RuntimeError, 'after context exit'):
   target.use()

 def test_empty_and_wrong_mode_retry_then_target_succeeds(self):
  empty = FakeContext('empty', self.events)
  wrong = FakeContext('wrong', self.events)
  target = FakeContext('target', self.events)
  wrong.devices = [WrongDevice()]
  target.devices = [OwnedDevice(target, 'target')]

  result, unused = self.run_wait([empty, wrong, target])

  self.assertTrue(result)
  self.assertEqual(1, target.exitCalls)

 def test_multiple_devices_fail_immediately(self):
  context = FakeContext('multiple', self.events)
  context.devices = [OwnedDevice(context), OwnedDevice(context)]

  with self.assertRaisesRegex(
   Exception, 'Multiple Sony devices found'
  ):
   self.run_wait([context, FakeContext('unused', self.events)])

  self.assertEqual(['enter:multiple', 'exit:multiple'], self.events)

 def test_timeout_returns_false_after_fresh_attempts(self):
  contexts = [
   FakeContext('attempt-%d' % i, self.events) for i in range(3)
  ]

  result, importDriver = self.run_wait(contexts)

  self.assertFalse(result)
  self.assertEqual(3, importDriver.call_count)
  self.assertEqual([1, 1, 1], [context.exitCalls for context in contexts])

 def test_driver_name_is_preserved_for_every_attempt(self):
  contexts = [
   FakeContext('attempt-%d' % i, self.events) for i in range(3)
  ]

  result, importDriver = self.run_wait(
   contexts, driverName='qemu'
  )

  self.assertFalse(result)
  self.assertEqual(
   [mock.call('qemu'), mock.call('qemu'), mock.call('qemu')],
   importDriver.call_args_list,
  )

 def test_context_construction_and_entry_failures_propagate(self):
  with self.subTest('construction'):
   with mock.patch.object(
    usb, 'importDriver',
    side_effect=RuntimeError('construction failed'),
   ), mock.patch.object(usb.time, 'sleep'):
    with self.assertRaisesRegex(RuntimeError, 'construction failed'):
     usb._waitForDevice('native', OwnedDevice, 1, .5, lambda dev: None)

  with self.subTest('entry'):
   context = FakeContext(
    'entry', self.events, enterError=RuntimeError('entry failed')
   )
   with self.assertRaisesRegex(RuntimeError, 'entry failed'):
    self.run_wait([context])
   self.assertEqual(0, context.exitCalls)

 def test_discovery_failures_propagate_without_another_attempt(self):
  failures = (
   'enumeration failed',
   'open failed',
   'identity probe failed',
   'recognition failed',
  )
  for message in failures:
   with self.subTest(message):
    context = FakeContext(message, self.events)
    with mock.patch.object(
     usb, 'importDriver', return_value=context
    ) as importDriver, mock.patch.object(
     usb, 'listDevices', side_effect=RuntimeError(message)
    ), mock.patch.object(usb.time, 'sleep'):
     with self.assertRaisesRegex(RuntimeError, message):
      usb._waitForDevice(
       'native', OwnedDevice, 3, .5, lambda dev: None
      )
    self.assertEqual(1, importDriver.call_count)
    self.assertEqual(1, context.exitCalls)

 def test_continuation_failure_propagates_and_closes_once(self):
  targetContext = FakeContext('target', self.events)
  targetContext.devices = [OwnedDevice(targetContext, 'target')]

  with self.assertRaisesRegex(RuntimeError, 'continuation failed'):
   self.run_wait(
    [targetContext, FakeContext('unused', self.events)],
    continuation=lambda device: (
     device.use(),
     (_ for _ in ()).throw(RuntimeError('continuation failed')),
    ),
   )

  self.assertEqual(1, targetContext.exitCalls)
  self.assertNotIn('enter:unused', self.events)

 def test_selected_device_failure_does_not_start_another_attempt(self):
  targetContext = FakeContext('target', self.events)
  targetContext.devices = [OwnedDevice(targetContext, 'target')]
  unusedContext = FakeContext('unused', self.events)
  importDriver = mock.Mock(
   side_effect=[targetContext, unusedContext]
  )

  with mock.patch.object(
   usb, 'importDriver', importDriver
  ), mock.patch.object(
   usb, 'listDevices', side_effect=lambda driver, quiet: driver.devices
  ), mock.patch.object(usb.time, 'sleep'):
   with self.assertRaisesRegex(RuntimeError, 'operational failure'):
    usb._waitForDevice(
     None, OwnedDevice, 2, .5,
     lambda device: (
      device.use(),
      (_ for _ in ()).throw(RuntimeError('operational failure')),
     ),
    )

  self.assertEqual(1, importDriver.call_count)
  self.assertEqual(1, targetContext.exitCalls)
  self.assertEqual(0, unusedContext.enterCalls)


class TransitionCommandTest(unittest.TestCase):
 def setUp(self):
  self.events = []

 def test_app_source_exits_before_poll_and_install_runs_inside_target(self):
  sourceContext = FakeContext('source', self.events)
  targetContext = FakeContext('target', self.events)
  source = sony_device(usb.SonyMscExtCmdDevice)
  target = sony_device(usb.SonyMtpAppInstallDevice)
  target.owner = targetContext
  targetContext.devices = [target]

  camera = mock.Mock()
  camera.switchToAppInstaller.side_effect = lambda: self.events.append(
   'switch'
  )

  def install(device, *args):
   self.assertTrue(device.owner.active)
   self.events.append('install')

  with mock.patch.object(
   usb, 'importDriver', side_effect=[sourceContext, targetContext]
  ) as importDriver, mock.patch.object(
   usb, 'getDevice', return_value=source
  ), mock.patch.object(
   usb, 'listDevices', side_effect=lambda driver, quiet: driver.devices
  ), mock.patch.object(
   usb, 'SonyExtCmdCamera', return_value=camera
  ), mock.patch.object(
   usb, 'installApp', side_effect=install
  ), mock.patch.object(usb.time, 'sleep'):
   usb.installCommand('native', appPackage='app')

  self.assertEqual(
   ['enter:source', 'switch', 'exit:source',
    'enter:target', 'install', 'exit:target'],
   self.events,
  )
  self.assertEqual(
   [mock.call('native'), mock.call('native')],
   importDriver.call_args_list,
  )

 def test_app_timeout_preserves_existing_message(self):
  sourceContext = FakeContext('source', self.events)
  source = sony_device(usb.SonyMscExtCmdDevice)
  polls = [
   FakeContext('poll-%d' % i, self.events) for i in range(10)
  ]
  output = io.StringIO()

  with mock.patch.object(
   usb, 'importDriver', side_effect=[sourceContext] + polls
  ), mock.patch.object(
   usb, 'getDevice', return_value=source
  ), mock.patch.object(
   usb, 'listDevices', return_value=[]
  ), mock.patch.object(
   usb, 'SonyExtCmdCamera', return_value=mock.Mock()
  ), mock.patch.object(usb.time, 'sleep'), redirect_stdout(output):
   usb.installCommand('libusb')

  self.assertIn(
   'Operation timed out. Please run this command again',
   output.getvalue(),
  )
  self.assertEqual(1, sourceContext.exitCalls)
  self.assertTrue(all(context.exitCalls == 1 for context in polls))

 def test_existing_app_install_mode_does_not_poll(self):
  context = FakeContext('existing', self.events)
  device = sony_device(usb.SonyMtpAppInstallDevice)
  device.owner = context

  def install(target, *args):
   self.assertTrue(target.owner.active)

  with mock.patch.object(
   usb, 'importDriver', return_value=context
  ) as importDriver, mock.patch.object(
   usb, 'getDevice', return_value=device
  ), mock.patch.object(usb, 'installApp', side_effect=install):
   usb.installCommand('native')

  self.assertEqual(1, importDriver.call_count)
  self.assertEqual(1, context.exitCalls)

 def test_firmware_continuation_runs_inside_fresh_target_context(self):
  sourceContext = FakeContext('source', self.events)
  targetContext = FakeContext('target', self.events)
  source = sony_device(usb.SonyMscExtCmdDevice)
  target = sony_device(usb.SonyMscUpdaterDevice)
  target.owner = targetContext
  targetContext.devices = [target]
  updaterInstances = []

  class FakeUpdater:
   def __init__(self, device):
    self.device = device
    updaterInstances.append(self)

   def init(self):
    pass

   def checkGuard(self, file, size):
    self.assertPosition(file)

   def assertPosition(self, file):
    if file.tell() != 0:
     raise AssertionError('firmware position was not preserved')

   def getFirmwareVersion(self):
    return '1.00', '2.00'

   def switchMode(self):
    self.events.append('switch')

   def writeFirmware(self, file, size, complete):
    if not self.device.owner.active:
     raise AssertionError('target context exited before firmware continuation')
    self.events.append('write')

   def complete(self):
    self.events.append('complete')

  FakeUpdater.events = self.events
  file = io.BytesIO(b'test')

  with mock.patch.object(
   usb.firmware, 'readDat', return_value=(0, 4)
  ), mock.patch.object(
   usb, 'importDriver', side_effect=[sourceContext, targetContext]
  ) as importDriver, mock.patch.object(
   usb, 'getDevice', return_value=source
  ), mock.patch.object(
   usb, 'listDevices', side_effect=lambda driver, quiet: driver.devices
  ), mock.patch.object(
   usb, 'SonyUpdaterCamera', FakeUpdater
  ), mock.patch.object(usb.time, 'sleep'):
   usb.firmwareUpdateCommand(file, 'qemu')

  self.assertEqual(
   ['enter:source', 'switch', 'exit:source',
    'enter:target', 'write', 'complete', 'exit:target'],
   self.events,
  )
  self.assertEqual(
   [mock.call('qemu'), mock.call('qemu')],
   importDriver.call_args_list,
  )
  self.assertEqual(2, len(updaterInstances))

 def test_service_continuation_runs_inside_target_and_stops_auth(self):
  sourceContext = FakeContext('source', self.events)
  targetContext = FakeContext('target', self.events)
  sourceDriver = FakeGenericDriver()
  targetDriver = FakeGenericDriver()
  source = sony_device(usb.SonyMscExtCmdDevice, sourceDriver)
  target = sony_device(usb.SonySenserDevice, targetDriver)
  target.owner = targetContext
  targetContext.devices = [target]
  authByDriver = {}

  class FakeAuth:
   def __init__(self, driver):
    self.driver = driver
    self.stopCalls = 0
    authByDriver[id(driver)] = self

   def start(self):
    self.events.append(
     'source-start' if self.driver is sourceDriver else 'target-start'
    )

   def authenticate(self):
    self.events.append(
     'source-auth' if self.driver is sourceDriver else 'target-auth'
    )

   def stop(self):
    self.stopCalls += 1
    self.events.append('target-stop')

  FakeAuth.events = self.events
  camera = mock.Mock()
  camera.getCameraInfo.return_value = SimpleNamespace(modelName='ILCE')

  def complete(device, modelName):
   self.assertTrue(target.owner.active)
   self.assertEqual('ILCE', modelName)
   self.events.append('complete')

  with mock.patch.object(
   usb, 'importDriver', side_effect=[sourceContext, targetContext]
  ) as importDriver, mock.patch.object(
   usb, 'getDevice', return_value=source
  ), mock.patch.object(
   usb, 'listDevices', side_effect=lambda driver, quiet: driver.devices
  ), mock.patch.object(
   usb, 'SonyExtCmdCamera', return_value=camera
  ), mock.patch.object(
   usb, 'SonySenserAuthDevice', FakeAuth
  ), mock.patch.object(
   usb, 'SonySenserCamera', return_value=object()
  ), mock.patch.object(usb.time, 'sleep'):
   usb.senserShellCommand('libusb', complete)

  self.assertEqual(
   [
    'enter:source', 'source-start', 'source-auth', 'exit:source',
    'enter:target', 'target-start', 'target-auth', 'complete',
    'target-stop', 'exit:target',
   ],
   self.events,
  )
  self.assertEqual(0, authByDriver[id(sourceDriver)].stopCalls)
  self.assertEqual(1, authByDriver[id(targetDriver)].stopCalls)
  self.assertEqual(
   [mock.call('libusb'), mock.call('libusb')],
   importDriver.call_args_list,
  )

 def test_service_continuation_failure_still_stops_and_closes_target(self):
  context = FakeContext('service', self.events)
  driver = FakeGenericDriver()
  device = sony_device(usb.SonySenserDevice, driver)
  device.owner = context
  context.devices = [device]
  auth = mock.Mock()
  complete = mock.Mock(side_effect=RuntimeError('service failed'))

  with mock.patch.object(
   usb, 'importDriver', return_value=context
  ) as importDriver, mock.patch.object(
   usb, 'listDevices', side_effect=lambda current, quiet: current.devices
  ), mock.patch.object(
   usb, 'SonySenserAuthDevice', return_value=auth
  ), mock.patch.object(
   usb, 'SonySenserCamera', return_value=object()
  ), mock.patch.object(usb.time, 'sleep'):
   with self.assertRaisesRegex(RuntimeError, 'service failed'):
    usb._waitForDevice(
     'libusb', usb.SonySenserDevice, 2, .5,
     lambda target: usb._runSenserContinuation(
      target, 'ILCE', complete
     ),
    )

  self.assertEqual(1, importDriver.call_count)
  self.assertEqual(1, context.exitCalls)
  auth.stop.assert_called_once_with()


class ServiceCleanupTest(unittest.TestCase):
 def setUp(self):
  self.events = []

 def make_operation(
  self, startError=None, authError=None, stopError=None,
  continuationError=None
 ):
  context = FakeContext('service', self.events)
  driver = FakeGenericDriver()
  device = sony_device(usb.SonySenserDevice, driver)
  device.owner = context
  context.devices = [device]

  class FakeAuth:
   def __init__(self):
    self.stopCalls = 0
    self.stopActive = []

   def start(self):
    self.events.append('start')
    if startError:
     raise startError

   def authenticate(self):
    self.events.append('authenticate')
    if authError:
     raise authError

   def stop(self):
    self.events.append('stop')
    self.stopCalls += 1
    self.stopActive.append(context.active)
    if stopError:
     raise stopError

  FakeAuth.events = self.events
  auth = FakeAuth()

  def complete(device, modelName):
   self.events.append('continuation')
   if continuationError:
    raise continuationError

  def run():
   with mock.patch.object(
    usb, 'importDriver', return_value=context
   ), mock.patch.object(
    usb, 'listDevices', side_effect=lambda current, quiet: current.devices
   ), mock.patch.object(
    usb, 'SonySenserAuthDevice', return_value=auth
   ), mock.patch.object(
    usb, 'SonySenserCamera', return_value=object()
   ), mock.patch.object(usb.time, 'sleep'):
    return usb._waitForDevice(
     'libusb', usb.SonySenserDevice, 1, .5,
     lambda target: usb._runSenserContinuation(
      target, 'ILCE', complete
     ),
    )

  return run, auth, context

 def test_authentication_failure_stops_once_and_preserves_exception(self):
  failure = RuntimeError('authentication failed')
  run, auth, context = self.make_operation(authError=failure)

  with self.assertRaises(RuntimeError) as raised:
   run()

  self.assertIs(failure, raised.exception)
  self.assertEqual(1, auth.stopCalls)
  self.assertEqual(1, context.exitCalls)

 def test_start_failure_does_not_stop(self):
  failure = RuntimeError('start failed')
  run, auth, context = self.make_operation(startError=failure)

  with self.assertRaises(RuntimeError) as raised:
   run()

  self.assertIs(failure, raised.exception)
  self.assertEqual(0, auth.stopCalls)
  self.assertEqual(1, context.exitCalls)

 def test_continuation_failure_stops_once_and_preserves_exception(self):
  failure = RuntimeError('continuation failed')
  run, auth, context = self.make_operation(continuationError=failure)

  with self.assertRaises(RuntimeError) as raised:
   run()

  self.assertIs(failure, raised.exception)
  self.assertEqual(1, auth.stopCalls)
  self.assertEqual(1, context.exitCalls)

 def test_authentication_failure_wins_over_stop_failure(self):
  authenticationFailure = RuntimeError('authentication failed')
  stopFailure = RuntimeError('stop failed')
  run, auth, context = self.make_operation(
   authError=authenticationFailure, stopError=stopFailure
  )

  with self.assertRaises(RuntimeError) as raised:
   run()

  self.assertIs(authenticationFailure, raised.exception)
  self.assertEqual(1, auth.stopCalls)
  self.assertEqual(1, context.exitCalls)

 def test_continuation_failure_wins_over_stop_failure(self):
  continuationFailure = RuntimeError('continuation failed')
  stopFailure = RuntimeError('stop failed')
  run, auth, context = self.make_operation(
   stopError=stopFailure, continuationError=continuationFailure
  )

  with self.assertRaises(RuntimeError) as raised:
   run()

  self.assertIs(continuationFailure, raised.exception)
  self.assertEqual(1, auth.stopCalls)
  self.assertEqual(1, context.exitCalls)

 def test_normal_operation_stops_once_inside_context(self):
  run, auth, context = self.make_operation()

  self.assertTrue(run())

  self.assertEqual(
   [
    'enter:service', 'start', 'authenticate', 'continuation',
    'stop', 'exit:service',
   ],
   self.events,
  )
  self.assertEqual(1, auth.stopCalls)
  self.assertEqual([True], auth.stopActive)
  self.assertEqual(1, context.exitCalls)

 def test_stop_failure_propagates_after_success(self):
  failure = RuntimeError('stop failed')
  run, auth, context = self.make_operation(stopError=failure)

  with self.assertRaises(RuntimeError) as raised:
   run()

  self.assertIs(failure, raised.exception)
  self.assertEqual(1, auth.stopCalls)
  self.assertEqual([True], auth.stopActive)
  self.assertEqual(1, context.exitCalls)


if __name__ == '__main__':
 unittest.main()
