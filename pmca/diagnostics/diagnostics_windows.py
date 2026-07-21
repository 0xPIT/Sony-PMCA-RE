"""Windows-specific USB diagnostics."""

import os
import subprocess

from . import DiagResult


def check_zadig_driver():
 """Check if a libusb-compatible driver is installed for Sony devices."""
 try:
  import usb.core
  devices = list(usb.core.find(find_all=True, idVendor=0x054c))
  if devices:
   for dev in devices:
    try:
     dev.get_active_configuration()
     return DiagResult('pass', 'USB driver (Zadig)',
      'Sony device accessible via libusb (PID 0x%04x).' % dev.idProduct, None)
    except usb.core.USBError as e:
     if 'Entity not found' in str(e) or 'Access' in str(e):
      return DiagResult('fail', 'USB driver (Zadig)',
       'Sony device found but not accessible. Wrong driver or access denied.',
       'Use Zadig 2.8 (zadig.akeo.ie) to install the "libusb-win32" driver for your Sony camera. '
       'Select your camera in Zadig, choose "libusb-win32" as the target driver, and click "Replace Driver".')
   return DiagResult('warn', 'USB driver (Zadig)',
    'Sony device found but could not verify driver.',
    'If operations fail, use Zadig to install libusb-win32 for your camera.')
  return DiagResult('warn', 'USB driver (Zadig)',
   'No Sony USB device detected to check driver.',
   'Connect your camera first, then re-run diagnostics.')
 except Exception as e:
  return DiagResult('fail', 'USB driver (Zadig)',
   'Cannot check driver: %s' % str(e),
   'Ensure libusb is installed. Use Zadig 2.8 to install libusb-win32 for your camera.')


def check_libusb_dll():
 """Check if libusb DLL is present on the system."""
 search_paths = [
  os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'System32', 'libusb0.dll'),
  os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'SysWOW64', 'libusb0.dll'),
 ]
 # Also check PATH
 found = []
 for p in search_paths:
  if os.path.exists(p):
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
  'libusb DLL not found in system directories.',
  'Install a libusb-compatible driver via Zadig, or manually place libusb-1.0.dll in your system PATH.')


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
      'Use Zadig to install libusb-win32 for the "service mode" USB device. '
      'The device appears only when the camera enters service mode — '
      'you may need to start the operation and then install the driver when it shows up in Zadig.')
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
  check_zadig_driver(),
  check_service_mode_driver(),
  check_wmp_not_claiming(),
 ]
