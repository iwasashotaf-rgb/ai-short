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

**自動で読み込む場合**（Premiere で `ウィンドウ > エクステンション > Feriest Premiere Bridge` を開き、共有フォルダが `~/premiere-bridge` になっていることを確かめて Start Bridge を押してあること）:

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

- **CoeFont でも作れる**（`narrate.py` は Fish Audio と CoeFont のどちらでも読ませられる）。script.json の voice を `{"provider": "coefont", "coefont": "<声のID>", "speed": 1.0, "pitch": 0, "kuten": 0.1, "toten": 0.2}` にする。鍵は `~/.zshrc` の `COEFONT_ACCESS_KEY` と `COEFONT_ACCESS_SECRET`。使える声の一覧は `narrate.py --coefonts`。CoeFont は読みをひらがなで渡せるので、読み違えやすい文は `lines[].kana` にひらがなの読みを書く（`yomi` は Fish と共通の「読ませる文面」、`kana` は CoeFont 専用のひらがな）。`kuten`・`toten` は句点・読点の後の間（秒）で、手引き2-4の「文の中に0.3秒以上の無音を作らない」に合わせて小さくしている。CoeFont の声は声ごとに提供者がいて、商用で使えるかは CoeFont の利用規約と契約のプランで決まる（声を決めたら確かめる）
- API キーは `~/.zshrc` の `FISH_API_KEY` から読む。API の残高が0のときは無料モデル `s2.1-pro-free` しか使えない（有料モデルを指定すると HTTP 402 が返る）
- wav のサンプルレートは 44100 まで。生成後に 48kHz に変換してつなぐ
- 生成した音声は `narration/cache/` に保存し、文面・声・設定が同じ文は API を呼ばずに再利用する。1文を直したときは、その文だけが生成し直される
- 生成した音声を文字起こしして台本と比べ、一致率が0.92未満の文を「要確認」として表示する
- **文字起こしでは漢字の読み違いを見分けられない**（「1日」を「ついたち」と読んでも「1日」と書き起こされる）。台本を書いた時点で、読みが複数ある語（1日・今日・辛い・生物・方・何人 など）と、英字・記号・単位を Claude が洗い出し、`yomi` に読み仮名を書く
- **`yomi` で、語の中の「は」「へ」をひらがなで書かない。** 音声生成が助詞として「わ」「え」と読むことがある（「きょうどうかいはつ」が「かいわつ」になった。2026-11-10）。読み違えにくい漢字（開発・配合など）は漢字のまま残し、ひらがなにするのは読みが複数ある語だけにする
- **使う声（Fish Audio）**。案件の字コンテや依頼者の指定がなければ、次から選ぶ。

  | 声 | ID | 作者 | 使いどころ |
  |---|---|---|---|
  | じん Jin | `d738c70d206149e69f8e3b6fc8af9d96` | Fish Official | 温かく落ち着いた中年男性。速さ1.3から（2026-11-10 に依頼者が選んだ） |
  | 元気な女性 | `5161d41404314212af1254556477c17d` | 一般の登録者 | 明るい若い女性。速さ1.4から。ASBee ①で了承済み。作者が公式ではないので、新しい案件では公式の声を優先する |

