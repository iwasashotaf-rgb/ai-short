"""字コンテ（Google ドキュメント）を動画ごとに分け、行ごとの文面と映像指示を JSON にする。

Google ドキュメントは Claude が Google Docs コネクタ（read_doc）で取得する。
結果が大きいとファイルに保存されるので、そのファイルを渡す。

使い方:
  .venv/bin/python scripts/jikonte_parse.py read_doc の結果.json projects/案件名/jikonte [--tab タブID]

想定している字コンテの形（1動画につき表2つ）:
  表A（概要）: 1行目にタイトル。以降「セリフの有無 / ナレーション種類 / 出演者 / 参考URL」の行
  表B（本体）: 1行目が「テロップ/ナレーション | 映像」。以降1行が1カット

出力:
  jikonte/01.raw.json … 動画1本分（タイトル・概要・行ごとの文面と映像指示）
  jikonte/index.txt   … 全動画の一覧（Claude が最初に読む）
"""
import argparse
import json
import re
import sys
from pathlib import Path


def cell_lines(cell):
    """セル内の段落を1行ずつ返す。画像は [画像] と書く。"""
    lines = []
    for e in cell.get("content", []):
        if "paragraph" not in e:
            continue
        s = ""
        for r in e["paragraph"].get("elements", []):
            if "textRun" in r:
                s += r["textRun"]["content"]
            elif "inlineObjectElement" in r:
                s += "[画像]"
        s = s.replace("\u000b", "\n").rstrip("\n")
        for part in s.split("\n"):
            lines.append(part.strip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


def table_rows(t):
    return [[cell_lines(c) for c in row["tableCells"]] for row in t["table"]["tableRows"]]


def find_tab(tabs, tab_id):
    for t in tabs:
        if tab_id is None or t["tabProperties"]["tabId"] == tab_id:
            return t
        found = find_tab(t.get("childTabs", []), tab_id)
        if found:
            return found
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_json")
    ap.add_argument("out_dir")
    ap.add_argument("--tab", help="対象タブのID（例 t.qw9xs8ya3s7c）。省略時は最初のタブ")
    args = ap.parse_args()

    d = json.load(open(args.doc_json, encoding="utf-8"))
    doc = d.get("content", d)
    tab = find_tab(doc["tabs"], args.tab)
    if tab is None:
        raise SystemExit(f"タブが見つかりません: {args.tab}")
    body = tab["documentTab"]["body"]["content"]
    tables = [e for e in body if "table" in e]

    videos, cur = [], None
    for t in tables:
        rows = table_rows(t)
        head = " ".join(rows[0][0]) if rows and rows[0] else ""
        if t["table"]["columns"] >= 2 and re.search(r"テロップ|ナレーション", head) \
                and any("映像" in " ".join(c) for c in rows[0][1:]):
            if cur is None:
                print(f"注意: 概要の表がない本体の表を読み飛ばしました（先頭: {head[:30]}）", file=sys.stderr)
                continue
            for i, row in enumerate(rows[1:], start=1):
                text, visual = row[0], (row[1] if len(row) > 1 else [])
                if not any(text) and not any(visual):
                    continue
                cur["rows"].append({"row": f"R{i:02d}", "text": text, "visual": visual})
            continue
        # 概要の表
        meta = {}
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            key = " ".join(row[0]).strip()
            meta[key] = " / ".join(x for c in row[1:] for x in c if x)
        title_lines = [x for x in rows[0][0] if x]
        cur = {"no": len(videos) + 1, "title": " ".join(title_lines), "meta": meta, "rows": []}
        videos.append(cur)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = [f"# {doc.get('title', '')}  タブ: {tab['tabProperties'].get('title')}"]
    for v in videos:
        p = out / f"{v['no']:02d}.raw.json"
        p.write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")
        index.append(f"{v['no']:02d}  {v['title']}  （{len(v['rows'])}行・"
                     f"ナレーション: {v['meta'].get('ナレーション種類', '?')}）")
    (out / "index.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    print("\n".join(index))


if __name__ == "__main__":
    main()
