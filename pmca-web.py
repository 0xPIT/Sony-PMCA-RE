#!/usr/bin/env python3
"""A web-based GUI using pywebview"""
import io
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import webview

import config
import json
import struct

from pmca.commands.usb import (
    listApps, infoCommand, installCommand, firmwareUpdateCommand,
    updaterShellCommand, senserShellCommand, importDriver, getDevice
)
from pmca.usb.sony import SonyExtCmdDevice, SonyExtCmdCamera
from pmca.usb import InvalidCommandException
from pmca.platform.backend.senser import SenserPlatformBackend
from pmca.platform.backend.usb import UsbPlatformBackend
from pmca.platform.tweaks import TweakInterface
from pmca.backup import BackupFile
from pmca.resources import get_bundle_resource_path
from pmca.plugins import call_web, get_web_plugins

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
        self._ui_lock = threading.RLock()
        self._closing = False
        self._ui_ready = False
        self._apps = []
        self._tweaks_data = None
        self._tweak_interface = None

    def set_window(self, window):
        with self._ui_lock:
            self._window = window

    def mark_ready(self):
        """Called on the WebView 'loaded' event; JS is now safe to evaluate."""
        with self._ui_lock:
            self._ui_ready = True

    def shutdown(self):
        """Called on window close; stop touching a destroyed WebView."""
        with self._ui_lock:
            self._closing = True
            self._ui_ready = False
            self._window = None
        # Release a tweak session that may be blocked waiting for apply/cancel.
        event = getattr(self, '_tweak_apply_event', None)
        if event:
            event.set()

    def _evaluate_js(self, script):
        """Serialize JS evaluation and drop calls outside the WebView lifetime."""
        with self._ui_lock:
            window = self._window
            if self._closing or not self._ui_ready or window is None:
                return False
            try:
                window.evaluate_js(script)
                return True
            except Exception:
                return False

    def push_log(self, text):
        self._evaluate_js('window._appendLog(%s)' % json.dumps(text))

    def signal_error(self):
        self._evaluate_js('window._signalError()')

    def _notify(self, event, data='null'):
        self._evaluate_js('window._onEvent(%s, %s)' % (json.dumps(event), data))

    def get_config(self):
        return {
            'version': version or '',
            'docsUrl': config.docsUrl,
            'githubAppListUser': config.githubAppListUser,
            'githubAppListRepo': config.githubAppListRepo,
            'plugins': get_web_plugins(),
        }

    def plugin_call(self, plugin_id, method, *args):
        """Dispatch a call to an optional plugin's web handler."""
        return call_web(self, plugin_id, method, args)

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

                def complete(dev, modelName=None):
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
                        try:
                            if multi:
                                settings = list(dev.getMultiWifiAPInfo())
                            else:
                                settings = list(dev.getWifiAPInfo())
                        except InvalidCommandException:
                            self._notify('wifi_result', json.dumps({
                                'error': 'Camera does not support %sWiFi settings' % ('multi-' if multi else '')}))
                            return
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

    @staticmethod
    def _backup_to_text(data):
        output = io.StringIO()
        def writeHexDump(raw, n=16, indent=0):
            for i in range(0, len(raw), n):
                line = bytearray(raw[i:i+n])
                hex_str = ' '.join('%02x' % c for c in line)
                text = ''.join(chr(c) if 0x21 <= c <= 0x7e else '.' for c in line)
                output.write('%*s%-*s %s\n' % (indent, '', n*3, hex_str, text))
        binf = io.BytesIO(data)
        for id, property in BackupFile(binf).listProperties():
            output.write('id=0x%08x, size=0x%04x, attr=0x%02x:\n' % (id, len(property.data), property.attr))
            writeHexDump(property.data, indent=2)
            if property.resetData and property.resetData != property.data:
                output.write('reset data:\n')
                writeHexDump(property.resetData, indent=2)
            output.write('\n')
        return output.getvalue()

    def download_backup(self, mode='updater', parsedtextproperties=False):
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
                    def complete(cam, modelName=None):
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

                if parsedtextproperties:
                    filename = 'Backup_%s.txt' % '_'.join(parts)
                    file_types = ('Text Files (*.txt)', 'All Files (*.*)')
                else:
                    filename = 'Backup_%s.bin' % '_'.join(parts)
                    file_types = ('Backup Files (*.bin)', 'All Files (*.*)')

                result = self._window.create_file_dialog(
                    webview.FileDialog.SAVE,
                    file_types=file_types,
                    save_filename=filename,
                )
                if result:
                    path = result if isinstance(result, str) else result[0]
                    if parsedtextproperties:
                        with open(path, 'w') as f:
                            f.write(self._backup_to_text(backup_data[0]))
                        print('Backup text saved to %s (%d bytes raw)' % (path, len(backup_data[0])))
                    else:
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
                    def complete(cam, modelName=None):
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


_REQUIRED_WEB_ASSETS = (
    'assets/index.html',
    'assets/app.js',
    'assets/style.css',
    'assets/icon.png',
)


def get_startup_page():
    """Return a valid file:// URL, or a self-contained resource error page.

    Path(...).as_uri() produces a correct file:///C:/... URI on Windows;
    the previous 'file://' + os.path.join(...) produced an invalid URI there.
    """
    missing = [path for path in _REQUIRED_WEB_ASSETS
               if not os.path.isfile(get_bundle_resource_path(path))]
    if not missing:
        return {'url': Path(get_bundle_resource_path('assets/index.html')).as_uri()}

    items = ''.join('<li><code>%s</code></li>' % path for path in missing)
    return {'html': '''<!doctype html><html><head><meta charset="utf-8">
<title>PMCA resource error</title></head><body style="font-family:sans-serif;padding:2rem">
<h1>PMCA could not start</h1><p>Required application resources are missing:</p>
<ul>%s</ul><p>Reinstall or rebuild the application with the complete assets directory.</p>
</body></html>''' % items}


def get_webview_start_options():
    """Runtime icon options. pywebview's GTK/Qt backends accept a PNG icon,
    but the Windows backend treats it as a WinForms .ico and crashes; frozen
    Windows executables set their icon at packaging time instead."""
    icon_path = get_bundle_resource_path('icon.png')
    if sys.platform != 'win32' and os.path.isfile(icon_path):
        return {'icon': icon_path}
    return {}


def main():
    api = Api()
    capture = OutputCapture(api)
    capture.start()

    title = 'PMCA Camera Utility' + (' ' + version if version else '')
    window = webview.create_window(
        title,
        js_api=api,
        width=560,
        height=672,
        min_size=(400, 400),
        **get_startup_page(),
    )
    api.set_window(window)
    window.events.loaded += api.mark_ready
    window.events.closed += api.shutdown
    webview.start(**get_webview_start_options())


if __name__ == '__main__':
    main()
