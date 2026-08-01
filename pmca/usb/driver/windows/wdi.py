"""Automated libusb-win32 driver management via wdi-helper.exe."""

import json
import os
import sys
import ctypes

_HELPER_NAME = 'wdi-helper.exe'
_DEFAULT_VID = 0x054C
_DEFAULT_NAME = 'Sony Camera Service Mode'
_STATE_FILE_NAME = 'sony_pmca_driver_state.json'


def _state_file_path():
 temp = os.environ.get('TEMP') or os.environ.get('TMP')
 if not temp:
  return None
 return os.path.join(temp, _STATE_FILE_NAME)


def pending_restore_pid():
 """Return PID from wdi-helper state file if a prior install left one, else None."""
 path = _state_file_path()
 if not path or not os.path.isfile(path):
  return None
 try:
  with open(path, 'r') as f:
   data = json.load(f)
  pid = int(data.get('pid', 0))
  return pid if pid else None
 except (OSError, ValueError, TypeError, json.JSONDecodeError):
  return None



def _find_helper():
 """Locate wdi-helper.exe for frozen and source checkouts."""
 candidates = []

 env = os.environ.get('PMCA_WDI_HELPER')
 if env:
  candidates.append(env)

 if getattr(sys, 'frozen', False):
  meipass = getattr(sys, '_MEIPASS', None)
  if meipass:
   candidates.append(os.path.join(meipass, _HELPER_NAME))
  candidates.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), _HELPER_NAME))

 here = os.path.dirname(os.path.abspath(__file__))
 candidates.append(os.path.join(here, _HELPER_NAME))

 # Repo layout: <root>/wdi-helper/wdi-helper.exe (CI artifact drop-in)
 repo_root = os.path.abspath(os.path.join(here, '..', '..', '..', '..'))
 candidates.append(os.path.join(repo_root, 'wdi-helper', _HELPER_NAME))

 for path in candidates:
  if path and os.path.isfile(path):
   return path
 return None


def helper_available():
 return _find_helper() is not None


def _run_elevated(exe_path, args):
 """Run exe with UAC elevation via ShellExecuteEx. Returns exit code, or -1 on failure."""
 import ctypes.wintypes

 class SHELLEXECUTEINFO(ctypes.Structure):
  _fields_ = [
   ('cbSize', ctypes.wintypes.DWORD),
   ('fMask', ctypes.c_ulong),
   ('hwnd', ctypes.wintypes.HANDLE),
   ('lpVerb', ctypes.c_wchar_p),
   ('lpFile', ctypes.c_wchar_p),
   ('lpParameters', ctypes.c_wchar_p),
   ('lpDirectory', ctypes.c_wchar_p),
   ('nShow', ctypes.c_int),
   ('hInstApp', ctypes.wintypes.HINSTANCE),
   ('lpIDList', ctypes.c_void_p),
   ('lpClass', ctypes.c_wchar_p),
   ('hkeyClass', ctypes.wintypes.HKEY),
   ('dwHotKey', ctypes.wintypes.DWORD),
   ('hIconOrMonitor', ctypes.wintypes.HANDLE),
   ('hProcess', ctypes.wintypes.HANDLE),
  ]

 SEE_MASK_NOCLOSEPROCESS = 0x00000040
 SW_HIDE = 0
 INFINITE = 0xFFFFFFFF

 sei = SHELLEXECUTEINFO()
 sei.cbSize = ctypes.sizeof(sei)
 sei.fMask = SEE_MASK_NOCLOSEPROCESS
 sei.lpVerb = 'runas'
 sei.lpFile = exe_path
 sei.lpParameters = args
 sei.nShow = SW_HIDE

 if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
  return -1

 ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, INFINITE)
 exit_code = ctypes.wintypes.DWORD()
 ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
 ctypes.windll.kernel32.CloseHandle(sei.hProcess)
 return exit_code.value


def install_libusb_driver(vid=_DEFAULT_VID, pid=0x0336, name=_DEFAULT_NAME):
 """Install libusb-win32 filter driver for the given VID/PID.

 Returns True on success, False on failure.
 """
 helper = _find_helper()
 if not helper:
  return False
 args = 'install --vid 0x%04X --pid 0x%04X --name "%s"' % (vid, pid, name)
 return _run_elevated(helper, args) == 0


def restore_original_driver(vid=_DEFAULT_VID, pid=0x0336):
 """Remove the libusb-win32 filter and restore the previous driver state.

 Returns True on success, False on failure.
 """
 helper = _find_helper()
 if not helper:
  return False
 args = 'restore --vid 0x%04X --pid 0x%04X' % (vid, pid)
 return _run_elevated(helper, args) == 0
