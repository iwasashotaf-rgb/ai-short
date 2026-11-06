"""Premiere Pro の拡張機能「MCP Bridge (CEP)」に ExtendScript を渡して実行させる。

拡張機能の仕様（拡張機能の bridge-cep.js で確認、2026-11-06）:
  - 共有フォルダ（既定 /tmp/premiere-mcp-bridge）に command-<id>.json を置くと、
    パネルが 0.25 秒ごとに見つけて実行し、response-<id>.json を書く
  - command: {"id": str, "script": str, "timeoutMs": int}
  - response: {"success": true, "result": ...} または {"success": false, "error": ...}
  - script に eval( / new Function( / require( / process. / __dirname / __filename /
    child_process が含まれると、実行せずに "Script validation failed" を返す（コメント内でも）
  - パネルで Start Bridge を押していないと応答しない

使い方（他のスクリプトから）:
  from premiere_bridge import run_jsx
  out = run_jsx(jsx_text)   # jsx_text は return を使ってよい（関数に包んで送る）
"""
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

BRIDGE_DIR = Path(os.environ.get("PR_BRIDGE_DIR", "/tmp/premiere-mcp-bridge"))
BANNED = [r"eval\s*\(", r"\bnew\s+Function\s*\(", r"\brequire\s*\(", r"\b__dirname\b",
          r"\b__filename\b", r"\bprocess\.", r"\bchild_process\b"]


class BridgeError(Exception):
    pass


def run_jsx(script, timeout_sec=120, wrap=True):
    """ExtendScript を実行し、拡張機能の応答（dict）を返す。応答がなければ BridgeError。"""
    if not BRIDGE_DIR.is_dir():
        raise BridgeError(f"共有フォルダがありません: {BRIDGE_DIR}。Premiere の MCP Bridge (CEP) パネルで "
                          "Save Configuration と Start Bridge を押してください")
    bad = [p for p in BANNED if re.search(p, script, re.I)]
    if bad:
        raise BridgeError(f"拡張機能が実行を拒否する語が含まれています: {bad}")
    if wrap:  # トップレベルの return は構文エラーになるので関数に包む
        script = "(function(){\n" + script + "\n})();"
    cid = "ais" + uuid.uuid4().hex[:12]
    cmd = BRIDGE_DIR / f"command-{cid}.json"
    res = BRIDGE_DIR / f"response-{cid}.json"
    tmp = BRIDGE_DIR / f".tmp-{cid}"
    tmp.write_text(json.dumps({"id": cid, "script": script, "timeoutMs": int(timeout_sec * 1000)},
                              ensure_ascii=False), encoding="utf-8")
    tmp.rename(cmd)  # 書きかけのファイルをパネルに読ませない
    deadline = time.time() + timeout_sec + 5
    while time.time() < deadline:
        if res.exists():
            time.sleep(0.05)
            try:
                data = json.loads(res.read_text(encoding="utf-8"))
            finally:
                res.unlink(missing_ok=True)
            return data
        time.sleep(0.25)
    cmd.unlink(missing_ok=True)
    raise BridgeError("Premiere から応答がありません。MCP Bridge (CEP) パネルで Start Bridge を押してください")


def ping():
    r = run_jsx("return app.version + ' / ' + (app.project ? app.project.name : 'プロジェクトなし');",
                timeout_sec=20)
    if not r.get("success"):
        raise BridgeError(f"実行エラー: {r.get('error')}")
    return r.get("result")


if __name__ == "__main__":
    try:
        print("応答あり:", ping())
    except BridgeError as e:
        print("NG:", e, file=sys.stderr)
        sys.exit(1)
