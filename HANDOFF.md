# 引き継ぎ: AIショート編集パイプライン

別のアカウント（別の Mac ユーザー、別の Claude アカウント）でこのシステムを動かすための手順。2026-11-10 時点。

## 1. 何ができるか

- **B 型（字コンテ型）**: 字コンテ（PowerPoint / Google ドキュメント）と撮影素材から、縦型ショートを作る。台本を書き、Fish Audio でナレーションを作り、カットを割り当て、確認用の MP4 と Premiere 用の XML・SRT を書き出す。ドン・キホーテの商品紹介ショートで5本試した（魚が焼けルンルン、豆LOVEサラダ、れんこんハンバーグ弁当、ドドンとプリン）
- **A 型（素材型）**: 話している横型の動画から区間を選び、縦型ショートにする
- 判断の基準は `guides/` にある手引き。Claude はこれを読んで編集の判断をする

## 2. 導入（新しい Mac・アカウントで1回だけ）

前提: Apple Silicon の Mac、Homebrew、Premiere Pro 2026（26.0 以降）、Claude Code。

```bash
brew install python@3.11 ffmpeg
```

リポジトリをクローンし、その中で次を実行する（mlx-whisper などを入れる。初回は数分かかる）。

```bash
git clone https://github.com/iwasashotaf-rgb/ai-short.git
cd ai-short
./setup.sh
```

`projects/`（案件のデータ）と `voices/`（クローンした声）はリポジトリに入っていない。続きの案件を引き継ぐときは、`projects/案件名` のフォルダを別の手段で受け取って同じ場所に置く。

Fish Audio の API キーを `~/.zshrc` に書く（キーは前任者から別の手段で受け取る。このフォルダには入っていない）。

```bash
echo 'export FISH_API_KEY="ここにキー"' >> ~/.zshrc
```

- API の残高が0のときは無料モデル `s2.1-pro-free` しか使えない（有料モデルを指定すると HTTP 402）
- 声は CLAUDE.md の「使う声」の表から選ぶ（2026-11-10 時点では Fish 公式の「じん Jin」）
- ナレーションは `narrate.py 台本 --tune` で作る。文ごとの話す速さが毎秒9〜10.5かなに入り、文の中に0.3秒以上の無音がなくなるまで、外れた文だけ作り直す。測り方は機械の検品（`check.py`）と同じ

素材は外付けディスク（岩佐HDD_03）にある前提で、パスが案件のファイルに書いてある。別の場所に置くときは、`projects/案件名/shots/shots.json` の `path` を書き換える。

## 3. Premiere への読み込み

**手動で読み込む（拡張機能なしで動く）**

1. `ファイル > 読み込み` で `projects/案件名/01/premiere/01.xml` を読み込む。1080x1920 のシーケンスができ、クリップは元素材を参照する
2. `01_captions.srt` を読み込み、タイムラインのキャプショントラックに置く。書式はキャプションのトラックスタイルで付ける

**自動で読み込む（`scripts/premiere_import.py`）の注意**

自動読み込みは、自社の Premiere 拡張機能「Feriest Premiere Bridge」（`ai.feriest.premiere.bridge`）と通信する。拡張機能はこのリポジトリに入っていない。`~/Library/Application Support/Adobe/CEP/extensions/FeriestPremiereBridge` に入っている（別の Mac では管理者から受け取り、同じ場所に置き、署名のない拡張機能を読み込めるように `defaults write com.adobe.CSXS.12 PlayerDebugMode 1` を設定する）。Premiere で「ウィンドウ > エクステンション > Feriest Premiere Bridge」を開き、共有フォルダが `~/premiere-bridge` になっていることを確かめて Start Bridge を押す。以前使っていた「MCP Bridge (CEP)」は VIDEO OPS（株式会社Bewibe のライセンス製品、利用期限 2026-11-15）の付属素材なので使わず、引き継ぎ先にも渡さない。

## 4. Claude に渡すときの決まり（前任者の運用ルール）

新しいアカウントの Claude には記憶（メモリ）が引き継がれないので、次をそのまま伝えるか、`~/.claude/CLAUDE.md` に書く。

- **出力はすべて日本語にする。** recap、途中の一言、表、キャプションも含む（英語で出して何度も指摘された）
- **VIDEO OPS を使わない。** `~/video-ops` と関連スキル（video-ops、short-video-pipeline、premiere-bridge-ops など）を呼ばない、中身を写さない（詳しくは CLAUDE.md の末尾）
- **作る役は Sonnet、採点役は既定のモデル。** 作る役は1本あたり25万〜35万トークンを使う
- **採点と修正は回しすぎない。** 1回採点して大きな問題だけ直したら、早めに Premiere に出して人が見る（ユーザーの指摘「採点しすぎ直しすぎ」、2026-11-10）
- 実運用では Premiere 上の見え方が大事。プレビューのテロップは確認用

## 5. 1本作る流れ（B 型）