- 声ライブラリの声は、実在の人物や既存キャラクターに似せたものを使わない。商用利用の条件は声ごとに確認する。声を選ぶときは `GET https://api.fish.audio/model/<ID>` で題名と作者を確かめ、作者が「Fish Official」の声を優先する。一般の人が登録した声には、他の製品の声や実在の人のまねが混じる（例: 「Holiday Twist」は TikTok の読み上げ機能の声「ホリデーツイスト」のまねで、使わない。2026-11-10）
- **声のクローン**（`scripts/voice_clone.py`）: 参考動画から声のサンプルを切り出し（`prepare`）、聞いて確かめてから Fish Audio に非公開のモデルを作る（`create`）。script.json の voice に `"clone": "名前"` と書くと narrate.py がそのモデルで読む。**クローンしてよいのは、声の持ち主の許諾が取れている声と、依頼者が指定したフリー素材の動画の声だけ。** 依頼者が動画を指定したら、その言葉どおりに `--owner` と `--consent` を書いて `prepare` する（依頼者の方針、2026-11-10）。フリー素材は、ライセンスの条文で AI による合成・声の利用が禁じられていないことを確かめ、`--basis フリー素材 --license URL` を付ける。許諾のない第三者の声（他社の完成版のナレーター、タレント、配信者など）はクローンしない。クローン済みの声は `voice_clone.py list` で一覧できる。`prepare` は `--owner`（声の持ち主）と `--consent`（誰が・いつ・どの用途で認めたか）がないと止まり、記録は `voices/名前/voice.json` に残る（`voices/` は git に入れない）。BGM の大きい動画からは声だけを取り出せないので、BGM のない区間か元の収録を使う

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

## 食品の動画: 主役の選び方と寄せ（案件共通）

食品・料理・食品を扱う調理家電の動画では、型（A / B）や案件にかかわらず、`guides/food_sizzle.md` を読んでから主役のカットを選ぶ。美味しそうに見える条件（綺麗・ボリューム・大きく・中央）で候補を比べ、主役のショットに `subject` を書いて寄せる。寄せは今のところ B 型（`build_b.py`）だけが対応している。

## B 字コンテ型: 編集の判断は手引きに従う

ドンキの商品紹介ショートは、`guides/donki_short_guide.md`（編集の手引き）を先に読み、その原則で台本・カット・テロップを判断する。字コンテは意図として扱い、短尺で伝わる形に書き直してよい（変えてよい範囲は手引き2-2）。

- 台本を書いたら、生成前に `.venv/bin/python scripts/narrate.py 台本 --estimate` で尺を見積もる（読みの文字数 ÷ 毎秒9.5かな）
- 生成は `narrate.py 台本 --tune` で行う。文ごとの話す速さが毎秒9〜10.5かなに入るまで、外れた文の speed を書き換えて自動で作り直す（同じ speed でも生成のたびに長さが変わるため）
- 書き出したら `scripts/review.py` で検品材料を作る。最後に `scripts/check.py`（機械の検品）が走り、採点表の項目3・4・5・6・9を自動で採点する。**作る役は check.json の「必ず直す」が0件になるまで自分で直してから、採点役に渡す**
- **採点は別のエージェントに1回だけ任せる**（作った本人の採点は甘くなる）。頼む文面は `guides/scoring_brief.md` をそのまま使う。採点役の「次に直すこと」（最大3件）と0点の項目だけを直し、check.py を通したら再び採点せずに Premiere に出す（採点と直しを3〜4回重ねて時間とトークンを使いすぎたため。ユーザーの指示、2026-11-10）
- 評価用の動画（魚が焼けルンルンの人の完成版）は手引きの根拠に使わない。手引きを直すときも見ない
- **作る役のサブエージェントは Sonnet で動かす**（Agent ツールの `model: "sonnet"`）。一覧画像を何十枚も読み、ナレーションを何度も作り直すので、1本あたり25万〜35万トークンを使う。採点役は既定のモデルのままにする（ユーザーの指示、2026-11-09）
- 主役（食べ物・商品）が映るショットには、cutlist.json のショットに `"subject": [左, 上, 右, 下]`（素材の画面に対する割合）を書く。build_b.py が主役を画面の中央に置いて大きく寄せる倍率と位置を計算し、プレビューと Premiere の両方に反映する（Premiere ではスケールと位置）。範囲は `scripts/framegrid.py 素材 開始秒 長さ 出力.jpg` で作る目盛り付きのコマで読む。直接 `zoom`（1〜2）と `center_x` / `center_y`（0〜1）を書いてもよい（手引き2-6）
- プレビューのテロップは確認用。実運用では Premiere 上の見え方が大事で、テロップはキャプションのトラックスタイルで付ける（将来 MOGRT に替える可能性がある）。構図はテロップの位置に合わせない
- 商品名が長いときは、主従の境目で改行した2行のテロップ1枚にする。script.json では商品名を1つの句にし、cutlist.json の `telops` に改行（`\n`）を入れて書く（手引き2-2）

