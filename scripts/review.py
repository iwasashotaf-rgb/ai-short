"""自己検品: 書き出したプレビューを Claude が見て採点するための材料を作る。

使い方:
  .venv/bin/python scripts/review.py projects/案件名/01 [--narration narration/narration.json]

出力（同じフォルダ）:
  review.jpg   各カットの中ほどのコマを時刻付きで並べた画像（テロップ込み）
  review.json  数値: 尺・カット数・カットの長さ・テロップの枚数と文字数・話す速さ・声の間
  check.json   機械の検品（最後に check.py を自動で走らせる）。採点表の数値で判定できる項目の点と「必ず直す」
作る役は check.py の「必ず直す」が0件になるまで直してから、採点役に渡す（CLAUDE.md の B 型の章）。
"""
import argparse
import json
import re
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


def measure_speech(video, lines, noise_db, min_sec=0.05):
    """プレビューの音声を silencedetect で測り、文の間・文の中の間・文ごとの話す速さ（かな文字毎秒）を返す。
    文の声の区間は、narration.json の文の開始・終了に重なる無音を除いた範囲。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-af",
                        f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-"],
                       capture_output=True, text=True)
    sil = list(zip([float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)],
                   [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]))
    spans, inner, rates, inner_by_id = [], [], {}, {}
    for ln in lines:
        s, e = ln["start"], ln["end"]
        for a, b in sil:
            if a <= s + 0.06 and b > s:
                s = b
            if a < e and b >= e - 0.06 and a > s:
                e = a
        spans.append((s, e))
        here = [b - a for a, b in sil if s < a and b < e]
        inner += here
        inner_by_id[ln["id"]] = max(here, default=0)
        k = len(reading(normalize(ln.get("yomi") or ln["text"])))
        rates[ln["id"]] = round(k / (e - s), 2) if e > s else 0
    gaps = [max(b[0] - a[1], 0) for a, b in zip(spans, spans[1:])]
    return {"gaps": gaps, "inner": inner, "rates": rates, "inner_by_id": inner_by_id}


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
            "文の間_設定値_平均": round(statistics.mean(gaps), 2) if gaps else 0,
            "文の間_設定値_最長": round(max(gaps), 2) if gaps else 0,
            "声のない時間の割合": round(1 - speech / tl["total_sec"], 2),
        })
        # 設定値の文の間には、生成音声の頭と尾に残した無音が入らない。プレビューの音声を実測する
        for db in (-40, -35):
            m = measure_speech(out / "preview.mp4", ls, db)
            stats[f"文の間_実測_最長_{db}dB"] = round(max(m["gaps"]), 3) if m["gaps"] else 0
            stats[f"文の中の間_実測_最長_{db}dB"] = round(max(m["inner"]), 3) if m["inner"] else 0
            stats[f"話す速さ_文ごと_実測_{db}dB"] = m["rates"]

    # 各カットの手ブレ量（素材の使った区間を測る）。SHAKE_NG を超えるカットは採用しない
    from shake import measure as shake_measure, label as shake_label, SHAKE_NG
    srcs = tl.get("sources") or [tl["source"]]
    shakes = []
    for c in clips:
        src = srcs[c.get("src", 0)]  # src_in / src_out はタイムラインの fps のコマ数（build_b.py）
        shakes.append(shake_measure(src["path"], c["src_in"] / fps, (c["src_out"] - c["src_in"]) / fps))
    stats["手ブレ_カットごと_%"] = {f"#{i + 1}": v for i, v in enumerate(shakes)}
    stats["手ブレ_最大_%"] = max(shakes) if shakes else 0
    stats["手ブレ_採用しない基準_%"] = SHAKE_NG
    stats["手ブレ_基準超えのカット"] = [f"#{i + 1}" for i, v in enumerate(shakes) if v > SHAKE_NG]

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
        if shakes[i] > SHAKE_NG:  # 手ブレが基準を超えるカットは赤枠で示す
            d.rectangle([0, 0, im.width - 1, im.height - 1], outline=(255, 40, 40), width=6)
            d.text((4, 24), f"手ブレ {shakes[i]}%", font=font, fill=(255, 60, 60))
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
    # 続けて機械の検品（check.py）を走らせる。「必ず直す」があれば終了コード 1
    print(flush=True)
    r = subprocess.run([sys.executable, str(Path(__file__).parent / "check.py"), str(out)])
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