詳しくは `CLAUDE.md` の B 型の章。Claude に「`CLAUDE.md` と `guides/donki_short_guide.md` を読んで、この素材と字コンテで作って」と頼めばよい。

1. 字コンテを読む: `scripts/jikonte_pptx.py 字コンテ.pptx projects/案件名/jikonte`（右の外に書かれた依頼者のコメントは `comments` に入る）
2. 素材の一覧画像と手ブレ: `scripts/shots.py 素材フォルダ projects/案件名/shots --every 3 --max 12`、`scripts/shake.py 素材フォルダ projects/案件名/shots/shake.json`
3. 作る役（Sonnet）が、素材の記録・台本・ナレーション・カット表を作り、`scripts/build_b.py` で書き出し、`scripts/review.py` で検品材料と機械の検品（`check.json`）を作る。「必ず直す」が0件になるまで作る役が自分で直す
4. 採点役が `guides/scoring_brief.md` の文面で1回だけ採点し、作る役が「次に直すこと」（最大3件）だけを直す。再び採点しない
5. `scripts/premiere_import.py projects/案件名/01/premiere`（または手動）で Premiere に読み込む

## 6. 手引きと道具の一覧

| ファイル | 中身 |
|---|---|
| `CLAUDE.md` | Claude 用の手順書（道具の使い方、決まり） |
| `guides/donki_short_guide.md` | ドンキ商品紹介ショートの編集の手引き v3（判断の原則、採点表12項目24点） |
| `guides/food_sizzle.md` | 食品の主役を美味しそうに見せる手引き（案件共通）。主役の選び方と寄せ |
| `guides/research/donki_cp_videos.md` | @donki_cp の人の完成版8本の分析（手引きの根拠） |
| `scripts/narrate.py` | Fish Audio（または CoeFont）でナレーションを作る。`--estimate` で尺を見積もり、`--tune` で文ごとの速さを揃える |
| `scripts/voice_clone.py` | 許諾のある声・依頼者が指定したフリー素材の声から、ナレーション用の声を作る（決まりは CLAUDE.md） |
| `scripts/build_b.py` | B 型の書き出し（プレビュー・timeline・Premiere 用 XML/SRT） |
| `scripts/review.py` | 検品材料（各カットのコマ、尺、話す速さの実測、手ブレ）。最後に check.py を走らせる |
| `scripts/check.py` | 機械の検品。採点表の項目3・4・5・6・9を自動で採点し、同じ区間の重なりなどを「必ず直す」に挙げる |
| `guides/scoring_brief.md` | 採点役への頼み方（読むもの・採点の決まり・1ページの書き方） |
| `scripts/shake.py` | 素材の手ブレを測る（1.2% を超えたら使わない） |
| `scripts/framegrid.py` / `scripts/framing.py` | 主役の範囲を読む目盛り付きのコマ／範囲から寄せの倍率と位置を計算 |
| `scripts/jikonte_pptx.py` / `scripts/jikonte_parse.py` | 字コンテの読み込み（PowerPoint / Google ドキュメント） |
| `scripts/premiere_import.py` / `scripts/premiere_bridge.py` | Premiere への自動読み込み（上の注意を参照） |

## 7. 試作の状況（2026-11-10）

`projects/` は git に入れていない。試作の台本・カット表・採点の記録は `handoff_projects.zip`（同じ場所に置いた）にある。素材と生成した音声・動画は入っていない。

| 案件 | フォルダ | 状況 |
|---|---|---|
| 魚が焼けルンルン | `projects/sakana/03` | 採点 20/20（v2 の採点表）。人の完成版と比べた |
| 豆LOVEサラダ | `projects/mame/01` | 採点 19/20。手ブレの大きい2カットを差し替え済み |
| れんこんハンバーグ弁当 | `projects/renkon/01` | 採点 20/20。ごはんと持ち上げのカットを寄せ済み、商品名は2行1枚 |
| ドドンとプリン | `projects/purin/01` | 3回直した版を Premiere に出した（最終の採点はしていない。2回目は 19/24） |
| 11/09 撮影の4本（ポリスウェット・GLACIER RUSH・発酵化粧水・フェイスマスク） | `projects/{sweat,glacier,hakko,mask}/01`、じん Jin の声の版は `01_jin` | 新しい流れ（機械の検品 → 採点1回 → 直し1回）で作り、Premiere に読み込んだ |

依頼者への確認事項は、各案件の `storyboard.json` の `questions` にある。

## 8. 残っている課題

- テロップを MOGRT（エッセンシャルグラフィックス）で載せる方式に替える可能性がある
- 寄せ（subject）は B 型だけが対応。A 型には未対応
- 食品の手引きに、プリンの試作で出た改訂案（横に並ぶ2つの主役、柔らかく崩れる商品、主役が皿・スプーン・カップにまたがるとき）がまだ入っていない（`projects/purin/01/review_score.md` に案がある）
