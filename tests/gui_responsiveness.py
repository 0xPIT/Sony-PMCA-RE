"""Manual/CI smoke test for the real pywebview event loop (no camera needed)."""

import argparse
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import webview

PROJECT_ROOT = Path(__file__).parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_web_module():
    path = PROJECT_ROOT / 'pmca-web.py'
    spec = importlib.util.spec_from_file_location('pmca_web_gui_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=5.0)
    parser.add_argument('--shutdown-after', type=float)
    args = parser.parse_args()

    module = load_web_module()
    api = module.Api()
    result = {'seconds': args.seconds}
    window = webview.create_window(
        'PMCA Responsiveness Test',
        js_api=api,
        width=560,
        height=672,
        min_size=(400, 400),
        **module.get_startup_page(),
    )
    api.set_window(window)
    window.events.loaded += api.mark_ready
    window.events.closed += api.shutdown

    def on_loaded():
        if args.shutdown_after is not None:
            def close_early():
                time.sleep(args.shutdown_after)
                result['shutdown_requested_during_task'] = True
                window.destroy()
            threading.Thread(target=close_early, name='pmca-gui-close-test', daemon=True).start()

        def task():
            try:
                window.evaluate_js(
                    'window.__pmcaTestTicks = 0;'
                    'window.__pmcaTestTimer = setInterval(function () {'
                    'window.__pmcaTestTicks += 1; }, 50);'
                )
                api._notify('task_start', '"info"')
                for index in range(int(args.seconds * 4)):
                    api.push_log('test-log-%03d\n' % index)
                    time.sleep(.25)

                result['ticks'] = window.evaluate_js('window.__pmcaTestTicks')
                result['camera_controls_disabled'] = window.evaluate_js(
                    "document.getElementById('btn-info').disabled && "
                    "document.getElementById('btn-wifi-read').disabled && "
                    "document.getElementById('btn-firmware').disabled"
                )
                result['log_order_ok'] = window.evaluate_js(
                    "document.getElementById('log').textContent.indexOf('test-log-000') < "
                    "document.getElementById('log').textContent.indexOf('test-log-%03d')"
                    % (int(args.seconds * 4) - 1)
                )
                api._notify('task_end', '"info"')
                time.sleep(.2)
                result['controls_reenabled'] = not window.evaluate_js(
                    "document.getElementById('btn-info').disabled"
                )
                result['responsive'] = result['ticks'] >= args.seconds * 10
            except Exception as exc:
                result['error'] = repr(exc)
            finally:
                window.destroy()

        threading.Thread(target=task, name='pmca-gui-smoke-test', daemon=True).start()

    window.events.loaded += on_loaded
    webview.start()
    print(json.dumps(result, sort_keys=True))
    if args.shutdown_after is not None:
        return 0 if result.get('shutdown_requested_during_task') else 1
    return 0 if all((
        result.get('responsive'),
        result.get('camera_controls_disabled'),
        result.get('controls_reenabled'),
        result.get('log_order_ok'),
    )) else 1


if __name__ == '__main__':
    sys.exit(main())
