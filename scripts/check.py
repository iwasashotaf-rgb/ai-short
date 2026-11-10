"""機械の検品: 採点表（guides/donki_short_guide.md 4章）のうち、数値と記録で判定できる項目を自動で採点する。

  .venv/bin/python scripts/check.py projects/案件名/01

review.py の最後にも自動で走る。先に build_b.py で書き出し、review.py で review.json を作っておく。

使い方の決まり（CLAUDE.md の B 型の章）:
  - 作る役は、このスクリプトの「必ず直す」が0件になるまで直す。この間は採点役を呼ばない
  - 採点役は「目視」の項目だけを採点し、「機械」の項目は check.json の点をそのまま写す

判定するもの:
  項目3 尺 / 項目4 声のテンポ / 項目5 カットの長さ / 項目6 止まった画と手ブレ / 項目9 テロップ
  項目2・10・11 の数値で判定できる部分（声の開始、商品名の位置と締めの問いかけ、寄せの倍率）
  採点表の外: 同じ素材の同じ区間を2回使う、同じ素材の繰り返し、寄りが続く・前半に引きがない

止まった画の測り方:
  プレビューを毎秒10コマ・90x160の灰色に縮め、テロップの帯（縦38〜62%）を除いて、
  0.5秒前のコマとの差（0〜255の平均）を取る。STILL 未満のコマが続く区間を「止まった画」とする。
  れんこん・プリンの試作で、採点役が止まった画と判断したカットは 1〜2、回す・持ち上げる画は 6 以上だった。
  初めは STILL を 3 にしたが、ポリスウェットの #9（袋の全景、人がわずかに動く、3〜4）を見逃し、
  採点役が「1.8秒止まって見える」と指摘したので 4.5 に上げた（2026-11-10）。
  SLOW 未満の区間は「ゆっくり動く画」として、目視で確かめる対象に挙げる。

出力: 同じフォルダの check.json。「必ず直す」が1件でもあれば終了コード 1。
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json  # noqa: E402

STILL = 4.5          # 0.5秒前のコマとの差がこれ未満なら止まった画
SLOW = 6.0           # これ未満ならゆっくり動く画（目視で確かめる）
STILL_MAX = 1.5      # 止まった画は、1カットでも続けても1.5秒以内（手引き2-5）
SHAKE_NG, SHAKE_OK = 1.2, 0.7
MW, MH, MR = 90, 160, 10   # 動きを測るコマの大きさと毎秒のコマ数


def chars(s):
    return len(re.sub(r"[\s　]", "", s))


def motion(video, total_frames_sec):
    """0.5秒前（カットの頭では0.5秒後）のコマとの差を、毎秒10コマで返す。"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                          f"fps={MR},scale={MW}:{MH},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, MH, MW).astype(np.float32)
    keep = np.ones(MH, bool)
    keep[int(MH * .38):int(MH * .62)] = False
    return f[:, keep, :]


