"""C インタビュー型の書き出し: カット表を検証し、プレビュー MP4 と Premiere 用 XML・SRT を作る。

使い方:
  .venv/bin/python scripts/build_c.py projects/案件名/shorts/01.interview.json --check
  .venv/bin/python scripts/build_c.py projects/案件名/shorts/01.interview.json [--no-preview]

  --check       検証だけして、尺・カット・字幕の一覧と注意を表示する（書き出さない）
  --no-preview  プレビューMP4を作らず、Premiere用ファイルだけ書き出す

カット表の形式は scripts/timeline_c.py の先頭にある。

出力（カット表と同じフォルダに、カット表の名前で作る）:
  01/preview.mp4             確認用（字幕・質問テロップ焼き込み）
  01/timeline.json           フレーム単位の対応表
  01/premiere/01.xml         Premiere 用タイムライン（FCP7 XML）
  01/premiere/01_captions.srt / 01_title.srt
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_tc, save_json  # noqa: E402
from timeline_c import CutError, resolve  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cutlist")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--seq-name", default=None, help="Premiere 上のシーケンス名（省略時はファイル名）")
    args = ap.parse_args()

    cut_path = Path(args.cutlist)
    name = cut_path.name.removesuffix(".json").removesuffix(".interview")
    try:
        tl, warnings = resolve(cut_path)
    except CutError as e:
        raise SystemExit(f"カット表の誤り: {e}")

    fps = tl["fps"]
    print(f"[{name}] 尺 {tl['total_sec']:.2f}秒 / カット {len(tl['audio'])} / ショット {len(tl['clips'])}"
          f" / 字幕 {len(tl['captions'])} / 質問テロップ {len(tl['titles'])}")
    for c in tl["clips"]:
        src = Path(tl["sources"][c["src"]]["path"]).name
        print(f"  {fmt_tc(c['rec_in'] / fps)}-{fmt_tc(c['rec_out'] / fps)} "
              f"（{(c['rec_out'] - c['rec_in']) / fps:.2f}秒）{src} {fmt_tc(c['src_in'] / fps)}"
              f" 寄せ{c['zoom']:.2f} ｜{c['note']}｜{c['text']}")
    for t in tl["titles"]:
        print(f"  質問 {fmt_tc(t['rec_in'] / fps)}-{fmt_tc(t['rec_out'] / fps)}: "
              f"{t['text'].replace(chr(10), ' / ')}")
    for c in tl["captions"]:
        print(f"  字幕 {fmt_tc(c['rec_in'] / fps)}-{fmt_tc(c['rec_out'] / fps)}: "
              f"{c['text'].replace(chr(10), ' / ')}")
    for w in warnings:
        print(f"  注意: {w}")
    if args.check:
        return

    out = cut_path.parent / name
    save_json(tl, out / "timeline.json")
    if not args.no_preview:
        from preview import render
        render(tl, out / "preview.mp4", out / "_tmp")
        print(f"  プレビュー: {out / 'preview.mp4'}")
    from premiere_export import export
    for f in export(tl, out / "premiere", name, seq_name=args.seq_name):
        print(f"  Premiere用: {f}")


if __name__ == "__main__":
    main()
