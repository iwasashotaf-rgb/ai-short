"""工程2〜4をまとめて実行する: カット表の検証 → プレビューMP4 → Premiere用 XML・SRT。

使い方:
  .venv/bin/python scripts/build.py 作業フォルダ/transcript.json 作業フォルダ/shorts/01.edl.json [--check] [--no-preview]

  --check       検証だけして、尺・カット・字幕の一覧と注意を表示する（書き出さない）
  --no-preview  プレビューMP4を作らず、Premiere用ファイルだけ書き出す

出力（カット表と同じフォルダに、カット表の名前で作る）:
  01/preview.mp4             確認用（字幕・タイトル焼き込み）
  01/premiere/01.xml         Premiere でファイル→読み込み
  01/premiere/01_captions.srt / 01_title.srt
  01/timeline.json           フレーム単位の対応表
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_tc, save_json  # noqa: E402
from timeline import EdlError, load_and_resolve  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("edl")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    edl_path = Path(args.edl)
    name = edl_path.name.removesuffix(".json").removesuffix(".edl")
    try:
        tl, warnings = load_and_resolve(args.transcript, edl_path)
    except EdlError as e:
        raise SystemExit(f"カット表の誤り: {e}")

    fps = tl["fps"]
    print(f"[{name}] 尺 {tl['total_sec']:.2f}秒 / カット {len(tl['clips'])} / 字幕 {len(tl['captions'])}")
    for c in tl["clips"]:
        print(f"  カット{c['i'] + 1}: 素材 {fmt_tc(c['src_in'] / fps)}-{fmt_tc(c['src_out'] / fps)}"
              f" → 出力 {fmt_tc(c['rec_in'] / fps)}  {c['note']}｜{c['text'][:40]}")
    for w in warnings:
        print(f"  注意: {w}")
    if args.check:
        return

    out = edl_path.parent / name
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
