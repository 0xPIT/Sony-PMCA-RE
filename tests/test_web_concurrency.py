import importlib.util
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'pmca_web_concurrency_test', ROOT / 'pmca-web.py'
)
WEB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WEB)


def wait_until(predicate, timeout=2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


class FakeWindow:
    def __init__(self, dialog_result=None):
        self.dialog_result = dialog_result

    def create_file_dialog(self, *args, **kwargs):
        return self.dialog_result


class ApiConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.api = WEB.Api()
        self.api._notify = mock.Mock()

    def assert_idle(self):
        self.assertTrue(wait_until(
            lambda: self.api._active_camera_operation is None
        ))

    def test_public_camera_actions_use_central_admission(self):
        actions = [
            ('get_info', (), 'info'),
            ('install_app', ('package.name',), 'install'),
            ('install_apk', (), 'install'),
            ('firmware_update', (), 'firmware'),
            ('start_tweaks_updater', (), 'tweaks'),
            ('start_tweaks_service', (), 'tweaks'),
            ('read_wifi', (), 'wifi'),
            ('write_wifi', ([],), 'wifi'),
            ('download_backup', (), 'backup'),
            ('restore_backup', (), 'backup'),
        ]
        for method_name, arguments, operation_name in actions:
            with self.subTest(method=method_name), mock.patch.object(
                self.api, '_start_camera_operation', return_value=True
            ) as start:
                result = getattr(self.api, method_name)(*arguments)
                self.assertTrue(result)
                start.assert_called_once()
                self.assertEqual(start.call_args.args[0], operation_name)

    def test_rapid_requests_enter_one_worker_and_rejection_does_not_end_it(self):
        entered = threading.Event()
        release = threading.Event()
        state_lock = threading.Lock()
        state = {'active': 0, 'peak': 0, 'calls': 0}

        def camera_body():
            with state_lock:
                state['active'] += 1
                state['calls'] += 1
                state['peak'] = max(state['peak'], state['active'])
            entered.set()
            release.wait(2)
            with state_lock:
                state['active'] -= 1
            return []

        try:
            with mock.patch.object(WEB, 'infoCommand', camera_body):
                self.assertTrue(self.api.get_info())
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.api.get_info())

                events = [call.args[0] for call in self.api._notify.call_args_list]
                self.assertEqual(events.count('task_start'), 1)
                self.assertNotIn('task_end', events)
                self.assertEqual(state['calls'], 1)
                self.assertEqual(state['peak'], 1)
                self.assertEqual(self.api._active_camera_operation[0], 'info')
        finally:
            release.set()
        self.assert_idle()
        events = [call.args[0] for call in self.api._notify.call_args_list]
        self.assertEqual(events.count('task_end'), 1)

    def test_two_simultaneous_callers_cannot_both_win_admission(self):
        start = threading.Event()
        entered = threading.Event()
        release = threading.Event()
        results = []
        results_lock = threading.Lock()

        def camera_body():
            entered.set()
            release.wait(2)
            return []

        def call_info():
            start.wait(1)
            result = self.api.get_info()
            with results_lock:
                results.append(result)

        callers = [threading.Thread(target=call_info) for _ in range(2)]
        try:
            with mock.patch.object(WEB, 'infoCommand', camera_body):
                for caller in callers:
                    caller.start()
                start.set()
                for caller in callers:
                    caller.join(1)
                self.assertTrue(entered.wait(1))
                self.assertCountEqual(results, [True, False])
        finally:
            release.set()
            for caller in callers:
                caller.join(1)
        self.assert_idle()

    def test_normal_completion_releases_admission_for_a_later_request(self):
        calls = []

        def camera_body():
            calls.append('called')
            return []

        with mock.patch.object(WEB, 'infoCommand', camera_body):
            self.assertTrue(self.api.get_info())
            self.assert_idle()
            self.assertTrue(self.api.get_info())
            self.assert_idle()
        self.assertEqual(len(calls), 2)

    def test_expected_empty_result_releases_admission(self):
        with mock.patch.object(WEB, 'infoCommand', return_value=None):
            self.assertTrue(self.api.get_info())
            self.assert_idle()
            self.assertTrue(self.api.get_info())
            self.assert_idle()

    def test_exception_releases_admission_for_a_later_request(self):
        calls = []

        def camera_body():
            calls.append('called')
            if len(calls) == 1:
                raise RuntimeError('camera failure')
            return []

        with mock.patch.object(WEB, 'infoCommand', camera_body), \
             mock.patch.object(WEB.traceback, 'print_exc'):
            self.assertTrue(self.api.get_info())
            self.assert_idle()
            self.assertTrue(self.api.get_info())
            self.assert_idle()
        self.assertEqual(len(calls), 2)

    def test_worker_start_failure_releases_admission(self):
        with mock.patch.object(
            WEB.threading.Thread, 'start', side_effect=RuntimeError('no thread')
        ):
            with self.assertRaisesRegex(RuntimeError, 'no thread'):
                self.api.get_info()
        self.assertIsNone(self.api._active_camera_operation)

        with mock.patch.object(WEB, 'infoCommand', return_value=[]):
            self.assertTrue(self.api.get_info())
            self.assert_idle()

    def test_worker_constructor_failure_releases_admission(self):
        worker_entered = threading.Event()

        with mock.patch.object(
            WEB.threading, 'Thread', side_effect=RuntimeError('no thread')
        ):
            with self.assertRaisesRegex(RuntimeError, 'no thread'):
                self.api._start_camera_operation(
                    'test', worker_entered.set
                )
        self.assertFalse(worker_entered.is_set())
        self.assertIsNone(self.api._active_camera_operation)

        with mock.patch.object(WEB, 'infoCommand', return_value=[]):
            self.assertTrue(self.api.get_info())
            self.assert_idle()

    def test_cancelled_file_dialog_releases_admission(self):
        self.api._window = FakeWindow(dialog_result=None)
        self.assertTrue(self.api.firmware_update())
        self.assert_idle()
        with mock.patch.object(WEB, 'infoCommand', return_value=[]):
            self.assertTrue(self.api.get_info())
            self.assert_idle()

    def test_app_list_loading_is_not_blocked_by_camera_operation(self):
        camera_entered = threading.Event()
        camera_release = threading.Event()
        apps_loaded = threading.Event()

        def camera_body():
            camera_entered.set()
            camera_release.wait(2)
            return []

        def list_apps():
            apps_loaded.set()
            return {}

        try:
            with mock.patch.object(WEB, 'infoCommand', camera_body), \
                 mock.patch.object(WEB, 'listApps', list_apps):
                self.assertTrue(self.api.get_info())
                self.assertTrue(camera_entered.wait(1))
                self.api.load_apps()
                self.assertTrue(apps_loaded.wait(1))
                self.assertIsNotNone(self.api._active_camera_operation)
        finally:
            camera_release.set()
        self.assert_idle()

    def test_system_diagnostics_are_blocked_because_they_open_the_camera(self):
        from pmca.plugins.system import web as system_web

        camera_entered = threading.Event()
        camera_release = threading.Event()

        def camera_body():
            camera_entered.set()
            camera_release.wait(2)
            return []

        try:
            with mock.patch.object(WEB, 'infoCommand', camera_body), \
                 mock.patch.object(system_web, 'run_all_checks') as diagnostics:
                self.assertTrue(self.api.get_info())
                self.assertTrue(camera_entered.wait(1))
                self.assertFalse(self.api.plugin_call('system', 'run'))
                diagnostics.assert_not_called()
        finally:
            camera_release.set()
        self.assert_idle()

    def test_shutdown_rejects_new_camera_operations(self):
        window = mock.Mock()
        self.api.set_window(window)
        self.api.mark_ready()
        self.assertTrue(self.api._evaluate_js('beforeShutdown()'))

        self.api.shutdown()

        self.assertTrue(self.api._closing)
        self.assertTrue(self.api._camera_operations_closed)
        self.assertFalse(self.api._evaluate_js('afterShutdown()'))
        window.evaluate_js.assert_called_once_with('beforeShutdown()')
        with mock.patch.object(WEB, 'infoCommand') as camera_body:
            self.assertFalse(self.api.get_info())
        camera_body.assert_not_called()
        self.assertIsNone(self.api._active_camera_operation)


if __name__ == '__main__':
    unittest.main()
