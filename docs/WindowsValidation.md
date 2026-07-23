# Windows validation report

Validated on 2026-07-23 from maintainer base commit `bdc946bc691fe132046d7f342b6a0f6b5693c525`.

The clean code-validation commit is `24cb7bebc95ab78a4e8f608a12c7de3b5be1e97c` (`v0.1.0-6-g24cb7be`). The Onefile executable described below was built from that commit. The later documentation commit only records the result and is not the executable's source commit.

No camera was connected. No USB device driver was installed, replaced, or removed, and service mode was not entered.

## Environment and provenance

| Item | Validated value |
|---|---|
| Marketing identity | Windows 11 Home |
| Edition / display version | Core / 25H2 |
| Build / UBR | 26200 / 8894 |
| Architecture | 64-bit |
| Python | 3.11.9 |
| PyInstaller | 6.21.0 |
| pywebview | 6.2.1 |
| Validation checkout | `E:\AGENTS\codex\_validation\sony-pmca-re-windows-clean` |
| Build command | `.venv\Scripts\python.exe -OO -m PyInstaller --clean --noconfirm pmca-web.spec` |
| Executable | `dist\pmca-web-v0.1.0-6-g24cb7be-win.exe` |
| Executable size | 18,511,380 bytes |
| Executable SHA-256 | `42B4CD572EF3AEA4B4F80C99C99C1D008060E870C908841A53E3D1A2A83D8E7C` |
| Displayed application title/version | `PMCA Camera Utility v0.1.0-6-g24cb7be` |
| libusb DLL | official libusb 1.0.27 Windows MS64 DLL, file/product version 1.0.27.11882 |
| libusb DLL SHA-256 | `D4E5DB4FAD8BEF7201DC5A4A71CA997E6DCCC9DA25E8A988D4BE63B13A02208D` |

The libusb archive was downloaded afresh from the same libusb 1.0.27 GitHub release URL used by the repository's Windows CI workflow. The extracted `VS2022\MS64\dll\libusb-1.0.dll` was placed on `PATH` for the build and confirmed inside the Onefile archive. This validates runtime availability, not a camera's Windows device-driver binding.

The system identity APIs disagree in a known compatibility-oriented way. `Get-ComputerInfo` reports `OsName: Microsoft Windows 11 Home`, and `Win32_OperatingSystem.Caption` reports `Microsoft Windows 11 Home`. However, `Get-ComputerInfo.WindowsProductName` and the registry `ProductName` remain `Windows 10 Home`. Python `platform.platform()` produced `Windows-10-10.0.26200-SP0`, `platform.release()` produced `10`, and PyInstaller printed the same Windows 10 compatibility label. The report therefore records the Windows 11 marketing identity separately from the compatibility API value.

The preliminary executable `dist\pmca-web-v0.1.0-win.exe` (18,510,311 bytes; SHA-256 `C80D2CEC22A25F6EBEF7AAE9CD8C5F3E421794B67E3811960C79B43E5FC98689`) was built from dirty pre-commit sources. It is not a clean release artifact or final committed-build evidence. Its hash and size differ from the clean executable, which is expected and is not a reproducibility failure. No claim of byte-for-byte reproducibility is made because two independent clean builds were not compared.

## Implementation status

| Checkpoint | Baseline | Current result | Evidence |
|---|---|---|---|
| Frozen detection | `ALREADY_IMPLEMENTED_BUT_UNVERIFIED` | `ALREADY_IMPLEMENTED_AND_VERIFIED` | Source and simulated `_MEIPASS` tests pass through `pmca.resources`. |
| Resource and asset resolution | `PARTIALLY_IMPLEMENTED` | `ALREADY_IMPLEMENTED_AND_VERIFIED` | Resolution is centralized, CWD-independent, and uses `Path.as_uri()`. |
| Web assets in PyInstaller | `ALREADY_IMPLEMENTED_BUT_UNVERIFIED` | `ALREADY_IMPLEMENTED_AND_VERIFIED` | The clean Onefile archive contains `assets/app.js`, `assets/icon.png`, `assets/index.html`, and `assets/style.css`. |
| Background execution | `PARTIALLY_IMPLEMENTED` | `ALREADY_IMPLEMENTED_AND_VERIFIED` for tested paths | At most one camera/USB operation is admitted; conflicting tasks are rejected synchronously. |
| JavaScript/Python communication | `PARTIALLY_IMPLEMENTED` | `ALREADY_IMPLEMENTED_AND_VERIFIED` for tested paths | Calls are serialized, JSON-escaped, gated on UI readiness, and dropped after shutdown. |
| Windows USB backends | `PARTIALLY_IMPLEMENTED` | `PARTIALLY_IMPLEMENTED` | Native WPD/MTP and SCSI mass-storage implementations exist; physical communication remains unverified. |

## Reproduced defects and fixes

1. Passing `icon.png` to the Windows WinForms runtime icon path crashed startup. Windows now relies on the packaged executable icon; non-Windows runtime icon behavior is preserved.
2. The previous `file://` concatenation produced an invalid Windows file URI. Startup now uses a proper `file:///...` URI.
3. Rapid actions could overlap camera operations. One shared admission state now rejects duplicate and incompatible camera tasks.
4. PyInstaller excluded `cffi`, then `plistlib`, from the Windows dependency chain. Windows now includes both while other platforms retain the previous exclusions.
5. Early and late worker output could reach WebView outside its valid lifetime. UI calls now wait for readiness, serialize, and stop after shutdown.
6. Tweak state could be applied more than once. Shared state is locked, apply is guarded, and shutdown releases a waiting session.
7. Diagnostics conflated a missing libusb runtime with a Windows device binding. They now report these separately, retain conditional Zadig troubleshooting for an exact USB identity, and do not prescribe one universal driver.
8. Build filenames could conceal source changes. `git describe --dirty` now marks tracked dirty builds while preserving clean tag naming.

