"""工程1: 素材を文字起こしして、単語の時刻と無音区間を transcript.json に書く。

使い方:
  .venv/bin/python scripts/transcribe.py 素材.mp4 作業フォルダ [--model mlx-community/whisper-large-v3-turbo]

出力:
  作業フォルダ/transcript.json  … 素材情報・単語・発話のまとまり・無音区間
  作業フォルダ/transcript.txt   … Claude が区間を選ぶときに読む一覧（行番号・開始・終了・文）
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import probe, save_json, fmt_tc  # noqa: E402

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def detect_silences(path, noise_db=-35, min_dur=0.3):
    """ffmpeg の silencedetect で無音区間を取る。カット点を無音側へ寄せるのに使う。"""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True)
    silences, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            silences.append({"start": round(start, 3), "end": round(float(m.group(1)), 3)})
            start = None
    return silences


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("workdir")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--language", default="ja")
    args = ap.parse_args()

    import mlx_whisper

    info = probe(args.src)
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)

    print(f"文字起こし中: {args.src}（モデル {args.model}）", file=sys.stderr)
    res = mlx_whisper.transcribe(
        args.src, path_or_hf_repo=args.model, language=args.language,
        word_timestamps=True, condition_on_previous_text=False, verbose=False)

    words, segments = [], []
    for si, seg in enumerate(res["segments"]):
        w0 = len(words)
        for w in seg.get("words", []):
            text = w["word"].strip()
            if not text:
                continue
            words.append({"i": len(words), "seg": si, "text": text,
                          "start": round(w["start"], 3), "end": round(w["end"], 3),
                          "prob": round(w.get("probability", 0.0), 3)})
        if len(words) == w0:
            continue
        segments.append({"i": len(segments), "text": seg["text"].strip(),
                         "start": words[w0]["start"], "end": words[-1]["end"],
                         "word_from": w0, "word_to": len(words) - 1})
    for s in segments:
        for k in range(s["word_from"], s["word_to"] + 1):
            words[k]["seg"] = s["i"]

    silences = detect_silences(args.src)
    out = {"source": info, "model": args.model, "segments": segments,
           "words": words, "silences": silences}
    save_json(out, work / "transcript.json")

    lines = [f"# 素材: {info['path']}  尺 {fmt_tc(info['duration'])}  "
             f"{info['width']}x{info['height']} {info['fps']:.3f}fps",
             "# 行番号 | 開始 - 終了 | 発話（[n] は単語番号の範囲）"]
    for s in segments:
        lines.append(f"{s['i']:3d} | {fmt_tc(s['start'])} - {fmt_tc(s['end'])} | "
                     f"{s['text']}  [w{s['word_from']}-{s['word_to']}]")
    lines.append("")
    lines.append("# 無音区間（0.3秒以上）")
    for z in silences:
        lines.append(f"    {fmt_tc(z['start'])} - {fmt_tc(z['end'])}  ({z['end'] - z['start']:.2f}s)")
    (work / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"書き出し: {work / 'transcript.json'} / transcript.txt "
          f"（発話 {len(segments)}、単語 {len(words)}、無音 {len(silences)}）", file=sys.stderr)


if __name__ == "__main__":
    main()
