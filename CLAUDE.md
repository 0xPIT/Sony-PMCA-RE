# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python 3 tool for interfacing with Sony digital cameras over USB. Capabilities: install custom Android apps (on PMCA-enabled cameras), dump firmware, tweak settings, and execute code via firmware updater or service mode shells. Upstream: `ma1co/Sony-PMCA-RE`.

## Running from Source

```bash
pip install -r requirements.txt
./pmca-console.py info              # camera info
./pmca-console.py install -i        # interactive app install
./pmca-console.py updatershell      # firmware updater debug shell
./pmca-console.py serviceshell      # service mode shell
./pmca-web.py                       # web GUI (pywebview)
```

The `-d` flag selects USB driver: `native` (OS-specific), `libusb`, or `qemu`.

On macOS, Sony's Camera Driver must be installed for mass storage mode. Close Photos/Dropbox/Google Drive before connecting.

## Building Executables

```bash
python -OO -m PyInstaller pmca-console.spec
python -OO -m PyInstaller pmca-gui.spec
```

Version is derived from `git describe --always --tags` at build time.

## Building the Updater Shell (C/C++ cross-compiled ARM)

```bash
cd updatershell
make all          # builds libupdaterbody for gen1/gen2/gen3
make pack         # packs into firmware .dat files via pack.py
make clean
```

Requires ARM cross-compiler. Uses git submodule `updatershell/platform` (OpenMemories-Platform). Targets: CXD4105, CXD4115, CXD4120, CXD4132, CXD90014. Not supported (signed firmware): CXD90045, CXD90057.

## Testing

No test suite exists. CI only verifies the built binary starts (`dist/pmca-console* -h`).

## Architecture

### `pmca/` Python Package

- **`usb/`** — USB device abstraction. `sony.py` defines Sony MTP/MSC protocol constants and vendor-specific operations. `driver/` has platform backends (libusb, macOS IOKit, Windows WPD/MSC/setupapi).
- **`platform/`** — High-level camera platform abstraction. `backend/` implements the senser (service mode) and USB updater transports. `tweaks.py` handles camera settings. `android.py` handles app operations.
- **`commands/`** — CLI command implementations (`usb.py` for direct camera commands, `market.py` for app store, `backup.py` for backup file operations).
- **`marketserver/`** — Local HTTPS server impersonating Sony's app store to sideload apps. Uses self-signed cert from `certs/`.
- **`installer/`** — App installation protocol (USB+SSL+REST communication with camera).
- **`shell/`** — Interactive shell framework with socket-based console and command parser.
- **`firmware/`** — Firmware .dat file parser (chunk-based format with FDAT sections).
- **`spk/`** — Sony's SPK package format (RSA+AES encrypted APK container).
- **`backup/`** — Parser for Backup.bin (camera settings binary format).
- **`util/`** — Binary struct helpers. The `Struct` class wraps Python's `struct` module with named tuple output; used pervasively for protocol parsing.

### Key Communication Flow

1. Camera detected via USB (MTP or mass storage class)
2. **App install**: local HTTPS market server intercepts camera's app store requests; SSL tunneled through USB
3. **Updater shell**: custom firmware .dat triggers boot into update mode, then USB bulk transfer shell
4. **Service shell**: camera switched to senser mode via MTP command, then proprietary USB bulk protocol

### USB Protocol Layers

- Mass Storage: SCSI vendor commands (opcode `0x7a`) with Sony-specific CDB format
- MTP/PTP: vendor operation codes `0x9280`–`0x9285`
- Senser mode: bulk transfers with custom packet framing