## B 字コンテ型: 素材を整理してショットを割り当てる

見本は `projects/sakana/01/`（魚が焼けルンルン、2026-11-06 にテスト稼働。`projects/` は git に入れていない）。

1. 字コンテが PowerPoint のときは `scripts/jikonte_pptx.py 字コンテ.pptx projects/案件名/jikonte` で読む（Google ドキュメントは `jikonte_parse.py`）。列はテキストボックスの横位置で「映像／ナレーション／テロップ」を判定する
2. 台本（`script.json`）とカット表（`storyboard.json`）を書き、`narrate.py` でナレーションを作る（上の章と同じ）
3. 素材の一覧画像を作る: `scripts/shots.py 素材フォルダ projects/案件名/shots --every 3 --max 12`。あわせて `scripts/shake.py 素材フォルダ projects/案件名/shots/shake.json` で素材の手ブレを1秒ごとに測る（1.2%を超える秒は使わない。手引き2-5）
4. 一覧画像（`shots/sheets/*.jpg`）を見て、`shots/shots.json` の各素材の `segments` に「何秒から何秒に何が映っているか・寄りか引きか・使えるか」を書く。素材が多いときはサブエージェントに任せ、字コンテの映像指示ごとの候補も報告させる
5. 候補の時刻のコマを切り出して目で確かめ、`cutlist.json` に行ごとのショット（素材名・開始秒・必要なら dur と center_x）を書く。形式は `scripts/build_b.py` の先頭
6. `scripts/build_b.py projects/案件名/01/cutlist.json --check` で長さと注意を確かめ、`--check` を外して書き出す
7. `scripts/review.py projects/案件名/01` で検品材料と機械の検品（check.json）を作る。「必ず直す」が0件になるまで 5〜7 を繰り返す（この間は画像を読まない）
8. 採点役に `guides/scoring_brief.md` の文面で1回だけ採点させ、「次に直すこと」だけを直して 7 を通す
9. `premiere_import.py projects/案件名/01/premiere --name 案件名_01` で Premiere に読み込む（「AIショート」ビンの中に同じ名前のビンを作り、シーケンスにもその名前を付ける）。映像はショット数、音声はナレーション1本になる

注意:
- iPhone の縦動画は横長（3840x2160）で記録され、回転の指示が付いている。`common.probe` は回転後の幅・高さを返す。これを横長のまま扱うと Premiere でのスケールがずれる
- 行の長さはナレーションで決まる（その行の最初の文の開始 − `lead_sec` から、次の行まで）。素材の残りが足りないとエラーになる
- 声で特定の商品や機能（例: 「煮込み料理や」「ホットサンドも」）を言っているのに、その使用シーンの素材がないときは、その商品の箱（パッケージ）を見せて声と画を合わせる（依頼者の方針、2026-11-09。2026-11-06 の「使用シーンで置き換える」から変更）
- 声が特定の商品を指していないのに字コンテの映像指示に合う素材がないときは、同じ商品のまだ使っていない使用シーン（食材を入れる、レンジに入れる、取り出す など）で置き換える

## C インタビュー型: インタビュー素材を字コンテに沿って切る

インタビューの収録（話し声をそのまま使う）から、9:16 の縦型ショートを作る。判断は `guides/interview_guide.md` を先に読む。
A 型との違いは、話者を見分けて聞き手の声を外すこと・質問を画面上部のテロップで見せること・
声を流したまま映像だけ差し替えるインサートを置けること。

素材の形は2つある。どちらも同じカット表で扱う。

- **素材1本**（長回しのインタビュー）: 1本を文字起こしし、話者を見分けて聞き手を外す
- **素材が行ごとに分かれている**（字コンテの行ごとに収録し、テイクが複数ある）: 素材ごとに文字起こしし、
  行ごとにどのテイクを使うか選ぶ。カットに `src`（素材の名前）を書く

