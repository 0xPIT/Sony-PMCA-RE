"""macOS-specific USB diagnostics."""

import os
import platform
import subprocess

from . import DiagResult


def check_sony_kext_installed():
 """Check if Sony Camera Driver kernel extension is installed."""
 kext_paths = [
  '/Library/Extensions/SONYDeviceType01.kext',
  '/Library/Extensions/SONYDeviceType04.kext',
 ]
 found = [p for p in kext_paths if os.path.exists(p)]
 if not found:
  return DiagResult('warn', 'Sony Camera Driver (kext)',
   'Sony kernel extension not found in /Library/Extensions/.',
   'Install the Sony Camera Driver from Sony\'s website. Required for native mass storage communication on macOS.')
 return DiagResult('pass', 'Sony Camera Driver (kext)',
  'Found: %s' % ', '.join(os.path.basename(p) for p in found), None)


def check_sony_kext_loaded():
 """Check if Sony kernel extension is loaded into the kernel."""
 try:
  result = subprocess.run(['kextstat'], capture_output=True, text=True, timeout=5)
  kext_ids = ['com.sony.driver.dsccamFirmwareUpdaterType00', 'com.sony.driver.dsccamDeviceInfo00']
  loaded = [k for k in kext_ids if k in result.stdout]
  if not loaded:
   kext_paths = [
    '/Library/Extensions/SONYDeviceType01.kext',
    '/Library/Extensions/SONYDeviceType04.kext',
   ]
   if any(os.path.exists(p) for p in kext_paths):
    return DiagResult('warn', 'Sony kext loaded',
     'Sony kext is installed but not loaded into the kernel.',
     'Try: sudo kextload /Library/Extensions/SONYDeviceType01.kext — or reboot. On macOS 11+ you may need to allow it in System Settings > Security.')
   return DiagResult('warn', 'Sony kext loaded',
    'Sony kext not loaded (not installed).',
    'Install the Sony Camera Driver first.')
  return DiagResult('pass', 'Sony kext loaded',
   'Loaded: %s' % ', '.join(loaded), None)
 except FileNotFoundError:
  # kextstat may not exist on newer macOS
  return DiagResult('warn', 'Sony kext loaded',
   'Cannot check kext status (kextstat not available on this macOS version).',
   'On macOS 11+, kernel extensions may require explicit approval in System Settings > Security.')
 except Exception as e:
  return DiagResult('warn', 'Sony kext loaded',
   'Could not check: %s' % str(e), None)


def check_interfering_apps():
 """Check if apps that claim USB cameras are running."""
 interfering = {
  'Photos': 'Photos.app',
  'Image Capture': 'Image Capture.app',
  'Dropbox': 'Dropbox',
  'Google Drive': 'Google Drive',
 }
 running = []
 try:
  result = subprocess.run(['ps', '-eo', 'comm'], capture_output=True, text=True, timeout=5)
  lines = result.stdout.lower()
  for name, label in interfering.items():
   if name.lower() in lines:
    running.append(label)
 except Exception:
  pass

 if running:
  return DiagResult('warn', 'Interfering applications',
   'These apps may claim the camera USB device: %s' % ', '.join(running),
   'Quit these applications before connecting your camera: %s' % ', '.join(running))
 return DiagResult('pass', 'Interfering applications',
  'No known interfering applications running.', None)


def check_libusb_homebrew():
 """Check if libusb is installed via Homebrew."""
 try:
  result = subprocess.run(['brew', 'list', 'libusb'], capture_output=True, text=True, timeout=10)
  if result.returncode == 0:
   return DiagResult('pass', 'Homebrew libusb',
    'libusb installed via Homebrew.', None)
  else:
   return DiagResult('warn', 'Homebrew libusb',
    'libusb not found via Homebrew.',
    'Install with: brew install libusb')
 except FileNotFoundError:
  return DiagResult('warn', 'Homebrew libusb',
   'Homebrew not found.',
   'Install Homebrew (https://brew.sh) then run: brew install libusb')
 except Exception as e:
  return DiagResult('warn', 'Homebrew libusb',
   'Could not check: %s' % str(e), None)


def check_macos_version():
 """Report macOS version."""
 ver = platform.mac_ver()[0]
 if not ver:
  return DiagResult('warn', 'macOS version', 'Could not detect macOS version.', None)
 return DiagResult('pass', 'macOS version', 'macOS %s' % ver, None)


def check_running_as_root():
 """Check if running with elevated privileges (needed for USB access on macOS)."""
 if os.geteuid() == 0:
  return DiagResult('pass', 'Elevated privileges', 'Running as root/sudo.', None)
 return DiagResult('fail', 'Elevated privileges',
  'Not running as root. USB operations will fail without elevated privileges.',
  'Run with: sudo python3 pmca-web.py')


def run_macos_checks():
 """Run all macOS-specific diagnostics."""
 return [
  check_macos_version(),
  check_libusb_homebrew(),
  check_sony_kext_installed(),
  check_sony_kext_loaded(),
  check_interfering_apps(),
  check_running_as_root(),
 ]
