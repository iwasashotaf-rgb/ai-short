# AIショート編集パイプライン（素材 → 縦型ショート → Premiere）

収録した横型の動画素材から、単独で意味が通る区間を Claude が選び、9:16 の縦型ショートを作る。
確認用の MP4 と、Premiere Pro で仕上げるための XML（カット済みタイムライン）・SRT（字幕とタイトル）を書き出す。

## 役割分担

| 担当 | やること |
|---|---|
| 依頼者 | 素材を渡す。視聴者・トーン・本数を伝える。プレビューを見て直す点を伝える。最後に Premiere で仕上げる |
| Claude | 文字起こしを読んで区間と字幕を決め、カット表（`*.edl.json`）を書く。スクリプトを実行し、プレビューを検品する |
| スクリプト | 文字起こし・カット点の計算・プレビュー書き出し・XML/SRT 書き出し（`scripts/`） |

## 手順

すべてこのフォルダで実行する。Python は `.venv/bin/python` を使う。`.venv` がなければ、先に `./setup.sh` で作る（mlx-whisper・Pillow・pykakasi を入れる。初回は数分かかる）。

### 1. 文字起こし

```bash
.venv/bin/python scripts/transcribe.py 素材.mp4 projects/案件名
```

`projects/案件名/transcript.txt` を読む。行ごとに発話・時刻・単語番号の範囲（`[w17-39]`）が並ぶ。
単語ごとの番号は `transcript.json` の `words` にある。区間を決めるときは次で該当範囲の単語を表示する。

```bash
.venv/bin/python -c "import json;t=json.load(open('projects/案件名/transcript.json'));print(' '.join(f\"{w['i']}:{w['text']}\" for w in t['words'][17:66]))"
```

### 2. 区間を選んでカット表を書く（Claude の仕事）

`projects/案件名/shorts/01.edl.json` のように1本につき1ファイル書く。形式は `scripts/timeline.py` の先頭にある。
見本は `test/work/shorts/01.edl.json`。

区間を選ぶときの決まり:

- **単独で意味が通る区間を選ぶ。** 長尺の要約を詰め込まず、結論を1つに絞る。前の話を受けた「それで」「さっきの」から始まる区間は選ばない
- **冒頭の1〜2秒で何の話かが分かるようにする。** 挨拶・前置き・「本題です」は削る
- **言い直しは前半を削る。**「3つ、あ、いや、2つあります」なら「2つあります」だけを残す。言い淀み（えーと、まあ）が文頭にあればその語を外してカットを始める
- **無音を全部は消さない。** 文と文の間は `pad`（既定で前0.1秒・後0.2秒）で残る。操作画面を見せる区間は `pad.tail` を延ばして読む時間を残す
- 尺は 15〜60 秒（`target_sec` で変えられる）

字幕を書くときの決まり:

- 誤認識を直して書く（例: 「部員」→「無音」）。数字は算用数字にそろえる
- **意味のまとまりで区切る。** 1枚は2行まで、1行13文字まで。改行は `\n` で書く
- 1枚あたり毎秒9文字を超えないようにする。超えるときは単語範囲を分けて2枚にする
- 強調は結論の1か所だけにする（今の版は文字色の指定がないので、Premiere で付ける）

### 3. 検証してから書き出す

```bash
.venv/bin/python scripts/build.py projects/案件名/transcript.json projects/案件名/shorts/01.edl.json --check
.venv/bin/python scripts/build.py projects/案件名/transcript.json projects/案件名/shorts/01.edl.json
```

`--check` で尺・カット・字幕の一覧と「注意」が出る。注意（行の文字数超過、毎秒の文字数超過、字幕のない発話）は直してから書き出す。

スクリプトを直したら、テスト素材で通して結果が変わらないことを確かめる（期待値: 尺26.19秒・カット6・字幕11）。テスト素材 `test/talk.mp4` は git に入れていないので、なければ `./test/make_test_media.sh` で作る。

### 4. 検品（書き出したら毎回やる）

1. プレビューを再度文字起こしし、残すはずの語が欠けていないこと・削るはずの言い直しが残っていないことを確かめる
2. 字幕が出ている時刻のフレームを数枚切り出して並べ、文字が画面幅に収まっているか・タイトルと重なっていないかを目で見る

```bash
.venv/bin/python -c "import mlx_whisper as m;r=m.transcribe('projects/案件名/shorts/01/preview.mp4',path_or_hf_repo='mlx-community/whisper-large-v3-turbo',language='ja');[print(f\"{s['start']:6.2f} {s['text']}\") for s in r['segments']]"
```

### 5. Premiere Pro で仕上げる

書き出し先 `shorts/01/premiere/` の3ファイルを使う。

**自動で読み込む場合**（Premiere で `ウィンドウ > エクステンション > MCP Bridge (CEP)` を開き、Start Bridge を押してあること）:

