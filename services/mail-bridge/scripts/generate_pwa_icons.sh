#!/usr/bin/env bash
# Regenerate PWA PNGs from app/static/favicon.svg (requires ImageMagick `convert`).
set -euo pipefail
cd "$(dirname "$0")/../app/static"
mkdir -p icons
convert -background none favicon.svg -resize 192x192 -depth 8 PNG32:icons/icon-192.png
convert -background none favicon.svg -resize 512x512 -depth 8 PNG32:icons/icon-512.png
convert -background none favicon.svg -resize 180x180 -depth 8 PNG32:icons/apple-touch-icon.png
convert -size 512x512 xc:'#2c5f4a' \
  \( favicon.svg -resize 410x410 \) -gravity center -compose over -composite \
  -depth 8 PNG32:icons/icon-512-maskable.png
echo "Wrote icons/icon-192.png icons/icon-512.png icons/icon-512-maskable.png icons/apple-touch-icon.png"
