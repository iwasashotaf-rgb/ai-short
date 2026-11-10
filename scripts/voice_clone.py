"""声のクローン: 参考動画（または音声）から声のサンプルを切り出し、Fish Audio に非公開の声のモデルを作る。

作ったモデルは script.json の voice に "clone": "<名前>" と書くと narrate.py で使える。

使い方:
  # 1. 参考動画から声のサンプルを切り出す（API は呼ばない）
  .venv/bin/python scripts/voice_clone.py prepare 名前 参考1.mp4 [参考2.mp4 ...] \
      --owner "声の持ち主" --consent "許諾の内容（誰が・いつ・どの用途で認めたか）"
  # 2.（任意）Fish Audio に非公開の声のモデルを作る。API の残高が0だと作れない（HTTP 403）。
  #    作らなくても、narrate.py はサンプルを毎回送って同じ声で読ませる（その場でのクローン。無料のモデルで使える）
  .venv/bin/python scripts/voice_clone.py create 名前
  # 一覧: どの声をクローン済みか、根拠と許諾の記録を表示する
  .venv/bin/python scripts/voice_clone.py list
  # 3. 試しに1文読ませる
  .venv/bin/python scripts/voice_clone.py test 名前 "この文を読ませる"

決まり（CLAUDE.md「ナレーションを生成する」）:
  - クローンしてよいのは、声の持ち主の許諾がある声（--basis 許諾）と、ライセンスが合成音声への利用を禁じていない
    フリー素材の声（--basis フリー素材 --license URL）だけ。--owner と --consent がないと prepare は止まる
  - 許諾の記録は voices/<名前>/voice.json に残る。voices/ は git に入れない（人の声のデータなので）
  - モデルは非公開（private）で作る

切り出し方:
  1. ffmpeg で 44.1kHz モノラルの wav にする
  2. mlx-whisper で文字起こしし、発話の区間ごとに文面と時刻を得る
  3. 聞き取りの確信度が低い区間（BGM・効果音・別の人の声が混じりやすい）を外す
  4. 区間を1つずつ切り出し、0.25秒の間を挟んでつないで 8〜25 秒のサンプルにし、文面と一緒に保存する（Fish Audio に文面も送ると発音が安定する）
  BGM が大きい動画では声だけを取り出せない。Fish Audio の enhance_audio_quality（雑音除去）は有効にするが、
  BGM のない区間か、BGM のない元の収録を使う方がよい。
"""
import argparse
import json
import subprocess
import sys
import uuid
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from narrate import api_key, tts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VOICES = ROOT / "voices"
RATE = 44100
SAMPLE_MIN, SAMPLE_MAX = 8.0, 25.0   # 1本のサンプルの長さ（秒）
MAX_SAMPLES = 12
MIN_LOGPROB = -0.8                   # これより確信度の低い区間は外す
MAX_NOSPEECH = 0.4
WHISPER = "mlx-community/whisper-large-v3-turbo"


def voice_dir(name):
    return VOICES / name


def load_voice(name):
    p = voice_dir(name) / "voice.json"
    if not p.exists():
        raise SystemExit(f"声 {name} がありません（先に prepare を実行してください）: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_voice(name, v):
    (voice_dir(name) / "voice.json").write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")


def cut(src, start, end, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
                    "-vn", "-ac", "1", "-ar", str(RATE), "-af", "afade=t=in:d=0.02,areverse,afade=t=in:d=0.05,areverse",
                    str(out)], check=True)


