#!/usr/bin/env bash
# Re-render the README diagrams: docs/diagrams/*.mmd -> *.png.
#
# The Mermaid sources are the editable originals; the PNGs are what the
# README shows (GitHub's in-page Mermaid renderer failed on these diagrams).
# Edit a .mmd, run this, commit both.
#
# Needs Node and a Chrome/Chromium. Override the browser with
#   RTP_CHROME=/path/to/chrome ./scripts/render_diagrams.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHROME="${RTP_CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
[ -n "$CHROME" ] || { echo "No Chrome/Chromium found; set RTP_CHROME." >&2; exit 1; }

PP="$(mktemp)"; trap 'rm -f "$PP"' EXIT
printf '{"executablePath":"%s","args":["--no-sandbox"]}\n' "$CHROME" > "$PP"

for src in "$ROOT"/docs/diagrams/*.mmd; do
  echo "render $(basename "$src")"
  PUPPETEER_SKIP_DOWNLOAD=1 npx -y @mermaid-js/mermaid-cli@12 \
    -p "$PP" -i "$src" -o "${src%.mmd}.png" --size 3600 -b white -q
done
