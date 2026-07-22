"""Windows-specific USB diagnostics."""

import os
import subprocess
import sys

from . import DiagResult


def check_libusb_device_access():
 """Check libusb device access without recommending an unverified driver."""
 try:
  import usb.core
  devices = list(usb.core.find(find_all=True, idVendor=0x054c))
  if devices:
   for dev in devices:
    try:
     dev.get_active_configuration()
     return DiagResult('pass', 'libusb device access',
      'Sony device accessible via libusb (PID 0x%04x).' % dev.idProduct, None)
    except usb.core.USBError as e:
     if 'Entity not found' in str(e) or 'Access' in str(e):
      return DiagResult('fail', 'libusb device access',
       'Sony device found but not accessible. Wrong driver or access denied.',
       'Record VID, PID, interface number, instance ID and current driver. Test only the exact identity required by PMCA, with a documented rollback.')
   return DiagResult('warn', 'libusb device access',
    'Sony device found but could not verify driver.',
    'Record the exact USB identity and current binding before considering a manual driver test.')
  return DiagResult('warn', 'libusb device access',
   'No Sony USB device detected to check driver.',
   'Connect your camera first, then re-run diagnostics.')
 except Exception as e:
  return DiagResult('warn', 'libusb device access',
   'Cannot check driver: %s' % str(e),
   'Verify that the libusb-1.0 runtime DLL is available before evaluating any device-driver binding.')


def check_libusb_dll():
 """Check if libusb DLL is present on the system."""
 search_paths = [
  os.path.join(getattr(sys, '_MEIPASS', ''), 'libusb-1.0.dll'),
  os.path.join(os.path.dirname(sys.executable), 'libusb-1.0.dll'),
  os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'System32', 'libusb0.dll'),
  os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'SysWOW64', 'libusb0.dll'),
 ]
 for directory in os.environ.get('PATH', '').split(os.pathsep):
  if directory:
   search_paths.extend([
    os.path.join(directory, 'libusb-1.0.dll'),
    os.path.join(directory, 'libusb0.dll'),
   ])

 found = []
 for p in search_paths:
  if os.path.isfile(p) and p not in found:
   found.append(p)

 if not found:
  # Check for libusb-1.0
  for p in [
   os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'System32', 'libusb-1.0.dll'),
   os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'SysWOW64', 'libusb-1.0.dll'),
  ]:
   if os.path.exists(p):
    found.append(p)

 if found:
  return DiagResult('pass', 'libusb DLL', 'Found: %s' % ', '.join(found), None)
 return DiagResult('warn', 'libusb DLL',
  'libusb runtime DLL not found in the bundle, application directory, or system directories.',
  'Provide libusb-1.0.dll to the application build or system PATH. This does not change the device driver.')


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
     return DiagResult('fail', 'Service mode driver',
      'Service mode device found but not accessible.',
      'Record this service-mode identity separately from normal MTP/mass storage. '
      'Verify VID, PID, interface, current driver, supported backend and rollback before any manual change.')
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
  check_libusb_device_access(),
  check_service_mode_driver(),
  check_wmp_not_claiming(),
 ]
