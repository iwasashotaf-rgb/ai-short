"""工程1b（インタビュー型）: 発話ごとに話者を見分けて transcript に書き足す。

  .venv/bin/python scripts/speakers.py 素材.mp4 projects/案件名          # 素材1本
  .venv/bin/python scripts/speakers.py projects/案件名                    # transcripts/*.json を全部（素材が複数）
  .venv/bin/python scripts/speakers.py projects/案件名 --n 3              # 3人として分ける
  .venv/bin/python scripts/speakers.py projects/案件名 --by motion                     # 画で分ける（下記）
  .venv/bin/python scripts/speakers.py projects/案件名 --fix "2610_hutaba-7:0-5=S2"   # 手で直す

分け方は2つある。

**--by motion（カメラに1人だけ映っている収録はこちら）**
  声が出ている区間で、画の中の顔のあたりが動いているかを測る。動いていなければ、その声はカメラに
  映っていない人（撮影側・聞き手）のもの。映っている人を S1、それ以外を S2 にする。
  収録のたびにマイクからの距離が変わっても効くので、音で分けるより確実。
  画が動いているかを測れない素材（素材ごとに映る人が違う、2人とも映っている）には使えない。

**--by audio（既定）**
  発話ごとに、声の高さ（基本周波数）・音量・帯域ごとの強さ（24帯域の平均）を測り、
  帯域ごとの強さを並べた値で k-means に掛けて n 群に分ける。話した時間が長い群から S1, S2 … と付ける。
  声の高さだけでは同性の2人を分けられないが、帯域ごとの強さにはマイクからの距離と声の質が出る。
  ただし、演者が小声で話した区間を「別の人」と間違えることがある（2026-11-13 に実際に起きた）。

**素材が複数のときは、全素材の発話をまとめて分ける。** 同じ人が複数の素材に出るので、素材ごとに分けると合わない。

できること / できないこと:
  - audio: 群の分かれ方が弱いとき（群の間の距離が群の中の散らばりより小さいとき）は「要確認」を出す
  - motion: 動きの基準が取れない素材は audio に切り替えて、その素材名を出す
  - 1つの発話に2人の声が混ざると、長く話した方に付く
  - 最後は Claude が transcript.txt を読んで、誰の台詞かで確かめる。違っていれば --fix で直す

出力: transcript の segments[].speaker と words[].speaker、transcript.txt（話者の列が増える）、
      speakers.json（発話ごとの測定値と群の中心）。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_tc, load_json, save_json  # noqa: E402
from transcribe import write_txt  # noqa: E402

SR = 16000
FMIN, FMAX = 70.0, 400.0     # 探す声の高さの範囲（Hz）
FRAME, HOP = 0.040, 0.020    # 1コマの長さと間隔（秒）
VOICED_RMS = 0.012           # これ未満のコマは声が出ていないとみなす
VOICED_AC = 0.30             # 自己相関の山がこれ未満なら声の高さを決めない
MIN_SEG_SEC = 0.25           # これより短い発話は測らない（前後の発話から推定する）
BANDS = 24                   # 声の特徴を測る帯域の数（100Hz〜7000Hz を対数で等分）
WEAK_SPLIT = 1.0             # 群の間の距離 ÷ 群の中の散らばり。これ未満なら分かれ方が弱い
MOTION_FPS = 10              # 画の動きを測るコマ数（毎秒）
MOTION_BAND = (0.10, 0.35)   # 顔のあたり（画面の上からの割合）
MOTION_RATIO = 0.5           # その素材で話しているときの動きの半分を下回れば「映っていない人」
MOTION_FLOOR = 1.0           # 動きの基準がこれ未満なら、画では判定できない


def decode(path):
    """素材の音声を 16kHz モノラルの波形（-1〜1）にする。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                        "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"音声を取り出せません: {path}\n{r.stderr.decode('utf-8', 'ignore')[:400]}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def motion_series(path):
    """素材を毎秒 MOTION_FPS コマの小さな灰色画像にして、顔のあたりのコマ間の差を返す。"""
    w, h = 90, 160
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                        f"fps={MOTION_FPS},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(a) // (w * h)
    if n < 3:
        return None
    a = a[:n * w * h].reshape(n, h, w).astype(np.float32)
    band = a[:, int(h * MOTION_BAND[0]):int(h * MOTION_BAND[1]), int(w * 0.2):int(w * 0.8)]
    return np.abs(np.diff(band, axis=0)).mean(axis=(1, 2))


