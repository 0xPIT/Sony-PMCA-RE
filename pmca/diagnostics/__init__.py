"""System diagnostics for USB/camera connectivity issues."""

import sys
from collections import namedtuple

DiagResult = namedtuple('DiagResult', 'status, label, detail, solution')


def check_libusb_available():
 """Check if the libusb backend is importable and functional."""
 try:
  import usb.core
  import usb.backend.libusb1
  backend = usb.backend.libusb1.get_backend()
  if backend is None:
   if sys.platform == 'win32':
    status = 'warn'
    solution = ('Provide a compatible libusb-1.0 runtime DLL to PyUSB. '
     'This is separate from a Windows USB device-driver binding; do not change a device driver until the exact USB identity is verified.')
   else:
    status = 'fail'
    solution = 'Install libusb: "brew install libusb" (macOS) or "apt install libusb-1.0-0-dev" (Linux).'
   return DiagResult(status, 'libusb backend',
    'libusb shared library not found by PyUSB.',
    solution)
  return DiagResult('pass', 'libusb backend', 'libusb backend loaded successfully.', None)
 except ImportError as e:
  return DiagResult('fail', 'libusb backend',
   'PyUSB not installed: %s' % str(e),
   'Install PyUSB: pip install pyusb')
 except Exception as e:
  return DiagResult('fail', 'libusb backend',
   'Unexpected error: %s' % str(e),
   'Check your libusb installation.')


def check_pyusb_version():
 """Report PyUSB version."""
 try:
  import usb.core
  version = getattr(usb, '__version__', 'unknown')
  return DiagResult('pass', 'PyUSB version', 'PyUSB %s' % version, None)
 except ImportError:
  return DiagResult('fail', 'PyUSB version', 'PyUSB not installed.', 'pip install pyusb')


def check_sony_device_visible():
 """Check if any Sony USB device is visible (vendor ID 0x054c)."""
 try:
  import usb.core
  devices = list(usb.core.find(find_all=True, idVendor=0x054c))
  if not devices:
   return DiagResult('warn', 'Sony USB device',
    'No Sony USB device detected (vendor 0x054c).',
    'Ensure camera is connected, powered on, and set to USB Mass Storage mode. Try a different USB cable or port.')
  names = []
  for d in devices:
   names.append('PID 0x%04x' % d.idProduct)
  return DiagResult('pass', 'Sony USB device',
   'Found %d Sony device(s): %s' % (len(devices), ', '.join(names)), None)
 except Exception as e:
  status = 'warn' if sys.platform == 'win32' else 'fail'
  solution = ('The libusb scan is unavailable. Native Windows MTP/WPD or mass-storage access may still work.'
   if sys.platform == 'win32' else 'Check libusb installation and USB permissions.')
  return DiagResult(status, 'Sony USB device',
   'Error scanning USB: %s' % str(e),
   solution)


def check_python_version():
 """Check Python version compatibility."""
 major, minor = sys.version_info[:2]
 version_str = '%d.%d.%d' % sys.version_info[:3]
 if major < 3 or (major == 3 and minor < 7):
  return DiagResult('warn', 'Python version',
   'Python %s detected. Python 3.7+ recommended.' % version_str,
   'Upgrade Python to 3.7 or later.')
 return DiagResult('pass', 'Python version', 'Python %s' % version_str, None)


def check_crypto_available():
 """Check if cryptography libraries are available (needed for service mode)."""
 try:
  try:
   from Cryptodome.Hash import SHA256
   return DiagResult('pass', 'Crypto library', 'PyCryptodome available.', None)
  except ImportError:
   from Crypto.Hash import SHA256
   return DiagResult('pass', 'Crypto library', 'PyCrypto available.', None)
 except ImportError:
  return DiagResult('warn', 'Crypto library',
   'Neither PyCryptodome nor PyCrypto found.',
   'Install with: pip install pycryptodome. Required for service mode operations.')


def run_common_checks():
 """Run all platform-independent diagnostics."""
 return [
  check_python_version(),
  check_libusb_available(),
  check_pyusb_version(),
  check_crypto_available(),
  check_sony_device_visible(),
 ]


def run_all_checks():
 """Run common checks plus platform-specific checks, sorted by severity."""
 results = run_common_checks()

 if sys.platform == 'darwin':
  from .diagnostics_macos import run_macos_checks
  results.extend(run_macos_checks())
 elif sys.platform == 'win32':
  from .diagnostics_windows import run_windows_checks
  results.extend(run_windows_checks())
 elif sys.platform.startswith('linux'):
  from .diagnostics_linux import run_linux_checks
  results.extend(run_linux_checks())

 order = {'fail': 0, 'warn': 1, 'pass': 2}
 results.sort(key=lambda r: order.get(r.status, 3))
 return results
