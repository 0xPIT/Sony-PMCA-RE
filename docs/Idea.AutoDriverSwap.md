# Automated libusb-win32 Driver Swap for Service Mode

## Problem

When the camera enters service mode, it re-enumerates as USB device `054C:0336` ("Sony USB Device", class vendor-specific). Windows has no built-in driver for this device, so it appears "driverless." Currently users must manually run Zadig to bind libusb-win32, and manually roll it back via Device Manager afterward.

## Goal

Automate the driver swap (and swap-back) within the app so users never need to touch Zadig. When `054C:0336` appears driverless after a service mode switch, the app installs libusb-win32 automatically, and restores the original state when the service shell exits.

---

## Architecture

### Overview

```
pmca-console.py serviceshell
    │
    ├── Camera found in MSC mode (native driver)
    ├── Sends service-mode switch command via libusb
    ├── Camera disconnects, re-enumerates as 054C:0336
    │
    ├── _waitForDevice() detects driverless 054C:0336
    │   ├── Prompts user: "Driver change required. Allow? [Y/n]"
    │   ├── Calls wdi-helper.exe install (UAC elevation prompt)
    │   ├── libusb-win32 bound to 054C:0336
    │   └── Device now openable via libusb
    │
    ├── Service shell runs normally
    │
    └── Shell exits (finally block)
        ├── Calls wdi-helper.exe restore (UAC elevation prompt)
        └── libusb-win32 filter removed, original state restored
```

### Components

| Component | Language | Purpose |
|-----------|----------|---------|
| `wdi-helper.exe` | C | Standalone CLI that calls libwdi to install/remove drivers |
| `pmca/usb/driver/windows/wdi.py` | Python | Wrapper that launches wdi-helper.exe with UAC elevation |
| Hook in `_waitForDevice()` | Python | Detects driverless 054C:0336 and triggers install |
| Hook in `_runSenserContinuation()` finally | Python | Triggers restore after shell exits |

---

## Component 1: `wdi-helper.exe`

A small C executable built against libwdi (source at `/Users/kpitrich/Development/camera/libwdi`). Provides two commands:

### Commands

```
wdi-helper.exe install --vid 0x054C --pid 0x0336 --name "Sony Camera Service Mode"
wdi-helper.exe restore --vid 0x054C --pid 0x0336
```

### Install Flow

1. Call `wdi_create_list()` to find the device by VID/PID
2. Record the current driver name (`wdi_device_info.driver`) to a state file at `%TEMP%\sony_pmca_driver_state.json`
3. Call `wdi_prepare_driver()` with `WDI_LIBUSB0` (libusb-win32) — extracts driver binaries and generates INF to a temp directory
4. Call `wdi_install_driver()` with `install_filter_driver = TRUE` — installs libusb-win32 as an upper filter (keeps original driver intact)
5. Print status to stdout, exit with code 0 on success

### Restore Flow

1. Call `wdi_create_list()` to find the device by VID/PID
2. Call `wdi_install_driver()` on a device that already has the libusb-win32 filter — libwdi automatically removes the filter (see `libwdi.c` line 1799: "Device already has the libusb-win32 filter => remove")
3. If filter removal fails, fall back to removing the INF from driver store via `pnputil` and calling `CM_Reenumerate_DevNode` to let Windows pick the best remaining driver
4. Delete the state file

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Device not found |
| 2 | Driver preparation failed |
| 3 | Driver installation failed (UAC denied or other) |
| 4 | Restore failed |

### Build

Build against the libwdi source tree using MSVC. The libwdi library embeds driver files (libusb0.sys, etc.) at compile time, so the resulting exe is self-contained.

```
# From the libwdi source tree
cd /path/to/libwdi
# Build libwdi.lib, then link wdi-helper.c against it
cl /I libwdi wdi-helper.c /link libwdi.lib setupapi.lib newdev.lib
```

The output `wdi-helper.exe` is bundled with the app distribution (PyInstaller `--add-data`).

### UAC Manifest

The exe should include an application manifest requesting `requireAdministrator` execution level, or the Python caller invokes it via `ShellExecuteEx` with `runas` verb.

---

## Component 2: Python Module `pmca/usb/driver/windows/wdi.py`

