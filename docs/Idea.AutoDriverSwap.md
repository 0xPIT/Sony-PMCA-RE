# Automated libusb-win32 Driver Swap for Service Mode

## Problem

When the camera enters service mode, it re-enumerates as USB device `054C:0336` or
`054C:02A9` ("Sony USB Device", class vendor-specific). Windows has no built-in
driver for this device, so it appears driverless. Previously users had to run Zadig
manually to bind libusb-win32, then roll it back via Device Manager afterward.

## Goal

Automate the driver swap (and swap-back) within the app. When a service-mode device
appears driverless, the app can install libusb-win32 automatically (with user consent
and a UAC prompt), and restore the original state when the service shell exits.

**Out of scope:** binding libusb on the MSC device used to *enter* service mode.
That still may require `-d libusb` / Zadig on the mass-storage interface.

---

## Architecture

```
pmca-console.py serviceshell
    │
    ├── Camera found in MSC mode (native or libusb)
    ├── Sends service-mode switch command via libusb
    ├── Camera disconnects, re-enumerates as 054C:0336 / 02A9
    │
    ├── _waitForDevice() detects driverless service-mode device
    │   ├── Prompts user: "Install libusb-win32 driver automatically? [Y/n]"
    │   ├── Calls wdi-helper.exe install (UAC via ShellExecuteEx runas)
    │   └── Device openable via libusb
    │
    ├── Service shell runs
    │
    └── finally
        └── Calls wdi-helper.exe restore (UAC)
```

### Components

| Component | Language | Purpose |
|-----------|----------|---------|
| `wdi-helper.exe` | C | CLI that calls libwdi to install/remove the filter |
| `pmca/usb/driver/windows/wdi.py` | Python | Locates helper, elevates via `runas` |
| Hooks in `pmca/commands/usb.py` | Python | Detect driverless device, prompt, restore |

---

## Build host: GitHub Actions only

There is **no** local Windows/MSVC requirement. Source lives in the repo; the binary
is produced by CI and bundled into Windows release builds.

### Do not commit the binary

`wdi-helper.exe` is gitignored / never checked into `pmca/`. Download the Actions
artifact for ad-hoc Windows testing:

```bash
gh run download --name wdi-helper --dir wdi-helper
# or set PMCA_WDI_HELPER=/path/to/wdi-helper.exe
```

### Workflow: `.github/workflows/build-wdi-helper.yml`

Triggers on `wdi-helper/**` changes, PRs, `workflow_dispatch`, and as a reusable
workflow (`workflow_call`) from the release build.

Steps (mirrors [libwdi `vs2022.yml`](https://github.com/pbatard/libwdi/blob/master/.github/workflows/vs2022.yml)):

1. Checkout this repo + libwdi at pinned SHA `30df0c0e051b0132c4b9ebed8c054bc8eb3aaaec`
2. Download WDK redistributable, libusb-win32, libusbK into the libwdi tree
3. `microsoft/setup-msbuild` + `ilammy/msvc-dev-cmd` (x64) so `cl.exe` is on PATH
4. MSBuild `libwdi_static.vcxproj` → `x64/Release/lib/libwdi.lib`
5. Compile/link `wdi-helper.c` with embedded `asInvoker` manifest
6. Smoke: `wdi-helper.exe --help`
7. Upload artifact `wdi-helper`

### Release integration: `.github/workflows/build.yml`

```text
jobs:
  wdi-helper:  uses: ./build-wdi-helper.yml
  build:       needs: wdi-helper
               (Windows leg downloads artifact into wdi-helper/)
```

[`build.spec`](../build.spec) bundles `wdi-helper/wdi-helper.exe` into the frozen
app as `wdi-helper.exe` under `sys._MEIPASS` when present.

---

## `wdi-helper` CLI

```text
wdi-helper.exe install --vid 0x054C --pid 0x0336 --name "Sony Camera Service Mode"
wdi-helper.exe restore --vid 0x054C --pid 0x0336
wdi-helper.exe --help
```

| Exit | Meaning |
|------|---------|
| 0 | Success |
| 1 | Device not found |
| 2 | Driver preparation failed |
| 3 | Driver installation failed |
| 4 | Restore failed |
| 5 | Bad arguments |

Install uses `WDI_LIBUSB0` with `install_filter_driver = TRUE` (upper filter; original
driver remains underneath). Restore calls the same API again; libwdi removes an
existing filter. Prior driver name is recorded in
`%TEMP%\sony_pmca_driver_state.json`.

UAC: the helper itself is `asInvoker`; Python elevates with `ShellExecuteEx` + `runas`.

---

## Python: `pmca/usb/driver/windows/wdi.py`

Helper search order:

1. `PMCA_WDI_HELPER` env
2. `sys._MEIPASS` / directory of frozen executable
3. Next to `wdi.py`
4. Repo `wdi-helper/wdi-helper.exe` (CI artifact drop-in)

API: `helper_available()`, `install_libusb_driver(vid, pid)`,
`restore_original_driver(vid, pid)`, `pending_restore_pid()`.

---

## Hooks in `pmca/commands/usb.py`

- `_waitForDevice(..., autoInstallServiceDriver=True)` — during service-mode wait,
  detect driverless `054C` + senser PID, prompt, install.
- `_runSenserContinuation` — if still not `GenericUsbDriver`, offer install and ask
  the user to re-run; otherwise run the shell and **always** restore in `finally`
  when this session (or a prior install via the state file) installed the filter.
- Zadig messaging remains as fallback when the helper is missing or the user declines.

---

## File layout

```text
wdi-helper/
  wdi-helper.c
  wdi-helper.exe.manifest
  README.md
pmca/usb/driver/windows/
  wdi.py
.github/workflows/
  build-wdi-helper.yml
  build.yml          # needs wdi-helper on tag builds
```

---

## Design notes

- **Filter driver preferred** over full driver replace (lower risk, easy remove).
- **Both PIDs** `0x0336` and `0x02A9` supported.
- **User consent** before every UAC elevation.
- Dev loop without Windows: edit C on macOS → push → download Actions artifact →
  test on a Windows VM / collaborator machine.

## Future enhancements

1. Automate MSC→libusb binding for entering service mode.
2. `--auto-driver` CLI flag to skip the Y/n prompt.
3. GUI dialog during install.