### 1. 文字起こしと話者の見分け

```bash
.venv/bin/python scripts/transcribe.py 素材.mp4 projects/案件名          # 素材1本
.venv/bin/python scripts/transcribe.py 素材フォルダ projects/案件名      # 素材が複数（transcripts/*.json）
.venv/bin/python scripts/speakers.py 素材.mp4 projects/案件名            # 話者の見分け（素材1本のとき）
```

素材が複数のときは `transcripts/<素材名>.json` と、全部をまとめた `transcript.txt` ができる。
`transcript.txt` を読んでテイクを選ぶ。NG テイク・「はいOKです」・撮影側の声もそのまま文字起こしされるので、
使う区間を単語番号で選ぶ。

`speakers.py` は発話ごとに話者を見分け、`transcript.txt` に話者の列（S1・S2…）を足す。分け方は2つある。

- **`--by motion`（カメラに1人だけ映っている収録はこちら）**: 声が出ている区間で、画の中の顔のあたりが
  動いているかを測る。動いていなければカメラに映っていない人（撮影側・聞き手）の声。映っている人が S1。
  マイクからの距離が変わっても効くので、音で分けるより確実
- **`--by audio`（既定）**: 声の高さ・音量・帯域ごとの強さで n 群に分ける。2人とも映っている収録で使う。
  **演者が小声で話した区間を「別の人」と間違えることがある**（2026-11-13 に実際に起きた）

```bash
.venv/bin/python scripts/speakers.py projects/案件名 --by motion          # 素材が複数（全部まとめて分ける）
.venv/bin/python scripts/speakers.py 素材.mp4 projects/案件名              # 素材1本・音で分ける
```

- 「要確認」が出た発話は、Claude が中身を見て決める。違っていれば直す:
  `.venv/bin/python scripts/speakers.py projects/案件名 --fix "2610_hutaba-7:14=S2"`（素材のパスは要らない）
- **撮影側が演者と同じセリフを読んでいることがある**（「一旦読んでいいですか」と言って見本を読む）。
  同じ文面のテイクが並んでいたら、必ず話者を確かめてから選ぶ（2026-11-13 に1カット取り違えた）

### 2. 区間を選んでカット表を書く（Claude の仕事）

`projects/案件名/shorts/01.interview.json` を1本につき1ファイル書く。形式は `scripts/timeline_c.py` の先頭にある。
見本は `test/ivwork/shorts/01.interview.json`。

- 素材が複数なら `"transcripts": "../transcripts"` と書き、カットごとに `src`（素材名）を書く。素材1本なら `"transcript": "../transcript.json"` だけでよい
- `keep_speakers` に答える人（例 `["S1"]`）を書く。聞き手の声が入ったカットに注意が出る
- 字幕はカットの中（`cuts[].captions`）に書くと、その素材の単語番号で読める。まとめて書くときは `captions` に `src` を添える
- **文字起こしの単語の時刻がずれていることがある**（言い直しで長く止まると、止まる前の語に後ろの時刻が付く）。
  その区間は `"in"` / `"out"`（素材上の秒）で範囲を上書きする。おかしいと思ったら、その区間だけ切り出して
  文字起こしし直して確かめる
- `question` に質問テロップ（1行16文字・2行まで）を書く。同じ文面が続くカットは1枚につながる
- `row` に字コンテの行番号を書くと、`storyboard` を指定したときに映像指示がシーケンスのマーカーに入る
- 隣のカットの寄せ（`zoom` / `center_x` / `center_y`）を変える。同じ素材で同じままだと画が飛ぶ（注意が出る）
- `inserts` で、声を流したまま映像だけ別素材に差し替えられる。位置はそのカットの中の単語番号で指定する
- **間尺は詰める。** カットの前後に残す余白は `pad`（既定 前0.05秒・後0.12秒）、台詞の中の長い無音は
  `trim_silence`（既定 0.30秒を超えたら0.12秒まで詰める）。詰めた量は `--check` の注意に出る
