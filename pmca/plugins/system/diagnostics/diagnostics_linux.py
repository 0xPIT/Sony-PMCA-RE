"""Linux-specific USB diagnostics."""

import os
import subprocess

from . import DiagResult


def check_usb_permissions():
 """Check if current user can access USB devices without root."""
 if os.geteuid() == 0:
  return DiagResult('pass', 'USB permissions', 'Running as root — full USB access.', None)

 # Check if user is in a USB-related group
 try:
  import grp
  user_groups = os.getgroups()
  usb_groups = []
  for name in ['plugdev', 'usb', 'dialout']:
   try:
    g = grp.getgrnam(name)
    if g.gr_gid in user_groups:
     usb_groups.append(name)
   except KeyError:
    pass

  if usb_groups:
   return DiagResult('pass', 'USB permissions',
    'User is in group(s): %s' % ', '.join(usb_groups), None)
 except ImportError:
  pass

 # Try to actually access a Sony device
 try:
  import usb.core
  devices = list(usb.core.find(find_all=True, idVendor=0x054c))
  if devices:
   try:
    devices[0].get_active_configuration()
    return DiagResult('pass', 'USB permissions', 'Sony device accessible without root.', None)
   except usb.core.USBError as e:
    if 'Access' in str(e) or 'Permission' in str(e) or 'Errno 13' in str(e):
     return DiagResult('fail', 'USB permissions',
      'Permission denied accessing Sony USB device.',
      'Either run with sudo, or add a udev rule:\n'
      'Create /etc/udev/rules.d/99-sony-camera.rules with:\n'
      'SUBSYSTEM=="usb", ATTR{idVendor}=="054c", MODE="0666"\n'
      'Then run: sudo udevadm control --reload-rules && sudo udevadm trigger')
 except Exception:
  pass

 return DiagResult('warn', 'USB permissions',
  'Not running as root and cannot verify USB access.',
  'If you get "Access denied" errors, run with sudo or add a udev rule:\n'
  'Create /etc/udev/rules.d/99-sony-camera.rules with:\n'
  'SUBSYSTEM=="usb", ATTR{idVendor}=="054c", MODE="0666"\n'
  'Then run: sudo udevadm control --reload-rules && sudo udevadm trigger')


def check_udev_rules():
 """Check if a udev rule exists for Sony cameras."""
 udev_dirs = ['/etc/udev/rules.d', '/usr/lib/udev/rules.d', '/run/udev/rules.d']
 found_rules = []
 for d in udev_dirs:
  if not os.path.isdir(d):
   continue
  try:
   for f in os.listdir(d):
    if f.endswith('.rules'):
     path = os.path.join(d, f)
     try:
      with open(path, 'r') as fh:
       content = fh.read()
       if '054c' in content.lower() or 'sony' in content.lower():
        found_rules.append(path)
     except (PermissionError, IOError):
      pass
  except PermissionError:
   pass

 if found_rules:
  return DiagResult('pass', 'udev rules',
   'Sony USB rules found: %s' % ', '.join(found_rules), None)
 return DiagResult('warn', 'udev rules',
  'No udev rule found for Sony cameras (vendor 054c).',
  'Without a udev rule, you need root access for USB operations.\n'
  'Create /etc/udev/rules.d/99-sony-camera.rules with:\n'
  'SUBSYSTEM=="usb", ATTR{idVendor}=="054c", MODE="0666"\n'
  'Then run: sudo udevadm control --reload-rules && sudo udevadm trigger')


def check_libusb_package():
 """Check if libusb system package is installed."""
 pkg_managers = [
  (['dpkg', '-s', 'libusb-1.0-0'], 'libusb-1.0-0', 'apt install libusb-1.0-0-dev'),
  (['rpm', '-q', 'libusb1'], 'libusb1', 'dnf install libusb1-devel'),
  (['pacman', '-Q', 'libusb'], 'libusb', 'pacman -S libusb'),
 ]
 for cmd, pkg, install_cmd in pkg_managers:
  try:
   result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
   if result.returncode == 0:
    return DiagResult('pass', 'libusb system package',
     '%s is installed.' % pkg, None)
   else:
    return DiagResult('warn', 'libusb system package',
     '%s not found.' % pkg,
     'Install with: sudo %s' % install_cmd)
  except FileNotFoundError:
   continue
  except Exception:
   continue

 return DiagResult('warn', 'libusb system package',
  'Could not detect package manager to check libusb.',
  'Ensure libusb is installed for your distribution.')


def check_vm_environment():
 """Detect if running inside a virtual machine."""
 vm_indicators = []

 # Check DMI
 dmi_paths = [
  '/sys/class/dmi/id/product_name',
  '/sys/class/dmi/id/sys_vendor',
  '/sys/class/dmi/id/board_vendor',
 ]
 vm_strings = ['virtualbox', 'vmware', 'qemu', 'kvm', 'parallels', 'hyper-v', 'xen']
 for path in dmi_paths:
  try:
   with open(path, 'r') as f:
    content = f.read().strip().lower()
    for vs in vm_strings:
     if vs in content:
      vm_indicators.append(content)
      break
  except (IOError, PermissionError):
   pass

 # Check systemd-detect-virt
 try:
  result = subprocess.run(['systemd-detect-virt'], capture_output=True, text=True, timeout=5)
  if result.returncode == 0 and result.stdout.strip() != 'none':
   vm_indicators.append(result.stdout.strip())
 except (FileNotFoundError, Exception):
  pass

 if vm_indicators:
  return DiagResult('warn', 'Virtual machine detected',
   'Running inside a VM: %s' % ', '.join(set(vm_indicators)),
   'USB passthrough in VMs can cause timeouts. Pass through the entire USB hub/controller '
   'to the guest rather than individual devices. If you get "Operation timed out" errors, '
   'consider running on bare metal or a bootable Linux USB stick instead.')
 return DiagResult('pass', 'Virtual machine',
  'Not running inside a detected VM.', None)


def check_kernel_modules():
 """Check if relevant USB kernel modules are loaded."""
 important_modules = ['usb_storage', 'usbcore', 'libcomposite']
 loaded = []
 try:
  with open('/proc/modules', 'r') as f:
   content = f.read()
   for mod in important_modules:
    if mod in content:
     loaded.append(mod)
 except (IOError, PermissionError):
  return DiagResult('warn', 'Kernel USB modules',
   'Could not read /proc/modules.', None)

 if 'usbcore' not in loaded:
  return DiagResult('fail', 'Kernel USB modules',
   'usbcore not loaded — USB subsystem may not be available.',
   'This is unusual. Check dmesg for USB errors: dmesg | grep -i usb')
 return DiagResult('pass', 'Kernel USB modules',
  'Loaded: %s' % ', '.join(loaded), None)


def run_linux_checks():
 """Run all Linux-specific diagnostics."""
 return [
  check_libusb_package(),
  check_usb_permissions(),
  check_udev_rules(),
  check_kernel_modules(),
  check_vm_environment(),
 ]
