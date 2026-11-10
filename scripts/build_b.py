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
  - 各カットの始まりは "row"（字コンテの行 R01）か "at"（行 R01・文 L03・句 L03.2 のどれか）で指定する。
    長さは、その位置のナレーションの開始から、次のカットの位置まで
  - "captions": "phrase" にすると、テロップを句ごとに1枚出す（句は script.json の phrases）。
    "telops": {"L03.2": "表示したい文面"} で句の文面を上書きできる。省略時は storyboard.json の行のテロップ
  - "caption_style": {"center_y": 900, "size": 72} でプレビューのテロップの位置・大きさを変える
  - 各行の長さは、その行の最初のナレーションの開始から、次の行の最初のナレーションの開始まで
  - 1行に複数のショットを置くときは dur（秒）で長さを決める。dur のないショットは残りを等分する
  - center_x は 9:16 に切り抜く位置（0=左端、0.5=中央、1=右端）
  - zoom は寄せの倍率（1=画面いっぱい、1.5=1.5倍に寄せる）。center_y は寄せたときの縦の位置（0=上端、0.5=中央、1=下端）。
    4K の縦動画は 1080x1920 の2倍の大きさがあるので、zoom 2.0 までは画質が落ちない（超えると警告）
  - subject は主役の範囲 [左, 上, 右, 下]（素材の画面に対する割合 0〜1、区間の中で動く範囲全体）。
    zoom を書かずに subject を書くと、主役を画面の中央に置いて大きく寄せる zoom / center_x / center_y を計算する（framing.py）。
    範囲は framegrid.py の目盛り付きのコマで読む

出力（cutlist.json と同じフォルダ）:
  preview.mp4 / timeline.json / premiere/<名前>.xml・_captions.srt
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_tc, load_json, probe, save_json  # noqa: E402

OUT_W, OUT_H = 1080, 1920


