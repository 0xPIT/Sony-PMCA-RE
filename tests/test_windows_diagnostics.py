import unittest
from unittest import mock

import usb.core

from pmca.diagnostics import diagnostics_windows


class Device:
    idProduct = 0x1234

    def __init__(self, error=None):
        self.error = error

    def get_active_configuration(self):
        if self.error:
            raise self.error
        return object()


class ZadigDiagnosticTests(unittest.TestCase):
    @mock.patch('usb.core.find')
    def test_accessible_device_retains_zadig_label(self, find):
        find.return_value = [Device()]

        result = diagnostics_windows.check_zadig_driver()

        self.assertEqual(result.status, 'pass')
        self.assertIn('Zadig', result.label)
        self.assertIsNone(result.solution)

    @mock.patch('usb.core.find')
    def test_inaccessible_device_gets_conditional_safe_guidance(self, find):
        find.return_value = [Device(usb.core.USBError('Access denied'))]

        result = diagnostics_windows.check_zadig_driver()

        self.assertEqual(result.status, 'fail')
        self.assertIn('Zadig', result.solution)
        self.assertIn('exact USB identity', result.solution)
        self.assertIn('Do not replace the normal MTP or mass-storage driver', result.solution)
        self.assertIn('rollback', result.solution)

    @mock.patch('usb.core.find', return_value=[])
    def test_no_device_does_not_recommend_a_driver_change(self, find):
        result = diagnostics_windows.check_zadig_driver()

        self.assertEqual(result.status, 'warn')
        self.assertIn('before considering Zadig or any driver change', result.solution)

    @mock.patch('usb.core.find', side_effect=RuntimeError('No backend available'))
    def test_missing_runtime_is_distinguished_from_zadig_binding(self, find):
        result = diagnostics_windows.check_zadig_driver()

        self.assertEqual(result.status, 'warn')
        self.assertIn('runtime DLL first', result.solution)
        self.assertIn('Zadig changes a device binding', result.solution)
        self.assertIn('does not provide the runtime DLL', result.solution)


if __name__ == '__main__':
    unittest.main()
