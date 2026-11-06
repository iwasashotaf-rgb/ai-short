"""工程2の後半: Claude が書いたカット表（edl.json）を検証し、フレーム単位のタイムラインに変換する。

edl.json の形式（単語番号は transcript.json の words[].i）:
{
  "title": "AIに任せるのは下準備だけ",          # 動画上部に出すタイトル（任意）
  "target_sec": [20, 60],                        # 目標尺の下限と上限（任意）
  "reframe": {"mode": "crop", "center_x": 0.5},  # crop=中央を9:16で切り抜く / fit=全体を収めて上下をぼかす
  "pad": {"head": 0.10, "tail": 0.20},           # 各カットの前後に残す秒数（隣の語には食い込まない）
  "clips": [
    {"from_word": 17, "to_word": 39, "note": "問題提起"}
  ],
  "captions": [
    {"from_word": 19, "to_word": 30, "text": "動画編集を外注すると"}
  ]
}
captions の text は Claude が誤認識を直して書く。改行は \\n で明示する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json  # noqa: E402

OUT_W, OUT_H = 1080, 1920
CAPTION_MAX_LINE = 13      # 縦型・字幕78pxで1行に収まる全角文字数（これを超えると字幕全体が縮む）
CAPTION_MAX_LINES = 2
CAPTION_MAX_CPS = 9.0      # 1秒あたりの文字数の上限（これを超えると読み切れない）
CAPTION_MIN_SEC = 0.6


class EdlError(Exception):
    pass


def resolve(transcript, edl):
    """カット表を検証し、素材フレームと出力フレームの対応表を返す。

    戻り値の warnings は書き出しを止めない注意、EdlError は書き出しを止める誤り。
    """
    src = transcript["source"]
    fps = src["fps"]
    words = transcript["words"]
    silences = transcript.get("silences", [])
    nw = len(words)
    pad = {"head": 0.10, "tail": 0.20, **edl.get("pad", {})}
    warnings = []

    if not edl.get("clips"):
        raise EdlError("clips が空です")

    clips = []
    rec = 0
    prev_to = -1
    for k, c in enumerate(edl["clips"]):
        a, b = c.get("from_word"), c.get("to_word")
        if a is None or b is None or not (0 <= a <= b < nw):
            raise EdlError(f"clips[{k}] の単語番号が範囲外です: {a}-{b}（単語数 {nw}）")
        if a <= prev_to:
            raise EdlError(f"clips[{k}] が前のカットと重なっているか、時間順になっていません")
        # Whisper の単語時刻は数百ms ずれることがあるので、近くに無音区間があれば
        # その終わりを発話の始まり、始まりを発話の終わりとみなす
        onset, offset = words[a]["start"], words[b]["end"]
        for z in silences:
            if words[a]["start"] - 1.0 <= z["end"] <= words[a]["start"] + 0.4:
                onset = z["end"]
            if words[b]["end"] - 0.4 <= z["start"] <= words[b]["end"] + 1.0:
                offset = z["start"]
        # 前後に余白を付けるが、使わない隣の語には食い込ませない
        lo = words[a - 1]["end"] if a > 0 else 0.0
        hi = words[b + 1]["start"] if b + 1 < nw else src["duration"]
        t0 = max(onset - pad["head"], lo, 0.0)
        t1 = min(offset + pad["tail"], hi, src["duration"])
        f0 = int(round(t0 * fps))
        f1 = int(round(t1 * fps))
        if f1 <= f0:
            raise EdlError(f"clips[{k}] の長さが0フレームです")
        clips.append({"i": k, "from_word": a, "to_word": b, "note": c.get("note", ""),
                      "src_in": f0, "src_out": f1,            # 素材上のフレーム（out は含まない）
                      "rec_in": rec, "rec_out": rec + f1 - f0,  # 出力上のフレーム
                      "text": "".join(w["text"] for w in words[a:b + 1])})
        rec += f1 - f0
        prev_to = b
    total_frames = rec
    total_sec = total_frames / fps

    lo_sec, hi_sec = edl.get("target_sec", [15, 60])
    if not (lo_sec <= total_sec <= hi_sec):
        warnings.append(f"尺 {total_sec:.1f}秒 が目標 {lo_sec}〜{hi_sec}秒 から外れています")

    # 字幕: 素材上の単語範囲を、出力タイムライン上の時刻へ写す
    def word_to_rec(wi, edge):
        t = words[wi]["start"] if edge == "start" else words[wi]["end"]
        f = t * fps
        for c in clips:
            if c["from_word"] <= wi <= c["to_word"]:
                return c["rec_in"] + min(max(f - c["src_in"], 0), c["src_out"] - c["src_in"])
        return None

    captions = []
    covered = set()
    for k, cap in enumerate(edl.get("captions", [])):
        a, b, text = cap.get("from_word"), cap.get("to_word"), cap.get("text", "").strip()
        if a is None or b is None or not (0 <= a <= b < nw) or not text:
            raise EdlError(f"captions[{k}] の単語番号か本文が不正です")
        r0, r1 = word_to_rec(a, "start"), word_to_rec(b, "end")
        if r0 is None or r1 is None:
            raise EdlError(f"captions[{k}]「{text}」の先頭か末尾の語がカットで使われていません")
        lines = text.split("\n")
        if len(lines) > CAPTION_MAX_LINES:
            warnings.append(f"captions[{k}]「{text}」が{len(lines)}行あります（上限{CAPTION_MAX_LINES}行）")
        for ln in lines:
            if len(ln) > CAPTION_MAX_LINE:
                warnings.append(f"captions[{k}] の行「{ln}」が{len(ln)}文字です（目安{CAPTION_MAX_LINE}文字）")
        captions.append({"i": k, "text": text, "rec_in": int(round(r0)), "rec_out": int(round(r1)),
                         "from_word": a, "to_word": b})
        covered.update(range(a, b + 1))

    # 字幕を隙間なくつなぐ: 次の字幕の直前まで表示を延ばす（0.5秒以内の隙間だけ）
    captions.sort(key=lambda c: c["rec_in"])
    if captions and captions[0]["rec_in"] <= int(0.5 * fps):
        captions[0]["rec_in"] = 0  # 冒頭の字幕は動画の頭から出す
    for i, cap in enumerate(captions):
        if i > 0 and cap["rec_in"] < captions[i - 1]["rec_out"]:
            raise EdlError(f"captions[{cap['i']}]「{cap['text']}」が前の字幕と重なっています")
        nxt = captions[i + 1]["rec_in"] if i + 1 < len(captions) else total_frames
        if nxt - cap["rec_out"] <= int(0.5 * fps):
            cap["rec_out"] = nxt
        dur = (cap["rec_out"] - cap["rec_in"]) / fps
        chars = len(cap["text"].replace("\n", ""))
        if dur < CAPTION_MIN_SEC:
            warnings.append(f"captions[{cap['i']}]「{cap['text']}」の表示が{dur:.2f}秒しかありません")
        elif chars / dur > CAPTION_MAX_CPS:
            warnings.append(f"captions[{cap['i']}]「{cap['text']}」が毎秒{chars / dur:.1f}文字です"
                            f"（上限{CAPTION_MAX_CPS}）")

    used = set()
    for c in clips:
        used.update(range(c["from_word"], c["to_word"] + 1))
    if edl.get("captions"):
        missing = sorted(used - covered)
        if missing:
            txt = "".join(words[i]["text"] for i in missing[:20])
            warnings.append(f"字幕が付いていない発話が{len(missing)}語あります（例: {txt}）")

    reframe = {"mode": "crop", "center_x": 0.5, **edl.get("reframe", {})}
    if reframe["mode"] not in ("crop", "fit"):
        raise EdlError(f"reframe.mode は crop か fit です: {reframe['mode']}")

    return {
        "source": src, "fps": fps, "out_w": OUT_W, "out_h": OUT_H,
        "title": edl.get("title", ""), "reframe": reframe,
        "clips": clips, "captions": captions,
        "total_frames": total_frames, "total_sec": round(total_sec, 3),
    }, warnings


def load_and_resolve(transcript_path, edl_path):
    return resolve(load_json(transcript_path), load_json(edl_path))
