#!/bin/bash
# C インタビュー型のテスト素材を作る（macOS の読み上げ音声2人分 ＋ ffmpeg のテスト映像）。
# interview.txt の各行は「Q|質問文」「A|答えの文」。Q は低い声、A は高い声で読ませる。
# 話者の見分け（speakers.py）が当たるかを確かめるため、どの行が誰かが分かる形にしてある。
# interview.mp4 と insert.mp4 は git に入れず、必要なときにこのスクリプトで作る。
set -euo pipefail
cd "$(dirname "$0")"
VQ="Grandpa (日本語（日本）)"   # 聞き手（低い声）
VA="Kyoko"                      # 答える人（高い声）
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

n=0
: > "$TMP/list.txt"
: > truth.txt
while IFS='|' read -r who text; do
  [ -z "${who:-}" ] && continue
  n=$((n + 1))
  f="$TMP/$(printf '%02d' $n).aiff"
  if [ "$who" = "Q" ]; then say -v "$VQ" -r 185 -o "$f" "$text"; else say -v "$VA" -r 195 -o "$f" "$text"; fi
  ffmpeg -hide_banner -loglevel error -y -i "$f" -af "apad=pad_dur=0.5" -ar 48000 "$TMP/$(printf '%02d' $n).wav"
  echo "file '$TMP/$(printf '%02d' $n).wav'" >> "$TMP/list.txt"
  echo "$who|$text" >> truth.txt
done < interview.txt

ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$TMP/list.txt" -c:a pcm_s16le "$TMP/all.wav"
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$TMP/all.wav")
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1920x1080:rate=30000/1001:duration=$D" -i "$TMP/all.wav" \
  -c:v libx264 -pix_fmt yuv420p -crf 23 -c:a aac -b:a 192k -ar 48000 -shortest interview.mp4
# インサート用の別素材（6秒）
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "smptebars=size=1920x1080:rate=30000/1001:duration=6" \
  -c:v libx264 -pix_fmt yuv420p -crf 23 insert.mp4
echo "作成: test/interview.mp4（${D}秒・$n 発話）/ test/insert.mp4 / test/truth.txt"
