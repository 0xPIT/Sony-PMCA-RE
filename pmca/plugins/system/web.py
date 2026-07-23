"""Web GUI backend handlers for the system diagnostics plugin."""

import json
import threading
import traceback

from .diagnostics import run_all_checks


def _run(target):
    threading.Thread(target=target, daemon=True).start()


def run(api):
    def task():
        try:
            api._notify('task_start', '"system"')
            print('Running system diagnostics...')
            results = run_all_checks()
            data = [
                {
                    'status': r.status,
                    'label': r.label,
                    'detail': r.detail,
                    'solution': r.solution,
                }
                for r in results
            ]
            passed = sum(1 for r in results if r.status == 'pass')
            warned = sum(1 for r in results if r.status == 'warn')
            failed = sum(1 for r in results if r.status == 'fail')
            print('Diagnostics complete: %d passed, %d warnings, %d failures'
                  % (passed, warned, failed))
            api._notify('diagnostics_result', json.dumps(data))
        except Exception:
            traceback.print_exc()
        finally:
            api._notify('task_end', '"system"')
    _run(task)