```python
"""Automated libusb-win32 driver management via wdi-helper.exe."""

import os
import sys
import subprocess
import ctypes

_HELPER_NAME = 'wdi-helper.exe'

def _find_helper():
    """Locate wdi-helper.exe next to the running script/frozen exe."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS  # PyInstaller bundle
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, _HELPER_NAME)
    if not os.path.isfile(path):
        return None
    return path


def _run_elevated(exe_path, args):
    """Run exe with UAC elevation via ShellExecuteEx. Returns exit code."""
    import ctypes.wintypes

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.wintypes.HANDLE),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.wintypes.HKEY),
            ("dwHotKey", ctypes.wintypes.DWORD),
            ("hIconOrMonitor", ctypes.wintypes.HANDLE),
            ("hProcess", ctypes.wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SW_HIDE = 0

    sei = SHELLEXECUTEINFO()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = exe_path
    sei.lpParameters = args
    sei.nShow = SW_HIDE

    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        return -1  # UAC denied or other failure

    ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 30000)
    exit_code = ctypes.wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(sei.hProcess)
    return exit_code.value


def install_libusb_driver(vid=0x054C, pid=0x0336, name="Sony Camera Service Mode"):
    """Install libusb-win32 filter driver for the given VID/PID.
    Returns True on success, False on failure."""
    helper = _find_helper()
    if not helper:
        return False
    args = f'install --vid 0x{vid:04X} --pid 0x{pid:04X} --name "{name}"'
    return _run_elevated(helper, args) == 0


def restore_original_driver(vid=0x054C, pid=0x0336):
    """Remove the libusb-win32 filter and restore original driver.
    Returns True on success, False on failure."""
    helper = _find_helper()
    if not helper:
        return False
    args = f'restore --vid 0x{vid:04X} --pid 0x{pid:04X}'
    return _run_elevated(helper, args) == 0
```

---

## Component 3: Hook Points in `pmca/commands/usb.py`

### Hook in `_waitForDevice()` (line ~340)

After the camera switches to service mode and re-enumerates as 054C:0336, the existing `_waitForDevice()` polls for the device. If the device appears but is driverless (detected via `driverless.VendorSpecificContext`), trigger the driver install:

```python
def _waitForDevice(driverName, expectedType, attempts, delay, continuation):
    driver_installed = False
    for i in range(attempts):
        time.sleep(delay)

        # On Windows, check if a driverless service-mode device appeared
        if sys.platform == 'win32' and not driver_installed:
            from ..usb.driver.windows.driverless import _listDevices as listDriverless
            driverless = [d for d in listDriverless()
                          if d.idVendor == 0x054C and d.idProduct == 0x0336]
            if driverless:
                print('Service mode device detected without a driver.')
                resp = input('Install libusb-win32 driver automatically? [Y/n] ')
                if resp.strip().lower() != 'n':
                    from ..usb.driver.windows.wdi import install_libusb_driver
                    print('Installing driver (UAC prompt may appear)...')
                    if install_libusb_driver():
                        print('Driver installed successfully.')
                        driver_installed = True
                        time.sleep(1)  # Let device settle
                    else:
                        print('Driver installation failed. Use Zadig manually.')
                else:
                    print('Skipped. Use Zadig to install libusb-win32 for 054C:0336.')

        with importDriver(driverName) as driver:
            devices = list(listDevices(driver, True))
            if len(devices) > 1:
                raise Exception(
                    'Multiple Sony devices found while waiting for camera mode change.'
                )
            if len(devices) == 1 and isinstance(devices[0], expectedType):
                continuation(devices[0])
                return True, driver_installed
        del devices
    return False, driver_installed
```

### Hook in `senserShellCommand()` (line ~836)

Pass the `driver_installed` flag through so we know to restore afterward:

```python
if switched:
    print('')
    print('Waiting for camera to switch...')
    found, driver_was_installed = _waitForDevice(
        driverName, SonySenserDevice, 10, .5,
        lambda device: _runSenserContinuation(device, modelName, complete),
    )
    if not found:
        print('Operation timed out. Please run this command again when your camera has connected.')

    # Restore driver after service shell exits
    if driver_was_installed:
        from ..usb.driver.windows.wdi import restore_original_driver
        print('Restoring original USB driver...')
        if restore_original_driver():
            print('Driver restored.')
        else:
            print('Warning: Could not restore driver automatically. '
                  'Use Device Manager to roll back if needed.')
```

### Hook in `_runSenserContinuation()` (line ~850)

Replace the Zadig error message with an attempt to install the driver on-the-fly (handles the case where the user runs the command after the camera is already in service mode):

