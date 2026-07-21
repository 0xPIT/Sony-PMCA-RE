#!/usr/bin/env python3
"""A web-based GUI using pywebview"""
import io
import os
import sys
import threading
import traceback
from datetime import datetime

import webview

import config
import json
import struct

from pmca.commands.usb import (
    listApps, infoCommand, installCommand, firmwareUpdateCommand,
    updaterShellCommand, senserShellCommand, importDriver, getDevice
)
from pmca.usb.sony import SonyExtCmdDevice, SonyExtCmdCamera
from pmca.platform.backend.senser import SenserPlatformBackend
from pmca.platform.backend.usb import UsbPlatformBackend
from pmca.platform.tweaks import TweakInterface
from pmca.diagnostics import run_all_checks

if getattr(sys, 'frozen', False):
    from frozenversion import version
else:
    version = None


_ERROR_MARKERS = (
    'error:',
    'traceback (most recent call last)',
    'no devices found',
    'native driver not installed',
)


class StdoutCapture:
    """Captures stdout and sends to the webview window. Detects error messages."""
    def __init__(self, api, original):
        self.api = api
        self._original = original

    def write(self, text):
        if text:
            self._original.write(text)
            try:
                self.api.push_log(text)
                if any(m in text.lower() for m in _ERROR_MARKERS):
                    self.api.signal_error()
            except Exception:
                pass

    def flush(self):
        self._original.flush()


class StderrCapture:
    """Captures stderr, sends to webview, and signals error state."""
    def __init__(self, api, original):
        self.api = api
        self._original = original

    def write(self, text):
        if text:
            self._original.write(text)
            try:
                self.api.push_log(text)
                self.api.signal_error()
            except Exception:
                pass

    def flush(self):
        self._original.flush()


class OutputCapture:
    """Installs stdout/stderr capturing."""
    def __init__(self, api):
        self.api = api
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr

    def start(self):
        sys.stdout = StdoutCapture(self.api, self._original_stdout)
        sys.stderr = StderrCapture(self.api, self._original_stderr)


