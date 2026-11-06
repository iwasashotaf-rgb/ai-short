"""B 字コンテ型の書き出し: カット割り当て表（cutlist.json）を検証し、プレビュー MP4 と Premiere 用 XML・SRT を作る。

使い方:
  .venv/bin/python scripts/build_b.py projects/案件名/01/cutlist.json [--check] [--no-preview]

cutlist.json の形式（Claude が書く。パスは cutlist.json からの相対でよい）:
{
  "narration": "narration/narration.json",   # narrate.py の出力
  "storyboard": "storyboard.json",            # 行ごとのテロップ
  "shots": "../shots/shots.json",             # shots.py の出力（素材の一覧）
  "title": "",                                # 上部タイトル（任意）
  "lead_sec": 0.1,                            # 映像をナレーションより何秒早く切り替えるか
  "tail_sec": 0.6,                            # 最後の文の後に残す秒数
  "reframe": {"mode": "crop"},
  "cuts": [
    {"row": "R01", "note": "焼き目の完成アップ",
     "shots": [{"clip": "IMG_0017.MOV", "in": 2.0, "center_x": 0.5}]},
    {"row": "R02", "shots": [{"clip": "IMG_0020.MOV", "in": 1.0, "dur": 1.2},
                             {"clip": "IMG_0022.MOV", "in": 3.5}]}
  ]
}
  - 各行の長さは、その行の最初のナレーションの開始から、次の行の最初のナレーションの開始まで
  - 1行に複数のショットを置くときは dur（秒）で長さを決める。dur のないショットは残りを等分する
  - center_x は 9:16 に切り抜く位置（0=左端、0.5=中央、1=右端）

出力（cutlist.json と同じフォルダ）:
  preview.mp4 / timeline.json / premiere/<名前>.xml・_captions.srt
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_tc, load_json, probe, save_json  # noqa: E402

OUT_W, OUT_H = 1080, 1920
MIN_SHOT_SEC = 0.5


class CutError(Exception):
    pass


def resolve(cl_path):
    cl_path = Path(cl_path)
    base = cl_path.parent
    cl = load_json(cl_path)
    narr = load_json(base / cl.get("narration", "narration/narration.json"))
    sb = load_json(base / cl.get("storyboard", "storyboard.json"))
    shots = load_json(base / cl.get("shots", "../shots/shots.json"))
    catalog = {c["clip"]: c for c in shots["clips"]}
    warnings = []

    lines = narr["lines"]
    rows = []  # 字コンテの行の順に、その行のナレーションを集める
    for ln in lines:
        r = next((x for x in rows if x["row"] == ln.get("row")), None)
        if r is None:
            r = {"row": ln.get("row"), "lines": []}
            rows.append(r)
        r["lines"].append(ln)
    cut_rows = [c["row"] for c in cl["cuts"]]
    if cut_rows != [r["row"] for r in rows]:
        raise CutError(f"cuts の行 {cut_rows} がナレーションの行 {[r['row'] for r in rows]} と一致しません")

    # 映像の素材と、時間の基準（最初の素材のフレームレート）
    sources, src_idx = [], {}

    def src_of(clip):
        if clip not in catalog:
            raise CutError(f"{clip} が shots.json にありません")
        if clip not in src_idx:
            info = probe(catalog[clip]["path"])
            src_idx[clip] = len(sources)
            sources.append(info)
        return src_idx[clip]

    first = src_of(cl["cuts"][0]["shots"][0]["clip"])
    fps = sources[first]["fps"]
    lead, tail = float(cl.get("lead_sec", 0.1)), float(cl.get("tail_sec", 0.6))
    total_sec = narr["duration"] + tail - 0.3  # narration.wav は末尾に0.3秒の余白を持つ
    total = int(round(total_sec * fps))

    # 行の切り替え点（フレーム）
    bounds = [0]
    for r in rows[1:]:
        bounds.append(int(round(max(r["lines"][0]["start"] - lead, 0) * fps)))
    bounds.append(total)

    clips, markers, prev_key = [], [], None
    for ri, (r, cut) in enumerate(zip(rows, cl["cuts"])):
        r0, r1 = bounds[ri], bounds[ri + 1]
        span = r1 - r0
        fixed = sum(int(round(s["dur"] * fps)) for s in cut["shots"] if s.get("dur"))
        free = [s for s in cut["shots"] if not s.get("dur")]
        if fixed > span:
            raise CutError(f"{r['row']} の dur の合計（{fixed / fps:.2f}秒）が行の長さ（{span / fps:.2f}秒）を超えています")
        each = (span - fixed) // len(free) if free else 0
        rec = r0
        for k, s in enumerate(cut["shots"]):
            n = int(round(s["dur"] * fps)) if s.get("dur") else each
            if k == len(cut["shots"]) - 1:
                n = r1 - rec  # 端数は最後のショットで吸収する
            k_src = src_of(s["clip"])
            info = catalog[s["clip"]]
            src_in = int(round(float(s.get("in", 0)) * fps))
            src_out = src_in + n
            if src_out > int(info["duration"] * fps):
                raise CutError(f"{r['row']} の {s['clip']}: in {s.get('in', 0)}秒 から {n / fps:.2f}秒 取ると"
                               f"素材の長さ（{info['duration']}秒）を超えます")
            if n < MIN_SHOT_SEC * fps:
                warnings.append(f"{r['row']} の {s['clip']} が {n / fps:.2f}秒しかありません")
            seg = next((g for g in info.get("segments", [])
                        if g["start"] <= src_in / fps < g["end"]), None)
            if seg and seg.get("usable") is False:
                warnings.append(f"{r['row']} の {s['clip']} {src_in / fps:.1f}秒 は使えない区間として記録されています: {seg.get('note', '')}")
            key = (s["clip"], src_in)
            if key == prev_key:
                warnings.append(f"{r['row']} で同じショットが続いています: {s['clip']}")
            prev_key = key
            clips.append({"i": len(clips), "src": k_src, "src_in": src_in, "src_out": src_out,
                          "rec_in": rec, "rec_out": rec + n, "center_x": float(s.get("center_x", 0.5)),
                          "note": cut.get("note", ""), "text": f"{s['clip']} {src_in / fps:.1f}s"})
            rec += n
        visual = next((c.get("visual", "") for c in sb["cuts"] if c["row"] == r["row"]), "")
        markers.append({"rec_in": r0, "name": f"{r['row']} {cut.get('note', '')}".strip(),
                        "comment": f"映像指示: {visual}"})

    # 字幕: 行のテロップを、その行のナレーションの時刻に合わせる
    captions = []
    for ri, r in enumerate(rows):
        sbc = next((c for c in sb["cuts"] if c["row"] == r["row"]), {"telop": []})
        telops = sbc.get("telop", [])
        r0, r1 = bounds[ri], bounds[ri + 1]
        if len(telops) == len(r["lines"]):
            starts = [r0] + [int(round(max(ln["start"] - lead, 0) * fps)) for ln in r["lines"][1:]]
            ends = starts[1:] + [r1]
        else:
            step = (r1 - r0) / max(len(telops), 1)
            starts = [r0 + int(step * i) for i in range(len(telops))]
            ends = starts[1:] + [r1]
        for t, a, b in zip(telops, starts, ends):
            captions.append({"i": len(captions), "text": t, "rec_in": a, "rec_out": b})
            for ln in t.split("\n"):
                if len(ln) > 13:
                    warnings.append(f"{r['row']} のテロップの行「{ln}」が{len(ln)}文字です（目安13文字）")

    narr_info = probe_audio(narr["wav"])
    sources.append(narr_info)
    audio = [{"src": len(sources) - 1, "src_in": 0, "src_out": min(total, int(narr_info["duration"] * fps)),
              "rec_in": 0, "rec_out": min(total, int(narr_info["duration"] * fps)), "link": None}]

    base_src = dict(sources[first])
    return {"type": "B", "source": base_src, "fps": fps, "out_w": OUT_W, "out_h": OUT_H,
            "title": cl.get("title", ""), "reframe": {"mode": "crop", **cl.get("reframe", {})},
            "sources": sources, "clips": clips, "audio": audio, "captions": captions,
            "markers": markers, "total_frames": total, "total_sec": round(total / fps, 3)}, warnings


def probe_audio(path):
    import json
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,sample_rate,channels", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    a = next(s for s in d["streams"] if s["codec_type"] == "audio")
    return {"path": str(Path(path).resolve()), "duration": float(d["format"]["duration"]),
            "width": None, "height": None, "audio_rate": int(a["sample_rate"]),
            "audio_channels": int(a["channels"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cutlist")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()
    cl = Path(args.cutlist)
    try:
        tl, warnings = resolve(cl)
    except CutError as e:
        raise SystemExit(f"カット割り当て表の誤り: {e}")
    fps = tl["fps"]
    print(f"尺 {tl['total_sec']:.2f}秒 / ショット {len(tl['clips'])} / 字幕 {len(tl['captions'])}")
    for c in tl["clips"]:
        print(f"  {fmt_tc(c['rec_in'] / fps)}-{fmt_tc(c['rec_out'] / fps)}  {c['note']}｜{c['text']}"
              f"（{(c['rec_out'] - c['rec_in']) / fps:.2f}秒）")
    for w in warnings:
        print(f"  注意: {w}")
    if args.check:
        return
    out = cl.parent
    name = out.name
    save_json(tl, out / "timeline.json")
    if not args.no_preview:
        from preview import render
        render(tl, out / "preview.mp4", out / "_tmp")
        print(f"  プレビュー: {out / 'preview.mp4'}")
    from premiere_export import export
    for f in export(tl, out / "premiere", name):
        print(f"  Premiere用: {f}")


if __name__ == "__main__":
    main()
