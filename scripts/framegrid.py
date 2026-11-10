"""ショットの被写体の範囲を読むための、目盛り付きのコマを作る。

  .venv/bin/python scripts/framegrid.py 素材.MOV 開始秒 長さ秒 出力.jpg [--n 4]

ショットの区間から等間隔に n コマ（最初・途中・最後）を切り出し、10%ごとの目盛りを描いて横に並べる。
Claude はこの画像を見て、区間の中で被写体（主役の食べ物・商品）が動く範囲を、
素材の画面に対する割合 [左, 上, 右, 下]（0〜1）で読み取り、cutlist.json のショットに "subject" として書く。
build_b.py が subject から寄せの倍率と位置を計算する（framing.py）。
"""
import argparse
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"
W, H = 360, 640


def grab(path, t):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                        "-vf", f"scale={W}:{H}", "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True, check=True)
    return Image.open(BytesIO(r.stdout)).convert("RGB")


def draw_grid(im, label):
    d = ImageDraw.Draw(im, "RGBA")
    font = ImageFont.truetype(FONT, 13)
    for i in range(1, 10):
        x, y = W * i / 10, H * i / 10
        a = 160 if i == 5 else 90
        d.line([(x, 0), (x, H)], fill=(255, 255, 0, a), width=1)
        d.line([(0, y), (W, y)], fill=(255, 255, 0, a), width=1)
        d.text((x + 2, 2), f".{i}", font=font, fill=(255, 255, 0, 230))
        d.text((2, y + 1), f".{i}", font=font, fill=(255, 255, 0, 230))
    d.rectangle([0, H - 20, W, H], fill=(0, 0, 0, 180))
    d.text((4, H - 18), label, font=font, fill=(255, 255, 255))
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("start", type=float)
    ap.add_argument("dur", type=float)
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=4)
    a = ap.parse_args()
    n = max(a.n, 2)
    times = [a.start + a.dur * k / (n - 1) for k in range(n)]
    times[-1] = a.start + max(a.dur - 0.05, 0)
    tiles = [draw_grid(grab(a.src, t), f"{Path(a.src).name} {t:.2f}s") for t in times]
    sheet = Image.new("RGB", (W * n + 6 * (n - 1), H), (20, 20, 20))
    for k, im in enumerate(tiles):
        sheet.paste(im, (k * (W + 6), 0))
    sheet.save(a.out, quality=88)
    print(a.out)


if __name__ == "__main__":
    sys.exit(main())