```bash
.venv/bin/python scripts/premiere_import.py projects/案件名/shorts/01/premiere
```

開いているプロジェクトの「AIショート」ビンに XML と SRT を読み込み、字幕とタイトルをキャプショントラックとして置き、シーケンスを開く。
結果に `video=カット数 audio=カット数` と `caption …: true` が出れば成功。音声が0本のときは XML の音声トラック属性が欠けている（`premiere_export.py` のコメント参照）。

**手動で読み込む場合**:

1. `ファイル > 読み込み` で `01.xml` を読み込む → 1080x1920 のシーケンス「01」ができる。クリップは元素材を参照しているので、カット点を前後に伸ばせる。カットごとの説明はシーケンスのマーカーに入っている
2. `01_captions.srt` を読み込み、タイムラインのキャプショントラックに置く。文言・タイミング・書式はキャプションパネルとエッセンシャルグラフィックスで直せる
3. `01_title.srt` も同じように置き、別のキャプショントラックで位置を上部に移す

リフレームが `fit` のときは、V1 に背景用の拡大映像、V2 に全体を収めた映像が入る。背景のぼかしは XML で渡せないので、Premiere で V1 にガウスぼかしを掛ける。

## ナレーションを生成する（Fish Audio）

台本を `projects/案件名/script.json` に書き、1文ずつ音声にしてつなぐ。形式は `scripts/narrate.py` の先頭にある。見本は `test/narr/script.json`。

```bash
.venv/bin/python scripts/narrate.py projects/案件名/script.json
```

- API キーは `~/.zshrc` の `FISH_API_KEY` から読む。API の残高が0のときは無料モデル `s2.1-pro-free` しか使えない（有料モデルを指定すると HTTP 402 が返る）
- wav のサンプルレートは 44100 まで。生成後に 48kHz に変換してつなぐ
- 生成した音声は `narration/cache/` に保存し、文面・声・設定が同じ文は API を呼ばずに再利用する。1文を直したときは、その文だけが生成し直される
- 生成した音声を文字起こしして台本と比べ、一致率が0.92未満の文を「要確認」として表示する
- **文字起こしでは漢字の読み違いを見分けられない**（「1日」を「ついたち」と読んでも「1日」と書き起こされる）。台本を書いた時点で、読みが複数ある語（1日・今日・辛い・生物・方・何人 など）と、英字・記号・単位を Claude が洗い出し、`yomi` に読み仮名を書く
- 声ライブラリの声は、実在の人物や既存キャラクターに似せたものを使わない。商用利用の条件は声ごとに確認する

## 字コンテから台本を作る

字コンテは Google ドキュメントで届く。1つのドキュメントに複数の動画があり、各動画は「概要の表」と「テロップ/ナレーション｜映像 の2列の表」の組でできている。

1. Google Docs コネクタの `read_doc` でドキュメントを取得する（結果が大きいとファイルに保存される）
2. 動画ごとに分ける

```bash
.venv/bin/python scripts/jikonte_parse.py read_docの結果ファイル projects/案件名/jikonte --tab タブID
```

   `jikonte/index.txt` に動画の一覧、`jikonte/01.raw.json` に1本分の行（文面・映像指示）が出る。タブIDは URL の `?tab=` の値

3. Claude が `01.raw.json` を読み、`projects/案件名/01/script.json`（ナレーション）と `storyboard.json`（行ごとのテロップ・映像指示・演出指示）を書く。見本は `test/jikonte/01/`

台本に直すときの決まり:

- 1行に複数の文があれば、ナレーションは文ごとに分ける（`lines` の `row` に字コンテの行番号を書く）。テロップは字コンテの改行どおりに `storyboard.json` の `telop` に残す
- 「（NA）…(テロップ)…」「テロップ：…」の行は、ナレーションとテロップを分けて書く
- 文面に入っている演出指示（「（保存必須のアイコン掲出）」など）はナレーションから外し、`directions` に書く
- 絵文字・記号（🚨 ⚠️ ◎ 📍 ✨）はナレーションから外し、テロップには残す
- ①② などの番号、英字のブランド名（ASBee、SKECHERS）、英単語（CHECK）、価格（￥8,690(税込)）、読みが複数ある語には `yomi` を書く
- 「（子どもの一言）」のように話者が違う行、誤字に見える箇所（「PUAM」など）、読み方を決めかねる箇所は、勝手に直さず `storyboard.json` の `questions` に書いて依頼者に確認する

ASBee の字コンテで依頼者が確認済みの読み方（2026-11-05）。同じ案件の別の動画にもこのまま使う:

| 字コンテの表記 | ナレーションの読み |
|---|---|
| 特徴① / 特徴② | 特徴、その1 / その2（`yomi` は「その、いち」） |
| 【正解】 | 正解！ |
| ASBee | アスビー |
| CHECK | チェック |
| 絵文字（🚨 📍 ✨ 🎀 など） | 読まない（テロップにだけ残す） |

