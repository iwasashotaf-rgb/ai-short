"""工程3: タイムラインから確認用の縦型MP4（字幕・タイトル焼き込み）を作る。

この環境の ffmpeg には字幕フィルタ（libass / drawtext）が入っていないので、
字幕は Pillow で透過PNGに描き、ffmpeg の overlay で重ねる。
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    str(Path.home() / "Library/Fonts/NotoSansJP-Black.otf"),
    "/Library/Fonts/NotoSansJP-Black.otf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
]

# 字幕とタイトルの見た目（縦型 1080x1920 基準）
CAPTION_STYLE = {"size": 78, "fill": (255, 255, 255, 255), "stroke": (0, 0, 0, 255),
                 "stroke_w": 9, "center_y": 1380, "max_w": 980, "line_gap": 14}
TITLE_STYLE = {"size": 86, "fill": (255, 255, 255, 255), "box": (0, 0, 0, 200),
               "center_y": 300, "max_w": 960, "line_gap": 16, "box_pad": 28}


def find_font():
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit("日本語フォントが見つかりません。preview.py の FONT_CANDIDATES に追加してください")


def _fit_font(font_path, lines, size, max_w, stroke_w):
    """全行が max_w に収まるまで文字サイズを下げる。"""
    while size > 30:
        font = ImageFont.truetype(font_path, size)
        widths = [font.getbbox(ln, stroke_width=stroke_w)[2] for ln in lines]
        if max(widths) <= max_w:
            return font
        size -= 4
    return ImageFont.truetype(font_path, size)


def draw_text_png(path, text, style, w, h, font_path, boxed=False):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lines = text.split("\n")
    sw = style.get("stroke_w", 0)
    font = _fit_font(font_path, lines, style["size"], style["max_w"], sw)
    asc, desc = font.getmetrics()
    lh = asc + desc
    total_h = lh * len(lines) + style["line_gap"] * (len(lines) - 1)
    y = style["center_y"] - total_h // 2
    if boxed:
        bw = max(font.getbbox(ln)[2] for ln in lines)
        p = style["box_pad"]
        d.rounded_rectangle([(w - bw) // 2 - p, y - p, (w + bw) // 2 + p, y + total_h + p],
                            radius=24, fill=style["box"])
    for ln in lines:
        lw = font.getbbox(ln, stroke_width=sw)[2]
        d.text(((w - lw) // 2, y), ln, font=font, fill=style["fill"],
               stroke_width=sw, stroke_fill=style.get("stroke"))
        y += lh + style["line_gap"]
    img.save(path)


def reframe_filter(tl, inp, out):
    w, h = tl["out_w"], tl["out_h"]
    rf = tl["reframe"]
    if rf["mode"] == "crop":
        cx = float(rf.get("center_x", 0.5))
        return (f"[{inp}]scale=-2:{h},crop={w}:{h}:x='(iw-{w})*{cx}':y=0,setsar=1[{out}]")
    # fit: 全体を横幅いっぱいに収め、背景は同じ映像を拡大してぼかす
    return (f"[{inp}]split[fg0][bg0];"
            f"[bg0]scale=-2:{h},crop={w}:{h},gblur=sigma=40,eq=brightness=-0.12[bg1];"
            f"[fg0]scale={w}:-2[fg1];"
            f"[bg1][fg1]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")


def render(tl, out_path, tmp_dir, burn=True):
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    fps = tl["fps"]
    src = tl["source"]["path"]
    font_path = find_font()

    parts, labels = [], []
    has_audio = tl["source"]["audio_channels"] > 0
    for k, c in enumerate(tl["clips"]):
        t0, t1 = c["src_in"] / fps, c["src_out"] / fps
        parts.append(f"[0:v]trim=start_frame={c['src_in']}:end_frame={c['src_out']},"
                     f"setpts=PTS-STARTPTS[v{k}]")
        labels.append(f"[v{k}]")
        if has_audio:
            d = t1 - t0
            # カット点のプチッという音を防ぐため、前後10msだけフェードする
            parts.append(f"[0:a]atrim=start={t0:.6f}:end={t1:.6f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.01,afade=t=out:st={max(d - 0.01, 0):.6f}:d=0.01[a{k}]")
            labels.append(f"[a{k}]")
    n = len(tl["clips"])
    parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}"
                 + ("[vc][ac]" if has_audio else "[vc]"))
    parts.append(reframe_filter(tl, "vc", "vr"))

    inputs = ["-i", src]
    last = "vr"
    overlays = []
    if burn and tl["title"]:
        p = tmp / "title.png"
        draw_text_png(p, tl["title"], TITLE_STYLE, tl["out_w"], tl["out_h"], font_path, boxed=True)
        overlays.append((p, 0, tl["total_frames"]))
    if burn:
        for cap in tl["captions"]:
            p = tmp / f"cap_{cap['i']:03d}.png"
            draw_text_png(p, cap["text"], CAPTION_STYLE, tl["out_w"], tl["out_h"], font_path)
            overlays.append((p, cap["rec_in"], cap["rec_out"]))
    for j, (p, f0, f1) in enumerate(overlays, start=1):
        inputs += ["-loop", "1", "-framerate", f"{tl['source']['fps_num']}/{tl['source']['fps_den']}",
                   "-i", str(p)]
        nxt = f"o{j}"
        parts.append(f"[{last}][{j}:v]overlay=0:0:shortest=1:"
                     f"enable='between(n,{f0},{f1 - 1})'[{nxt}]")
        last = nxt

    fc_path = tmp / "filter.txt"
    fc_path.write_text(";\n".join(parts), encoding="utf-8")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
           "-filter_complex_script", str(fc_path), "-map", f"[{last}]"]
    if has_audio:
        cmd += ["-map", "[ac]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-r", f"{tl['source']['fps_num']}/{tl['source']['fps_den']}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise SystemExit("プレビューの書き出しに失敗しました")