def lossless_zoom(sw, sh):
    """画質を落とさずに寄せられる倍率の上限。画面を埋める縮小率の逆数（4K 縦なら2.0、4K 横なら1.125）。"""
    if not sw or not sh:
        return 2.0
    return max(1.0, min(sw / OUT_W, sh / OUT_H))
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
    # カットの始まりに指定できる位置: 字コンテの行（R01）・文（L03）・句（L03.2）
    anchors = {}
    for r in rows:
        anchors[r["row"]] = r["lines"][0]["start"]
    for ln in lines:
        anchors[ln["id"]] = ln["start"]
        for ph in ln.get("phrases", []):
            anchors[ph["id"]] = ph["start"]
    row_of = {}
    for ln in lines:
        row_of[ln["id"]] = ln.get("row")
        for ph in ln.get("phrases", []):
            row_of[ph["id"]] = ln.get("row")

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

    def frame_at(sec):
        return int(round(max(sec - lead, 0) * fps))

    # カットの切り替え点（フレーム）
    keys = [c.get("at") or c.get("row") for c in cl["cuts"]]
    for k in keys:
        if k not in anchors:
            raise CutError(f"カットの位置 {k} がナレーションにありません（行 R.. / 文 L.. / 句 L...n）")
    bounds = [0] + [frame_at(anchors[k]) for k in keys[1:]] + [total]
    for a, b, k in zip(bounds, bounds[1:], keys):
        if b <= a:
            raise CutError(f"カット {k} の長さが0以下です（位置の順番を確かめる）")

    clips, markers, prev_key = [], [], None
    for ci, (key, cut) in enumerate(zip(keys, cl["cuts"])):
        r0, r1 = bounds[ci], bounds[ci + 1]
        span = r1 - r0
        fixed = sum(int(round(s["dur"] * fps)) for s in cut["shots"] if s.get("dur"))
        free = [s for s in cut["shots"] if not s.get("dur")]
        if fixed > span:
            raise CutError(f"{key} の dur の合計（{fixed / fps:.2f}秒）がカットの長さ（{span / fps:.2f}秒）を超えています")
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
                raise CutError(f"{key} の {s['clip']}: in {s.get('in', 0)}秒 から {n / fps:.2f}秒 取ると"
                               f"素材の長さ（{info['duration']}秒）を超えます")
            if "subject" in s and "zoom" not in s:
                # 被写体の範囲（framegrid.py で読む）から、寄せの倍率と位置を決める（framing.py）
                from framing import frame_subject
                sw, sh = sources[k_src]["width"], sources[k_src]["height"]
                base_w = min(1.0, (OUT_W / OUT_H) / (sw / sh)) if sw and sh else 1.0
                zmax = lossless_zoom(sw, sh)
                try:
                    s = {**s, **frame_subject(s["subject"], max(zmax, 1.0), base_w)}
                except ValueError as e:
                    raise CutError(f"{key} の {s['clip']}: {e}")
            z = float(s.get("zoom", 1.0))
            if z < 1.0:
                raise CutError(f"{key} の {s['clip']}: zoom は1以上にしてください（{z}）")
            if z > lossless_zoom(sources[k_src]["width"], sources[k_src]["height"]) + 0.01:
                warnings.append(f"{key} の {s['clip']}: zoom {z} は素材の解像度を超えるので画質が落ちます")
            if n < MIN_SHOT_SEC * fps:
                warnings.append(f"{key} の {s['clip']} が {n / fps:.2f}秒しかありません")
            seg = next((g for g in info.get("segments", [])
                        if g["start"] <= src_in / fps < g["end"]), None)
            if seg and seg.get("usable") is False:
                warnings.append(f"{key} の {s['clip']} {src_in / fps:.1f}秒 は使えない区間として記録されています: {seg.get('note', '')}")
            if (s["clip"], src_in) == prev_key:
                warnings.append(f"{key} で同じショットが続いています: {s['clip']}")
            prev_key = (s["clip"], src_in + n)
            clips.append({"i": len(clips), "src": k_src, "src_in": src_in, "src_out": src_out,
                          "rec_in": rec, "rec_out": rec + n, "center_x": float(s.get("center_x", 0.5)),
                          "zoom": float(s.get("zoom", 1.0)), "center_y": float(s.get("center_y", 0.5)),
                          "note": cut.get("note", ""), "text": f"{s['clip']} {src_in / fps:.1f}s"})
            rec += n
        row = key if key in [r["row"] for r in rows] else row_of.get(key)
        visual = next((c.get("visual", "") for c in sb["cuts"] if c["row"] == row), "")
        markers.append({"rec_in": r0, "name": f"{key} {cut.get('note', '')}".strip(),
                        "comment": f"字コンテ {row} の映像指示: {visual}"})

    captions = []
    if cl.get("captions") == "phrase":
        # 字幕: 句ごとに1枚。文面は telops で上書きできる（読みと表記を変えたいとき）
        over = cl.get("telops", {})
        phs = [ph for ln in lines for ph in (ln.get("phrases") or
               [{"id": ln["id"], "text": ln["text"], "start": ln["start"]}])]
        starts = [0] + [frame_at(ph["start"]) for ph in phs[1:]]
        ends = starts[1:] + [total]
        for ph, a, b in zip(phs, starts, ends):
            t = over.get(ph["id"], ph["text"])
            captions.append({"i": len(captions), "text": t, "rec_in": a, "rec_out": b, "id": ph["id"]})
    else:
        # 字幕: 字コンテの行のテロップを、その行のナレーションの時刻に合わせる
        rb = [0] + [frame_at(r["lines"][0]["start"]) for r in rows[1:]] + [total]
        for ri, r in enumerate(rows):
            sbc = next((c for c in sb["cuts"] if c["row"] == r["row"]), {"telop": []})
            telops = sbc.get("telop", [])
            r0, r1 = rb[ri], rb[ri + 1]
            if len(telops) == len(r["lines"]):
                starts = [r0] + [frame_at(ln["start"]) for ln in r["lines"][1:]]
            else:
                step = (r1 - r0) / max(len(telops), 1)
                starts = [r0 + int(step * i) for i in range(len(telops))]
            ends = starts[1:] + [r1]
            for t, a, b in zip(telops, starts, ends):
                captions.append({"i": len(captions), "text": t, "rec_in": a, "rec_out": b})
    for c in captions:
        for ln in c["text"].split("\n"):
            if len(ln) > 13:
                warnings.append(f"テロップの行「{ln}」が{len(ln)}文字です（目安13文字）")

    narr_info = probe_audio(narr["wav"])
    sources.append(narr_info)
    audio = [{"src": len(sources) - 1, "src_in": 0, "src_out": min(total, int(narr_info["duration"] * fps)),
              "rec_in": 0, "rec_out": min(total, int(narr_info["duration"] * fps)), "link": None}]

    base_src = dict(sources[first])
    return {"type": "B", "source": base_src, "fps": fps, "out_w": OUT_W, "out_h": OUT_H,
            "title": cl.get("title", ""), "reframe": {"mode": "crop", **cl.get("reframe", {})},
            "sources": sources, "clips": clips, "audio": audio, "captions": captions,
            "markers": markers, "total_frames": total, "total_sec": round(total / fps, 3),
            "caption_style": cl.get("caption_style", {})}, warnings


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
    # 同じ番号のシーケンスがプロジェクトに並んでも見分けられるよう、案件フォルダ名を前に付ける
    for f in export(tl, out / "premiere", name, seq_name=f"{out.parent.name}_{name}"):
        print(f"  Premiere用: {f}")


if __name__ == "__main__":
    main()
