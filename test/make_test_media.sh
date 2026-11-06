#!/bin/bash
# テスト素材 talk.mp4 を talk.txt から作る（macOS の読み上げ音声 Kyoko ＋ ffmpeg のテスト映像）。
# talk.mp4 は約40MBあるので git に入れず、必要なときにこのスクリプトで作る。
set -euo pipefail
cd "$(dirname "$0")"
say -v Kyoko -r 190 -f talk.txt -o talk.aiff
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 talk.aiff)
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc2=size=1920x1080:rate=30000/1001:duration=$D" -i talk.aiff \
  -c:v libx264 -pix_fmt yuv420p -crf 23 -c:a aac -b:a 192k -ar 48000 -shortest talk.mp4
echo "作成: test/talk.mp4（${D}秒）"
