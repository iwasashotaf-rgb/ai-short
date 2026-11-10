"""ナレーション生成: 台本（script.json）を1文ずつ Fish Audio で音声にし、間を入れてつなぐ。

使い方:
  .venv/bin/python scripts/narrate.py projects/案件名/script.json [--check-only] [--no-verify]

  --check-only  生成済みの音声で読みの検査だけをやり直す（API を呼ばない）
  --no-verify   読みの検査を省く
  --tune [回数]  文ごとの話す速さが毎秒9〜10.5かなに入るまで、外れた文の speed を変えて作り直す

script.json の形式:
{
  "voice": {"reference_id": "0089dce5fefb4c6ba9b9f2f0debe1ddc",  # Fish Audio の声のID
            "model": "s2.1-pro-free",                            # 有料: s2-pro / s2.1-pro
            "speed": 1.0},
  # クローンした声を使うときは reference_id の代わりに "clone": "名前"（voice_clone.py で作った声）を書く
  # CoeFont を使うときは "voice": {"provider": "coefont", "coefont": "<声のID>", "speed": 1.0, "pitch": 0,
  #   "kuten": 0.1, "toten": 0.2}。鍵は ~/.zshrc の COEFONT_ACCESS_KEY / COEFONT_ACCESS_SECRET。
  #   声の一覧は narrate.py --coefonts 。lines[].kana にひらがなの読みを書くと CoeFont にそのまま渡す
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
  speed     この文だけ速さを変える（voice.speed を上書き。1文だけ速く読まれたときに使う）
  phrases   文をテロップ1行ぶんの句に分けたもの（例 ["焼く手間なしで", "この仕上がり"]）。
            書くと、生成音声の文字起こしから句ごとの開始・終了時刻を割り出し、narration.json に入れる。
            句ごとにカットを替え、テロップを出すとき（build_b.py の at / captions: "phrase"）に使う

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
BLIP_SEC = 0.06      # 頭と終わりの無音を測るとき、これより短い音（息・クリック）は無音として扱う
VERIFY_THRESHOLD = 0.92  # 台本と文字起こしの一致率がこれ未満の文を「要確認」にする


def env_key(name):
    k = os.environ.get(name)
    if not k:  # Claude Code のシェルは起動時の環境を使うので、後から ~/.zshrc に足したキーを読む
        k = subprocess.run(["zsh", "-c", f'source ~/.zshrc >/dev/null 2>&1; printf %s "${name}"'],
                           capture_output=True, text=True).stdout.strip()
    if not k:
        raise SystemExit(f"{name} が設定されていません（~/.zshrc に export してください）")
    return k


def api_key(voice=None):
    """voice の provider に合わせた鍵を返す。CoeFont はアクセスキーとアクセスシークレットの組。"""
    if voice and voice.get("provider") == "coefont":
        return (env_key("COEFONT_ACCESS_KEY"), env_key("COEFONT_ACCESS_SECRET"))
    return env_key("FISH_API_KEY")


COEFONT_URL = "https://api.coefont.cloud/v2"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def coefont_request(path, key, body=None, method="POST"):
    """CoeFont API を呼ぶ。署名は HMAC-SHA256（アクセスシークレットで、UNIX 時刻＋JSON 本文を署名）。
    text2speech は 302 で音声ファイルの URL を返すので、転送先は鍵を付けずに取りにいく。"""
    import hmac
    access, secret = key
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
    date = str(int(time.time()))
    sig = hmac.new(secret.encode(), date.encode() + data, hashlib.sha256).hexdigest()
    req = urllib.request.Request(COEFONT_URL + path, data=data if body is not None else None, method=method,
                                 headers={"Authorization": access, "X-Coefont-Date": date,
                                          "X-Coefont-Content": sig, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307):
            return e.code, e.headers, b""
        raise


def tts_coefont(text, voice, key, out_path, retries=3):
    body = {"coefont": voice["coefont"], "text": text, "format": "wav",
            "speed": voice.get("speed", 1.0), "pitch": voice.get("pitch", 0), "volume": voice.get("volume", 1.0),
            "kuten": voice.get("kuten", 0.1), "toten": voice.get("toten", 0.2)}
    if voice.get("_kana"):  # 読みをひらがなで渡せるときは渡す（漢字の読み違いを防ぐ）
        body["yomi"] = voice["_kana"]
    for attempt in range(retries):
        try:
            code, headers, _ = coefont_request("/text2speech", key, body)
            loc = headers.get("Location")
            if code not in (301, 302, 303, 307) or not loc:
                raise SystemExit(f"CoeFont が音声の場所を返しませんでした（HTTP {code}）")
            with urllib.request.urlopen(loc, timeout=120) as r:
                out_path.write_bytes(r.read())
            return
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503) and attempt + 1 < retries:
                time.sleep(3 * (attempt + 1))
                continue
            hint = {401: "（キーの誤りか、ポイントの残高不足）", 403: "（この声を使う権限がない）", 404: "（声の ID が見つからない）"}.get(e.code, "")
            raise SystemExit(f"CoeFont がエラーを返しました（HTTP {e.code}）{hint}: {msg}")


def list_coefonts(key):
    code, _, data = coefont_request("/coefonts/pro", key, method="GET")
    for c in json.loads(data):
        print(f"{c.get('coefont')}  {c.get('name')}  {str(c.get('description') or '')[:50]}")


def tts(text, voice, key, out_path, retries=3):
    if voice.get("provider") == "coefont":
        return tts_coefont(text, voice, key, out_path, retries)
    body = {"text": text, "format": "wav", "sample_rate": TTS_RATE, "normalize": True}
    if voice.get("speed", 1.0) != 1.0 or voice.get("volume", 0) != 0:
        body["prosody"] = {"speed": voice.get("speed", 1.0), "volume": voice.get("volume", 0)}
    if voice.get("references"):
        # その場でのクローン: 参考音声と文面を毎回送る（モデルを作らない。MessagePack でしか送れない）
        import msgpack
        body["references"] = [{"audio": Path(r["audio"]).read_bytes(), "text": r["text"]} for r in voice["references"]]
        data, ctype = msgpack.packb(body), "application/msgpack"
    else:
        body["reference_id"] = voice["reference_id"]
        data, ctype = json.dumps(body).encode("utf-8"), "application/json"
    req = urllib.request.Request(API_URL, data=data, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": ctype,
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
    if voice.get("provider") == "coefont":
        s = json.dumps(["coefont", text, voice.get("_kana"), voice["coefont"], voice.get("speed", 1.0),
                        voice.get("pitch", 0), voice.get("volume", 1.0), voice.get("kuten", 0.1),
                        voice.get("toten", 0.2)], ensure_ascii=False)
        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]
    s = json.dumps([text, voice.get("reference_id") or voice.get("clone_sig"), voice.get("model", "s2.1-pro-free"),
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
    # 無音の間に挟まった短い雑音（息・クリック、BLIP_SEC 未満）は無音とみなしてつなぐ。
    # つながないと、頭や終わりの無音が雑音の手前で切れて、文の間が設定より長くなる
    i = 0
    while i + 1 < len(starts) and i < len(ends):
        if starts[i + 1] - ends[i] < BLIP_SEC:
            del ends[i], starts[i + 1]
        else:
            i += 1
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
    """生成音声を文字起こしし、台本との一致率と、語ごとの時刻（生成音声の先頭からの秒）を返す。"""
    import mlx_whisper
    results = []
    for ln, p in zip(lines, wav_paths):
        r = mlx_whisper.transcribe(str(p), path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                                   language="ja", condition_on_previous_text=False, word_timestamps=True)
        # initial_prompt に台本を渡すと、読み間違いを台本どおりに聞き取ってしまい検査にならない
        heard = r["text"].strip()
        ratio = match_ratio(ln["text"], ln.get("yomi"), heard)
        words = [{"text": w["word"].strip(), "start": w["start"], "end": w["end"]}
                 for s in r["segments"] for w in s.get("words", []) if w["word"].strip()]
        results.append({"heard": heard, "match": round(ratio, 3), "ok": ratio >= VERIFY_THRESHOLD,
                        "words": words})
    return results


def phrase_times(phrases, words, trim_start, line_start, line_end, pauses=None):
    """1文の中の句（テロップ1行ぶん）の開始・終了を、文字起こしの語の時刻から割り出す。

    句の境目を「読み（ひらがな）の文字数の割合」で決め、同じ割合の位置にある語の時刻を補間する。
    表記が台本と違っても（漢字/かな）、読みの長さはほぼ同じになるのでずれにくい。
    """
    plen = [max(len(reading(normalize(p))), 1) for p in phrases]
    total_p = sum(plen)
    wl = [(max(len(reading(normalize(w["text"]))), 1), w["start"], w["end"]) for w in words]
    total_w = sum(n for n, _, _ in wl)

    def time_at(frac):
        if not wl:
            return line_start + frac * (line_end - line_start)
        pos, acc = frac * total_w, 0
        for n, s, e in wl:
            if acc + n >= pos:
                t = s + (e - s) * (pos - acc) / n
                return min(max(line_start + (t - trim_start), line_start), line_end)
            acc += n
        return line_end

    bounds = [line_start]
    acc = 0
    for n in plen[:-1]:
        acc += n
        bounds.append(time_at(acc / total_p))
    bounds.append(line_end)
    # 読点などで文の中に間があると、文字数の割合で決めた境目が間の手前に来て、
    # 次の句のテロップが声より先に出る。境目の近く（前後 PAUSE_SNAP 秒）に間があれば、間の終わり（声の出だし）に寄せる
    # 1つの間は1つの境目にだけ使い、前後の境目（割合で決めた位置）を越える間は使わない
    orig, used = list(bounds), set()
    for i in range(1, len(bounds) - 1):
        near = [(min(abs(orig[i] - s), abs(orig[i] - e)), k, e) for k, (s, e) in enumerate(pauses or [])
                if k not in used and s - PAUSE_SNAP <= orig[i] <= e + PAUSE_SNAP
                and s > orig[i - 1] and e < orig[i + 1]]
        if near:
            _, k, e = min(near)
            used.add(k)
            bounds[i] = min(max(e, bounds[i - 1]), bounds[i + 1])
    return [{"text": p, "start": round(bounds[i], 3), "end": round(bounds[i + 1], 3)}
            for i, p in enumerate(phrases)]


PAUSE_SNAP = 0.25  # 句の境目を間に寄せる範囲（秒）


def inner_pauses(path, trim_start, line_start, noise_db=-40, min_sec=0.15):
    """生成音声の中の間（無音）を、ナレーション上の秒で返す。前後の無音は除く。
    促音（「作った」「1分」の「っ」）の無音は長いと0.14秒になるので、min_sec（0.15秒）で外す。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"silencedetect=noise={noise_db}dB:d={min_sec}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return [(line_start + s - trim_start, line_start + e - trim_start)
            for s, e in zip(starts, ends) if s > trim_start + 0.05]


