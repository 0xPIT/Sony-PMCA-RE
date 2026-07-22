"""Interactive smoke test for pywebview dialogs invoked from a worker thread."""

import argparse
import json
import threading

import webview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('open', 'save'))
    args = parser.parse_args()

    window = webview.create_window(
        'PMCA File Dialog Test',
        html='<html><body><p>Waiting for the file dialog...</p></body></html>',
        width=1280,
        height=800,
    )
    result = {'mode': args.mode}

    def loaded():
        def task():
            if args.mode == 'open':
                selection = window.create_file_dialog(
                    webview.FileDialog.OPEN,
                    file_types=('APK Files (*.apk)', 'All Files (*.*)'),
                )
            else:
                selection = window.create_file_dialog(
                    webview.FileDialog.SAVE,
                    file_types=('Backup Files (*.bin)', 'All Files (*.*)'),
                    save_filename='Backup_test.bin',
                )
            result['cancelled'] = not selection
            window.destroy()

        threading.Thread(target=task, name='pmca-dialog-smoke', daemon=True).start()

    window.events.loaded += loaded
    webview.start()
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get('cancelled') else 1


if __name__ == '__main__':
    raise SystemExit(main())
