# Windows / Cross-Platform Analysis

This document captures the analysis of:

1. **PR [ma1co/Sony-PMCA-RE#5](https://github.com/0xPIT/Sony-PMCA-RE/pull/5)** ("Fix and validate the web GUI on Windows"), and
2. The Windows/USB connectivity **issue reports on the upstream tracker** ([ma1co/Sony-PMCA-RE/issues](https://github.com/ma1co/Sony-PMCA-RE/issues)),

to decide what changes are warranted to make the app cross-platform (macOS, Linux, Windows) and how to detect USB connectivity problems early.

---

## 1. PR #5 overview

- Title: *"Fix and validate the web GUI on Windows."*
- **Draft**, Codex-assisted PR (795+/109−, 17 files) branched from `0xPIT/Sony-PMCA-RE:master` at `bdc946bc` — i.e. **before this fork's Preact frontend refactor**.
- Validated **software-only**: no camera and no Windows USB driver were ever exercised. It fixes *GUI startup, packaging, and threading* on Windows — **not** actual camera communication.

Two structural facts shape the plan:

- The **Python/build fixes are real** and apply to our tree with light adaptation.
- The PR's `assets/app.js` / `index.html` edits target the **old imperative frontend** we replaced, so those parts must be **re-implemented in Preact** (or dropped).

---

## 2. Genuine cross-platform bugs (must-fix)

### 2.1 Broken `file://` URL on Windows (headline bug)

Current code:

```python
url = 'file://' + os.path.join(ASSETS_DIR, 'index.html')
```

On Windows this yields `file://C:\Users\…\index.html` (backslashes, no triple slash) which the WebView can't load → blank window. Fix: resolve via `pathlib.Path(...).as_uri()` → `file:///C:/Users/.../index.html`. Remains correct on mac/linux.

### 2.2 PNG passed as the Windows runtime icon crashes startup

```python
webview.start(icon=icon_path)
```

pywebview's Windows (Edge/WinForms) backend interprets `icon=` as a native `.ico` and crashes on a PNG. Fix: only pass `icon` on non-Windows; the frozen Windows exe gets its icon at packaging time.

### 2.3 Ad-hoc resource resolution (CWD / `_MEIPASS`)

`pmca/commands/usb.py` and `pmca-web.py` each re-derive paths. Centralize in a new `pmca/resources.py` (`get_bundle_resource_path`) — CWD-independent, frozen-safe, with a path-escape guard.

### 2.4 PyInstaller excludes break Windows builds

`build.spec` excludes `cffi` and `plistlib` on all platforms, but Windows/pywebview's Edge integration needs both → frozen exe fails. Fix: keep them only on non-Windows.

---

## 3. Correctness fixes (cross-platform, worst on Windows Edge WebView2)

### 3.1 `evaluate_js` lifecycle safety

`push_log`/`_notify` call `window.evaluate_js` with no readiness/shutdown gate, from many threads, and stdout capture starts *before* the window loads. WKWebView tolerates this; Edge WebView2 can throw/crash. Fix: a small `_evaluate_js()` gated on a `loaded` → `mark_ready` and `closed` → `shutdown` lifecycle, plus `json.dumps` escaping (more correct than the hand-rolled escaping for quotes/unicode/U+2028).

### 3.2 Concurrency guard — **explicitly rejected**

PR #5 adds single-camera-task admission (`_start_camera_task`, `task_rejected`) and tweak apply-once locking. **Decision: do not adopt.** For a single-user desktop tool driving one camera over one USB bus, users won't realistically start two camera operations at once, and the UI already disables per-task. This keeps the diff smaller and the code simpler. Consequently the PR's frontend edits (which existed only to serve the guard) are moot, and `assets/app.js` / `index.html` need **no changes**.

---

## 4. Windows/USB issue analysis (upstream tracker)

Real-world failure taxonomy from the issue reports and their answers:

| Failure mode | Representative issues | Root cause | What actually fixes it |
|---|---|---|---|
| GUI/console won't even launch on Windows | #374 | packaging/startup bug | §2 fixes (`file://`/icon/`cffi`) |
| Service mode needs libusb binding; **Zadig version matters** | #549, #626, #429 | driver binding | **Zadig 2.8 + libusb-win32 1.2.7.3**; Zadig 2.9 / libusb-win32 1.4.0.0 → "no tweaks available" |
| Zadig UI changed (missing "List All Devices") | #718, PR #714 | tooling drift | fork / menu workaround |
| Driver replace fails (Win11 / VM) | #513, #366 | driver install | retry / real HW / Linux VM |
| Must roll back libusb driver to use camera again | #672 | Zadig side effect | Device Manager rollback so Sony updater sees the camera |
| Camera in **MTP instead of Mass Storage** | #682 | user setup | switch camera USB mode to Mass Storage |
| Other apps holding the camera (mac analog) | #41 | exclusive access | close Photos/Image Capture/Dropbox/Google Drive → fixes `[Errno 13] Access denied` |
| No libusb backend | #401 | runtime missing | install libusb |
| "No tweaks available" | #261, #484, #542, #641, #459 | **usually NOT connectivity** — unsupported model/firmware, or connected fine but nothing to tweak | often nothing; firmware too new |

**Two key insights:**

1. **"No tweaks available" is the most common symptom and mostly a red herring for connectivity.** It appears on Linux/macOS with perfect libusb too. It means "reached service mode, but this model/firmware has no tweaks" (or firmware newer than supported). Diagnostics must not let users read it as a driver problem.
2. On Windows, **normal MTP/MSC works with the native drivers; only service mode needs a libusb binding.** A libusb-centric health check misrepresents the Windows reality — `usb.core.find()` may not even see a camera that is bound to the native MTP/disk driver, producing false "no Sony device found."

---

## 5. Verdict on the libusb-vs-Zadig wording change

**Partially warranted — adopt the severity/separation changes, but keep the concrete Zadig recipe.**

Keep from PR #5:

- **Windows libusb failures `fail` → `warn`.** MTP/MSC operations (info, app install, firmware, mass-storage backup) work without any libusb binding on Windows; marking the app "broken" is wrong.
- **Separating "libusb runtime DLL present" from "device bound to a libusb driver."** Different problems (#401 vs #549).
- **Rollback caution** (don't blindly replace the normal-mode driver; document Device Manager rollback) — addresses #672.

Do **not** adopt as-is:

- The PR **strips the one thing that actually helps end users** — the concrete, community-proven procedure — replacing it with abstract "record VID/PID/interface; no universal recommendation." The issues show there *is* a known-good recipe: **Zadig 2.8 + libusb-win32 1.2.7.3, for service mode only, then roll back.** Keep that specificity in the solution text; an internal rename (e.g. `check_zadig_driver` → `check_service_mode_libusb_binding`) is fine, but do not remove the how-to.

---

## 6. Best early-detection verification (recommended design)

Detect problems by **exercising the same driver stack the app uses**, not by libusb introspection. A capability ladder with one clear verdict per layer:

1. **Native presence & mode** — use the repo's existing native Windows backends (`pmca/usb/driver/windows/{wpd,msc,setupapi}`), i.e. the same `importDriver()`/`getDevice()` path the app uses. Report: *no Sony device* / *found in MTP mode* / *found in Mass Storage mode*. Catches the #1/#2 newbie failures (#682) with zero libusb setup.
2. **Identity & firmware preflight** — if reachable in MSC/MTP, read model + firmware (via `getCameraInfo`) and flag "firmware newer than last known-good for this model." Preempts the "no tweaks available" confusion class (#261/#484/#542…).
3. **libusb runtime** — DLL findable / PyUSB backend loads. `warn` on Windows (needed only for service mode), `fail` on mac/linux. (PR is right here.)
4. **Service-mode binding** — only relevant for tweaks/backup in service mode: try to open VID `0x054c` via libusb; `Entity not found`/`Access denied` → "not bound to a libusb driver," with the concrete Zadig recipe and rollback note.
5. **Interfering software** — highest-value cross-platform check (maps to #41): macOS detect Photos/Image Capture/Dropbox/Google Drive; Windows detect WPD/Explorer/WMP holding the device. Surface it *before* the user hits `[Errno 13]`.

**Unifying principle:** verify with the app's real driver selection, and label each USB path by what it needs — "MTP/MSC: native Windows drivers, works out of the box" vs "service mode: needs a libusb binding (Zadig)."

---

## 7. Caveats

- **No hardware validation on any OS.** This makes the *GUI + build* cross-platform; Windows camera MTP/MSC/service-mode comms remain unproven. State this plainly in README/changelog.
- **Windows exe icon:** stopping the runtime PNG is correct, but no `.ico` is set in the PyInstaller `EXE(...)`, so Windows builds get a default icon. Optional follow-up: ship `icon.ico` and set it in `build.spec`.
- **libusb on Windows:** service mode needs `libusb-1.0.dll` present (bundled or on `PATH`). Diagnostics detect its absence, but `build.spec` doesn't bundle it — the Windows release workflow must provide it.
