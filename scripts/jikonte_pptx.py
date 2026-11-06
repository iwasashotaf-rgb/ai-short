"""字コンテ（PowerPoint）を読み、カットごとの「映像」「ナレーション」「テロップ」を JSON にする。

想定している形（PPIH のコンテ案の様式で確認、2026-11-06）:
  - スライドの上部に「▼音声ナレーション」「▼テロップ」の見出しがある
  - 左端の数字（カット番号）が各カットの行の始まり
  - 列は左から 映像の説明 / 音声ナレーション / テロップ。テキストボックスの横位置で列を判定する
  - テキストボックスは重なって置かれていることがあるので、同じ列・同じ行の同じ文面は1つにまとめる

使い方:
  .venv/bin/python scripts/jikonte_pptx.py 字コンテ.pptx projects/案件名/jikonte

出力:
  jikonte/01.raw.json … jikonte_parse.py（Google ドキュメント用）と同じ形
                         {"no", "title", "meta", "rows": [{"row", "text", "narration", "visual"}]}
"""
import json
import re
import sys
from pathlib import Path

from pptx import Presentation

EMU = 914400


def boxes(slide):
    out = []

    def walk(shapes):
        for s in shapes:
            if s.shape_type is not None and "GROUP" in str(s.shape_type):
                walk(s.shapes)
                continue
            if not getattr(s, "has_text_frame", False) or not s.has_text_frame:
                continue
            lines = [p.text.strip() for p in s.text_frame.paragraphs]
            while lines and not lines[-1]:
                lines.pop()
            while lines and not lines[0]:
                lines.pop(0)
            if not any(lines):
                continue
            out.append({"x": (s.left or 0) / EMU, "y": (s.top or 0) / EMU,
                        "w": (s.width or 0) / EMU, "lines": lines, "text": " ".join(x for x in lines if x)})
    walk(slide.shapes)
    return out


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    pres = Presentation(sys.argv[1])
    rows, title, meta = [], "", {}
    last_no = 0
    for sl in pres.slides:
        bs = boxes(sl)
        for b in [b for b in bs if b["text"].startswith("※")]:  # 注記はカットの中身に混ぜない
            meta["注記"] = b["text"]
            bs.remove(b)
        na = next((b for b in bs if "音声ナレーション" in b["text"]), None)
        te = next((b for b in bs if b["text"].startswith("▼テロップ")), None)
        if not na or not te:
            print("注意: 見出し（▼音声ナレーション / ▼テロップ）がないスライドを読み飛ばしました", file=sys.stderr)
            continue
        head_y = max(na["y"], te["y"]) + 0.2
        # カット番号: 左端（x < 0.6）の数字だけの箱。同じ番号が重なっていれば1つにする
        cands = sorted({(int(b["text"]), round(b["y"], 1)) for b in bs
                        if b["x"] < 0.6 and re.fullmatch(r"\d+", b["text"]) and b["y"] > head_y - 0.3},
                       key=lambda t: t[1])
        # 同じ高さに番号が重なっている（雛形の番号が残っている）ときは、前のカットから続く番号を採る
        marks, last = [], last_no
        for y in sorted({y for _, y in cands}):
            here = [n for n, yy in cands if yy == y]
            no = last + 1 if last + 1 in here else max(here)
            marks.append((no, y))
            last = no
        last_no = last
        if not marks:
            continue
        col_na = na["x"] - 0.5   # これより右がナレーション列
        col_te = te["x"] - 0.8   # これより右がテロップ列
        for i, (no, y0) in enumerate(marks):
            y1 = marks[i + 1][1] if i + 1 < len(marks) else 99
            cell = {"visual": [], "narration": [], "telop": []}
            for b in bs:
                if b in (na, te) or b["y"] < y0 - 0.1 or b["y"] >= y1 - 0.1:
                    continue
                if b["x"] < 0.6 and re.fullmatch(r"\d+", b["text"]):
                    continue
                col = "telop" if b["x"] >= col_te else "narration" if b["x"] >= col_na else "visual"
                if b["lines"] not in cell[col]:
                    cell[col].append(b["lines"])
            if not any(cell.values()):
                continue
            rows.append({"row": f"R{no:02d}",
                         "visual": [x for ls in cell["visual"] for x in ls if x],
                         "narration": [x for ls in cell["narration"] for x in ls if x],
                         "text": [x for ls in cell["telop"] for x in ls if x]})
        for b in bs:  # 表紙情報（タイトル・発注元・冒頭の型）
            if b["y"] < head_y and b not in (na, te):
                if not title and b["x"] < 1 and "冒頭" not in b["text"]:
                    title = b["text"]
                elif "冒頭の型" in b["text"]:
                    meta["冒頭の型"] = b["text"]
                elif "株" in b["text"]:
                    meta["発注元"] = b["text"]
                elif b["text"].startswith("※"):
                    meta["注記"] = b["text"]
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    v = {"no": 1, "title": title, "meta": meta, "source": str(Path(sys.argv[1]).resolve()), "rows": rows}
    (out / "01.raw.json").write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{title}（{len(rows)}カット）")
    for r in rows:
        print(f"  {r['row']} 映像: {' '.join(r['visual'])}")
        print(f"       NA: {' '.join(r['narration'])} / テロップ: {' / '.join(r['text'])}")


if __name__ == "__main__":
    main()
