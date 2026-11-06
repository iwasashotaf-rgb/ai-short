"""自己検品: 書き出したプレビューを Claude が見て採点するための材料を作る。

使い方:
  .venv/bin/python scripts/review.py projects/案件名/01 [--narration narration/narration.json]

出力（同じフォルダ）:
  review.jpg   各カットの中ほどのコマを時刻付きで並べた画像（テロップ込み）
  review.json  数値: 尺・カット数・カットの長さ・テロップの枚数と文字数・話す速さ・声の間
Claude は review.jpg と review.json を見て、編集の手引き（guides/）の採点表で採点し、直す点を cutlist.json と
script.json に戻してから書き出し直す。
"""
import argparse
import json
import statistics
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json  # noqa: E402
from narrate import normalize, reading  # noqa: E402

FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"


def grab(video, t, w=240):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={w}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                       capture_output=True)
    return Image.open(BytesIO(r.stdout)).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--narration", default="narration/narration.json")
    args = ap.parse_args()
    out = Path(args.outdir)
    tl = load_json(out / "timeline.json")
    fps = tl["fps"]
    clips = tl["clips"]
    lens = [(c["rec_out"] - c["rec_in"]) / fps for c in clips]
    caps = tl["captions"]
    cap_lines = [ln for c in caps for ln in c["text"].split("\n")]

    stats = {
        "尺_秒": round(tl["total_sec"], 2),
        "カット数": len(clips),
        "カットの長さ_平均": round(statistics.mean(lens), 2),
        "カットの長さ_最短": round(min(lens), 2),
        "カットの長さ_最長": round(max(lens), 2),
        "テロップ枚数": len(caps),
        "テロップ1行の文字数_最大": max((len(x) for x in cap_lines), default=0),
        "テロップ1行の文字数_平均": round(statistics.mean(len(x) for x in cap_lines), 1) if cap_lines else 0,
        "テロップ1枚の行数_最大": max((len(c["text"].split("\n")) for c in caps), default=0),
    }
    narr_path = out / args.narration
    if narr_path.exists():
        narr = load_json(narr_path)
        ls = narr["lines"]
        speech = sum(ln["end"] - ln["start"] for ln in ls)
        mora = sum(len(reading(normalize(ln.get("yomi") or ln["text"]))) for ln in ls)
        gaps = [b["start"] - a["end"] for a, b in zip(ls, ls[1:])]
        stats.update({
            "声の開始_秒": round(ls[0]["start"], 2),
            "話す速さ_かな文字毎秒": round(mora / speech, 1) if speech else 0,
            "文の間_平均": round(statistics.mean(gaps), 2) if gaps else 0,
            "文の間_最長": round(max(gaps), 2) if gaps else 0,
            "声のない時間の割合": round(1 - speech / tl["total_sec"], 2),
        })

    # 各カットの中ほどのコマ
    font = ImageFont.truetype(FONT, 15)
    tiles = []
    for i, c in enumerate(clips):
        t = (c["rec_in"] + c["rec_out"]) / 2 / fps
        im = grab(out / "preview.mp4", t)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, im.width, 22], fill=(0, 0, 0))
        d.text((4, 2), f"#{i + 1} {c['rec_in'] / fps:.1f}-{c['rec_out'] / fps:.1f}s {c['text'][:16]}",
               font=font, fill=(255, 220, 0))
        tiles.append(im)
    cols = min(6, len(tiles))
    w, h = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), (20, 20, 20))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    sheet.save(out / "review.jpg", quality=85)
    json.dump(stats, open(out / "review.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  一覧画像: {out / 'review.jpg'}")


if __name__ == "__main__":
    main()