```python
def _runSenserContinuation(device, modelName, complete):
    if not isinstance(device.driver, GenericUsbDriver):
        if sys.platform == 'win32':
            from ..usb.driver.windows.wdi import install_libusb_driver
            print('Service mode device requires libusb-win32 driver.')
            resp = input('Install automatically? [Y/n] ')
            if resp.strip().lower() != 'n':
                if install_libusb_driver():
                    print('Driver installed. Please run this command again.')
                else:
                    print('Installation failed. Use Zadig manually.')
            else:
                print('Use Zadig 2.8 to bind libusb-win32 to 054C:0336.')
        else:
            print('Error: Only libusb drivers are supported for service mode.')
        return
    # ... rest of function unchanged
```

---

## Design Decisions

### Filter Driver Mode (Preferred)

Using `install_filter_driver = TRUE` in libwdi installs libusb-win32 as an **upper filter** rather than fully replacing the device driver. Advantages:

- Original driver remains intact underneath
- Removal is trivial: calling `wdi_install_driver()` again on a device with the filter already present automatically removes it
- Lower risk of bricking the device state
- No need to track/replay the original driver INF

This only works with `WDI_LIBUSB0` (libusb-win32), which is exactly what we need.

### Pre-built Binary

`wdi-helper.exe` should be pre-built and included in releases rather than requiring users to compile from source. This matches how the project already distributes frozen executables via PyInstaller.

### Scope: Service Mode Only

This plan addresses only the service mode device (054C:0336). The initial MSC device that must use libusb to *enter* service mode is a separate concern — users can already use `-d libusb` for that, and the native MSC driver works for all other operations.

### User Consent

The app always prompts before triggering UAC elevation. The flow is:
1. Informational message explaining what's happening
2. Y/n prompt (default yes)
3. UAC system dialog (Windows-native, cannot be suppressed)

---

## File Layout

```
Sony-PMCA-RE-public/
├── pmca/
│   └── usb/
│       └── driver/
│           └── windows/
│               ├── wdi.py              (NEW - Python wrapper)
│               └── wdi-helper.exe      (NEW - pre-built binary)
├── wdi-helper/
│   ├── wdi-helper.c                    (NEW - C source)
│   ├── Makefile                        (NEW - build instructions)
│   └── wdi-helper.exe.manifest         (NEW - UAC manifest)
└── docs/
    └── AutoDriverSwap.md              (this file)
```

---

## CI Build: GitHub Actions Workflow

Since there's no Windows dev machine available, `wdi-helper.exe` is built entirely in GitHub Actions on `windows-latest`. The libwdi project itself already does this successfully (see `libwdi/.github/workflows/vs2022.yml`).

### Workflow: `.github/workflows/build-wdi-helper.yml`

