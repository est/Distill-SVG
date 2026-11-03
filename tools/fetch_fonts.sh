#!/usr/bin/env bash
# Download open-source Noto Sans SC weights for font matching / output.
# Fonts land in assets/fonts/ (gitignored).  Noto Sans SC is SIL OFL 1.1.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$root/assets/fonts"
mkdir -p "$dest"
for weight in 400 500 600 700; do
  url="$(curl -fsSL -A 'Mozilla/5.0' \
    "https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@${weight}" \
    | grep -o 'https://[^)]*' | head -1)"
  if [ -z "$url" ]; then
    echo "could not resolve Noto Sans SC ${weight}" >&2
    exit 1
  fi
  echo "downloading NotoSansSC-${weight}.ttf"
  curl -fsSL "$url" -o "$dest/NotoSansSC-${weight}.ttf"
done
echo "fonts written to $dest"
