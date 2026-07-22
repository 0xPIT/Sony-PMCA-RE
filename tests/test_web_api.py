import importlib.util
import json
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


def load_web_module():
    path = Path(__file__).parents[1] / 'pmca-web.py'
    spec = importlib.util.spec_from_file_location('pmca_web_api_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeWindow:
    def __init__(self):
        self.scripts = []
        self.lock = threading.Lock()

    def evaluate_js(self, script):
        with self.lock:
            self.scripts.append(script)


class ApiConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = load_web_module()

    def setUp(self):
        self.window = FakeWindow()
        self.api = self.web.Api()
        self.api.set_window(self.window)
        self.api.mark_ready()

    def test_duplicate_camera_operation_is_rejected(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def delayed_info():
            calls.append(threading.current_thread().name)
            entered.set()
            release.wait(2)
            return []

        with mock.patch.object(self.web, 'infoCommand', delayed_info):
            self.assertTrue(self.api.get_info())
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.api.get_info())
            release.set()
            deadline = time.time() + 2
            while self.api._active_camera_task is not None and time.time() < deadline:
                time.sleep(.01)

        self.assertEqual(len(calls), 1)
        self.assertTrue(any('task_rejected' in script for script in self.window.scripts))

    def test_different_camera_operations_cannot_overlap(self):
        release = threading.Event()
        entered = threading.Event()

        def delayed_info():
            entered.set()
            release.wait(2)
            return []

        with mock.patch.object(self.web, 'infoCommand', delayed_info):
            self.assertTrue(self.api.get_info())
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.api.read_wifi())
            release.set()

    def test_shutdown_drops_late_javascript_calls(self):
        self.api.push_log('before')
        self.api.shutdown()
        self.api.push_log('after')
        self.api._notify('late_event')
        self.assertEqual(len(self.window.scripts), 1)

    def test_log_messages_use_json_escaping(self):
        message = "quote ' \\ newline\nü\u2028"
        self.api.push_log(message)
        script = self.window.scripts[-1]
        payload = script[len('window._appendLog('):-1]
        self.assertEqual(json.loads(payload), message)

    def test_javascript_waits_until_ui_is_ready(self):
        api = self.web.Api()
        window = FakeWindow()
        api.set_window(window)
        api.push_log('too early')
        self.assertEqual(window.scripts, [])

        api.mark_ready()
        api.push_log('ready')
        self.assertEqual(len(window.scripts), 1)

    def test_javascript_evaluation_is_serialized(self):
        state_lock = threading.Lock()
        start = threading.Event()
        active = 0
        maximum_active = 0

        class BlockingWindow:
            def evaluate_js(inner_self, script):
                nonlocal active, maximum_active
                with state_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(.05)
                with state_lock:
                    active -= 1

        api = self.web.Api()
        api.set_window(BlockingWindow())
        api.mark_ready()

        def push(message):
            start.wait(1)
            api.push_log(message)

        threads = [threading.Thread(target=push, args=(str(i),)) for i in range(3)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join(1)

        self.assertEqual(maximum_active, 1)

    def test_shutdown_releases_waiting_tweak_session(self):
        event = threading.Event()
        self.api._tweak_apply_event = event
        self.api.shutdown()
        self.assertTrue(event.is_set())

    def test_tweaks_cannot_be_applied_twice(self):
        entered = threading.Event()
        release = threading.Event()

        class Tweaks:
            def __init__(self):
                self.calls = 0

            def apply(self):
                self.calls += 1
                entered.set()
                release.wait(2)

        tweaks = Tweaks()
        self.api._tweak_interface = tweaks
        self.api._tweak_apply_event = threading.Event()
        self.assertTrue(self.api.apply_tweaks())
        self.assertTrue(entered.wait(1))
        self.assertFalse(self.api.apply_tweaks())
        release.set()
        self.assertTrue(self.api._tweak_apply_event.wait(1))
        self.assertEqual(tweaks.calls, 1)


if __name__ == '__main__':
    unittest.main()
