# wdi-helper

Small Windows CLI that installs or removes the **libusb-win32** upper filter for
Sony service-mode USB devices (`054C:0336` / `054C:02A9`) using
[libwdi](https://github.com/pbatard/libwdi).

There is no local Windows/MSVC requirement: the binary is built by GitHub Actions.

## Commands

```text
wdi-helper.exe install --vid 0x054C --pid 0x0336 --name "Sony Camera Service Mode"
wdi-helper.exe restore --vid 0x054C --pid 0x0336
wdi-helper.exe --help
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Device not found |
| 2 | Driver preparation failed |
| 3 | Driver installation failed |
| 4 | Restore failed |
| 5 | Bad arguments |

## Building (CI)

Workflow: [`.github/workflows/build-wdi-helper.yml`](../.github/workflows/build-wdi-helper.yml)

- Triggers on changes under `wdi-helper/**`, PRs, and `workflow_dispatch`
- Clones a pinned libwdi commit, embeds WDK / libusb-win32 / libusbK redistributables
- Uploads `wdi-helper.exe` as the `wdi-helper` artifact

### Download the artifact (macOS / Linux)

```bash
# After a successful Actions run:
gh run download --name wdi-helper --dir wdi-helper
```

Or open the Actions run in the GitHub UI and download the `wdi-helper` artifact.

For local Windows testing of a non-frozen checkout, place the exe at
`wdi-helper/wdi-helper.exe` or set `PMCA_WDI_HELPER` to its full path.

## Release builds

Tag builds (`.github/workflows/build.yml`) rebuild `wdi-helper.exe` and bundle it
into the Windows PyInstaller console/web executables.