TUNE_LO, TUNE_HI, TUNE_TARGET = 9.0, 10.5, 9.75  # 手引き2-4: 文ごとに毎秒9〜10.5かな
TUNE_PAUSE = 0.3  # 文の中の無音の上限（秒、-40dB）


def tune(sp, rounds, no_verify=False):
    """生成 → 文ごとの話す速さを測る → 範囲を外れた文だけ lines[].speed を書き換えて作り直す、を繰り返す。
    同じ speed でも生成のたびに長さが変わるので、1回で決まらないことがある（手引き2-4）。"""
    for rnd in range(rounds):
        cmd = [sys.executable, str(Path(__file__)), str(sp)] + (["--no-verify"] if no_verify else [])
        if subprocess.run(cmd).returncode:
            raise SystemExit("ナレーションの生成に失敗しました")
        script = load_json(sp)
        narr = load_json(sp.parent / "narration" / "narration.json")
        base = script["voice"].get("speed", 1.0)
        # check.py（review.py）と同じ測り方にする: 書き出した音声を -40dB で無音検出し、文の頭と尾の無音を除いて測る
        from review import measure_speech
        m = measure_speech(Path(narr["wav"]), narr["lines"], -40)
        changed = []
        for ln, nl in zip(script["lines"], narr["lines"]):
            rate = m["rates"][nl["id"]]
            sp_now = ln.get("speed", base)
            pause = m["inner_by_id"].get(nl["id"], 0)
            if not TUNE_LO <= rate <= TUNE_HI:
                ln["speed"] = round(min(max(sp_now * TUNE_TARGET / rate, 0.8), 2.0), 2)
                changed.append(f"{ln['id']} 毎秒{rate:.1f}かな → speed {ln['speed']}")
            elif pause >= TUNE_PAUSE:
                # 文の中に0.3秒以上の無音（手引き2-4）。speed を 0.01 変えて生成し直す（同じ設定だとキャッシュが使われる）
                ln["speed"] = round(sp_now + (0.01 if rate < TUNE_TARGET else -0.01), 2)
                changed.append(f"{ln['id']} 文の中の無音 {pause:.2f}秒 → 生成し直す")
        if not changed:
            print(f"調整 {rnd + 1}回目: すべての文が毎秒{TUNE_LO}〜{TUNE_HI}かなに入り、文の中に{TUNE_PAUSE}秒以上の無音はありません")
            return
        print(f"調整 {rnd + 1}回目: 範囲外 " + "、".join(changed))
        sp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{rounds}回で範囲に入らない文が残りました。script.json の speed は書き換え済みなので、もう一度 --tune を実行してください")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--estimate", type=float, metavar="かな毎秒", nargs="?", const=9.5,
                    help="生成せずに、台本の読みの文字数から尺を見積もる（既定 毎秒9.5かな）")
    ap.add_argument("--coefonts", action="store_true", help="CoeFont で使える声の一覧を表示する")
    ap.add_argument("--tune", type=int, metavar="回数", nargs="?", const=5,
                    help="文ごとの話す速さが毎秒9〜10.5かなに入るまで、外れた文の speed を変えて作り直す（既定 最大5回）")
    args = ap.parse_args()
    if args.coefonts:
        return list_coefonts(api_key({"provider": "coefont"}))
    if args.tune:
        return tune(Path(args.script), args.tune, args.no_verify)

    sp = Path(args.script)
    script = load_json(sp)
    voice = script["voice"]
    if "clone" in voice:  # クローンした声（voices/<名前>/voice.json）の reference_id を使う
        from voice_clone import resolve
        voice = resolve(voice)
    gap = float(script.get("gap_sec", 0.35))
    lead = float(script.get("lead_in_sec", 0.2))

    if args.estimate:
        # 台本を書いた段階で尺を見積もる（API を呼ばない）。尺を決めるのは読みの文字数
        total_kana, t = 0, lead
        for i, ln in enumerate(script["lines"]):
            k = len(reading(normalize(ln.get("yomi") or ln["text"])))
            total_kana += k
            sec = k / args.estimate
            print(f"  {ln['id']} {k:3d}字 {sec:4.1f}秒  {ln['text']}")
            t += sec + (float(ln.get("gap_after", gap)) if i + 1 < len(script["lines"]) else 0)
        print(f"読み {total_kana}字 → 毎秒{args.estimate}かなで 約{t:.1f}秒")
        return
    out = sp.parent / "narration"
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = None if args.check_only else api_key(voice)

    ids = [ln["id"] for ln in script["lines"]]
    if len(set(ids)) != len(ids):
        raise SystemExit("lines の id が重複しています")

    raw_paths, generated = [], 0
    for ln in script["lines"]:
        say = ln.get("yomi") or ln["text"]
        v = {**voice, "speed": ln["speed"]} if "speed" in ln else voice  # 文ごとの速さ（任意）
        if voice.get("provider") == "coefont" and ln.get("kana"):
            v = {**v, "_kana": ln["kana"]}  # CoeFont は読みをひらがなで渡せる（lines[].kana）
        p = cache / f"{cache_key(say, v)}.wav"
        if not p.exists():
            if args.check_only:
                raise SystemExit(f"{ln['id']} の音声がまだありません（--check-only を外して生成してください）")
            print(f"  生成: {ln['id']} {say}", file=sys.stderr)
            tts(say, v, key, p)
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

    has_phrases = any(ln.get("phrases") for ln in script["lines"])
    if not args.no_verify or has_phrases:
        for ln, item, v, (a, _b) in zip(script["lines"], timeline, verify(script["lines"], raw_paths), trimmed):
            words = v.pop("words")
            if not args.no_verify:
                item["verify"] = v
            # 句（テロップ1行ぶん）の時刻。句の指定がなければ文全体を1句にする
            phrases = ln.get("phrases") or [ln["text"]]
            pauses = inner_pauses(cache / item["cache"], a, item["start"])
            item["phrases"] = phrase_times(phrases, words, a, item["start"], item["end"], pauses)
            for k, ph in enumerate(item["phrases"], start=1):
                ph["id"] = f"{ln['id']}.{k}"

    save_json({"voice": voice, "duration": round(total, 3), "wav": str(wav.resolve()),
               "lines": timeline}, out / "narration.json")
    print(f"ナレーション: {wav}（{total:.2f}秒、{len(timeline)}文、新規生成 {generated}文）")
    for item in timeline:
        v = item.get("verify")
        mark = "" if v is None else ("OK " if v["ok"] else "要確認")
        print(f"  {mark:4s} {item['id']} {item['start']:6.2f}-{item['end']:6.2f}  {item['text']}")
        if v and not v["ok"]:
            print(f"         聞こえた文: {v['heard']}（一致率 {v['match']:.2f}）")
        if len(item.get("phrases", [])) > 1:
            for ph in item["phrases"]:
                print(f"         {ph['id']} {ph['start']:6.2f}-{ph['end']:6.2f}  {ph['text']}")


if __name__ == "__main__":
    main()
