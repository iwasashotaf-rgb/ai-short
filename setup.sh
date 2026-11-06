#!/bin/bash
# 導入スクリプト: このフォルダに Python の環境（.venv）を作り、必要なパッケージを入れる。
# 前提: Apple Silicon の Mac、Homebrew の python@3.11 と ffmpeg。
#   brew install python@3.11 ffmpeg
set -euo pipefail
cd "$(dirname "$0")"

PY=/opt/homebrew/bin/python3.11
[ -x "$PY" ] || { echo "python3.11 がありません: brew install python@3.11"; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg がありません: brew install ffmpeg"; exit 1; }

if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -c "import mlx_whisper, PIL, pykakasi; print('導入完了')"

if [ -z "${FISH_API_KEY:-}" ] && ! zsh -c 'source ~/.zshrc >/dev/null 2>&1; [ -n "$FISH_API_KEY" ]'; then
  echo "注意: FISH_API_KEY が未設定です（ナレーション生成に必要）。~/.zshrc に export してください"
fi
