"""被写体の範囲から、寄せの倍率と位置（cutlist.json の zoom / center_x / center_y）を計算する。

  .venv/bin/python scripts/framing.py 左 上 右 下 [--zmax 2.0]

被写体の範囲は、素材の画面に対する割合（0〜1）。ショットの区間の中で被写体が動くなら、動く範囲全体を含める
（framegrid.py の目盛り付きのコマで読む）。

決め方:
  - 被写体の中心を画面の中心に置く（画面の端を越えるときは、越えない範囲でできるだけ中心に寄せる）
  - 被写体が画面の幅の FILL_W、または高さの FILL_H に届くところまで寄せる（先に届いた方で止める）
  - 素材の解像度を超えて拡大しない（zmax。4K の縦素材を 1080x1920 に出すなら 2.0）
  - 寄せが MIN_ZOOM 未満なら寄せない（ほぼ変わらないので、素材のままにする）
"""
import argparse

FILL_W = 0.85   # 被写体を画面の幅のこの割合まで大きくする
FILL_H = 0.60   # 被写体を画面の高さのこの割合まで大きくする（縦長の画面で高さいっぱいにすると窮屈になる）
MIN_ZOOM = 1.08


def _center(c, win):
    """被写体の中心 c（0〜1）を画面の中心に置くための、切り抜き位置（0=左/上端〜1=右/下端）。
    win は切り抜く窓の大きさ（素材に対する割合）。"""
    if win >= 1.0:
        return 0.5
    off = min(max(c - win / 2, 0.0), 1.0 - win)
    return round(off / (1.0 - win), 3)


def frame_subject(box, zmax=2.0, base_w=1.0, fill_w=FILL_W, fill_h=FILL_H):
    """base_w は寄せる前（zoom 1）に切り抜く窓の幅（素材の幅に対する割合）。
    縦素材を縦の画面に出すなら1、横素材を縦の画面に切り抜くなら 9/16 ÷ 16/9 ≈ 0.32。"""
    x0, y0, x1, y1 = [float(v) for v in box]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"被写体の範囲が正しくありません: {box}（左 上 右 下 を 0〜1 で）")
    w, h = x1 - x0, y1 - y0
    z = min(fill_w * base_w / w, fill_h / h, zmax)
    if z < MIN_ZOOM:
        z = 1.0
    z = round(z, 2)
    return {"zoom": z, "center_x": _center((x0 + x1) / 2, base_w / z), "center_y": _center((y0 + y1) / 2, 1.0 / z)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("box", type=float, nargs=4)
    ap.add_argument("--zmax", type=float, default=2.0)
    ap.add_argument("--base-w", type=float, default=1.0)
    a = ap.parse_args()
    print(frame_subject(a.box, a.zmax, a.base_w))


if __name__ == "__main__":
    main()
