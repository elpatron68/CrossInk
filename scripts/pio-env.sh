#!/usr/bin/env bash
# Local PlatformIO environment for this checkout (matches CI: pioarduino 6.1.19).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$ROOT/.pio-venv/bin:$PATH"
export PLATFORMIO_CORE_DIR="$ROOT/.platformio-core"
export PLATFORMIO_SETTING_ENABLE_TELEMETRY=No
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy 2>/dev/null || true
echo "pio: $(command -v pio) ($(pio --version 2>/dev/null | tr '\n' ' '))"
echo "CORE: $PLATFORMIO_CORE_DIR"
