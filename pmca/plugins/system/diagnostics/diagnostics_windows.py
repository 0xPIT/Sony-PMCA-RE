"""Windows-specific USB diagnostics."""

import os
import subprocess
import sys

from . import DiagResult

# Concrete, community-proven recipe for binding a libusb driver on Windows.
# Service mode ONLY — never replace the normal-mode (MTP/MSC) driver.
ZADIG_RECIPE = (
 'Service mode only: with the device in service mode, use Zadig 2.8 (zadig.akeo.ie) to '
 'install the "libusb-win32" (1.2.7.3) driver for that specific service-mode device. '
 'Verify the VID (054C), PID and interface before replacing, do NOT replace the '
 'normal-mode (MTP/Mass Storage) driver, and roll the driver back via Device Manager '
 'when you are done.')


def check_service_mode_libusb_binding():
 """Check whether Sony devices are accessible to libusb (needed for service mode only).

 Normal MTP/Mass Storage operations use the native Windows driver and do not need a
 libusb binding, so inaccessibility here is a warning, not a failure."""
 try:
  import usb.core
  devices = list(usb.core.find(find_all=True, idVendor=0x054c))
  if devices:
   for dev in devices:
    try:
     dev.get_active_configuration()
     return DiagResult('pass', 'Service mode libusb binding',
      'Sony device accessible via libusb (PID 0x%04x).' % dev.idProduct, None)
    except usb.core.USBError as e:
     if 'Entity not found' in str(e) or 'Access' in str(e):
      return DiagResult('warn', 'Service mode libusb binding',
       'Sony device visible to libusb but not accessible (no libusb driver bound). '
       'This only affects service mode; normal operations are unaffected.',
       ZADIG_RECIPE)
   return DiagResult('warn', 'Service mode libusb binding',
    'Sony device found but libusb accessibility could not be verified.',
    'If service mode operations fail: ' + ZADIG_RECIPE)
  return DiagResult('pass', 'Service mode libusb binding',
   'No Sony device bound to libusb (normal for MTP/Mass Storage mode).', None)
 except Exception as e:
  return DiagResult('warn', 'Service mode libusb binding',
   'Cannot check libusb binding: %s' % str(e),
   ZADIG_RECIPE)


def check_libusb_dll():
 """Check whether a libusb runtime DLL is discoverable by the application.

 Searches the PyInstaller bundle and executable directory (where a frozen
 build ships the DLL) first, then the system directories and PATH."""
 systemroot = os.environ.get('SYSTEMROOT', 'C:\\Windows')
 search_dirs = []
 meipass = getattr(sys, '_MEIPASS', None)
 if meipass:
  search_dirs.append(meipass)
 search_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
 search_dirs.append(os.path.join(systemroot, 'System32'))
 search_dirs.append(os.path.join(systemroot, 'SysWOW64'))
 search_dirs.extend(os.environ.get('PATH', '').split(os.pathsep))

 names = ['libusb-1.0.dll', 'libusb0.dll']
 found = []
 for d in search_dirs:
  if not d:
   continue
  for name in names:
   p = os.path.join(d, name)
   if os.path.isfile(p) and p not in found:
    found.append(p)

 if found:
  return DiagResult('pass', 'libusb DLL', 'Found: %s' % ', '.join(found), None)
 return DiagResult('warn', 'libusb DLL',
  'No libusb runtime DLL (libusb-1.0.dll) found in the bundle, executable directory, '
  'system directories, or PATH. Only required for service mode.',
  'Optional (service mode only): place libusb-1.0.dll next to the executable or on your PATH.')


def check_service_mode_driver():
 """Check if vendor-specific driver is available for service mode."""
 try:
  import usb.core
  # Service mode devices: PID 0x02a9 or 0x0336
  service_pids = [0x02a9, 0x0336]
  for pid in service_pids:
   devices = list(usb.core.find(find_all=True, idVendor=0x054c, idProduct=pid))
   if devices:
    try:
     devices[0].get_active_configuration()
     return DiagResult('pass', 'Service mode driver',
      'Service mode device (PID 0x%04x) accessible.' % pid, None)
    except usb.core.USBError:
     return DiagResult('warn', 'Service mode driver',
      'Service mode device found but not accessible (no libusb driver bound).',
      ZADIG_RECIPE + ' The service-mode device appears only after the camera enters '
      'service mode, so you may need to start the operation and then bind the driver '
      'when the device shows up in Zadig.')
  return DiagResult('pass', 'Service mode driver',
   'No service mode device detected (camera not in service mode — this is normal).', None)
 except Exception as e:
  return DiagResult('warn', 'Service mode driver',
   'Could not check: %s' % str(e), None)


def check_wmp_not_claiming():
 """Check if Windows Media Player / WPD is interfering."""
 try:
  result = subprocess.run(
   ['tasklist', '/FI', 'IMAGENAME eq WMPNetworkSvc.exe'],
   capture_output=True, text=True, timeout=5
  )
  if 'WMPNetworkSvc.exe' in result.stdout:
   return DiagResult('warn', 'WMP Network Service',
    'Windows Media Player Network Sharing Service is running.',
    'This service can interfere with MTP camera access. '
    'Stop it via: net stop WMPNetworkSvc')
  return DiagResult('pass', 'WMP Network Service',
   'Not running.', None)
 except Exception:
  return DiagResult('pass', 'WMP Network Service', 'Could not check (non-critical).', None)


def check_windows_version():
 """Report Windows version for debugging."""
 try:
  import platform
  ver = platform.version()
  release = platform.release()
  return DiagResult('pass', 'Windows version', 'Windows %s (build %s)' % (release, ver), None)
 except Exception:
  return DiagResult('pass', 'Windows version', 'Could not detect version.', None)


def run_windows_checks():
 """Run all Windows-specific diagnostics."""
 return [
  check_windows_version(),
  check_libusb_dll(),
  check_service_mode_libusb_binding(),
  check_service_mode_driver(),
  check_wmp_not_claiming(),
 ]
