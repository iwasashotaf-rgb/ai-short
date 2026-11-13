"""工程1: 素材を文字起こしして、単語の時刻と無音区間を transcript.json に書く。

使い方:
  .venv/bin/python scripts/transcribe.py 素材.mp4 作業フォルダ [--model mlx-community/whisper-large-v3-turbo]
  .venv/bin/python scripts/transcribe.py 素材フォルダ 作業フォルダ      # フォルダの中の動画をすべて

出力（素材1本のとき）:
  作業フォルダ/transcript.json  … 素材情報・単語・発話のまとまり・無音区間
  作業フォルダ/transcript.txt   … Claude が区間を選ぶときに読む一覧（行番号・開始・終了・文）

出力（フォルダを渡したとき。テイクが素材ごとに分かれている収録で使う）:
  作業フォルダ/transcripts/<素材名>.json  … 素材ごとに上と同じ中身
  作業フォルダ/transcript.txt             … 全素材をまとめた一覧（素材ごとに見出しを付ける）
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


def write_txt(t, path):
    """Claude が区間を選ぶときに読む一覧を書く。話者が付いていれば行の頭に出す。"""
    info = t["source"]
    lines = [f"# 素材: {info['path']}  尺 {fmt_tc(info['duration'])}  "
             f"{info['width']}x{info['height']} {info['fps']:.3f}fps",
             "# 行番号 | 開始 - 終了 | 発話（[n] は単語番号の範囲）"]
    has_spk = any(s.get("speaker") for s in t["segments"])
    if has_spk:
        lines[-1] = "# 行番号 | 話者 | 開始 - 終了 | 発話（[n] は単語番号の範囲）"
    for s in t["segments"]:
        spk = f" {s.get('speaker', '--'):>3s} |" if has_spk else ""
        lines.append(f"{s['i']:3d} |{spk} {fmt_tc(s['start'])} - {fmt_tc(s['end'])} | "
                     f"{s['text']}  [w{s['word_from']}-{s['word_to']}]")
    lines.append("")
    lines.append("# 無音区間（0.3秒以上）")
    for z in t.get("silences", []):
        lines.append(f"    {fmt_tc(z['start'])} - {fmt_tc(z['end'])}  ({z['end'] - z['start']:.2f}s)")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".wav", ".m4a", ".mp3"}


def transcribe_one(src, model, language):
    """素材1本を文字起こしして、transcript.json と同じ中身の辞書を返す。"""
    import mlx_whisper

    info = probe(src)
    print(f"文字起こし中: {src}（モデル {model}）", file=sys.stderr)
    res = mlx_whisper.transcribe(
        str(src), path_or_hf_repo=model, language=language,
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

    silences = detect_silences(src)
    return {"source": info, "model": model, "segments": segments,
            "words": words, "silences": silences}


def write_index(parts, path):
    """素材が複数のときの一覧。素材ごとに見出しを付けて、発話と単語番号を並べる。"""
    lines = ["# 素材ごとの文字起こし（カット表には src と単語番号を書く）"]
    for stem, t in parts:
        info = t["source"]
        lines.append("")
        lines.append(f"## {stem}  尺 {fmt_tc(info['duration'])}  "
                     f"{info['width']}x{info['height']} {info['fps']:.3f}fps  "
                     f"発話{len(t['segments'])} 単語{len(t['words'])}")
        for s in t["segments"]:
            spk = f" {s.get('speaker', '--'):>3s} |" if any(x.get("speaker") for x in t["segments"]) else ""
            lines.append(f"  {s['i']:3d} |{spk} {fmt_tc(s['start'])} - {fmt_tc(s['end'])} | "
                         f"{s['text']}  [w{s['word_from']}-{s['word_to']}]")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="素材のファイル、または素材の入ったフォルダ")
    ap.add_argument("workdir")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--language", default="ja")
    args = ap.parse_args()

    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    src = Path(args.src)

    if src.is_dir():
        files = sorted((p for p in src.iterdir()
                        if p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")),
                       key=lambda p: (len(p.stem), p.stem))
        if not files:
            raise SystemExit(f"動画が見つかりません: {src}")
        parts = []
        for n, f in enumerate(files, start=1):
            print(f"[{n}/{len(files)}] {f.name}", file=sys.stderr)
            t = transcribe_one(f, args.model, args.language)
            save_json(t, work / "transcripts" / f"{f.stem}.json")
            parts.append((f.stem, t))
        write_index(parts, work / "transcript.txt")
        print(f"書き出し: {work / 'transcripts'}/*.json と transcript.txt"
              f"（素材 {len(files)}本）", file=sys.stderr)
        return

    out = transcribe_one(src, args.model, args.language)
    save_json(out, work / "transcript.json")
    write_txt(out, work / "transcript.txt")
    print(f"書き出し: {work / 'transcript.json'} / transcript.txt "
          f"（発話 {len(out['segments'])}、単語 {len(out['words'])}、"
          f"無音 {len(out['silences'])}）", file=sys.stderr)


if __name__ == "__main__":
    main()