def seg_motion(m, t0, t1):
    if m is None:
        return None
    i, j = int(t0 * MOTION_FPS), max(int(t1 * MOTION_FPS), int(t0 * MOTION_FPS) + 1)
    seg = m[min(i, len(m) - 1):min(j, len(m))]
    return round(float(seg.mean()), 2) if len(seg) else None


def band_edges(nfft):
    """24帯域（100Hz〜7000Hz を対数で等分）の、スペクトルの要素番号の境目。"""
    freqs = np.linspace(0, SR / 2, nfft // 2 + 1)
    edges = np.geomspace(100.0, 7000.0, BANDS + 1)
    idx = np.searchsorted(freqs, edges)
    return [(max(int(idx[k]), 1), max(int(idx[k + 1]), int(idx[k]) + 1)) for k in range(BANDS)]


def measure(x):
    """波形から（声の高さの中央値 Hz, 音量 dBFS, 声が出ていたコマ数, 帯域ごとの強さ）を返す。"""
    n_fr, n_hop = int(FRAME * SR), int(HOP * SR)
    if len(x) < n_fr:
        return None, -120.0, 0, None
    nfft = 1 << (2 * n_fr - 1).bit_length()
    lo, hi = int(SR / FMAX), int(SR / FMIN)
    bands = band_edges(nfft)
    f0s, acc, n_acc = [], np.zeros(BANDS), 0
    for i in range(0, len(x) - n_fr + 1, n_hop):
        f = x[i:i + n_fr]
        if float(np.sqrt(np.mean(f * f))) < VOICED_RMS:
            continue
        f = f - f.mean()
        sp = np.fft.rfft(f, nfft)
        pw = (sp * np.conj(sp)).real
        acc += [float(pw[a:b].mean()) for a, b in bands]
        n_acc += 1
        ac = np.fft.irfft(sp * np.conj(sp), nfft)[:n_fr]
        if ac[0] <= 0:
            continue
        k = int(np.argmax(ac[lo:hi])) + lo
        if ac[k] / ac[0] < VOICED_AC:
            continue
        f0s.append(SR / k)
    rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    db = 20 * np.log10(max(rms, 1e-6))
    mel = None
    if n_acc:
        mel = [round(float(v), 2) for v in 10 * np.log10(np.maximum(acc / n_acc, 1e-12))]
    return (float(np.median(f0s)) if f0s else None), round(float(db), 1), len(f0s), mel


def kmeans(vecs, weights, n, iters=60):
    """多次元の k-means。vecs は帯域ごとの強さ、weights は発話の長さ。

    戻り値は（発話ごとの群番号, 群の中心, 分かれ方の強さ）。
    分かれ方の強さ = 群の中心どうしの最短距離 ÷ 群の中の散らばりの平均。
    """
    v = np.asarray(vecs, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    # 一番ばらつく向きで初期の中心を置く（音量の差がここに出る）
    proj = v @ np.linalg.svd(v - v.mean(0), full_matrices=False)[2][0]
    c = v[np.argsort(proj)[np.clip(((np.arange(n) + 0.5) / n * len(v)).astype(int), 0, len(v) - 1)]]
    lab = np.zeros(len(v), dtype=int)
    for _ in range(iters):
        d = ((v[:, None, :] - c[None, :, :]) ** 2).sum(axis=2)
        new_lab = np.argmin(d, axis=1)
        new_c = c.copy()
        for k in range(n):
            m = new_lab == k
            if m.any() and w[m].sum() > 0:
                new_c[k] = (v[m] * w[m, None]).sum(0) / w[m].sum()
        if (new_lab == lab).all() and np.allclose(new_c, c):
            c, lab = new_c, new_lab
            break
        c, lab = new_c, new_lab
    spread = []
    for k in range(n):
        m = lab == k
        if m.any():
            spread.append(float(np.sqrt(((v[m] - c[k]) ** 2).sum(axis=1)).mean()))
    gap = min(float(np.sqrt(((c[i] - c[j]) ** 2).sum()))
              for i in range(n) for j in range(i + 1, n)) if n > 1 else 0.0
    strength = gap / max(float(np.mean(spread)), 1e-9) if spread else 0.0
    # 話した時間が長い群から S1, S2 …
    total = [w[lab == k].sum() for k in range(n)]
    order = np.argsort(-np.asarray(total))
    remap = {int(o): i for i, o in enumerate(order)}
    return [remap[int(k)] for k in lab], [c[int(o)].tolist() for o in order], strength


def parse_fix(text):
    """"12,20-24=S2" や "素材名:0-5=S2" を {(素材名 or None, 発話番号): 話者} にする。"""
    out = {}
    for part in text.split():
        body, _, spk = part.rpartition("=")
        if not body or not spk:
            raise SystemExit(f"--fix の書き方が違います: {part}（例 12,20-24=S2 / 素材名:0-5=S2）")
        name = None
        if ":" in body:
            name, body = body.split(":", 1)
        for r in body.split(","):
            m = re.fullmatch(r"(\d+)(?:-(\d+))?", r.strip())
            if not m:
                raise SystemExit(f"--fix の発話番号が読めません: {r}")
            a = int(m.group(1))
            b = int(m.group(2) or a)
            for i in range(a, b + 1):
                out[(name, i)] = spk.strip()
    return out


def collect(work):
    """(素材名, transcript のパス, 中身) の一覧と、素材が複数かどうか。"""
    d = work / "transcripts"
    if d.is_dir():
        items = [(q.stem, q, load_json(q)) for q in sorted(d.glob("*.json"))]
        if items:
            return items, True
    q = work / "transcript.json"
    if not q.exists():
        raise SystemExit(f"文字起こしがありません: {q}")
    return [(q.stem, q, load_json(q))], False


def run_audio(rows, meas_ok, items, n, work):
    """声の特徴で分ける（--by audio）。rows と items に話者を書き込む。"""
    labs, centers, strength = kmeans([r["mel"] for r in meas_ok], [r["dur"] for r in meas_ok], n)
    for r, k in zip(meas_ok, labs):
        r["speaker"] = f"S{k + 1}"
    # 測れなかった短い発話は、同じ素材で時間が近い発話の話者を借りる
    for r in rows:
        if "speaker" not in r:
            same = [m for m in meas_ok if m["clip"] == r["clip"]] or meas_ok
            near = min(same, key=lambda m: abs(m["start"] - r["start"]))
            r["speaker"] = near["speaker"]
            r["borrowed"] = True
    by = {}
    for r in rows:
        by.setdefault(r["clip"], {})[r["i"]] = r["speaker"]
    for name, path, t in items:
        for s2 in t["segments"]:
            s2["speaker"] = by[name][s2["i"]]
    save_json({"by": "audio", "n": n, "strength": round(strength, 2),
               "centers": [[round(v, 2) for v in c] for c in centers], "segments": rows},
              work / "speakers.json")
    # 群ごとの様子（声の高さと音量の中央値）。音量が小さい群は撮影側のことが多い
    for k in range(n):
        mine = [r for r in rows if r.get("speaker") == f"S{k + 1}"]
        f0s = [r["f0"] for r in mine if r["f0"]]
        dbs = [r["db"] for r in mine]
        print(f"  S{k + 1}: {len(mine)}発話 "
              f"声の高さ {np.median(f0s):.0f}Hz / 音量 {np.median(dbs):.1f}dB"
              if f0s else f"  S{k + 1}: {len(mine)}発話", file=sys.stderr)
    print(f"分かれ方の強さ: {strength:.2f}（1.0 未満は弱い）", file=sys.stderr)
    if strength < WEAK_SPLIT:
        print("要確認: 群の分かれ方が弱いので、transcript.txt を読んで --fix で直してください",
              file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", help="素材のパス（省略時は文字起こしに記録された素材を使う）")
    ap.add_argument("workdir")
    ap.add_argument("--n", type=int, default=2, help="話者の人数（既定2。--by audio のとき）")
    ap.add_argument("--by", choices=["audio", "motion"], default="audio",
                    help="分け方。audio=声の特徴（既定）/ motion=画で顔が動いているか")
    ap.add_argument("--fix", default="", help='手で直す（例 "12,20-24=S2" / "素材名:0-5=S2"）。測り直さない')
    args = ap.parse_args()

    work = Path(args.workdir)
    items, multi = collect(work)

    if args.fix:
        fixes = parse_fix(args.fix)
        done = 0
        for name, path, t in items:
            mine = {i: spk for (nm, i), spk in fixes.items() if nm in (None, name)}
            bad = [i for i in mine if not (0 <= i < len(t["segments"]))]
            if bad:
                raise SystemExit(f"{name} の発話番号が範囲外です: {bad}（発話 {len(t['segments'])}）")
            for i, spk in mine.items():
                t["segments"][i]["speaker"] = spk
                done += 1
        if not done:
            raise SystemExit("--fix がどの素材にも当たりませんでした（素材名と発話番号を確かめる）")
        print(f"手で直しました: {done}発話", file=sys.stderr)
    else:
        rows = []
        for name, path, t in items:
            src = args.src if (args.src and not multi) else t["source"]["path"]
            if not Path(src).exists():
                raise SystemExit(f"素材が見つかりません: {src}")
            x = decode(src)
            mo = motion_series(src) if args.by == "motion" else None
            for s2 in t["segments"]:
                a, b = int(s2["start"] * SR), int(s2["end"] * SR)
                f0, db, voiced, mel = measure(x[max(a, 0):min(b, len(x))])
                rows.append({"clip": name, "i": s2["i"], "start": s2["start"], "end": s2["end"],
                             "dur": round(s2["end"] - s2["start"], 3),
                             "f0": None if f0 is None else round(f0, 1),
                             "db": db, "voiced": voiced, "mel": mel,
                             "motion": seg_motion(mo, s2["start"], s2["end"]), "text": s2["text"]})
        if args.by == "motion":
            fallback = []
            for name, path, t in items:
                mine = [r for r in rows if r["clip"] == name]
                ref_src = [r["motion"] for r in mine if r["motion"] is not None and r["dur"] >= MIN_SEG_SEC]
                ref = float(np.percentile(ref_src, 75)) if ref_src else 0.0
                if ref < MOTION_FLOOR:
                    fallback.append(name)          # 画では判定できない素材
                    for r in mine:
                        r["speaker"] = None
                    continue
                for r in mine:
                    m = r["motion"]
                    r["ref"] = round(ref, 2)
                    r["speaker"] = "S1" if (m is not None and m >= MOTION_RATIO * ref) else "S2"
            if fallback:
                print(f"画では判定できない素材（動きの基準が取れない）: {'/'.join(fallback)}", file=sys.stderr)
                for r in rows:
                    if r["speaker"] is None:
                        r["speaker"] = "S1"
            for name, path, t in items:
                for s2 in t["segments"]:
                    s2["speaker"] = next(r["speaker"] for r in rows
                                         if r["clip"] == name and r["i"] == s2["i"])
            save_json({"by": "motion", "ratio": MOTION_RATIO, "segments": rows},
                      work / "speakers.json")
            for k, lab in [(0, "S1 映っている人"), (1, "S2 映っていない人")]:
                mine = [r for r in rows if r["speaker"] == f"S{k + 1}"]
                if mine:
                    print(f"  {lab}: {len(mine)}発話 "
                          f"動き {np.median([r['motion'] for r in mine if r['motion'] is not None]):.1f} / "
                          f"音量 {np.median([r['db'] for r in mine]):.1f}dB", file=sys.stderr)
            close = [r for r in rows if r.get("ref") and r["motion"] is not None
                     and 0.8 * MOTION_RATIO * r["ref"] <= r["motion"] <= 1.3 * MOTION_RATIO * r["ref"]]
            for r in close:
                print(f"  要確認（境目に近い）: {r['clip']} 発話{r['i']} "
                      f"動き{r['motion']:.1f}（基準{r['ref']:.1f}） {r['text'][:24]}", file=sys.stderr)
            meas_ok = []
        else:
            meas_ok = [r for r in rows if r["mel"] and r["dur"] >= MIN_SEG_SEC]
        if args.by == "audio" and len(meas_ok) < args.n:
            raise SystemExit(f"声を測れた発話が{len(meas_ok)}個しかありません（{args.n}人に分けられません）")
        if meas_ok:
            run_audio(rows, meas_ok, items, args.n, work)

    for name, path, t in items:
        for s2 in t["segments"]:
            for k in range(s2["word_from"], s2["word_to"] + 1):
                t["words"][k]["speaker"] = s2.get("speaker")
        save_json(t, path)
    if multi:
        from transcribe import write_index
        write_index([(name, t) for name, _, t in items], work / "transcript.txt")
    else:
        write_txt(items[0][2], work / "transcript.txt")

    tally = {}
    for name, path, t in items:
        for s2 in t["segments"]:
            v = tally.setdefault(s2.get("speaker", "--"), [0, 0.0])
            v[0] += 1
            v[1] += s2["end"] - s2["start"]
    print("話者ごとの発話数と合計の長さ:", file=sys.stderr)
    for spk, (n, sec) in sorted(tally.items()):
        print(f"  {spk}: {n}発話 {fmt_tc(sec)}", file=sys.stderr)
    if multi:
        print("素材ごとの話者:", file=sys.stderr)
        for name, path, t in items:
            spks = sorted({s2.get("speaker") for s2 in t["segments"]} - {None})
            print(f"  {name}: {'/'.join(spks)}", file=sys.stderr)
    print(f"書き出し: 文字起こし{len(items)}件 / transcript.txt / speakers.json", file=sys.stderr)


if __name__ == "__main__":
    main()