```yaml
name: Build wdi-helper

on:
  push:
    paths:
      - 'wdi-helper/**'
  pull_request:
    paths:
      - 'wdi-helper/**'
  workflow_dispatch:

env:
  WDK_URL: https://go.microsoft.com/fwlink/p/?LinkID=253170
  LIBUSB0_URL: https://github.com/mcuee/libusb-win32/releases/download/release_1.4.0.0/libusb-win32-bin-1.4.0.0.zip
  LIBUSBK_URL: https://github.com/mcuee/libusbk/releases/download/V3.1.0.0/libusbK-3.1.0.0-bin.7z
  LIBWDI_REPO: https://github.com/pbatard/libwdi.git

jobs:
  build:
    runs-on: windows-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Checkout libwdi
        run: git clone --depth 1 ${{ env.LIBWDI_REPO }} libwdi-src

      - name: Download driver support files
        shell: cmd
        run: |
          curl -L %WDK_URL% -o wdk-redist.msi
          curl -L %LIBUSB0_URL% -o libusb0-redist.zip
          curl -L %LIBUSBK_URL% -o libusbk-redist.7z
          msiexec /a wdk-redist.msi /qn TARGETDIR=%CD%\libwdi-src\wdk
          7z x libusb0-redist.zip
          7z x libusbk-redist.7z
          move libusb-win32* libwdi-src\libusb0
          move libusbK* libwdi-src\libusbk

      - name: Add MSBuild to PATH
        uses: microsoft/setup-msbuild@v2

      - name: Build libwdi static library
        shell: cmd
        run: |
          cd libwdi-src
          set BUILD_MACROS="WDK_DIR=\"../wdk/Windows Kits/8.0\";LIBUSB0_DIR=\"../libusb0\";LIBUSBK_DIR=\"../libusbk/bin\""
          msbuild libwdi.sln /m /t:libwdi_static /p:Configuration=Release,Platform=x64,BuildMacros=%BUILD_MACROS%

      - name: Build wdi-helper.exe
        shell: cmd
        run: |
          cd wdi-helper
          cl /O2 /MT /I ..\libwdi-src\libwdi ^
            wdi-helper.c ^
            /link /OUT:wdi-helper.exe ^
            ..\libwdi-src\x64\Release\libwdi_static.lib ^
            setupapi.lib newdev.lib ntdll.lib ole32.lib ^
            /MANIFESTUAC:"level='requireAdministrator'" ^
            /SUBSYSTEM:CONSOLE

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: wdi-helper
          path: wdi-helper/wdi-helper.exe

      - name: Attach to release
        if: startsWith(github.ref, 'refs/tags/')
        run: |
          gh release upload ${{ github.ref_name }} wdi-helper/wdi-helper.exe
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### How It Works

1. **Checks out libwdi source** — cloned fresh from pbatard/libwdi (or pinned to a tag)
2. **Downloads embedded driver files** — the same WDK, libusb-win32, and libusbK binaries that libwdi's own CI uses. These get embedded into `libwdi_static.lib` at build time so the final exe is self-contained.
3. **Builds `libwdi_static.lib`** via MSBuild (the same solution file libwdi's own CI uses)
4. **Compiles `wdi-helper.c`** against the static lib with MSVC `cl.exe`, linking in the UAC manifest directly via linker flag
5. **Uploads the artifact** — downloadable from the Actions run, and attached to GitHub Releases on tag push

### Integration with Main Build

The existing `build.yml` (which produces PyInstaller bundles) can be extended to download `wdi-helper.exe` from the latest artifact:

```yaml
    - name: Download wdi-helper (Windows)
      if: runner.os == 'Windows'
      uses: actions/download-artifact@v4
      with:
        name: wdi-helper
        path: .
        # Or use gh CLI to grab from latest release:
        # gh release download --pattern 'wdi-helper.exe' --dir .

    - name: Build
      run: |
        python -OO -m PyInstaller pmca-console.spec
```

The PyInstaller spec would include `wdi-helper.exe` as additional data:

```python
# In pmca-console.spec, add to Analysis datas:
datas=[('wdi-helper.exe', '.')]
```

### Development Workflow (Without a Windows Machine)

1. Write/edit `wdi-helper/wdi-helper.c` on macOS
2. Push to a branch — CI builds automatically
3. Download the artifact from the Actions tab to test
4. For testing, use a Windows VM (e.g., GitHub Codespaces with Windows, or a free-tier Azure VM) or ask a collaborator to run the exe

### Alternative: MinGW Cross-Compile

If you'd prefer to avoid MSVC entirely, libwdi also supports MinGW builds (see `libwdi/.github/workflows/mingw.yml`). This uses MSYS2 on the GitHub runner:

```yaml
    - name: Install MinGW
      uses: msys2/setup-msys2@v2
      with:
        msystem: mingw64
        install: mingw-w64-x86_64-toolchain base-devel autotools

    - name: Build libwdi + wdi-helper
      shell: msys2 {0}
      run: |
        cd libwdi-src
        ./bootstrap.sh
        ./configure --disable-shared --enable-static \
          --with-wdkdir="wdk/Windows Kits/8.0" \
          --with-libusb0="libusb0" \
          --with-libusbk="libusbk/bin"
        make
        cd ../wdi-helper
        gcc -O2 -I ../libwdi-src/libwdi wdi-helper.c \
          -L ../libwdi-src/libwdi/.libs -lwdi \
          -lsetupapi -lnewdev -lole32 \
          -o wdi-helper.exe
```

The MSVC route is recommended (matches libwdi's primary build, produces smaller binaries, native UAC manifest embedding).

---

## Future Enhancements

1. **Swap MSC driver too**: Automate the initial `-d libusb` requirement so the full service-mode flow works without any manual driver setup at all.
2. **Silent mode**: Skip the Y/n prompt with a `--auto-driver` CLI flag for scripted/automated use.
3. **GUI integration**: Show a dialog in `pmca-gui.py` with a progress indicator during driver installation.
4. **PID 0x02A9 support**: Some cameras use PID 0x02A9 for service mode instead of 0x0336. Handle both.