## Automated validation

Sixteen unattended `unittest` tests passed in the fresh validation virtual environment. Four focused Windows diagnostic-message tests were added after maintainer feedback, bringing the current automated total to twenty:

- eight resource/startup tests covering source and frozen resolution, path confinement, Unicode/spaces, missing assets, valid file URIs, and platform icon behavior;
- eight Web API tests covering duplicate/incompatible task admission, JSON escaping, JavaScript serialization, UI readiness, shutdown behavior, tweak-session release, and apply-once behavior.
- four Windows diagnostic tests covering accessible devices, conditional Zadig guidance, no-device behavior, and the runtime-DLL-versus-binding distinction.

All tracked Python and spec files passed `py_compile`. `git diff --check` passed before and after the build. Diagnostics executed without a camera and returned neutral warnings for unavailable libusb/device access. JavaScript syntax validation also passed during the per-commit checks.

## Clean committed-code validation

The detached worktree was created directly at `CODE_VALIDATION_COMMIT` and did not reuse the original checkout's `.venv`, `build`, `dist`, untracked sources, or downloaded dependencies. A fresh Python 3.11 virtual environment was created and `requirements.txt` was installed from scratch.

| Scenario | Result |
|---|---|
| Source GUI from validation root | Pass |
| Source GUI by absolute script path with `%TEMP%` as CWD | Pass |
| Onefile build from clean code | Pass |
| Onefile archive web-asset inspection | Pass |
| Onefile archive libusb DLL inspection | Pass |
| Executable by absolute path with `%TEMP%` as CWD | Pass; displayed the exact clean description |
| Artificial three-second camera task | Pass; 60 heartbeat ticks, correct log order, all checked camera controls disabled and re-enabled |
| Window shutdown one second into a five-second task | Pass; process exited without hanging |
| Native Open dialog | Pass; opened and cancelled cleanly |
| Native Save dialog | Pass; default filename shown and cancelled cleanly |
| Final validation worktree status | Clean; exact commit and description unchanged |

Earlier pre-commit checks also covered a Windows shortcut, Unicode/space paths, and a read-only installation directory. Those results remain useful software-side evidence, but they are distinguished from the final fresh-worktree run above.

Bundle resources are read-only and resolve below `_MEIPASS` in Onefile mode. The GUI keeps logs in memory. Backup, APK, and firmware files are read or written only at user-selected dialog paths, so no new persistent user-data helper was introduced.

## Threading model

- pywebview owns the GUI event loop and dispatches exposed API calls on worker threads.
- App-list loading and host diagnostics may run independently because they do not open a camera.
- Camera/USB operations use daemon workers and one admission state; at most one is active.
- The tweak connection waits for apply/cancel while guarded shared state prevents a second apply.
- Window access and `evaluate_js()` are serialized. Closing marks the UI unavailable, releases a waiting tweak session, and discards late output.
- Native Open and Save dialogs remain on the existing pywebview path; the interactive smoke harnesses validate cancellation without introducing a speculative dispatcher.

## Windows USB and driver matrix

The code selects native Windows WPD for MTP and native SCSI pass-through for mass storage, with libusb fallbacks. Native vendor-specific support can enumerate a service-mode identity but does not implement communication. The matrix is intentionally documentary, not prescriptive.

| Camera state | USB identity | Starting driver | Tested driver | PMCA backend | Detection | Communication | Rollback | Result |
|---|---|---|---|---|---|---|---|---|
| Normal MTP | Pending physical observation | Native Windows MTP/WPD expected | None changed | Windows WPD | Not hardware-tested | Not hardware-tested | Not applicable | `ALREADY_IMPLEMENTED_BUT_UNVERIFIED` |
| Normal mass storage | Pending physical observation | Native disk/USB storage expected | None changed | Windows SCSI pass-through | Not hardware-tested | Not hardware-tested | Not applicable | `ALREADY_IMPLEMENTED_BUT_UNVERIFIED` |
| Sony service mode | Project recognizes VID `054c`, PID `02a9` or `0336`; interface pending | Pending observation | None changed | PyUSB/libusb vendor-specific | Not hardware-tested | Not hardware-tested | Not tested | `PARTIALLY_IMPLEMENTED` |
| Composite interface | Pending descriptors | Pending observation | None changed | Pending descriptor/backend match | Not hardware-tested | Not hardware-tested | Not tested | `NOT_APPLICABLE` until observed |

Before any future driver experiment, record the exact device name, VID, PID, interface, instance ID, physical port, descriptors, current binding, intended PMCA backend, and rollback procedure. Keep normal MTP/mass-storage and re-enumerated service identities separate. Zadig remains relevant for inspecting or installing a binding on the specifically observed identity; WinUSB, libusbK, and libusb-win32 are candidates rather than universal recommendations.

## Remaining hardware limitations

The following are explicitly unverified: physical ILCE-7M3 detection, MTP communication, mass-storage communication, service-mode enumeration, service-mode binding, PyUSB/libusb camera communication, and driver rollback. No firmware operation or camera backup was attempted.
