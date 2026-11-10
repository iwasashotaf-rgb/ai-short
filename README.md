# ai-short — AIショート編集パイプライン

撮影素材と字コンテから、9:16 の縦型ショートを作る道具一式。Claude Code が手引き（`guides/`）を読んで台本・カット・テロップを決め、スクリプト（`scripts/`）が確認用の MP4 と Premiere Pro 用の XML・SRT を書き出す。ナレーションは Fish Audio で生成する。

## 始め方

```bash
git clone https://github.com/iwasashotaf-rgb/ai-short.git
cd ai-short
brew install python@3.11 ffmpeg
./setup.sh
```

Fish Audio の API キーを `~/.zshrc` に `export FISH_API_KEY="..."` で書く（キーはリポジトリに入れない。管理者から別の手段で受け取る）。

## 読む順番

1. [HANDOFF.md](HANDOFF.md) — 導入、Premiere への読み込み、運用の決まり、道具の一覧
2. [CLAUDE.md](CLAUDE.md) — Claude Code が読む手順書。A 型（話している素材から切り出す）と B 型（字コンテから作る）の手順
3. [guides/donki_short_guide.md](guides/donki_short_guide.md) — 編集の判断の基準と採点表

## リポジトリに入れないもの

`.gitignore` で次を外している。各自の手元に置く。

| もの | 理由 |
|---|---|
| `projects/`（案件ごとの台本・カット表・生成物） | 依頼者の素材と生成物を含む。共有するときは案件ごとに別の手段で渡す |
| `voices/`（クローンした声のサンプルと許諾の記録） | 人の声のデータを含む |
| 撮影素材 | 外付けディスクにある。案件の `shots/shots.json` の `path` が素材の場所を指す |
| API キー | `~/.zshrc` に書く |

## 変更するとき

スクリプトを直したら、テスト素材で通して結果が変わらないことを確かめる（手順は CLAUDE.md の「3. 検証してから書き出す」）。テスト素材は `./test/make_test_media.sh` で作る。
