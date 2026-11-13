"""C インタビュー型: Claude が書いたカット表（*.interview.json）を検証し、タイムラインに変換する。

収録した話し声をそのまま使う（ナレーションを作らない）点は A 型と同じで、次が増えている:
  - 素材が複数でもよい。素材ごとに文字起こしを持ち、カットごとにどの素材のどの語を使うか選ぶ
    （行ごとにテイクが分かれている収録で使う）
  - 話者で区間を絞る（聞き手の声を外す）
  - 字コンテの行（row）とカットを対応づけ、映像指示をシーケンスのマーカーに書く
  - 質問テロップ（カットの間だけ画面上部に出す）
  - インサート（声を流したまま、映像だけ別素材に差し替える）
  - カットごとの寄せ（zoom / center_x / center_y、または subject から計算）

カット表の形式（単語番号はその素材の transcript の words[].i）:
{
  "transcripts": "transcripts",                # 素材が複数: このフォルダの *.json を読む。名前はファイル名
  "transcript": "../transcript.json",          # 素材が1本: こちらを書く（src は省略できる）
  "storyboard": "../jikonte/01.raw.json",      # 任意。行の映像指示をマーカーに書く
  "shots": "../shots/shots.json",              # 任意。インサートを素材名で指定したいとき
  "title": "",                                 # 全編に出す見出し（任意）
  "target_sec": [20, 60],
  "pad": {"head": 0.05, "tail": 0.10},          # 各カットの前後に残す秒数（後ろは max_tail が上限）
  "trim_silence": {"over": 0.24, "keep": 0.10},  # 語と語の間が over を超えたら keep まで詰める
  "reframe": {"mode": "crop", "center_x": 0.5},
  "keep_speakers": ["S1"],                     # 残す話者。これ以外の声が入ると注意が出る
  "caption_style": {"center_y": 1380},         # プレビューの字幕の位置・大きさ（任意）
  "cuts": [
    {"row": "R01", "src": "2610_hutaba-1", "from_word": 3, "to_word": 14, "note": "つかみ",
     "question": "一番人気は\\nどれですか？",
     "zoom": 1.2, "center_x": 0.52, "center_y": 0.42,
     "subject": [0.30, 0.08, 0.68, 0.62],
     "captions": [{"from_word": 3, "to_word": 9, "text": "イチオシの漫画"}],
     "inserts": [{"clip": "IMG_2000.MOV", "from_word": 10, "to_word": 14, "in": 3.0}]}
  ],
  "captions": [
    {"src": "2610_hutaba-1", "from_word": 3, "to_word": 9, "text": "イチオシの漫画"}
  ]
}
  - 字幕はカットの中（cuts[].captions、src はそのカットのもの）か、まとめて（captions[]、素材が複数なら src を書く）
  - subject は主役（話者の顔・手元・商品）の範囲 [左, 上, 右, 下]（素材の画面に対する割合）。
    zoom を書かずに subject を書くと、寄せの倍率と位置を計算する（framing.py）
  - インサートは "from_word"/"to_word"（そのカットの中の語）か、"at"/"dur"（カットの頭からの秒数）で置く。
    声は切れずに続く。インサートの "in" は差し替える素材の開始秒
  - captions の text は誤認識を直して書く。改行は \\n
  - 単語の時刻がずれている区間は、カットに "in" / "out"（素材上の秒）を書いて範囲を上書きする。
    字幕の時刻は単語から計算するので、上書きした範囲に収まる語だけに字幕を付ける
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, probe  # noqa: E402
from timeline import clip_range  # noqa: E402

OUT_W, OUT_H = 1080, 1920
CAPTION_MAX_LINE = 13        # 字幕1行の全角文字数の目安
CAPTION_MAX_LINES = 2
CAPTION_MAX_CPS = 9.0        # 字幕1枚の毎秒文字数の上限
CAPTION_MIN_SEC = 0.6
QUESTION_MAX_LINE = 16       # 質問テロップ1行の文字数の目安
QUESTION_MAX_LINES = 2
MIN_INSERT_SEC = 0.5
MIN_SHOT_SEC = 0.5           # インサートの前後に残る素材の切れ端がこれより短ければ、インサートを伸ばす
MIN_CUT_SEC = 0.8
MIN_PIECE_SEC = 0.15         # 無音を詰めたときに残る切れ端がこれより短ければ捨てる
DEFAULT_PAD = {"head": 0.05, "tail": 0.10}
DEFAULT_TRIM = {"over": 0.24, "keep": 0.10}


class CutError(Exception):
    pass


def lossless_zoom(sw, sh):
    """画質を落とさずに寄せられる倍率の上限（9:16 に切り抜いて画面を埋める縮小率の逆数）。"""
    if not sw or not sh:
        return 2.0
    return max(1.0, min(sw / OUT_W, sh / OUT_H))


def _framing(s, src, warnings, where):
    """ショットの寄せ（zoom / center_x / center_y）を決める。subject があれば計算する。"""
    sw, sh = src["width"], src["height"]
    zmax = lossless_zoom(sw, sh)
    if "subject" in s and "zoom" not in s:
        from framing import frame_subject
        base_w = min(1.0, (OUT_W / OUT_H) / (sw / sh)) if sw and sh else 1.0
        try:
            s = {**s, **frame_subject(s["subject"], max(zmax, 1.0), base_w)}
        except ValueError as e:
            raise CutError(f"{where}: {e}")
    z = float(s.get("zoom", 1.0))
    if z < 1.0:
        raise CutError(f"{where}: zoom は1以上にしてください（{z}）")
    if z > zmax + 0.01:
        warnings.append(f"{where}: zoom {z} は素材の解像度を超えるので画質が落ちます")
    return {"zoom": z, "center_x": float(s.get("center_x", 0.5)), "center_y": float(s.get("center_y", 0.5))}


def _load_speech(cl, base):
    """話し声の素材（名前 → 文字起こし）を読む。transcripts はフォルダ、transcript は1本。"""
    speech = {}
    if cl.get("transcripts"):
        d = base / cl["transcripts"]
        if not d.is_dir():
            raise CutError(f"transcripts のフォルダがありません: {d}")
        for p in sorted(d.glob("*.json")):
            speech[p.stem] = load_json(p)
    if cl.get("transcript"):
        t = load_json(base / cl["transcript"])
        speech[Path(cl["transcript"]).stem] = t
    if isinstance(cl.get("sources"), dict):
        for k, v in cl["sources"].items():
            speech[k] = load_json(base / v)
    if not speech:
        raise CutError("transcripts（フォルダ）か transcript（1本）を書いてください")
    return speech


def silence_drops(transcript, a, b, f0, f1, fps, trim):
    """カットの中の長い間から、抜き取るフレーム範囲を作る。

    **単語と単語の間しか詰めない。** 小声の語は無音より静かなことがあるので
    （「さらっと」が -47dB で、同じ素材の部屋の音 -45dB より静かだった。2026-11-13）、
    音量だけを見て詰めると語を削ってしまう。
    さらに、文字起こしが落とした息や笑い声を消さないよう、無音と重なる部分だけを詰める。
    単語の境目の誤差を見込んで、前後に margin 秒を残す。
    """
    if not trim:
        return []
    over, keep = float(trim.get("over", 0.24)), float(trim.get("keep", 0.10))
    margin = float(trim.get("margin", 0.05))
    words = transcript["words"]
    sil = transcript.get("silences", [])
    drops = []
    for i in range(a, b):
        g0 = max(words[i]["end"] + margin, f0 / fps)
        g1 = min(words[i + 1]["start"] - margin, f1 / fps)
        if g1 - g0 <= over:
            continue
        quiet = [(max(g0, z["start"]), min(g1, z["end"])) for z in sil]
        quiet = [(x, y) for x, y in quiet if y > x]
        if not quiet:
            continue
        x, y = max(quiet, key=lambda q: q[1] - q[0])
        if y - x <= over:
            continue
        cut = (y - x) - keep
        mid = (x + y) / 2
        drops.append((int(round((mid - cut / 2) * fps)), int(round((mid + cut / 2) * fps))))
    return drops


def plan_pieces(f0, f1, drops, min_len=0):
    """抜き取る範囲を除いた、残す範囲の一覧。min_len に満たない切れ端は捨てる（無音の端なので）。"""
    pieces, pos = [], f0
    for a, b in sorted(drops):
        a, b = max(a, f0), min(b, f1)
        if b <= a or a <= pos:
            pos = max(pos, b)
            continue
        pieces.append((pos, a))
        pos = b
    if pos < f1:
        pieces.append((pos, f1))
    kept = [q for q in pieces if q[1] - q[0] >= min_len]
    return kept or [max(pieces, key=lambda q: q[1] - q[0])] if pieces else [(f0, f1)]


def resolve(cut_path):
    cut_path = Path(cut_path)
    base = cut_path.parent
    cl = load_json(cut_path)
    speech = _load_speech(cl, base)
    pad = {**DEFAULT_PAD, **cl.get("pad", {})}
    keep = cl.get("keep_speakers")
    warnings = []

    if not cl.get("cuts"):
        raise CutError("cuts が空です")

    # 使う素材だけを並べる（0 番は最初のカットの素材。フレームレートの基準にする）
    order = []
    for c in cl["cuts"]:
        nm = c.get("src")
        if nm is None:
            if len(speech) != 1:
                raise CutError(f"素材が{len(speech)}本あるので、カットに src を書いてください")
            nm = next(iter(speech))
        if nm not in speech:
            raise CutError(f"src「{nm}」の文字起こしがありません（ある: {'/'.join(sorted(speech))}）")
        if nm not in order:
            order.append(nm)
    fps = speech[order[0]]["source"]["fps"]
    for nm in order:
        f = speech[nm]["source"]["fps"]
        if abs(f - fps) > 0.01:
            raise CutError(f"素材のフレームレートがそろっていません: {order[0]} は {fps:.3f}fps、"
                           f"{nm} は {f:.3f}fps")

    sources, src_idx = [], {}
    for nm in order:
        src_idx[speech[nm]["source"]["path"]] = len(sources)
        sources.append(dict(speech[nm]["source"]))
    name_idx = {nm: src_idx[speech[nm]["source"]["path"]] for nm in order}

    sb = {}
    if cl.get("storyboard"):
        raw = load_json(base / cl["storyboard"])
        for r in raw.get("cuts", raw.get("rows", [])):
            sb[r.get("row")] = r
    catalog = {}
    if cl.get("shots"):
        catalog = {c["clip"]: c for c in load_json(base / cl["shots"])["clips"]}

    def insert_src(clip, where):
        if clip in catalog:
            path = catalog[clip]["path"]
        else:
            p = Path(clip)
            path = str((p if p.is_absolute() else base / p).resolve())
            if not Path(path).exists():
                raise CutError(f"{where}: インサートの素材が見つかりません（{clip}）")
        if path not in src_idx:
            info = probe(path)
            # カット点は話し声の素材のフレームレートで数えるので、違うと数フレームずれる
            if abs(info["fps"] - fps) > 0.01:
                warnings.append(f"{where}: {Path(path).name} は {info['fps']:.3f}fps で、"
                                f"話し声の素材（{fps:.3f}fps）と違います。"
                                f"Premiere 上でインサートの長さが数フレームずれることがあります")
            src_idx[path] = len(sources)
            sources.append(info)
        return src_idx[path]

    clips, audio, titles, markers, cut_map = [], [], [], [], []
    rec = 0
    used_ranges = {}
    prev_look = None
    for k, c in enumerate(cl["cuts"]):
        where = f"cuts[{k}]" + (f"（{c['row']}）" if c.get("row") else "")
        nm = c.get("src") or order[0]
        t = speech[nm]
        words = t["words"]
        nw = len(words)
        ksrc = name_idx[nm]
        info = sources[ksrc]

        a, b = c.get("from_word"), c.get("to_word")
        if a is None or b is None or not (0 <= a <= b < nw):
            raise CutError(f"{where} の単語番号が範囲外です: {a}-{b}（{nm} の単語数 {nw}）")
        for a2, b2, k2 in used_ranges.get(nm, []):
            if a <= b2 and a2 <= b:
                raise CutError(f"{where} が cuts[{k2}] と同じ区間（{nm} の {a2}-{b2}）を使っています")
        used_ranges.setdefault(nm, []).append((a, b, k))

        f0, f1 = clip_range(t, a, b, pad)
        # カットの後ろに残る無音に上限を付ける（無音へ寄せると最大1秒伸びることがある）
        max_tail = float(pad.get("max_tail", pad["tail"] + 0.06))
        f1 = max(min(f1, int(round((words[b]["end"] + max_tail) * fps))), f0 + 1)
        # 文字起こしの単語の時刻がずれているときは、素材上の秒で上書きできる
        if c.get("in") is not None:
            f0 = int(round(float(c["in"]) * fps))
        if c.get("out") is not None:
            f1 = int(round(float(c["out"]) * fps))
        if f0 < 0 or f1 > int(info["duration"] * fps):
            raise CutError(f"{where} の in/out が素材の外です（素材 {info['duration']:.2f}秒）")
        if f1 <= f0:
            raise CutError(f"{where} の長さが0フレームです")
        # カットの中の長い無音を詰める（抜き取った残りが pieces）
        trim = c.get("trim_silence", cl.get("trim_silence", DEFAULT_TRIM))
        pieces = plan_pieces(f0, f1, silence_drops(t, a, b, f0, f1, fps, trim),
                             int(MIN_PIECE_SEC * fps))
        span = sum(q - pq for pq, q in pieces)
        cut_sec = (f1 - f0 - span) / fps
        if cut_sec > 0.01:
            warnings.append(f"{where} の中の無音を {cut_sec:.2f}秒 詰めました")
        if span < MIN_CUT_SEC * fps:
            warnings.append(f"{where} が {span / fps:.2f}秒しかありません")

        spks = sorted({words[i].get("speaker") for i in range(a, b + 1)} - {None})
        if keep is not None and spks:
            extra = [s for s in spks if s not in keep]
            if extra:
                txt = "".join(words[i]["text"] for i in range(a, b + 1)
                              if words[i].get("speaker") in extra)[:30]
                warnings.append(f"{where} に残さない話者（{'/'.join(extra)}）の声が入っています: {txt}")
        elif len(spks) > 1:
            warnings.append(f"{where} に{len(spks)}人の声（{'/'.join(spks)}）が入っています")

        look = _framing(c, info, warnings, where)
        r0 = rec
        text = "".join(w["text"] for w in words[a:b + 1])

        # インサートの位置（カットの中のフレーム範囲）を決める
        ins = []
        for j, s in enumerate(c.get("inserts", [])):
            w2 = f"{where} の inserts[{j}]"
            if s.get("from_word") is not None:
                ia, ib = s["from_word"], s.get("to_word", s["from_word"])
                if not (a <= ia <= ib <= b):
                    raise CutError(f"{w2} の単語番号 {ia}-{ib} がこのカットの {a}-{b} の外です")
                p0 = int(round(words[ia]["start"] * fps))
                p1 = int(round(words[ib]["end"] * fps))
            elif s.get("at") is not None:
                p0 = f0 + int(round(float(s["at"]) * fps))
                p1 = p0 + int(round(float(s.get("dur", 1.5)) * fps))
            else:
                raise CutError(f"{w2} は from_word か at で位置を書いてください")
            p0, p1 = max(p0, f0), min(p1, f1)
            if p1 - p0 < MIN_INSERT_SEC * fps:
                raise CutError(f"{w2} の長さが {max(p1 - p0, 0) / fps:.2f}秒 です"
                               f"（{MIN_INSERT_SEC}秒以上にしてください）")
            ki = insert_src(s["clip"], w2)
            ins.append({"p0": p0, "p1": p1, "src": ki,
                        "src_in": int(round(float(s.get("in", 0)) * fps)),
                        "look": _framing(s, sources[ki], warnings, w2),
                        "name": Path(sources[ki]["path"]).name})
        ins.sort(key=lambda x: x["p0"])
        for x, y in zip(ins, ins[1:]):
            if y["p0"] < x["p1"]:
                raise CutError(f"{where} のインサートが重なっています")
        # インサートの前後に短い切れ端が残ると画が一瞬だけ戻るので、インサートを伸ばして飲み込む
        if ins:
            if ins[0]["p0"] - f0 < MIN_SHOT_SEC * fps:
                ins[0]["p0"] = f0
            if f1 - ins[-1]["p1"] < MIN_SHOT_SEC * fps:
                ins[-1]["p1"] = f1
            for x, y in zip(ins, ins[1:]):
                if y["p0"] - x["p1"] < MIN_SHOT_SEC * fps:
                    y["p0"] = x["p1"]
            for x in ins:
                need = x["src_in"] + x["p1"] - x["p0"]
                have = int(sources[x["src"]]["duration"] * fps)
                if need > have:
                    raise CutError(f"{where} の {x['name']}: インサートに"
                                   f"{(x['p1'] - x['p0']) / fps:.2f}秒 必要ですが、"
                                   f"in {x['src_in'] / fps:.2f}秒 からでは素材が足りません"
                                   f"（素材 {sources[x['src']]['duration']:.2f}秒）")

        # 残す範囲を出力の時間に並べる
        laid, r = [], r0
        for pa, pb in pieces:
            laid.append((pa, pb, r))
            r += pb - pa

        # 映像: 話し声の素材を、インサートのところだけ差し替える
        for pa, pb, pr in laid:
            pos = pa
            for x in ins:
                xa, xb = max(x["p0"], pa), min(x["p1"], pb)
                if xb <= xa:
                    continue
                if xa > pos:
                    clips.append({"i": len(clips), "src": ksrc, "src_in": pos, "src_out": xa,
                                  "rec_in": pr + pos - pa, "rec_out": pr + xa - pa, **look,
                                  "note": c.get("note", ""), "text": text[:40]})
                clips.append({"i": len(clips), "src": x["src"],
                              "src_in": x["src_in"] + xa - x["p0"],
                              "src_out": x["src_in"] + xb - x["p0"],
                              "rec_in": pr + xa - pa, "rec_out": pr + xb - pa, **x["look"],
                              "note": f"インサート {x['name']}", "text": x["name"]})
                pos = xb
            if pos < pb:
                clips.append({"i": len(clips), "src": ksrc, "src_in": pos, "src_out": pb,
                              "rec_in": pr + pos - pa, "rec_out": pr + pb - pa, **look,
                              "note": c.get("note", ""), "text": text[:40]})

        # 音声: 残す範囲ごとに1本。インサートでは切れない
        for pa, pb, pr in laid:
            whole = [x for x in clips if x["src"] == ksrc and x["rec_in"] == pr
                     and x["rec_out"] == pr + pb - pa]
            audio.append({"src": ksrc, "src_in": pa, "src_out": pb, "rec_in": pr,
                          "rec_out": pr + pb - pa, "link": whole[0]["i"] if whole else None})
        cut_map.append({"src_name": nm, "from_word": a, "to_word": b, "pieces": laid,
                        "rec_in": r0, "rec_out": r0 + span})
        if c.get("question"):
            q = c["question"]
            lines = q.split("\n")
            if len(lines) > QUESTION_MAX_LINES:
                warnings.append(f"{where} の質問テロップが{len(lines)}行あります（上限{QUESTION_MAX_LINES}行）")
            for ln in lines:
                if len(ln) > QUESTION_MAX_LINE:
                    warnings.append(f"{where} の質問テロップの行「{ln}」が{len(ln)}文字です"
                                    f"（目安{QUESTION_MAX_LINE}文字）")
            if titles and titles[-1]["text"] == q and titles[-1]["rec_out"] == r0:
                titles[-1]["rec_out"] = r0 + span       # 同じ質問が続くカットはつなげる
            else:
                titles.append({"i": len(titles), "text": q, "rec_in": r0, "rec_out": r0 + span})

        row = c.get("row")
        visual = (sb.get(row) or {}).get("visual", "")
        mname = " ".join(x for x in [row, c.get("note", "")] if x) or f"カット{k + 1}"
        markers.append({"rec_in": r0, "name": mname,
                        "comment": f"字コンテ {row} の映像指示: {visual}" if visual else text})

        # 同じ素材を続けて切ると画が飛ぶので、素材か寄せを変えたか見る
        this_look = (ksrc, look["zoom"], look["center_x"], look["center_y"])
        if prev_look is not None and this_look == prev_look and not ins:
            warnings.append(f"{where} は前のカットと同じ素材・同じ寄せです（つなぐと画が飛ぶので"
                            f"zoom か center を変える、別のテイクにする、インサートを入れる）")
        prev_look = this_look
        rec += span
    total_frames = rec
    total_sec = total_frames / fps

    lo_sec, hi_sec = cl.get("target_sec", [15, 60])
    if not (lo_sec <= total_sec <= hi_sec):
        warnings.append(f"尺 {total_sec:.1f}秒 が目標 {lo_sec}〜{hi_sec}秒 から外れています")

    # 字幕: 素材上の単語範囲を、出力タイムライン上の時刻へ写す
    def word_to_rec(src_name, wi, edge):
        w = speech[src_name]["words"]
        if not (0 <= wi < len(w)):
            return None
        t = w[wi]["start"] if edge == "start" else w[wi]["end"]
        f = t * fps
        for m in cut_map:
            if m["src_name"] != src_name or not (m["from_word"] <= wi <= m["to_word"]):
                continue
            prev = m["rec_in"]
            for pa, pb, pr in m["pieces"]:   # 詰めた無音のぶんを飛ばしながら探す
                if f < pa:
                    return pr               # 抜き取った無音の中なら、次の範囲の頭
                if f <= pb:
                    return pr + (f - pa)
                prev = pr + (pb - pa)
            return prev
        return None

    raw_caps = [(cap.get("src") or order[0], cap) for cap in cl.get("captions", [])]
    for k, c in enumerate(cl["cuts"]):
        nm = c.get("src") or order[0]
        for cap in c.get("captions", []):
            raw_caps.append((cap.get("src") or nm, cap))

    captions = []
    covered = set()
    for k, (nm, cap) in enumerate(raw_caps):
        a, b, text = cap.get("from_word"), cap.get("to_word"), cap.get("text", "").strip()
        if a is None or b is None or a > b or not text:
            raise CutError(f"captions[{k}] の単語番号か本文が不正です")
        r0, r1 = word_to_rec(nm, a, "start"), word_to_rec(nm, b, "end")
        if r0 is None or r1 is None:
            raise CutError(f"captions[{k}]「{text}」の先頭か末尾の語（{nm} の {a}-{b}）が"
                           f"カットで使われていません")
        lines = text.split("\n")
        if len(lines) > CAPTION_MAX_LINES:
            warnings.append(f"captions[{k}]「{text}」が{len(lines)}行あります（上限{CAPTION_MAX_LINES}行）")
        for ln in lines:
            if len(ln) > CAPTION_MAX_LINE:
                warnings.append(f"captions[{k}] の行「{ln}」が{len(ln)}文字です（目安{CAPTION_MAX_LINE}文字）")
        captions.append({"i": k, "text": text, "rec_in": int(round(r0)), "rec_out": int(round(r1)),
                         "src_name": nm, "from_word": a, "to_word": b})
        covered.add((nm, a, b))

    captions.sort(key=lambda c: c["rec_in"])
    for i, cap in enumerate(captions):
        if i > 0 and cap["rec_in"] < captions[i - 1]["rec_out"]:
            raise CutError(f"captions[{cap['i']}]「{cap['text']}」が前の字幕と重なっています")
    # 字幕はそのカットの頭から出す（前の字幕にかぶらない範囲で）。語の始まりを待つと画と字幕がずれる
    for i, cap in enumerate(captions):
        m = next((m for m in cut_map if m["rec_in"] <= cap["rec_in"] < m["rec_out"]), None)
        if m:
            cap["rec_in"] = max(m["rec_in"], captions[i - 1]["rec_out"] if i else 0)
    if captions and captions[0]["rec_in"] <= int(0.5 * fps):
        captions[0]["rec_in"] = 0
    for i, cap in enumerate(captions):
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

    if raw_caps:
        miss = []
        for m in cut_map:
            cov = set()
            for nm, a, b in covered:
                if nm == m["src_name"]:
                    cov.update(range(a, b + 1))
            rest = [i for i in range(m["from_word"], m["to_word"] + 1) if i not in cov]
            if rest:
                w = speech[m["src_name"]]["words"]
                miss.append("".join(w[i]["text"] for i in rest[:12]))
        if miss:
            warnings.append(f"字幕が付いていない発話があります（例: {' / '.join(miss[:3])}）")

    reframe = {"mode": "crop", "center_x": 0.5, **cl.get("reframe", {})}
    if reframe["mode"] not in ("crop", "fit"):
        raise CutError(f"reframe.mode は crop か fit です: {reframe['mode']}")
    if reframe["mode"] == "fit" and len(sources) > 1:
        raise CutError("素材が複数のときの reframe.mode は crop だけです")

    return {
        "type": "C", "source": dict(sources[0]), "fps": fps, "out_w": OUT_W, "out_h": OUT_H,
        "title": cl.get("title", ""), "titles": titles, "reframe": reframe,
        "sources": sources, "clips": clips, "audio": audio, "captions": captions,
        "markers": markers, "total_frames": total_frames, "total_sec": round(total_sec, 3),
        "caption_style": cl.get("caption_style", {}),
    }, warnings
