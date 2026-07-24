import importlib.util
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'pmca_web_tweak_session_test', ROOT / 'pmca-web.py'
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


class FakeBackend:
    def __init__(self, block_stop=False, stop_error=None):
        self.started = threading.Event()
        self.stop_entered = threading.Event()
        self.stop_release = threading.Event()
        self.stop_calls = 0
        self.stop_error = stop_error
        if not block_stop:
            self.stop_release.set()

    def start(self):
        self.started.set()

    def stop(self):
        self.stop_calls += 1
        self.stop_entered.set()
        self.stop_release.wait(2)
        if self.stop_error:
            raise self.stop_error


class FakeTweaks:
    def __init__(self, apply=None, get_tweaks=None):
        self.apply_calls = 0
        self.set_calls = 0
        self._apply = apply
        self._get_tweaks = get_tweaks

    def getTweaks(self):
        if self._get_tweaks:
            return self._get_tweaks()
        return [('test', 'Test tweak', False, 'Off')]

    def setEnabled(self, tweak_id, enabled):
        self.set_calls += 1

    def apply(self):
        self.apply_calls += 1
        if self._apply:
            self._apply()


class TweakSessionTest(unittest.TestCase):
    def setUp(self):
        self.api = WEB.Api()
        self.api._notify = mock.Mock()

    def assert_idle(self):
        self.assertTrue(wait_until(
            lambda: self.api._active_camera_operation is None
        ))

    def assert_session_clean(self):
        self.assertEqual(self.api._TWEAK_IDLE, self.api._tweak_state)
        self.assertIsNone(self.api._tweak_interface)
        self.assertIsNone(self.api._tweak_apply_event)
        self.assertIsNone(self.api._tweaks_data)

    def prepare_session(self, tweaks=None, backend=None):
        tweaks = tweaks or FakeTweaks()
        backend = backend or FakeBackend()

        def shell(complete):
            complete(object())

        patches = (
            mock.patch.object(WEB, 'senserShellCommand', shell),
            mock.patch.object(WEB, 'SenserPlatformBackend', return_value=backend),
            mock.patch.object(WEB, 'TweakInterface', return_value=tweaks),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return tweaks, backend

    def start_session(self, tweaks=None, backend=None):
        tweaks, backend = self.prepare_session(tweaks, backend)
        self.assertTrue(self.api.start_tweaks_service())
        self.assertTrue(wait_until(
            lambda: self.api._tweak_state == self.api._TWEAK_WAITING
        ))
        self.assertTrue(backend.started.is_set())
        return tweaks, backend

    def test_apply_is_accepted_only_once(self):
        apply_entered = threading.Event()
        apply_release = threading.Event()

        def apply():
            apply_entered.set()
            apply_release.wait(2)

        tweaks, backend = self.start_session(FakeTweaks(apply))
        try:
            self.assertTrue(self.api.apply_tweaks())
            self.assertTrue(apply_entered.wait(1))
            self.assertEqual(self.api._TWEAK_APPLYING, self.api._tweak_state)
            self.assertFalse(self.api.apply_tweaks())
            self.assertFalse(self.api.set_tweak('test', True))
            self.assertEqual(1, tweaks.apply_calls)
        finally:
            apply_release.set()
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_apply_and_cancel_race_has_one_winner(self):
        start = threading.Event()
        apply_entered = threading.Event()
        apply_release = threading.Event()
        results = []
        results_lock = threading.Lock()

        def apply():
            apply_entered.set()
            apply_release.wait(2)

        _, backend = self.start_session(FakeTweaks(apply))

        def invoke(method):
            start.wait(1)
            result = method()
            with results_lock:
                results.append(result)

        callers = [
            threading.Thread(target=invoke, args=(self.api.apply_tweaks,)),
            threading.Thread(target=invoke, args=(self.api.cancel_tweaks,)),
        ]
        try:
            for caller in callers:
                caller.start()
            start.set()
            for caller in callers:
                caller.join(1)
            self.assertEqual(1, results.count(True))
            self.assertEqual(1, results.count(False))
        finally:
            apply_release.set()
            for caller in callers:
                caller.join(1)
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_cancel_immediately_rejects_apply_and_set_before_cleanup(self):
        tweaks = FakeTweaks()
        backend = FakeBackend(block_stop=True)
        self.start_session(tweaks, backend)
        try:
            self.assertTrue(self.api.cancel_tweaks())
            self.assertTrue(backend.stop_entered.wait(1))
            self.assertEqual(self.api._TWEAK_CLOSING, self.api._tweak_state)
            self.assertFalse(self.api.apply_tweaks())
            self.assertFalse(self.api.set_tweak('test', True))
            self.assertEqual(0, tweaks.apply_calls)
            self.assertEqual(0, tweaks.set_calls)
        finally:
            backend.stop_release.set()
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_shutdown_while_waiting_closes_session(self):
        backend = FakeBackend(block_stop=True)
        self.start_session(backend=backend)
        try:
            self.api.shutdown()
            self.assertTrue(backend.stop_entered.wait(1))
            self.assertEqual(self.api._TWEAK_CLOSING, self.api._tweak_state)
            self.assertFalse(self.api.apply_tweaks())
            self.assertFalse(self.api.set_tweak('test', True))
        finally:
            backend.stop_release.set()
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_shutdown_during_apply_waits_before_stopping_backend(self):
        apply_entered = threading.Event()
        apply_release = threading.Event()

        def apply():
            apply_entered.set()
            apply_release.wait(2)

        _, backend = self.start_session(FakeTweaks(apply))
        self.assertTrue(self.api.apply_tweaks())
        self.assertTrue(apply_entered.wait(1))
        self.api.shutdown()
        self.assertEqual(self.api._TWEAK_APPLYING, self.api._tweak_state)
        self.assertFalse(backend.stop_entered.wait(.1))
        apply_release.set()
        self.assert_idle()
        self.assertTrue(backend.stop_entered.is_set())
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_apply_success_releases_session(self):
        tweaks, backend = self.start_session()
        self.assertTrue(self.api.apply_tweaks())
        self.assert_idle()
        self.assertEqual(1, tweaks.apply_calls)
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_apply_failure_releases_session(self):
        def fail():
            raise RuntimeError('apply failed')

        _, backend = self.start_session(FakeTweaks(fail))
        with mock.patch.object(WEB.traceback, 'print_exc'):
            self.assertTrue(self.api.apply_tweaks())
            self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_apply_worker_start_failure_releases_session(self):
        _, backend = self.start_session()
        with mock.patch.object(
            WEB.threading.Thread, 'start', side_effect=RuntimeError('no thread')
        ):
            with self.assertRaisesRegex(RuntimeError, 'no thread'):
                self.api.apply_tweaks()
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_apply_worker_constructor_failure_releases_session(self):
        tweaks, backend = self.start_session()
        with mock.patch.object(
            WEB.threading, 'Thread', side_effect=RuntimeError('no thread')
        ):
            with self.assertRaisesRegex(RuntimeError, 'no thread'):
                self.api.apply_tweaks()
        self.assert_idle()
        self.assertEqual(0, tweaks.apply_calls)
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_notification_failure_closes_and_releases_session(self):
        backend = FakeBackend()
        self.prepare_session(backend=backend)
        self.api._notify = WEB.Api._notify.__get__(self.api, WEB.Api)

        with mock.patch.object(
            self.api, '_evaluate_js', return_value=False
        ) as evaluate_js:
            self.assertTrue(self.api.start_tweaks_service())
            self.assert_idle()

        scripts = [call.args[0] for call in evaluate_js.call_args_list]
        self.assertTrue(any('tweaks_available' in script for script in scripts))
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_shutdown_before_session_registration_closes_without_notification(self):
        get_entered = threading.Event()
        get_release = threading.Event()

        def get_tweaks():
            get_entered.set()
            get_release.wait(2)
            return [('test', 'Test tweak', False, 'Off')]

        backend = FakeBackend()
        self.prepare_session(FakeTweaks(get_tweaks=get_tweaks), backend)
        self.assertTrue(self.api.start_tweaks_service())
        self.assertTrue(get_entered.wait(1))

        self.api.shutdown()
        get_release.set()
        self.assert_idle()

        events = [call.args[0] for call in self.api._notify.call_args_list]
        self.assertNotIn('tweaks_available', events)
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_backend_stop_failure_still_releases_session(self):
        backend = FakeBackend(stop_error=RuntimeError('stop failed'))
        self.start_session(backend=backend)
        with mock.patch.object(WEB.traceback, 'print_exc'):
            self.assertTrue(self.api.cancel_tweaks())
            self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_apply_completion_requires_matching_session_event(self):
        tweaks, backend = self.start_session()
        captured = {}

        class DeferredThread:
            def __init__(self, target, **kwargs):
                captured['target'] = target

            def start(self):
                pass

        with mock.patch.object(WEB.threading, 'Thread', DeferredThread):
            self.assertTrue(self.api.apply_tweaks())

        original_event = self.api._tweak_apply_event
        replacement_event = threading.Event()
        with self.api._tweak_lock:
            self.api._tweak_apply_event = replacement_event

        captured['target']()

        self.assertEqual(self.api._TWEAK_APPLYING, self.api._tweak_state)
        self.assertFalse(original_event.is_set())
        self.assertFalse(replacement_event.is_set())
        self.assertEqual(1, tweaks.apply_calls)
        self.assertEqual(0, backend.stop_calls)

        with self.api._tweak_lock:
            self.api._tweak_apply_event = original_event
            self.api._tweak_state = self.api._TWEAK_CLOSING
        original_event.set()
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_connection_failure_releases_operation_admission(self):
        with mock.patch.object(
            WEB, 'senserShellCommand', side_effect=RuntimeError('connect failed')
        ), mock.patch.object(WEB.traceback, 'print_exc'):
            self.assertTrue(self.api.start_tweaks_service())
            self.assert_idle()
        self.assert_session_clean()

    def test_camera_operation_waits_until_backend_stop_finishes(self):
        backend = FakeBackend(block_stop=True)
        self.start_session(backend=backend)
        self.assertTrue(self.api.cancel_tweaks())
        self.assertTrue(backend.stop_entered.wait(1))
        with mock.patch.object(WEB, 'infoCommand') as camera_body:
            self.assertFalse(self.api.get_info())
        camera_body.assert_not_called()
        self.assertIsNotNone(self.api._active_camera_operation)
        backend.stop_release.set()
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

    def test_later_camera_operation_succeeds_after_cleanup(self):
        _, backend = self.start_session()
        self.assertTrue(self.api.cancel_tweaks())
        self.assert_idle()
        self.assertEqual(1, backend.stop_calls)
        self.assert_session_clean()

        with mock.patch.object(WEB, 'infoCommand', return_value=[]):
            self.assertTrue(self.api.get_info())
            self.assert_idle()


if __name__ == '__main__':
    unittest.main()