class Api:
    """Python API exposed to the JavaScript frontend via pywebview."""

    def __init__(self):
        self._window = None
        self._apps = []
        self._tweaks_data = None
        self._tweak_interface = None

    def set_window(self, window):
        self._window = window

    def push_log(self, text):
        if self._window:
            safe = text.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
            self._window.evaluate_js(f"window._appendLog('{safe}')")

    def signal_error(self):
        if self._window:
            self._window.evaluate_js("window._signalError()")

    def _notify(self, event, data='null'):
        if self._window:
            self._window.evaluate_js(f"window._onEvent('{event}', {data})")

    def get_config(self):
        return {
            'version': version or '',
            'docsUrl': config.docsUrl,
            'githubAppListUser': config.githubAppListUser,
            'githubAppListRepo': config.githubAppListRepo,
        }

    def load_apps(self):
        def task():
            try:
                self._notify('apps_loading')
                apps = list(listApps().values())
                self._apps = apps
                app_list = [{'name': a.name, 'package': a.package} for a in apps]
                self._notify('apps_loaded', json.dumps(app_list))
            except Exception:
                traceback.print_exc()
                self._notify('apps_loaded', '[]')
        threading.Thread(target=task, daemon=True).start()

    def get_info(self):
        def task():
            try:
                self._notify('task_start', '"info"')
                props = infoCommand()
                if props:
                    data = [{'key': k, 'value': v} for k, v in props]
                    self._notify('camera_info', json.dumps(data))
            except Exception:
                traceback.print_exc()
            finally:
                self._notify('task_end', '"info"')
        threading.Thread(target=task, daemon=True).start()

    def install_app(self, package):
        def task():
            try:
                self._notify('task_start', '"install"')
                if package:
                    installCommand(appPackage=package)
                else:
                    installCommand()
            except Exception:
                traceback.print_exc()
            finally:
                self._notify('task_end', '"install"')
        threading.Thread(target=task, daemon=True).start()

    def select_apk(self):
        file_types = ('APK Files (*.apk)', 'All Files (*.*)')
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=file_types
        )
        if result and len(result) > 0:
            self._selected_apk = result[0]
            self._notify('apk_selected', json.dumps(os.path.basename(result[0])))
        return None

    def install_apk(self):
        def task():
            try:
                apk_path = getattr(self, '_selected_apk', None)
                if apk_path:
                    self._notify('task_start', '"install"')
                    with open(apk_path, 'rb') as f:
                        installCommand(apkFile=f)
            except Exception:
                traceback.print_exc()
            finally:
                self._notify('task_end', '"install"')
        threading.Thread(target=task, daemon=True).start()

    def firmware_update(self):
        def task():
            try:
                file_types = ('Firmware Files (*.dat)', 'All Files (*.*)')
                result = self._window.create_file_dialog(
                    webview.FileDialog.OPEN, file_types=file_types
                )
                if result and len(result) > 0:
                    self._notify('task_start', '"firmware"')
                    with open(result[0], 'rb') as f:
                        firmwareUpdateCommand(f)
                    self._notify('task_end', '"firmware"')
            except Exception:
                traceback.print_exc()
                self._notify('task_end', '"firmware"')
        threading.Thread(target=task, daemon=True).start()

    def start_tweaks_updater(self):
        def task():
            try:
                self._notify('task_start', '"tweaks"')

                def complete(dev):
                    backend = UsbPlatformBackend(dev)
                    self._run_tweaks(backend)

                updaterShellCommand(complete=complete)
            except Exception:
                traceback.print_exc()
            finally:
                self._notify('task_end', '"tweaks"')
        threading.Thread(target=task, daemon=True).start()

    def start_tweaks_service(self):
        def task():
            try:
                self._notify('task_start', '"tweaks"')

                def complete(dev):
                    backend = SenserPlatformBackend(dev)
                    self._run_tweaks(backend)

                senserShellCommand(complete=complete)
            except Exception:
                traceback.print_exc()
            finally:
                self._notify('task_end', '"tweaks"')
        threading.Thread(target=task, daemon=True).start()

    def _run_tweaks(self, backend):
        backend.start()
        try:
            tweaks = TweakInterface(backend)
            tweak_list = list(tweaks.getTweaks())
            if not tweak_list:
                print('No tweaks available')
                return

            self._tweak_interface = tweaks
            data = [{'id': t[0], 'desc': t[1], 'enabled': bool(t[2]), 'value': t[3]} for t in tweak_list]
            self._tweaks_data = data

            event = threading.Event()
            self._tweak_apply_event = event
            self._notify('tweaks_available', json.dumps(data))
            event.wait()
        finally:
            backend.stop()

    def set_tweak(self, tweak_id, enabled):
        if self._tweak_interface:
            self._tweak_interface.setEnabled(tweak_id, enabled)
            tweak_list = list(self._tweak_interface.getTweaks())
            data = [{'id': t[0], 'desc': t[1], 'enabled': bool(t[2]), 'value': t[3]} for t in tweak_list]
            self._tweaks_data = data
            self._notify('tweaks_available', json.dumps(data))

    def apply_tweaks(self):
        def task():
            try:
                if self._tweak_interface:
                    self._notify('tweaks_applying')
                    print('Applying tweaks...')
                    self._tweak_interface.apply()
                    print('Tweaks applied successfully')
            except Exception:
                traceback.print_exc()
            finally:
                self._notify('tweaks_done')
                if hasattr(self, '_tweak_apply_event'):
                    self._tweak_apply_event.set()
        threading.Thread(target=task, daemon=True).start()

    def cancel_tweaks(self):
        self._notify('tweaks_done')
        if hasattr(self, '_tweak_apply_event'):
            self._tweak_apply_event.set()

    def read_wifi(self, multi=False):
        def task():
            try:
                self._notify('task_start', '"wifi"')
                with importDriver() as driver:
                    device = getDevice(driver)
                    if device:
                        if not isinstance(device, SonyExtCmdDevice):
                            print('Error: Cannot use camera in this mode.')
                            self._notify('wifi_result', json.dumps({'error': 'Cannot use camera in this mode'}))
                            return
                        dev = SonyExtCmdCamera(device)
                        if multi:
                            settings = dev.getMultiWifiAPInfo()
                        else:
                            settings = dev.getWifiAPInfo()
                        aps = []
                        for ap in settings:
                            ap_dict = ap._asdict()
                            aps.append({
                                'sid': ap_dict['sid'].decode('ascii').split('\x00')[0],
                                'key': ap_dict['key'].decode('ascii').split('\x00')[0],
                                'keyType': ap_dict['keyType'],
                            })
                        print('Found %d WiFi network(s)' % len(aps))
                        for ap in aps:
                            print('  SSID: %s  Key type: %d' % (ap['sid'], ap['keyType']))
                        self._notify('wifi_result', json.dumps({'networks': aps, 'multi': multi}))
                    else:
                        self._notify('wifi_result', json.dumps({'error': 'No device found'}))
            except Exception:
                traceback.print_exc()
                self._notify('wifi_result', json.dumps({'error': 'Failed to read WiFi settings'}))
            finally:
                self._notify('task_end', '"wifi"')
        threading.Thread(target=task, daemon=True).start()

    def write_wifi(self, networks, multi=False):
        def task():
            try:
                self._notify('task_start', '"wifi"')
                with importDriver() as driver:
                    device = getDevice(driver)
                    if device:
                        if not isinstance(device, SonyExtCmdDevice):
                            print('Error: Cannot use camera in this mode.')
                            self._notify('wifi_write_result', json.dumps({'error': 'Cannot use camera in this mode'}))
                            return
                        dev = SonyExtCmdCamera(device)
                        data = struct.pack('<i', len(networks))
                        for net in networks:
                            data += SonyExtCmdCamera.APInfo.pack(
                                keyType=int(net['keyType']),
                                sid=net['sid'].encode('ascii').ljust(33, b'\x00'),
                                key=net['key'].encode('ascii').ljust(65, b'\x00'),
                            )
                        if multi:
                            dev.setMultiWifiAPInfo(data)
                        else:
                            dev.setWifiAPInfo(data)
                        print('WiFi settings written successfully')
                        self._notify('wifi_write_result', json.dumps({'success': True}))
                    else:
                        self._notify('wifi_write_result', json.dumps({'error': 'No device found'}))
            except Exception:
                traceback.print_exc()
                self._notify('wifi_write_result', json.dumps({'error': 'Failed to write WiFi settings'}))
            finally:
                self._notify('task_end', '"wifi"')
        threading.Thread(target=task, daemon=True).start()

    def _get_camera_model_serial(self):
        """Get camera model and serial via info command (MTP/MSC mode)."""
        try:
            with importDriver() as driver:
                device = getDevice(driver)
                if device and isinstance(device, SonyExtCmdDevice):
                    dev = SonyExtCmdCamera(device)
                    info = dev.getCameraInfo()
                    return info.modelName, info.serial
        except Exception:
            pass
        return None, None

    def download_backup(self, mode='updater'):
        def task():
            try:
                self._notify('task_start', '"backup"')
                self._notify('backup_status', json.dumps({'message': 'Reading camera info...'}))

                model, serial = self._get_camera_model_serial()
                if not model:
                    model = 'unknown'
                if not serial:
                    serial = ''

                self._notify('backup_status', json.dumps({'message': 'Connecting in %s mode...' % mode}))

                backup_data = [None]

                if mode == 'updater':
                    def complete(dev):
                        backend = UsbPlatformBackend(dev)
                        backend.start()
                        self._notify('backup_status', json.dumps({'message': 'Downloading backup data...'}))
                        backup_data[0] = backend.getBackupData()
                        backend.stop()
                    updaterShellCommand(complete=complete)
                else:
                    def complete(cam):
                        backend = SenserPlatformBackend(cam)
                        self._notify('backup_status', json.dumps({'message': 'Downloading backup data...'}))
                        backup_data[0] = backend.getBackupData()
                    senserShellCommand(complete=complete)

                if backup_data[0] is None:
                    self._notify('backup_status', json.dumps({'message': 'Failed to download backup.', 'done': True}))
                    return

                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                parts = [model.replace(' ', '_')]
                if serial:
                    parts.append(serial)
                parts.append(timestamp)
                filename = 'Backup_%s.bin' % '_'.join(parts)

                result = self._window.create_file_dialog(
                    webview.FileDialog.SAVE,
                    file_types=('Backup Files (*.bin)', 'All Files (*.*)'),
                    save_filename=filename,
                )
                if result:
                    path = result if isinstance(result, str) else result[0]
                    with open(path, 'wb') as f:
                        f.write(backup_data[0])
                    print('Backup saved to %s (%d bytes)' % (path, len(backup_data[0])))
                    self._notify('backup_status', json.dumps({'message': 'Backup saved: %s' % os.path.basename(path), 'done': True}))
                else:
                    self._notify('backup_status', json.dumps({'message': 'Save cancelled.', 'done': True}))
            except Exception:
                traceback.print_exc()
                self._notify('backup_status', json.dumps({'message': 'Backup download failed.', 'done': True}))
            finally:
                self._notify('task_end', '"backup"')
        threading.Thread(target=task, daemon=True).start()

    def restore_backup(self, mode='updater'):
        def task():
            try:
                self._notify('task_start', '"backup"')

                result = self._window.create_file_dialog(
                    webview.FileDialog.OPEN,
                    file_types=('Backup Files (*.bin)', 'All Files (*.*)'),
                )
                if not result or len(result) == 0:
                    self._notify('backup_status', json.dumps({'message': 'Restore cancelled.', 'done': True}))
                    self._notify('task_end', '"backup"')
                    return

                path = result[0]
                with open(path, 'rb') as f:
                    data = f.read()

                if len(data) < 0x100:
                    self._notify('backup_status', json.dumps({'message': 'Invalid backup file (too small).', 'done': True}))
                    self._notify('task_end', '"backup"')
                    return

                print('Restoring backup from %s (%d bytes)' % (os.path.basename(path), len(data)))
                self._notify('backup_status', json.dumps({'message': 'Connecting in %s mode...' % mode}))

                success = [False]

                if mode == 'updater':
                    def complete(dev):
                        backend = UsbPlatformBackend(dev)
                        backend.start()
                        self._notify('backup_status', json.dumps({'message': 'Writing backup data...'}))
                        backend.setBackupData(data)
                        verify = backend.getBackupData()
                        if verify[0x100:] == data[0x100:]:
                            success[0] = True
                        else:
                            print('Warning: Backup verification mismatch')
                        backend.stop()
                    updaterShellCommand(complete=complete)
                else:
                    def complete(cam):
                        backend = SenserPlatformBackend(cam)
                        self._notify('backup_status', json.dumps({'message': 'Writing backup data...'}))
                        backend.setBackupData(data)
                        verify = backend.getBackupData()
                        if verify[0x100:] == data[0x100:]:
                            success[0] = True
                        else:
                            print('Warning: Backup verification mismatch')
                    senserShellCommand(complete=complete)

                if success[0]:
                    print('Backup restored and verified successfully')
                    self._notify('backup_status', json.dumps({'message': 'Backup restored successfully.', 'done': True}))
                else:
                    self._notify('backup_status', json.dumps({'message': 'Restore completed but verification failed.', 'done': True}))
            except Exception:
                traceback.print_exc()
                self._notify('backup_status', json.dumps({'message': 'Backup restore failed.', 'done': True}))
            finally:
                self._notify('task_end', '"backup"')
        threading.Thread(target=task, daemon=True).start()

    def run_diagnostics(self):
        def task():
            try:
                self._notify('task_start', '"system"')
                print('Running system diagnostics...')
                results = run_all_checks()
                data = [{'status': r.status, 'label': r.label, 'detail': r.detail, 'solution': r.solution} for r in results]
                passed = sum(1 for r in results if r.status == 'pass')
                warned = sum(1 for r in results if r.status == 'warn')
                failed = sum(1 for r in results if r.status == 'fail')
                print('Diagnostics complete: %d passed, %d warnings, %d failures' % (passed, warned, failed))
                self._notify('diagnostics_result', json.dumps(data))
            except Exception:
                traceback.print_exc()
            finally:
                self._notify('task_end', '"system"')
        threading.Thread(target=task, daemon=True).start()


ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')


def main():
    api = Api()
    capture = OutputCapture(api)
    capture.start()

    title = 'PMCA Camera Utility' + (' ' + version if version else '')
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
    url = 'file://' + os.path.join(ASSETS_DIR, 'index.html')
    window = webview.create_window(
        title,
        url=url,
        js_api=api,
        width=560,
        height=672,
        min_size=(400, 400),
    )
    api.set_window(window)
    webview.start(icon=icon_path)


if __name__ == '__main__':
    main()
