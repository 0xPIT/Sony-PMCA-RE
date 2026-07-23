# Cross-Platform Implementation Plan

Recommended plan to make the app run on **macOS, Linux, and Windows**, based on
[`ISSUES-ANALYSIS.md`](ISSUES-ANALYSIS.md). Work is split into small, independently
reviewable commits. Each commit builds on the previous but leaves the tree in a
working state.

**Scope decisions carried in from discussion:**

- **No concurrency guard** (no single-camera-task admission, no `task_rejected`, no tweak apply-once locking). Camera ops stay as plain daemon threads.
- **Frontend (`assets/app.js`, `index.html`) is untouched** — the PR's frontend edits only existed to serve the rejected concurrency guard.
- **Keep the concrete Zadig recipe** (Zadig 2.8 + libusb-win32 1.2.7.3, service mode only, roll back after) while adopting the PR's severity/separation improvements.
- **No hardware validation** is possible here; Windows camera comms remain unverified and this is stated in docs.

---

## Commit 1 — Add centralized bundle resource resolution

**Goal:** one CWD-independent, frozen-safe way to locate bundled read-only resources.

**Files:**
- `pmca/resources.py` (new) — `get_bundle_root()` + `get_bundle_resource_path(relative)` with a path-escape guard (adopted from PR #5).
- `pmca/commands/usb.py` — replace
  `scriptRoot = getattr(sys, '_MEIPASS', os.path.dirname(__file__) + '/../..')`
  with `scriptRoot = get_bundle_resource_path('')`.

**Rationale:** foundation for Commit 2; fixes CWD-dependent asset lookup (contributes to Windows startup reliability, #374).

**Verify:** `python -c "from pmca.resources import get_bundle_resource_path as g; print(g('assets/index.html'))"` from an unrelated CWD; existing CLI (`./pmca-console.py info`) still imports cleanly.

**Depends on:** none.

---

## Commit 2 — Fix the web GUI startup on Windows

**Goal:** the GUI launches on Windows and drives the WebView safely across platforms.

**Files:** `pmca-web.py`

**Changes:**
- Replace `_BASE_DIR` / `ASSETS_DIR` / `'file://' + os.path.join(...)` with `get_startup_page()`:
  - valid `file:///…` URL via `pathlib.Path(get_bundle_resource_path('assets/index.html')).as_uri()`;
  - self-contained "missing assets" HTML fallback page if required assets are absent.
- Add `get_webview_start_options()` so `icon=` is passed only on non-Windows (avoids the PNG-as-`.ico` crash).
- **Lifecycle safety (not a concurrency guard):**
  - `window.events.loaded += api.mark_ready`, `window.events.closed += api.shutdown`;
  - route `push_log` / `signal_error` / `_notify` through `_evaluate_js()` that no-ops before load / after close and uses `json.dumps` for escaping.
- Camera task methods keep `threading.Thread(target=task, daemon=True).start()` — unchanged.

**Rationale:** fixes the two headline Windows bugs (broken `file://`, icon crash) and prevents `evaluate_js` calls outside the WebView's valid lifetime (Edge WebView2 is stricter than WKWebView).

**Verify (macOS):** `./pmca-web.py` launches, tabs render, logs stream, window closes cleanly; temporarily rename an asset to confirm the fallback page.

**Depends on:** Commit 1.

---

## Commit 3 — Fix the PyInstaller build for Windows

**Goal:** a frozen Windows executable that includes the right dependency chain.

**Files:** `build.spec`, `pmca-web.spec`

**Changes:**
- On `win32`, remove `cffi` and `plistlib` from `excludes` (needed by pywebview's Edge backend); keep the existing exclusions on other platforms.
- `git describe --always --tags --dirty` so dirty builds are marked.
- `pmca-web.spec`: opt-in console via `console = os.environ.get('PMCA_BUILD_CONSOLE') == '1'`.

**Rationale:** without `cffi`/`plistlib` the Windows frozen exe fails to start.

**Verify:** `pyinstaller pmca-web.spec` completes on macOS (regression check); Windows build is deferred to CI.

**Depends on:** none (independent, but logically follows Commit 2).

---

## Commit 4 — Diagnostics wording: libusb runtime vs. driver binding

**Goal:** stop conflating "libusb missing" with "app broken" on Windows; keep actionable guidance.

**Files:**
- `pmca/diagnostics/__init__.py` — Windows: `check_libusb_available` / `check_sony_device_visible` become `warn` (native MTP/MSC still works); platform-specific solution text.
- `pmca/diagnostics/diagnostics_windows.py` — rename `check_zadig_driver` → `check_service_mode_libusb_binding` (label only); **keep the concrete Zadig 2.8 + libusb-win32 recipe** and add the rollback caution (#672); broaden `check_libusb_dll` search to `_MEIPASS` / exe dir / `PATH`.
- `pmca/commands/usb.py` — reword the two Windows service-mode messages: keep the Zadig recipe, add "verify VID/PID/interface and rollback; don't replace the normal-mode driver."

**Rationale:** adopts the warranted parts of PR #5's diagnostics change while retaining the community-proven fix (see analysis §5).

**Verify:** `python -c "from pmca.diagnostics import run_all_checks; [print(r) for r in run_all_checks()]"` on macOS returns sensible severities; inspect Windows strings by code review.

**Depends on:** none.

---

## Commit 5 — Early USB connectivity detection (capability ladder)

**Goal:** detect the *real* Windows/macOS connectivity problems early, using the app's actual driver stack.

**Files:** `pmca/diagnostics/__init__.py` (+ `diagnostics_windows.py` / `diagnostics_macos.py` as needed), reusing `importDriver()` / `getDevice()` / `getCameraInfo`.

**New checks (analysis §6):**
1. **Native presence & mode** — enumerate via the same native path the app uses; report *no device* / *MTP mode* / *Mass Storage mode* (actionable "switch to Mass Storage", #682).
2. **Identity & firmware preflight** — read model + firmware when reachable; flag "firmware newer than last known-good" to preempt the "no tweaks available" confusion class (#261/#484/#542).
3. **Interfering software** — macOS: Photos/Image Capture/Dropbox/Google Drive; Windows: WPD/Explorer/WMP holding the device (#41) — surfaced before `[Errno 13]`.

**Rationale:** the issue data shows most "it doesn't work" reports are mode, firmware, or interfering-app problems — not driver binding. Verifying with the real stack gives a trustworthy early verdict.

**Verify:** run diagnostics with and without a camera on macOS; confirm mode detection and interfering-app warnings; Windows behavior by code review + CI.

**Depends on:** Commit 4 (shared diagnostics structure).

---

## Commit 6 — Documentation

**Goal:** document cross-platform status honestly and keep the working Windows recipe discoverable.

**Files:**
- `README.md` — Windows section: distinguish "MTP/MSC via native drivers (works out of the box)" from "service mode (needs a libusb binding)"; keep the concrete Zadig 2.8 + libusb-win32 recipe + Device Manager rollback; note macOS interfering-apps guidance (#41).
- `ISSUES-ANALYSIS.md`, `IMPLEMENTATION-PLAN.md` — the planning docs (this analysis and plan).
- Changelog/README note: **Windows camera communication is unverified on hardware.**

**Rationale:** the analysis found the most valuable user-facing content is the concrete recipe + the native-vs-service-mode distinction; keep it front and center.

**Depends on:** none (do last so it reflects the final code).

---

## Optional / follow-up (not scheduled)

- **Windows exe icon:** ship an `icon.ico` and set `icon=` in the PyInstaller `EXE(...)` for branding.
- **Bundle `libusb-1.0.dll`** in the Windows release (or confirm the CI workflow provides it on `PATH`) so service mode works out of the box.
- **Automated tests** (PR #5's `tests/`): resource resolution, startup page, icon selection — adopt selectively once the above lands.

---

## Suggested order & grouping

Two natural review batches:

- **Batch A (startup + build):** Commits 1 → 2 → 3 — makes the GUI launch and build on Windows.
- **Batch B (diagnostics + docs):** Commits 4 → 5 → 6 — makes connectivity problems self-diagnosing and documents the result.
