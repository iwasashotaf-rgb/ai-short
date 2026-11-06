"""ナレーション生成: 台本（script.json）を1文ずつ Fish Audio で音声にし、間を入れてつなぐ。

使い方:
  .venv/bin/python scripts/narrate.py projects/案件名/script.json [--check-only] [--no-verify]

  --check-only  生成済みの音声で読みの検査だけをやり直す（API を呼ばない）
  --no-verify   読みの検査を省く

script.json の形式:
{
  "voice": {"reference_id": "0089dce5fefb4c6ba9b9f2f0debe1ddc",  # Fish Audio の声のID
            "model": "s2.1-pro-free",                            # 有料: s2-pro / s2.1-pro
            "speed": 1.0},
  "gap_sec": 0.35,                      # 文と文の間（秒）
  "lead_in_sec": 0.2,                   # 冒頭の無音
  "lines": [
    {"id": "L01", "text": "驚きの新商品が登場しました。"},
    {"id": "L02", "text": "価格は1980円。", "yomi": "価格はせんきゅうひゃくはちじゅうえん。", "gap_after": 0.6}
  ]
}
  text      字幕に出す文面
  yomi      読み間違えるときだけ書く。音声合成にはこちらを送る
  gap_after この文の後の間を個別に変える

出力（script.json と同じフォルダ）:
  narration/narration.wav   つないだナレーション（48kHz）
  narration/narration.json  文ごとの開始・終了時刻（ナレーション上の秒）と読みの検査結果
  narration/cache/          文ごとの生成音声。文面・声・設定が同じなら再利用し、API を呼ばない
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json  # noqa: E402

API_URL = "https://api.fish.audio/v1/tts"
TTS_RATE = 44100      # Fish Audio の wav が受け付ける最大のサンプルレート
OUT_RATE = 48000      # Premiere の映像用シーケンスに合わせる
EDGE_KEEP = 0.04      # 生成音声の前後の無音を削ったあとに残す秒数
VERIFY_THRESHOLD = 0.92  # 台本と文字起こしの一致率がこれ未満の文を「要確認」にする


def api_key():
    k = os.environ.get("FISH_API_KEY")
    if not k:  # Claude Code のシェルは起動時の環境を使うので、後から ~/.zshrc に足したキーを読む
        k = subprocess.run(["zsh", "-c", 'source ~/.zshrc >/dev/null 2>&1; printf %s "$FISH_API_KEY"'],
                           capture_output=True, text=True).stdout.strip()
    if not k:
        raise SystemExit("FISH_API_KEY が設定されていません（~/.zshrc に export してください）")
    return k


def tts(text, voice, key, out_path, retries=3):
    body = {"text": text, "reference_id": voice["reference_id"], "format": "wav",
            "sample_rate": TTS_RATE, "normalize": True}
    if voice.get("speed", 1.0) != 1.0 or voice.get("volume", 0) != 0:
        body["prosody"] = {"speed": voice.get("speed", 1.0), "volume": voice.get("volume", 0)}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json",
                                          "model": voice.get("model", "s2.1-pro-free")})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            out_path.write_bytes(data)
            return
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503) and attempt + 1 < retries:
                time.sleep(3 * (attempt + 1))
                continue
            raise SystemExit(f"Fish Audio がエラーを返しました（HTTP {e.code}）: {msg}")


def cache_key(text, voice):
    s = json.dumps([text, voice.get("reference_id"), voice.get("model", "s2.1-pro-free"),
                    voice.get("speed", 1.0), voice.get("volume", 0)], ensure_ascii=False)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def speech_bounds(path, noise_db=-40):
    """音声の最初と最後の発話位置（秒）を silencedetect で求める。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"silencedetect=noise={noise_db}dB:d=0.05", "-f", "null", "-"],
                       capture_output=True, text=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    head = ends[0] if starts and starts[0] <= 0.01 and ends else 0.0
    tail = starts[-1] if starts and (len(ends) < len(starts) or ends[-1] >= dur - 0.01) else dur
    return max(head - EDGE_KEEP, 0.0), min(tail + EDGE_KEEP, dur)


PUNCT = r"[\s、。，．,.！？!?「」『』（）()・…〜~\-🚨⚠️◎📍✨🎀🎍📌🎁♡]"
_kks = None


