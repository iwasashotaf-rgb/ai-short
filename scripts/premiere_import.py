"""工程5（自動）: 書き出した XML と SRT を、開いている Premiere Pro のプロジェクトに読み込む。

前提: Premiere で ウィンドウ > エクステンション > MCP Bridge (CEP) を開き、Start Bridge を押してあること。

使い方:
  .venv/bin/python scripts/premiere_import.py projects/案件名/shorts/01/premiere

やること:
  1. プロジェクトに「AIショート」ビンがなければ作る
  2. XML を読み込む → カット済みの 9:16 シーケンスができる
  3. 字幕とタイトルの SRT を読み込み、そのシーケンスにキャプショントラックとして置く
  4. シーケンスをタイムラインで開く
結果（シーケンス名・映像/音声クリップ数・キャプショントラックの作成可否）を表示する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from premiere_bridge import BRIDGE_DIR, BridgeError, run_jsx  # noqa: E402

BIN_NAME = "AIショート"

JSX = r"""
var out = new File("__RESULT__"); out.encoding = "UTF-8"; out.open("w");
function findBin(name) {
  for (var i = 0; i < app.project.rootItem.children.numItems; i++) {
    var c = app.project.rootItem.children[i];
    if (c.type == 2 && c.name == name) return c;
  }
  return app.project.rootItem.createBin(name);
}
var before = {};
for (var i = 0; i < app.project.sequences.numSequences; i++) before[app.project.sequences[i].sequenceID] = 1;
var bin = findBin("__BIN__");
app.project.importFiles(["__XML__"], true, bin, false);
var seq = null;
for (var i = 0; i < app.project.sequences.numSequences; i++) {
  var s = app.project.sequences[i];
  if (!before[s.sequenceID]) seq = s;
}
if (!seq) { out.writeln("NG: XML からシーケンスが作られませんでした"); out.close(); return "noseq"; }
var v = 0, a = 0;
for (var t = 0; t < seq.videoTracks.numTracks; t++) v += seq.videoTracks[t].clips.numItems;
for (var t = 0; t < seq.audioTracks.numTracks; t++) a += seq.audioTracks[t].clips.numItems;
out.writeln("sequence=" + seq.name + " size=" + seq.frameSizeHorizontal + "x" + seq.frameSizeVertical + " video=" + v + " audio=" + a);
var srts = [__SRTS__];
for (var k = 0; k < srts.length; k++) {
  var f = new File(srts[k]);
  var n0 = bin.children.numItems;
  app.project.importFiles([srts[k]], true, bin, false);
  var item = bin.children.numItems > n0 ? bin.children[bin.children.numItems - 1] : null;
  for (var i = 0; i < bin.children.numItems && !item; i++) if (bin.children[i].name == f.name) item = bin.children[i];
  out.writeln("caption " + f.name + ": " + (item ? seq.createCaptionTrack(item, 0) : "読み込み失敗"));
}
app.project.openSequence(seq.sequenceID);
out.close();
return "ok";
"""


def js_str(p):
    return str(p).replace("\\", "\\\\").replace('"', '\\"')


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    d = Path(sys.argv[1]).resolve()
    xmls = sorted(d.glob("*.xml"))
    if len(xmls) != 1:
        raise SystemExit(f"XML が1つに決まりません: {d}")
    srts = [p for p in (d / f"{xmls[0].stem}_captions.srt", d / f"{xmls[0].stem}_title.srt") if p.exists()]
    result = Path(BRIDGE_DIR) / "aishort_import_result.txt"
    result.unlink(missing_ok=True)
    jsx = (JSX.replace("__RESULT__", js_str(result)).replace("__BIN__", BIN_NAME)
           .replace("__XML__", js_str(xmls[0]))
           .replace("__SRTS__", ", ".join(f'"{js_str(p)}"' for p in srts)))
    try:
        r = run_jsx(jsx, timeout_sec=120)
    except BridgeError as e:
        raise SystemExit(str(e))
    if not r.get("success"):
        raise SystemExit(f"Premiere 側でエラー: {r.get('error')}")
    if not result.exists():
        raise SystemExit(f"結果ファイルがありません（戻り値: {r.get('result')}）")
    print(result.read_text(encoding="utf-8").replace("\r", "\n").strip())


if __name__ == "__main__":
    main()