def prepare(args):
    if not args.owner.strip() or not args.consent.strip():
        raise SystemExit("--owner（声の持ち主）と --consent（許諾の内容）を書いてください。許諾のない声はクローンしません")
    if args.basis == "フリー素材" and not args.license.strip():
        raise SystemExit("フリー素材の声は --license にライセンスの URL を書いてください（合成音声への利用を禁じていないことを確かめる）")
    import mlx_whisper
    d = voice_dir(args.name)
    sd = d / "samples"
    sd.mkdir(parents=True, exist_ok=True)
    for old in sd.glob("*.wav"):
        old.unlink()
    picked = []
    for src in map(Path, args.sources):
        wav = d / f"_src_{src.stem}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(RATE), str(wav)],
                       check=True)
        r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=WHISPER, language="ja")
        segs = [s for s in r["segments"] if s["text"].strip()]
        good = [s for s in segs if s.get("avg_logprob", 0) >= MIN_LOGPROB and s.get("no_speech_prob", 0) <= MAX_NOSPEECH]
        print(f"{src.name}: 発話 {len(segs)}区間、使える {len(good)}区間（合計 {sum(s['end'] - s['start'] for s in good):.1f}秒）")
        for x in good:
            picked.append({"src": src, "wav": wav, "start": x["start"], "end": x["end"], "text": x["text"].strip()})
    if not picked:
        raise SystemExit("聞き取れる発話が見つかりません。BGM のない区間か元の収録を使ってください")
    # 発話の区間を1つずつ切り出し、0.25秒の間を挟んでつなぎ、SAMPLE_MIN〜SAMPLE_MAX 秒のサンプルにする
    tmp = d / "_seg"
    tmp.mkdir(exist_ok=True)
    groups, cur, cur_sec = [], [], 0.0
    for p in picked:
        dur = p["end"] - p["start"] + 0.15
        if cur and cur_sec + dur > SAMPLE_MAX:
            groups.append(cur)
            cur, cur_sec = [], 0.0
        cur.append(p)
        cur_sec += dur + 0.25
    if cur:
        groups.append(cur)
    if len(groups) > 1 and sum(p["end"] - p["start"] for p in groups[-1]) < SAMPLE_MIN:
        groups[-2] += groups.pop()  # 短すぎる最後のまとまりは前に足す
    groups = groups[:MAX_SAMPLES]
    samples = []
    for gi, g in enumerate(groups):
        parts = []
        for k, p in enumerate(g):
            seg = tmp / f"g{gi}_{k}.wav"
            cut(p["wav"], max(p["start"] - 0.05, 0), p["end"] + 0.1, seg)
            parts.append(seg)
        out = sd / f"s{gi + 1:02d}.wav"
        inputs = sum((["-i", str(x)] for x in parts), [])
        n = len(parts)
        flt = "".join(f"[{i}:a]apad=pad_dur=0.25[a{i}];" for i in range(n)) + "".join(f"[a{i}]" for i in range(n)) \
            + f"concat=n={n}:v=0:a=1[o]"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", flt, "-map", "[o]",
                        "-ac", "1", "-ar", str(RATE), str(out)], check=True)
        sec = sum(p["end"] - p["start"] for p in g)
        text = "".join(p["text"] for p in g)
        samples.append({"file": out.name, "text": text, "seconds": round(sec, 1),
                        "parts": [{"source": str(p["src"]), "start": round(p["start"], 2), "end": round(p["end"], 2)}
                                  for p in g]})
        print(f"  {out.name} {sec:5.1f}秒（{n}区間）  {text[:40]}")
    for w in list(d.glob("_src_*.wav")) + list(tmp.glob("*.wav")):
        w.unlink()
    tmp.rmdir()
    v = {"name": args.name, "owner": args.owner, "basis": args.basis, "consent": args.consent,
         "license": args.license or None, "prepared": date.today().isoformat(),
         "sources": [str(Path(s).resolve()) for s in args.sources], "samples": samples,
         "total_sec": round(sum(s["seconds"] for s in samples), 1), "reference_id": None}
    save_voice(args.name, v)
    print(f"サンプル {len(samples)}本・合計 {v['total_sec']}秒 → {sd}")
    print("サンプルを聞いて、別の人の声・BGM・効果音が入っていないことを確かめてから create を実行してください")


def multipart(fields, files):
    b = uuid.uuid4().hex
    out = bytearray()
    for k, val in fields:
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{val}\r\n".encode("utf-8")
    for k, path in files:
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{Path(path).name}\"\r\n"
                f"Content-Type: audio/wav\r\n\r\n").encode("utf-8")
        out += Path(path).read_bytes() + b"\r\n"
    out += f"--{b}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={b}"