声は「元気な女性」（`5161d41404314212af1254556477c17d`）、速さ1.1倍、文の間0.25秒で作った①が了承されている。

4. `narrate.py` でナレーションを作る。「要確認」の文は音声を聞いて確かめる。表記の揺れ（大ケガ／大怪我、コレ／これ）は一致とみなすので、残るのはブランド名や聞き取りにくい語が多い

## B 字コンテ型: 素材を整理してショットを割り当てる

見本は `projects/sakana/01/`（魚が焼けルンルン、2026-11-06 にテスト稼働。`projects/` は git に入れていない）。

1. 字コンテが PowerPoint のときは `scripts/jikonte_pptx.py 字コンテ.pptx projects/案件名/jikonte` で読む（Google ドキュメントは `jikonte_parse.py`）。列はテキストボックスの横位置で「映像／ナレーション／テロップ」を判定する
2. 台本（`script.json`）とカット表（`storyboard.json`）を書き、`narrate.py` でナレーションを作る（上の章と同じ）
3. 素材の一覧画像を作る: `scripts/shots.py 素材フォルダ projects/案件名/shots --every 3 --max 12`
4. 一覧画像（`shots/sheets/*.jpg`）を見て、`shots/shots.json` の各素材の `segments` に「何秒から何秒に何が映っているか・寄りか引きか・使えるか」を書く。素材が多いときはサブエージェントに任せ、字コンテの映像指示ごとの候補も報告させる
5. 候補の時刻のコマを切り出して目で確かめ、`cutlist.json` に行ごとのショット（素材名・開始秒・必要なら dur と center_x）を書く。形式は `scripts/build_b.py` の先頭
6. `scripts/build_b.py projects/案件名/01/cutlist.json --check` で長さと注意を確かめ、`--check` を外して書き出す
7. プレビューから各カットのコマを切り出して並べ、映像指示に合っているか・テロップが収まっているかを見る
8. `premiere_import.py projects/案件名/01/premiere` で Premiere に読み込む。映像はショット数、音声はナレーション1本になる

注意:
- iPhone の縦動画は横長（3840x2160）で記録され、回転の指示が付いている。`common.probe` は回転後の幅・高さを返す。これを横長のまま扱うと Premiere でのスケールがずれる
- 行の長さはナレーションで決まる（その行の最初の文の開始 − `lead_sec` から、次の行まで）。素材の残りが足りないとエラーになる
- 字コンテの映像指示に合う素材がないとき（例: 煮込み・ホットサンドの調理映像がなく箱しかない）は、箱の映像でつながず、同じ商品のまだ使っていない使用シーン（食材を入れる、レンジに入れる、取り出す など）で置き換える（依頼者の方針、2026-11-06）

## 出力の一覧

```
projects/案件名/
  transcript.json / transcript.txt   文字起こし（単語の時刻・無音区間）
  shorts/01.edl.json                 カット表（Claude が書く）
  shorts/01/preview.mp4              確認用（字幕・タイトル焼き込み）
  shorts/01/timeline.json            素材フレームと出力フレームの対応表
  shorts/01/premiere/01.xml          Premiere 用タイムライン（FCP7 XML）
  shorts/01/premiere/01_captions.srt 字幕
  shorts/01/premiere/01_title.srt    タイトル
```

## 既知の制約

- この環境の ffmpeg には字幕フィルタがないので、プレビューの字幕は Pillow で PNG に描いて重ねている。見た目は `scripts/preview.py` の `CAPTION_STYLE` と `TITLE_STYLE` で変える
- XML のクロップ位置（`reframe.center_x` が 0.5 以外のとき）は、Premiere 上の位置の換算をまだ実機で確かめていない
- デザインしたテロップ（色・部分強調・アニメーション）は SRT では渡せない。Premiere 上でキャプションにトラックスタイルを当てるか、「キャプションをグラフィックにアップグレード」して手で付ける

## VIDEO OPS（外部製品）を使わない

`~/video-ops` と、そこからリンクされたスキル（video-ops、short-video-pipeline、premiere-bridge-ops、audio-precision、reference-video-clone、script-from-footage、short-telop-craft、short-video-cut-craft、short-video-qc、video-revision-db-workflow、`_video-core`）、`~/video-ops-support` は株式会社Bewibe のライセンス製品。契約は更新しない（2026-11-06 決定。契約終了 11-12、利用期限 11-15）。

- このパイプラインから VIDEO OPS のスクリプト・スキル・参照文書を呼ばない。中身を写さない（著作権は Bewibe、契約終了後は派生物も削除の対象）
- Premiere との通信は自前の `scripts/premiere_bridge.py` を使う。相手は Premiere の拡張機能「MCP Bridge (CEP)」で、VIDEO OPS とは別物
- 動作確認: `.venv/bin/python scripts/premiere_bridge.py` で「応答あり: <Premiereの版> / <プロジェクト名>」が出ればよい
