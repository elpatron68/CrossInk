---
title: Getting Started
parent: Development
nav_order: 1
---

# Getting Started

This guide helps you build and run CrossInk locally.

## Prerequisites

- **pioarduino PlatformIO Core 6.1.19** (`pio`) — not stock PlatformIO from PyPI
- Python 3.8+
- `clang-format` 21+ in your `PATH` (CI uses clang-format 21)
- USB-C cable
- Xteink X4, X3, or X4 Pro for hardware testing

### Why pioarduino 6.1.19

CrossInk builds against the pioarduino Espressif32 platform. CI installs
exactly this core:

```sh
pip install -U https://github.com/pioarduino/platformio-core/archive/refs/tags/v6.1.19.zip
```

Stock PlatformIO 6.2.x from PyPI can fail during the SCons/tool install step
(for example `ModuleNotFoundError: SCons.Tool.FortranCommon`) or fight over
`tool-scons` package versions. Prefer the same pin as
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).

### Local venv setup (Debian/Ubuntu/WSL)

Modern Debian/Ubuntu block system-wide `pip install` (PEP 668). A repo-local
venv keeps the toolchain self-contained:

```sh
python3 -m venv .pio-venv
.pio-venv/bin/pip install -U \
  https://github.com/pioarduino/platformio-core/archive/refs/tags/v6.1.19.zip

# Optional: keep packages under the repo instead of ~/.platformio
source scripts/pio-env.sh
```

`scripts/pio-env.sh` puts `.pio-venv/bin` on `PATH` and sets
`PLATFORMIO_CORE_DIR` to `.platformio-core/` (gitignored). First build downloads
several GB of toolchains into that directory.

If `./bin/clang-format-fix` fails with either of these errors, install clang-format 21:

- `clang-format: No such file or directory`
- `.clang-format: error: unknown key 'AlignFunctionDeclarations'`

Examples:

```sh
# Debian/Ubuntu (try this first)
sudo apt-get update && sudo apt-get install -y clang-format-21

# If the package is unavailable, add LLVM apt repo and retry
wget https://apt.llvm.org/llvm.sh
chmod +x llvm.sh
sudo ./llvm.sh 21
sudo apt-get update
sudo apt-get install -y clang-format-21

# macOS (Homebrew)
brew install clang-format
```

Then verify:

```sh
clang-format-21 --version
```

The reported major version must be 21 or newer.

## Clone and initialize

```sh
git clone --recursive https://github.com/uxjulia/CrossInk
cd CrossInk
```

If you already cloned without submodules:

```sh
git submodule update --init --recursive
```

## Build

```sh
# If you used the local venv above:
source scripts/pio-env.sh

pio run -e simulator
pio run -e default    # X3 / X4 (ESP32-C3)
pio run -e x4-pro     # X4 Pro (ESP32-S3)
```

Successful builds produce renamed images such as
`.pio/build/default/firmware-x3-x4.bin` and
`.pio/build/x4-pro/firmware-x4-pro.bin`.

`pio run` without an environment builds the X3/X4 and Sticky firmware targets listed in `platformio.ini`.

## Flash

```sh
pio run -e default --target upload
pio run -e x4-pro --target upload
```

On WSL2, the serial device is often only visible after attaching USB with
`usbipd` (Windows) so `/dev/ttyACM*` or `/dev/ttyUSB*` appears in Linux.

## Validation

```sh
./bin/clang-format-fix
pio check --fail-on-defect low --fail-on-defect medium --fail-on-defect high
pio run
```

## What to read next

- [Architecture Overview](./architecture.md)
- [Testing and Debugging](./testing-debugging.md)