def to_hira(s):
    """カタカナをひらがなに直す（漢字はそのまま）。"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def reading(s):
    """漢字も含めて辞書の読み（ひらがな）に直す。"""
    global _kks
    if _kks is None:
        import pykakasi
        _kks = pykakasi.kakasi()
    return "".join(x["hira"] for x in _kks.convert(s))


def normalize(s):
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(PUNCT, "", s)


def match_ratio(expected, yomi, heard):
    """台本と文字起こしの一致率。表記の揺れ（大ケガ/大怪我、コレ/これ）で下がらないよう、
    表記どうし・読みどうし・読み仮名（yomi）との比較のうち最も高い値を使う。"""
    def r(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio()
    e, h = normalize(expected), normalize(heard)
    cands = [r(to_hira(e), to_hira(h)), r(reading(e), reading(h))]
    if yomi:
        cands.append(r(reading(normalize(yomi)), reading(h)))
    return max(cands)


def verify(lines, wav_paths):
    import mlx_whisper
    results = []
    for ln, p in zip(lines, wav_paths):
        r = mlx_whisper.transcribe(str(p), path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                                   language="ja", condition_on_previous_text=False)
        # initial_prompt に台本を渡すと、読み間違いを台本どおりに聞き取ってしまい検査にならない
        heard = r["text"].strip()
        ratio = match_ratio(ln["text"], ln.get("yomi"), heard)
        results.append({"heard": heard, "match": round(ratio, 3), "ok": ratio >= VERIFY_THRESHOLD})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    sp = Path(args.script)
    script = load_json(sp)
    voice = script["voice"]
    gap = float(script.get("gap_sec", 0.35))
    lead = float(script.get("lead_in_sec", 0.2))
    out = sp.parent / "narration"
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = None if args.check_only else api_key()

    ids = [ln["id"] for ln in script["lines"]]
    if len(set(ids)) != len(ids):
        raise SystemExit("lines の id が重複しています")

    raw_paths, generated = [], 0
    for ln in script["lines"]:
        say = ln.get("yomi") or ln["text"]
        p = cache / f"{cache_key(say, voice)}.wav"
        if not p.exists():
            if args.check_only:
                raise SystemExit(f"{ln['id']} の音声がまだありません（--check-only を外して生成してください）")
            print(f"  生成: {ln['id']} {say}", file=sys.stderr)
            tts(say, voice, key, p)
            generated += 1
        raw_paths.append(p)

    # 前後の無音を削って、指定の間でつなぐ
    t = lead
    timeline, parts = [], [f"aevalsrc=0:d={lead}:s={OUT_RATE}"]
    trimmed = []
    for i, (ln, p) in enumerate(zip(script["lines"], raw_paths)):
        a, b = speech_bounds(p)
        trimmed.append((a, b))
        timeline.append({"id": ln["id"], "row": ln.get("row"), "text": ln["text"], "yomi": ln.get("yomi"),
                         "start": round(t, 3), "end": round(t + (b - a), 3), "cache": p.name})
        t += b - a
        if i + 1 < len(script["lines"]):
            g = float(ln.get("gap_after", gap))
            timeline[-1]["gap_after"] = g
            t += g
    total = t + 0.3

    inputs, fc = [], []
    for i, (p, (a, b)) in enumerate(zip(raw_paths, trimmed)):
        inputs += ["-i", str(p)]
        delay = int(round(timeline[i]["start"] * 1000))
        fc.append(f"[{i}:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,"
                  f"aresample={OUT_RATE},afade=t=in:d=0.01,adelay={delay}:all=1[a{i}]")
    fc.append("".join(f"[a{i}]" for i in range(len(raw_paths)))
              + f"amix=inputs={len(raw_paths)}:normalize=0,apad=whole_dur={total:.3f}[out]")
    wav = out / "narration.wav"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
                        "-filter_complex", ";".join(fc), "-map", "[out]", "-ar", str(OUT_RATE),
                        "-ac", "1", "-c:a", "pcm_s16le", str(wav)], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise SystemExit("ナレーションの連結に失敗しました")

    if not args.no_verify:
        for item, v in zip(timeline, verify(script["lines"], raw_paths)):
            item["verify"] = v

    save_json({"voice": voice, "duration": round(total, 3), "wav": str(wav.resolve()),
               "lines": timeline}, out / "narration.json")
    print(f"ナレーション: {wav}（{total:.2f}秒、{len(timeline)}文、新規生成 {generated}文）")
    for item in timeline:
        v = item.get("verify")
        mark = "" if v is None else ("OK " if v["ok"] else "要確認")
        print(f"  {mark:4s} {item['id']} {item['start']:6.2f}-{item['end']:6.2f}  {item['text']}")
        if v and not v["ok"]:
            print(f"         聞こえた文: {v['heard']}（一致率 {v['match']:.2f}）")


if __name__ == "__main__":
    main()
