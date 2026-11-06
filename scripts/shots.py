"""素材の整理: 素材フォルダの動画ごとに、一定間隔の静止画を時刻付きで並べた一覧画像を作る。

Claude はこの一覧画像を見て、各動画の「何秒から何秒に何が映っているか・寄りか引きか・使えるか」を
shots.json の segments に書く。カット選定はその記録を見て行う。

使い方:
  .venv/bin/python scripts/shots.py 素材フォルダ projects/案件名/shots [--every 2.0] [--max 16]

出力:
  shots/sheets/<動画名>.jpg  一覧画像（4列、各コマに経過秒）
  shots/shots.json          動画の一覧（長さ・解像度・一覧画像の場所）。segments は空で作り、Claude が埋める。
                            既に shots.json があれば、書き込み済みの segments は消さずに残す
"""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from common import probe  # noqa: E402

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mts"}
THUMB_W = 360
COLS = 4
FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"


def sample_times(dur, every, max_n):
    n = max(4, min(max_n, int(dur // every) + 1))
    step = dur / n
    return [round(step * (i + 0.5), 2) for i in range(n)]


def grab(path, t):
    """t 秒のコマを縮小して取り出す（入力側で -ss するので 4K でも速い）。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t}", "-i", str(path),
                        "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2", "-f", "image2pipe",
                        "-vcodec", "mjpeg", "-"], capture_output=True)
    from io import BytesIO
    return Image.open(BytesIO(r.stdout)).convert("RGB") if r.stdout else None


def sheet(path, times, out):
    frames = [grab(path, t) for t in times]
    frames = [(t, f) for t, f in zip(times, frames) if f is not None]
    if not frames:
        return False
    w, h = frames[0][1].size
    rows = (len(frames) + COLS - 1) // COLS
    head = 34
    img = Image.new("RGB", (COLS * w, head + rows * h), (20, 20, 20))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 18)
    d.text((8, 7), path.name, font=font, fill=(255, 255, 255))
    for i, (t, f) in enumerate(frames):
        x, y = (i % COLS) * w, head + (i // COLS) * h
        img.paste(f, (x, y))
        d.rectangle([x, y, x + 64, y + 24], fill=(0, 0, 0))
        d.text((x + 5, y + 2), f"{t:.1f}s", font=font, fill=(255, 220, 0))
    img.save(out, quality=82)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--max", type=int, default=16)
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    vids = sorted(p for p in src.rglob("*") if p.suffix.lower() in VIDEO_EXT and not p.name.startswith("."))
    old = {}
    if (out / "shots.json").exists():
        old = {c["clip"]: c for c in json.load(open(out / "shots.json", encoding="utf-8"))["clips"]}

    def work(p):
        info = probe(p)
        times = sample_times(info["duration"], args.every, args.max)
        sp = out / "sheets" / f"{p.stem}.jpg"
        ok = sheet(p, times, sp)
        return {"clip": p.name, "path": str(p.resolve()), "duration": round(info["duration"], 2),
                "width": info["width"], "height": info["height"], "fps": round(info["fps"], 3),
                "has_audio": info["audio_channels"] > 0, "sheet": str(sp.resolve()) if ok else None,
                "sampled": times, "segments": old.get(p.name, {}).get("segments", [])}

    with ThreadPoolExecutor(max_workers=4) as ex:
        clips = list(ex.map(work, vids))
    json.dump({"source_dir": str(src.resolve()), "clips": clips},
              open(out / "shots.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    total = sum(c["duration"] for c in clips)
    print(f"{len(clips)}本・合計{total / 60:.1f}分 → {out / 'sheets'}")


if __name__ == "__main__":
    main()
