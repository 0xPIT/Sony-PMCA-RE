import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'pmca_web_single_instance_test', ROOT / 'pmca-web.py'
)
WEB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WEB)


class FakeCtypes:
    def __init__(self, error=0):
        self.error = error
        self.set_errors = []
        self.WinError = mock.Mock(
            side_effect=lambda error: OSError(error, 'Windows API failure')
        )

    def set_last_error(self, error):
        self.set_errors.append(error)

    def get_last_error(self):
        return self.error


class WindowsMutexTest(unittest.TestCase):
    def make_api(self, handle, error=0, close_result=True):
        ctypes_module = FakeCtypes(error)
        kernel32 = mock.Mock()
        kernel32.CreateMutexW.return_value = handle
        kernel32.CloseHandle.return_value = close_result
        return ctypes_module, kernel32

    def test_first_instance_acquires_handle_without_closing_it(self):
        ctypes_module, kernel32 = self.make_api(101)
        with mock.patch.object(
            WEB, '_get_windows_kernel32',
            return_value=(ctypes_module, kernel32),
        ):
            self.assertEqual(WEB._acquire_windows_single_instance(), 101)

        kernel32.CreateMutexW.assert_called_once_with(
            None, False, WEB._WINDOWS_INSTANCE_MUTEX
        )
        kernel32.CloseHandle.assert_not_called()
        self.assertEqual(ctypes_module.set_errors, [0])

    def test_duplicate_closes_only_its_returned_handle(self):
        owner_handle = 101
        duplicate_handle = 202
        ctypes_module, kernel32 = self.make_api(
            duplicate_handle, WEB._ERROR_ALREADY_EXISTS
        )
        with mock.patch.object(
            WEB, '_get_windows_kernel32',
            return_value=(ctypes_module, kernel32),
        ):
            self.assertIsNone(WEB._acquire_windows_single_instance())

        kernel32.CloseHandle.assert_called_once_with(duplicate_handle)
        self.assertNotEqual(
            kernel32.CloseHandle.call_args.args[0], owner_handle
        )

    def test_create_mutex_failure_propagates(self):
        ctypes_module, kernel32 = self.make_api(0, error=5)
        with mock.patch.object(
            WEB, '_get_windows_kernel32',
            return_value=(ctypes_module, kernel32),
        ):
            with self.assertRaisesRegex(OSError, 'Windows API failure'):
                WEB._acquire_windows_single_instance()

        ctypes_module.WinError.assert_called_once_with(5)
        kernel32.CloseHandle.assert_not_called()

    def test_already_exists_is_not_interpreted_for_invalid_handle(self):
        ctypes_module, kernel32 = self.make_api(
            0, error=WEB._ERROR_ALREADY_EXISTS
        )
        with mock.patch.object(
            WEB, '_get_windows_kernel32',
            return_value=(ctypes_module, kernel32),
        ):
            with self.assertRaisesRegex(OSError, 'Windows API failure'):
                WEB._acquire_windows_single_instance()

        ctypes_module.WinError.assert_called_once_with(
            WEB._ERROR_ALREADY_EXISTS
        )
        kernel32.CloseHandle.assert_not_called()

    def test_owner_release_closes_handle_exactly_once(self):
        ctypes_module, kernel32 = self.make_api(101)
        with mock.patch.object(
            WEB, '_get_windows_kernel32',
            return_value=(ctypes_module, kernel32),
        ):
            WEB._release_windows_single_instance(101)

        kernel32.CloseHandle.assert_called_once_with(101)

    def test_duplicate_message_uses_windows_dialog_once(self):
        import ctypes

        user32 = mock.Mock()
        user32.MessageBoxW.return_value = 1
        with mock.patch.object(ctypes, 'WinDLL', return_value=user32), \
                mock.patch.object(ctypes, 'set_last_error') as set_error:
            WEB._show_already_running_message()

        set_error.assert_called_once_with(0)
        user32.MessageBoxW.assert_called_once_with(
            None,
            'PMCA is already running.\n'
            'Close the existing window before starting another instance.',
            'PMCA Camera Utility',
            0x30,
        )


class StartupGuardTest(unittest.TestCase):
    def test_first_windows_instance_continues_and_closes_once(self):
        with mock.patch.object(WEB.sys, 'platform', 'win32'), \
                mock.patch.object(
                    WEB, '_acquire_windows_single_instance', return_value=101
                ) as acquire, \
                mock.patch.object(
                    WEB, '_run_application', return_value='started'
                ) as run, \
                mock.patch.object(
                    WEB, '_release_windows_single_instance'
                ) as release, \
                mock.patch.object(WEB, '_show_already_running_message') as show:
            self.assertEqual(WEB.main(), 'started')

        acquire.assert_called_once_with()
        run.assert_called_once_with()
        release.assert_called_once_with(101)
        show.assert_not_called()

    def test_duplicate_shows_one_message_without_startup_side_effects(self):
        with mock.patch.object(WEB.sys, 'platform', 'win32'), \
                mock.patch.object(
                    WEB, '_acquire_windows_single_instance', return_value=None
                ), \
                mock.patch.object(WEB, '_run_application') as run, \
                mock.patch.object(
                    WEB, '_release_windows_single_instance'
                ) as release, \
                mock.patch.object(
                    WEB, '_show_already_running_message'
                ) as show:
            self.assertIsNone(WEB.main())

        show.assert_called_once_with()
        run.assert_not_called()
        release.assert_not_called()

    def test_owner_handle_closes_once_when_startup_raises(self):
        startup_error = RuntimeError('startup failed')
        with mock.patch.object(WEB.sys, 'platform', 'win32'), \
                mock.patch.object(
                    WEB, '_acquire_windows_single_instance', return_value=101
                ), \
                mock.patch.object(
                    WEB, '_run_application', side_effect=startup_error
                ), \
                mock.patch.object(
                    WEB, '_release_windows_single_instance'
                ) as release:
            with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                WEB.main()

        release.assert_called_once_with(101)

    def test_acquisition_failure_does_not_start_or_show_duplicate_message(self):
        mutex_error = OSError(5, 'CreateMutexW failed')
        with mock.patch.object(WEB.sys, 'platform', 'win32'), \
                mock.patch.object(
                    WEB, '_acquire_windows_single_instance',
                    side_effect=mutex_error,
                ), \
                mock.patch.object(WEB, '_run_application') as run, \
                mock.patch.object(
                    WEB, '_show_already_running_message'
                ) as show:
            with self.assertRaisesRegex(OSError, 'CreateMutexW failed'):
                WEB.main()

        run.assert_not_called()
        show.assert_not_called()

    def test_non_windows_startup_uses_no_windows_api(self):
        with mock.patch.object(WEB.sys, 'platform', 'linux'), \
                mock.patch.object(
                    WEB, '_acquire_windows_single_instance'
                ) as acquire, \
                mock.patch.object(
                    WEB, '_release_windows_single_instance'
                ) as release, \
                mock.patch.object(
                    WEB, '_show_already_running_message'
                ) as show, \
                mock.patch.object(
                    WEB, '_run_application', return_value='started'
                ) as run:
            self.assertEqual(WEB.main(), 'started')

        run.assert_called_once_with()
        acquire.assert_not_called()
        release.assert_not_called()
        show.assert_not_called()


if __name__ == '__main__':
    unittest.main()