def create(args):
    v = load_voice(args.name)
    if v.get("reference_id") and not args.force:
        raise SystemExit(f"声 {args.name} のモデルは作成済みです（{v['reference_id']}）。作り直すときは --force")
    sd = voice_dir(args.name) / "samples"
    fields = [("type", "tts"), ("title", f"ai-short {args.name}"), ("train_mode", "fast"),
              ("visibility", "private"), ("enhance_audio_quality", "true" if not args.no_enhance else "false")]
    fields += [("texts", s["text"]) for s in v["samples"]]
    files = [("voices", sd / s["file"]) for s in v["samples"]]
    body, ctype = multipart(fields, files)
    req = urllib.request.Request("https://api.fish.audio/model", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {api_key()}", "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            m = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Fish Audio がエラーを返しました（HTTP {e.code}）: {e.read().decode('utf-8', 'replace')[:300]}")
    v.update({"reference_id": m["_id"], "state": m.get("state"), "created": date.today().isoformat(),
              "visibility": m.get("visibility")})
    save_voice(args.name, v)
    print(f"声のモデルを作りました: {m['_id']}（状態 {m.get('state')}、{m.get('visibility')}）")
    print(f'script.json の voice に "clone": "{args.name}" と書くと使えます')


def test(args):
    voice = resolve({"clone": args.name, "model": args.model, "speed": args.speed,
                     **({"clone_mode": "instant"} if args.instant else {})})
    out = voice_dir(args.name) / "test.wav"
    tts(args.text, voice, api_key(), out)
    import mlx_whisper
    heard = "".join(s["text"] for s in mlx_whisper.transcribe(str(out), path_or_hf_repo=WHISPER, language="ja")["segments"])
    how = "モデル " + voice["reference_id"] if voice.get("reference_id") else "その場でのクローン（サンプルを送る）"
    print(f"生成: {out}（{how}）\n  台本: {args.text}\n  聞こえた: {heard}")


def list_voices(args):
    rows = sorted(VOICES.glob("*/voice.json")) if VOICES.exists() else []
    if not rows:
        print("クローンした声はまだありません")
    for p in rows:
        v = json.loads(p.read_text(encoding="utf-8"))
        how = f"モデル {v['reference_id']}" if v.get("reference_id") else "その場でのクローン"
        print(f"{v['name']}: {v.get('owner')}｜根拠 {v.get('basis', '許諾')}｜{v.get('total_sec')}秒・{len(v['samples'])}本｜{how}")
        print(f"    許諾: {v.get('consent')}" + (f"｜ライセンス: {v['license']}" if v.get("license") else ""))


def resolve(voice):
    """narrate.py から呼ぶ: voice に "clone" があれば、その声で読ませる設定にして返す。
    Fish Audio にモデルを作ってあれば reference_id を使う。なければ、サンプルを毎回送る（その場でのクローン）。
    "clone_mode": "instant" を書くと、モデルがあってもサンプルを送る。"""
    if "clone" not in voice:
        return voice
    import hashlib
    v = load_voice(voice["clone"])
    if v.get("reference_id") and voice.get("clone_mode") != "instant":
        return {**voice, "reference_id": v["reference_id"]}
    sd = voice_dir(voice["clone"]) / "samples"
    refs = [{"audio": str(sd / s["file"]), "text": s["text"]} for s in v["samples"]]
    sig = hashlib.sha1(b"".join(Path(r["audio"]).read_bytes() for r in refs)).hexdigest()[:12]
    return {**voice, "references": refs, "clone_sig": f"clone:{voice['clone']}:{sig}"}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("name")
    p.add_argument("sources", nargs="+")
    p.add_argument("--owner", required=True)
    p.add_argument("--consent", required=True)
    p.add_argument("--basis", choices=["許諾", "フリー素材"], default="許諾",
                   help="クローンしてよい根拠。許諾=声の持ち主が認めた / フリー素材=ライセンスが合成音声への利用を禁じていない")
    p.add_argument("--license", default="", help="フリー素材のライセンスの URL（--basis フリー素材 のとき必須）")
    p.set_defaults(fn=prepare)
    c = sub.add_parser("create")
    c.add_argument("name")
    c.add_argument("--force", action="store_true")
    c.add_argument("--no-enhance", action="store_true")
    c.set_defaults(fn=create)
    t = sub.add_parser("test")
    t.add_argument("name")
    t.add_argument("text")
    t.add_argument("--model", default="s2.1-pro-free")
    t.add_argument("--speed", type=float, default=1.0)
    t.add_argument("--instant", action="store_true", help="モデルがあってもサンプルを送って読ませる")
    t.set_defaults(fn=test)
    li = sub.add_parser("list")
    li.set_defaults(fn=list_voices)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
