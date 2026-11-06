"""共通処理: 素材の情報取得・JSON入出力・時刻とフレームの変換。"""
import json
import subprocess
from fractions import Fraction
from pathlib import Path


def probe(path):
    """ffprobe で素材の尺・解像度・フレームレート・音声の有無を取る。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate,channels"
         ":stream_side_data=rotation",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        raise SystemExit(f"映像ストリームがありません: {path}")
    fps = Fraction(v["r_frame_rate"])
    # iPhone の縦動画は横長で記録され、回転の指示（-90 など）が付いている。
    # ffmpeg も Premiere も回転してから扱うので、幅と高さは回転後の値を返す
    rot = next((int(sd.get("rotation", 0)) for sd in v.get("side_data_list", []) if "rotation" in sd), 0)
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w
    return {
        "path": str(Path(path).resolve()),
        "duration": float(info["format"]["duration"]),
        "width": w,
        "height": h,
        "rotation": rot,
        "fps_num": fps.numerator,
        "fps_den": fps.denominator,
        "fps": float(fps),
        "audio_rate": int(a["sample_rate"]) if a else None,
        "audio_channels": int(a["channels"]) if a else 0,
    }


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(obj, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def sec_to_frame(t, fps):
    return int(round(t * fps))


def fmt_tc(t):
    """秒を 00:00.00 形式にする（人とClaudeが読む用）。"""
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"