def still_runs(frames, a, b):
    """カットの範囲 [a, b)（コマ番号）の中で、止まった画・ゆっくりの画が続く区間を返す。"""
    seg = frames[a:b]
    n = len(seg)
    if n == 0:
        return [], []
    k = MR // 2
    d = [float(np.abs(seg[i] - seg[i - k]).mean()) if i >= k else
         float(np.abs(seg[min(n - 1, i + k)] - seg[i]).mean()) for i in range(n)]

    def runs(th):
        out, s = [], None
        for i, v in enumerate(d + [1e9]):
            if v < th and s is None:
                s = i
            elif v >= th and s is not None:
                out.append((a + s, a + i))
                s = None
        return out
    return runs(STILL), runs(SLOW)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    args = ap.parse_args()
    out = Path(args.outdir)
    tl = load_json(out / "timeline.json")
    rv = load_json(out / "review.json")
    cl = load_json(out / "cutlist.json") if (out / "cutlist.json").exists() else {}
    narr = load_json(out / cl.get("narration", "narration/narration.json"))
    shots_path = out / cl.get("shots", "../shots/shots.json")
    catalog = {c["clip"]: c for c in load_json(shots_path)["clips"]} if shots_path.exists() else {}
    fps = tl["fps"]
    total = tl["total_sec"]
    clips = tl["clips"]
    srcs = tl.get("sources") or [tl["source"]]
    lens = [(c["rec_out"] - c["rec_in"]) / fps for c in clips]
    must, notes, items = [], [], {}

    def item(no, name, kind, score, basis, fix=None, look=None):
        items[no] = {"項目": name, "判定": kind, "点": score, "根拠": basis}
        if fix:
            items[no]["直すこと"] = fix
        if look:
            items[no]["目視で確かめること"] = look
        if kind == "機械" and score == 0:
            must.append(f"項目{no} {name}: {fix or basis}")

    # 項目2（一部）: 0秒から声とテロップ
    voice0 = rv.get("声の開始_秒", 0)
    cap0 = tl["captions"][0]["rec_in"] / fps if tl["captions"] else 99
    late = max(voice0, cap0)
    item(2, "つかみの言葉", "目視", None,
         f"声の開始 {voice0:.2f}秒、最初のテロップ {cap0:.2f}秒" + ("（0.3秒以上遅れる → 2点にはならない）" if late >= 0.3 else ""),
         look="冒頭の文が手引き2-1の型のどれに当たるか（字コンテの型の名前との対応は2-1の表）")

    # 項目3 尺
    s3 = 2 if 10 <= total <= 16 else 1 if 16 < total <= 18 else 0
    item(3, "尺", "機械", s3, f"{total:.2f}秒",
         None if s3 == 2 else "読みの文字数を削る（手引き2-3。予算は 目標秒 × 9.5字）")

    # 項目4 声のテンポ（文ごとの速さは -40dB、文の間は厳しい方の -35dB、文の中の間は -40dB で見る）
    rates = rv.get("話す速さ_文ごと_実測_-40dB", {})
    bad_rate = {k: v for k, v in rates.items() if not 9.0 <= v <= 10.5}
    gap = rv.get("文の間_実測_最長_-35dB", 0)
    inner = rv.get("文の中の間_実測_最長_-40dB", 0)
    ok_rate = not bad_rate
    ok_gap = gap <= 0.15 and inner < 0.3
    s4 = 2 if ok_rate and ok_gap else 1 if ok_rate or ok_gap else 0
    fix4 = []
    if bad_rate:
        fix4.append("範囲外の文の speed を変えて作り直す: " + "、".join(f"{k} 毎秒{v}" for k, v in bad_rate.items()))
    if gap > 0.15:
        fix4.append(f"文の間の最長 {gap:.3f}秒（-35dB）を0.15秒以下にする")
    if inner >= 0.3:
        fix4.append(f"文の中に {inner:.3f}秒の無音がある。その文を生成し直すか句の区切りを替える（手引き2-4: 0.3秒以上の無音を作らない）")
    item(4, "声のテンポ", "機械", s4,
         f"文ごとの速さ {min(rates.values(), default=0)}〜{max(rates.values(), default=0)}かな/秒、文の間の最長 {gap:.3f}秒、文の中の間の最長 {inner:.3f}秒",
         "。".join(fix4) or None)

    # 項目5 カットの長さ
    over3 = [f"#{i + 1}（{l:.2f}秒）" for i, l in enumerate(lens) if l > 3.0]
    mean = statistics.mean(lens)
    s5 = 2 if not over3 and mean <= 2.0 else 1 if len(over3) == 1 and mean <= 2.0 else 0
    item(5, "カットの長さ", "機械", s5, f"平均 {mean:.2f}秒、3秒超え {'・'.join(over3) or 'なし'}",
         None if s5 == 2 else "3秒を超えるカットを動作の区切りで分ける（手引き2-5）",
         look="3秒超えが1つのとき、画が動き続けている見せ場か" if s5 == 1 else None)

    # 項目6 止まった画と手ブレ
    shakes = rv.get("手ブレ_カットごと_%", {})
    shake_ng = [k for k, v in shakes.items() if v > SHAKE_NG]
    frames = motion(out / "preview.mp4", total)
    per_cut = []
    for i, c in enumerate(clips):
        a, b = int(round(c["rec_in"] / fps * MR)), min(int(round(c["rec_out"] / fps * MR)), len(frames))
        st, sl = still_runs(frames, a, b)
        per_cut.append({"a": a, "b": b, "still": st, "slow": sl})
    # 止まった区間をカットをまたいでつなぐ（前のカットの終わりまで止まり、次のカットの頭から止まる）
    spans = []
    for i, pc in enumerate(per_cut):
        for s, e in pc["still"]:
            if spans and spans[-1]["e"] == pc["a"] == s and spans[-1]["cut_end"]:
                spans[-1].update(e=e, cut_end=(e == pc["b"]))
                spans[-1]["cuts"].append(i + 1)
            else:
                spans.append({"s": s, "e": e, "cut_end": e == pc["b"], "cuts": [i + 1]})
    long_still = [x for x in spans if (x["e"] - x["s"]) / MR > STILL_MAX]
    slow = [(i + 1, (e - s) / MR) for i, pc in enumerate(per_cut) for s, e in pc["slow"]
            if (e - s) / MR > STILL_MAX]
    desc = [f"{'・'.join(f'#{n}' for n in x['cuts'])} {x['s'] / MR:.1f}〜{x['e'] / MR:.1f}秒（{(x['e'] - x['s']) / MR:.1f}秒）"
            for x in long_still]
    s6 = 0 if shake_ng or len(long_still) >= 2 else 1 if long_still else 2
    fix6 = []
    if shake_ng:
        fix6.append(f"手ブレが{SHAKE_NG}%を超えるカット {'・'.join(shake_ng)} を替える")
    if long_still:
        fix6.append("止まった画を1.5秒以内にする: " + "、".join(desc) + "。動きのある区間に替えるか、区間を短くする")
    item(6, "画の動きと手ブレ", "機械", s6,
         f"手ブレの最大 {rv.get('手ブレ_最大_%', 0)}%、1.5秒を超える止まった画 {'、'.join(desc) or 'なし'}",
         "。".join(fix6) or None,
         look=("ゆっくり動くだけの区間（止まって見えるか）: " + "、".join(f"#{n} {d:.1f}秒" for n, d in slow)) if slow else None)
    for k, v in shakes.items():
        if SHAKE_OK < v <= SHAKE_NG:
            notes.append(f"{k} の手ブレが {v}%（使えるが、0.7%以下の区間があれば替える）")

    # 項目9 テロップ
    product_ids = {k for k, v in cl.get("telops", {}).items() if "\n" in v}
    if cl.get("product_at"):
        product_ids.add(cl["product_at"])
    caps = tl["captions"]
    p9, bad9 = 2, []
    for c in caps:
        dur = (c["rec_out"] - c["rec_in"]) / fps
        ls = c["text"].split("\n")
        is_product = c.get("id") in product_ids
        n = max(chars(x) for x in ls)
        if len(ls) >= 3 or (len(ls) == 2 and not is_product) or n > 16:
            p9 = 0
            bad9.append(f"「{c['text']}」は{len(ls)}行・{n}字")
        elif not is_product and n >= 13:
            p9 = min(p9, 1)
            bad9.append(f"「{c['text']}」は{n}字（12字まで）")
        if dur < 0.5:
            p9 = min(p9, 1)
            bad9.append(f"「{c['text']}」は{dur:.2f}秒（0.5秒以上。隣の句とまとめる）")
        if not is_product and n < 4:
            notes.append(f"テロップ「{c['text']}」は{n}字（目安4字以上）")
    item(9, "テロップ", "機械", p9, "すべて基準内" if not bad9 else "、".join(bad9),
         None if p9 == 2 else "上のテロップの句を分ける・まとめる（手引き2-8）",
         look="名詞の途中で切っていないか")

    # 項目10（一部）: 商品名の位置と締めの問いかけ
    pcap = [c for c in caps if c.get("id") in product_ids]
    pos = pcap[0]["rec_in"] / fps / total if pcap else None
    last = narr["lines"][-1]["text"].strip()
    question = last.endswith(("？", "?"))
    basis10 = (f"商品名のテロップの開始 {pos:.0%}" if pos is not None else "商品名のテロップが見つからない（cutlist.json に product_at を書くと判定できる）")
    basis10 += f"、最後の文「{last}」" + ("は問いかけ" if question else "は問いかけではない")
    if pos is not None and not 0.6 <= pos <= 0.9:
        notes.append(f"商品名の開始が全長の{pos:.0%}にある（手引き2-9: 60〜90%）")
    item(10, "商品名と締め", "目視", None, basis10,
         look="商品名の画面で商品が大きく映っているか。問いかけでないときは字コンテ指定の締めか")

    # 項目11（一部）: 寄せの倍率
    flat = [s for cut in cl.get("cuts", []) for s in cut.get("shots", [])]
    zoomless = []
    if len(flat) == len(clips):
        for i, (s, c) in enumerate(zip(flat, clips)):
            if "subject" in s and c.get("zoom", 1.0) < 1.05:
                zoomless.append(f"#{i + 1}（{s['clip']}、zoom {c.get('zoom', 1.0)}）")
        n_subj = sum(1 for s in flat if "subject" in s or "zoom" in s)
    else:
        n_subj = None
        notes.append("cutlist.json のショット数と timeline.json のカット数が合わないので、寄せを判定していない")
    for z in zoomless:
        notes.append(f"{z} は subject を書いたが寄せが掛かっていない。主役の範囲が広すぎる（食品の手引き: subject は句が言う物だけ）")
    item(11, "主役の見え方（シズル）", "目視", None,
         f"subject か zoom を書いたショット {n_subj}本" + (f"、寄せが掛かっていない {'・'.join(zoomless)}" if zoomless else ""),
         look="主役が映るカットで、綺麗・量が見える・中央で大きいか（review.jpg）")

    # 採点表の外: 同じ素材の同じ区間・同じ素材の繰り返し・寄りと引き
    for i in range(len(clips)):
        for j in range(i + 1, len(clips)):
            a, b = clips[i], clips[j]
            if a.get("src", 0) == b.get("src", 0) and a["src_in"] < b["src_out"] and b["src_in"] < a["src_out"]:
                ov = (min(a["src_out"], b["src_out"]) - max(a["src_in"], b["src_in"])) / fps
                name = Path(srcs[a.get("src", 0)]["path"]).name
                must.append(f"#{i + 1} と #{j + 1} が {name} の同じ区間を {ov:.2f}秒 重ねて使っている（手引き2-5）")
    by_src = {}
    for i, c in enumerate(clips):
        by_src.setdefault(c.get("src", 0), []).append(i + 1)
    for k, ns in by_src.items():
        name = Path(srcs[k]["path"]).name
        if len(ns) >= 3:
            notes.append(f"{name} を {len(ns)}回使っている（#{'・#'.join(map(str, ns))}）。同じ構図が3回以上になっていないか")
        for x, y in zip(ns, ns[1:]):
            if y - x == 2:
                notes.append(f"#{x} と #{y} が同じ素材 {name} で、間に1カットしかない。同じ画の繰り返しに見えないか")

    def framing(i, c):
        name = Path(srcs[c.get("src", 0)]["path"]).name
        t = c["src_in"] / fps
        for seg in catalog.get(name, {}).get("segments", []):
            if seg.get("start", 0) <= t < seg.get("end", 0):
                return seg.get("framing", "")
        return ""
    fr = [framing(i, c) for i, c in enumerate(clips)]
    if any(fr):
        cut60 = [i for i, c in enumerate(clips) if c["rec_in"] / fps < total * 0.6]
        if not any("引" in fr[i] for i in cut60):
            notes.append("前半60%に引きの画がない（shots.json の framing で判定。手引き2-5）")
        run = 0
        for i, f in enumerate(fr):
            run = run + 1 if "寄" in f else 0
            if run == 4:
                notes.append(f"#{i - 2}〜 寄りが4カット以上続く（手引き2-5）")

    # 項目12 の材料: 依頼者のコメントと確認事項
    sb_path = out / cl.get("storyboard", "storyboard.json")
    qs = load_json(sb_path).get("questions", []) if sb_path.exists() else []

    res = {"必ず直す": must, "注意": notes, "項目": items, "依頼者への確認事項": len(qs),
           "機械の点の合計": sum(v["点"] for v in items.values() if v["判定"] == "機械"),
           "機械の項目の満点": 2 * sum(1 for v in items.values() if v["判定"] == "機械")}
    json.dump(res, open(out / "check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"機械の検品: {res['機械の点の合計']}/{res['機械の項目の満点']}点（項目3・4・5・6・9）")
    for no in sorted(items):
        v = items[no]
        pt = f"{v['点']}点" if v["点"] is not None else "目視"
        print(f"  {no:>2} {v['項目']}: {pt}  {v['根拠']}")
    print(f"必ず直す: {len(must)}件")
    for m in must:
        print(f"  - {m}")
    if notes:
        print(f"注意: {len(notes)}件")
        for m in notes:
            print(f"  - {m}")
    print(f"  → {out / 'check.json'}")
    sys.exit(1 if must else 0)


if __name__ == "__main__":
    main()
