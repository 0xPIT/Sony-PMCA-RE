import unittest
import sys
from unittest import mock

from pmca.plugins.system.diagnostics import diagnostics_windows


class WindowsDiagnosticsTest(unittest.TestCase):
    def completed(self, stdout=b'', returncode=0, stderr=b''):
        return diagnostics_windows.subprocess.CompletedProcess(
            ['tasklist'], returncode, stdout, stderr
        )

    def test_tasklist_runs_without_a_console_window(self):
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=self.completed(),
        ) as run:
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('pass', result.status)
        self.assertEqual('Not running.', result.detail)
        run.assert_called_once_with(
            ['tasklist', '/FI', 'IMAGENAME eq WMPNetworkSvc.exe'],
            capture_output=True,
            timeout=5,
            creationflags=diagnostics_windows._CREATE_NO_WINDOW,
        )
        self.assertNotIn('text', run.call_args.kwargs)
        self.assertNotIn('encoding', run.call_args.kwargs)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_exact_process_row_is_detected(self):
        output = b'WMPNetworkSvc.exe       1234 Services 0 12,000 K\r\n'
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=self.completed(output),
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('warn', result.status)
        self.assertEqual(
            'Windows Media Player Network Sharing Service is running.',
            result.detail,
        )

    def test_process_name_matching_is_ascii_case_insensitive(self):
        output = b'wMpNeTwOrKsVc.ExE       1234 Services 0 12,000 K\r\n'
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=self.completed(output),
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('warn', result.status)

    def test_localized_tasklist_bytes_do_not_require_decoding(self):
        output = (
            b'localized byte: \x81\r\n'
            b'WMPNetworkSvc.exe       1234 Services 0 12,000 K\r\n'
        )
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=self.completed(output),
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('warn', result.status)

    def test_prefixed_process_name_does_not_match(self):
        output = b'OtherWMPNetworkSvc.exe  1234 Services 0 12,000 K\r\n'
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=self.completed(output),
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('pass', result.status)
        self.assertEqual('Not running.', result.detail)

    def test_suffixed_process_name_does_not_match(self):
        output = b'WMPNetworkSvc.exe.backup 1234 Services 0 12,000 K\r\n'
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=self.completed(output),
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('pass', result.status)
        self.assertEqual('Not running.', result.detail)

    def test_target_text_later_in_explanatory_line_does_not_match(self):
        output = b'INFO: WMPNetworkSvc.exe is not running\r\n'
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=self.completed(output),
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('pass', result.status)
        self.assertEqual('Not running.', result.detail)

    def test_nonzero_return_code_uses_controlled_failure_result(self):
        completed = self.completed(
            b'WMPNetworkSvc.exe 1234 Services\r\n',
            returncode=1,
            stderr=b'localized failure: \x81\r\n',
        )
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            return_value=completed,
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('pass', result.status)
        self.assertEqual('Could not check (non-critical).', result.detail)
        self.assertNotEqual('Not running.', result.detail)

    def test_timeout_uses_controlled_failure_result(self):
        error = diagnostics_windows.subprocess.TimeoutExpired(
            ['tasklist'], 5
        )
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run', side_effect=error
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('pass', result.status)
        self.assertEqual('Could not check (non-critical).', result.detail)

    def test_process_creation_failure_uses_controlled_failure_result(self):
        with mock.patch.object(
            diagnostics_windows.subprocess, 'run',
            side_effect=OSError('tasklist unavailable'),
        ):
            result = diagnostics_windows.check_wmp_not_claiming()

        self.assertEqual('pass', result.status)
        self.assertEqual('Could not check (non-critical).', result.detail)

    def test_import_fallback_is_zero_when_flag_is_unavailable(self):
        self.assertEqual(
            getattr(
                diagnostics_windows.subprocess, 'CREATE_NO_WINDOW', 0
            ),
            diagnostics_windows._CREATE_NO_WINDOW,
        )
        if sys.platform == 'win32':
            self.assertNotEqual(0, diagnostics_windows._CREATE_NO_WINDOW)


if __name__ == '__main__':
    unittest.main()