- 字幕（`captions`）の決まりは A 型と同じ（1行13文字・2行まで・毎秒9文字まで・誤認識を直して書く）

### 3. 検証してから書き出す

```bash
.venv/bin/python scripts/build_c.py projects/案件名/shorts/01.interview.json --check
.venv/bin/python scripts/build_c.py projects/案件名/shorts/01.interview.json
```

書き出し先は A 型と同じ（`shorts/01/preview.mp4` と `shorts/01/premiere/`）。
質問テロップは `01_title.srt` に時間帯ごとに入る。Premiere への読み込みは A 型・B 型と同じ `premiere_import.py` を使う。

### 4. 検品（書き出したら毎回やる）

手引き9章のとおり。プレビューを再度文字起こしして欠けと聞き手の声を確かめ、
質問テロップが出ている時刻のコマを切り出して、上の質問・下の字幕・顔が重なっていないか目で見る。

スクリプトを直したら、テスト素材で通して結果が変わらないことを確かめる
（期待値: 尺15.02秒・カット6・ショット7・字幕9・質問テロップ4）。
テスト素材 `test/interview.mp4` は git に入れていないので、なければ `./test/make_interview_media.sh` で作る。

## 出力の一覧

```
projects/案件名/
  transcript.json / transcript.txt   文字起こし（単語の時刻・無音区間）
  speakers.json                      話者ごとの測定値（C 型。speakers.py）
  shorts/01.edl.json                 カット表（A 型。Claude が書く）
  shorts/01.interview.json           カット表（C 型。Claude が書く）
  shorts/01/preview.mp4              確認用（字幕・タイトル焼き込み）
  shorts/01/timeline.json            素材フレームと出力フレームの対応表
  shorts/01/premiere/01.xml          Premiere 用タイムライン（FCP7 XML）
  shorts/01/premiere/01_captions.srt 字幕
  shorts/01/premiere/01_title.srt    タイトル（C 型は質問テロップ）
```

## 既知の制約

- この環境の ffmpeg には字幕フィルタがないので、プレビューの字幕は Pillow で PNG に描いて重ねている。見た目は `scripts/preview.py` の `CAPTION_STYLE` と `TITLE_STYLE` で変える
- XML の位置（Center）は、Premiere 26 が値を2倍して読む。縦の位置は実機で確かめた（2026-11-09、`premiere_export.py` の `_layouts`）。横の位置は同じ換算を使っているが、実機ではまだ確かめていない
- デザインしたテロップ（色・部分強調・アニメーション）は SRT では渡せない。Premiere 上でキャプションにトラックスタイルを当てるか、「キャプションをグラフィックにアップグレード」して手で付ける

## VIDEO OPS（外部製品）を使わない

`~/video-ops` と、そこからリンクされたスキル（video-ops、short-video-pipeline、premiere-bridge-ops、audio-precision、reference-video-clone、script-from-footage、short-telop-craft、short-video-cut-craft、short-video-qc、video-revision-db-workflow、`_video-core`）、`~/video-ops-support` は株式会社Bewibe のライセンス製品。契約は更新しない（2026-11-06 決定。契約終了 11-12、利用期限 11-15）。

- このパイプラインから VIDEO OPS のスクリプト・スキル・参照文書を呼ばない。中身を写さない（著作権は Bewibe、契約終了後は派生物も削除の対象）
- Premiere との通信は自前の `scripts/premiere_bridge.py` と、自社の拡張機能「Feriest Premiere Bridge」（`ai.feriest.premiere.bridge`、`~/Library/Application Support/Adobe/CEP/extensions/FeriestPremiereBridge`、共有フォルダ `~/premiere-bridge`）を使う（2026-11-10 に切り替え）。以前使っていた「MCP Bridge (CEP)」（`com.mcp.premiere.cepbridge`）は VIDEO OPS の付属素材なので、使わない・引き継ぎ先に渡さない
- 動作確認: `.venv/bin/python scripts/premiere_bridge.py` で「応答あり: <Premiereの版> / <プロジェクト名>」が出ればよい
