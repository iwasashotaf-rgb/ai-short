"""素材の手ブレを数値で測る。

  .venv/bin/python scripts/shake.py 素材フォルダ 出力.json [--fps 15]
  .venv/bin/python scripts/shake.py 素材.MOV --from 4.6 --dur 2.0   （区間だけ測って表示）

測り方:
  1. 縦横を縮めた白黒のコマを等間隔に切り出し、隣り合うコマのずれ（上下左右の移動量）を位相相関で求める
  2. ずれを足し合わせたものがカメラの動いた道筋。これを0.5秒の移動平均でならしたものが「意図した動き」（パン・寄り）
  3. 道筋と移動平均の差が手ブレ。画面の幅に対する割合（%）で表し、区間の中のコマの中央値を取る
     （ずれは画面を9区画に分けて測った中央値なので、一部の物が動いてもカメラの動きとは数えない）

目安は SHAKE_OK / SHAKE_NG。豆LOVEサラダ（iPhone 縦動画）で、人が「OKテイクではない」と判断した
手持ちの寄り（IMG_0470 4.6秒〜）が 1.7%、三脚が 0.0%、手持ちでも落ち着いた区間が 0.1〜0.6%、
固定カメラの前を箱が横切る区間（魚が焼けルンルン IMG_0025 42.2秒〜）が 0.1% だった。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import probe  # noqa: E402

W, H = 144, 256          # 解析用に縮めるコマの大きさ（縦動画）
SMOOTH_SEC = 0.5         # 意図した動きとみなす移動平均の幅
SHAKE_OK = 0.7           # これ以下ならブレは目立たない（%）
SHAKE_NG = 1.2           # これを超えるとブレが目立つ。採用しない（%）
MARGIN_SEC = 0.5         # 区間を測るとき前後に足す長さ（移動平均の端で値が跳ねるのを防ぐ）


def frames(path, start=None, dur=None, fps=15):
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(path)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd += ["-vf", f"fps={fps},scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


TW, TH = W // 2, H // 2  # ずれを測る小区画の大きさ。画面を半分ずつ重ねて3x3=9区画に分ける
TILES = [(x, y) for y in (0, H // 4, H // 2) for x in (0, W // 4, W // 2)]


def shift(a, b, win):
    """b が a からどれだけずれたか（x, y 画素）を位相相関で求める。"""
    h, w = a.shape
    fa, fb = np.fft.fft2(a * win), np.fft.fft2(b * win)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    c = np.fft.ifft2(r).real
    y, x = np.unravel_index(np.argmax(c), c.shape)
    if y > h // 2:
        y -= h
    if x > w // 2:
        x -= w
    return -x, -y


def frame_shift(a, b, win):
    """画面全体のずれ。9区画のずれの中央値を取るので、手や箱など一部の物が動いてもカメラの動きとは数えない。"""
    s = np.array([shift(a[y:y + TH, x:x + TW], b[y:y + TH, x:x + TW], win) for x, y in TILES])
    return float(np.median(s[:, 0])), float(np.median(s[:, 1]))


def shake_series(f, fps):
    """コマごとの手ブレ量（画面の幅に対する%）を返す。"""
    if len(f) < 3:
        return np.zeros(len(f))
    win = np.outer(np.hanning(TH), np.hanning(TW)).astype(np.float32)
    d = np.array([frame_shift(f[i], f[i + 1], win) for i in range(len(f) - 1)], dtype=np.float32)
    path = np.vstack([[0, 0], np.cumsum(d, axis=0)])
    k = max(3, int(fps * SMOOTH_SEC) | 1)
    pad = np.pad(path, ((k // 2, k // 2), (0, 0)), mode="edge")
    smooth = np.stack([np.convolve(pad[:, j], np.ones(k) / k, mode="valid") for j in range(2)], axis=1)
    resid = path - smooth
    return np.hypot(resid[:, 0], resid[:, 1]) / W * 100


def score(series):
    """区間の手ブレ量。コマごとの値の中央値を取る。
    手持ちのブレは区間のほとんどのコマで出続けるが、箱や手が一瞬横切る動きは数コマで終わるので、中央値なら拾わない。"""
    return float(np.median(series)) if len(series) else 0.0


def measure(path, start=None, dur=None, fps=15):
    """区間の手ブレ量（%）。前後に MARGIN_SEC を足して測り、区間の中だけで集計する。"""
    if start is None or dur is None:
        return round(score(shake_series(frames(path, start, dur, fps), fps)), 3)
    s0 = max(start - MARGIN_SEC, 0)
    series = shake_series(frames(path, s0, dur + (start - s0) + MARGIN_SEC, fps), fps)
    i = int(round((start - s0) * fps))
    return round(score(series[i:i + max(int(dur * fps), 1)]), 3)


def scan(path, fps=15, step=1.0):
    """1秒ごとの手ブレ量の一覧を返す。"""
    f = frames(path, fps=fps)
    s = shake_series(f, fps)
    n = int(step * fps)
    return [{"t": round(i / fps, 2), "shake": round(score(s[i:i + n]), 3)} for i in range(0, len(s), n)]


def label(v):
    return "ブレ大" if v > SHAKE_NG else "ややブレ" if v > SHAKE_OK else "安定"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--from", dest="start", type=float)
    ap.add_argument("--dur", type=float)
    ap.add_argument("--fps", type=int, default=15)
    a = ap.parse_args()
    src = Path(a.src)
    if src.is_file():
        v = measure(src, a.start, a.dur, a.fps)
        print(f"{src.name} {a.start or 0}s〜 手ブレ {v}%（{label(v)}）")
        return
    result = {}
    for p in sorted(src.glob("*.MOV")) + sorted(src.glob("*.mp4")) + sorted(src.glob("*.mov")):
        rows = scan(p, a.fps)
        result[p.name] = {"duration": probe(p)["duration"], "per_sec": rows}
        bad = [r["t"] for r in rows if r["shake"] > SHAKE_NG]
        print(f"{p.name}: 全体 {score(np.array([r['shake'] for r in rows])):.2f}%  ブレ大の秒 {bad}")
    if a.out:
        Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
