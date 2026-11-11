# 多様な視点で学ぶ AIロールプレイ学習ツール「よみより」

TND 2026秋のハッカソン（テーマ「AI×学校」）チーム4の作品です。

## 概要

教科書の作品を、さまざまな立場を演じるAIとの対話で学べる学習ツールです。
生徒は「主人公の葛藤を重く見る」「相手への責任を問う」といった立場のAIに質問したり、自分の考えを伝えたりしながら、1つの物語を複数の視点で読み解きます。

最初のデモ題材は、高校国語の定番教材である森鷗外『舞姫』です。

## 解決したいこと

- 国語の読解は「登場人物の気持ちを考えよう」と言われても、1人で考えるのが難しい
- 授業では先生や一部の生徒の解釈に寄りやすく、ほかの視点に触れる機会が少ない
- 異なる立場と対話することで、自分の解釈を見直し、作品への理解を深められる

## なぜAIを使うのか

- 生徒ごとに質問が違うので、決まった答えを用意しておく方式では対応しきれない
- 同じ場面でも、作品を読む立場に沿って考え方を変えて答えられるのは、生成AIならではの強み
- 生徒が自分のペースで、何度でも気軽に問いかけられる

## 対象ユーザー

高校生（国語の授業や予習・復習での利用を想定）

## 使用技術

| 役割 | 技術 |
| --- | --- |
| サーバー | FastAPI（Python） |
| 画面 | htmx ＋ Jinja2（HTMLをPythonから返す） |
| 見た目 | Tailwind CSS（CDN） |
| AI | 【使うAIのAPI（OpenAI / Gemini / Claude など）】 |
| データ保存 | 必要になったら SQLite |
| ソース管理 | GitHub |
| デプロイ | 【Render など】 |

チーム全員がPython中心のため、画面もPython（FastAPI）から返す構成にしています。
TypeScriptなどの別言語を覚えなくても、全員が画面と処理の両方を書けます。

## フォルダ構成

```
fall-hackathon-team4/
├ frontend/              画面担当が触るところ
│  ├ templates/          画面のHTML（htmx・Tailwind）
│  │  ├ index.html       トップ画面
│  │  └ _message.html    チャットの1往復分（htmxで追加される部品）
│  └ static/             CSSや画像など
└ backend/               処理担当が触るところ
   ├ main.py             サーバーの処理（URLごとの動き、AIの呼び出し）
   ├ requirements.txt    使っているPythonのライブラリ一覧
   └ .env.example        APIキーの見本（中身は空）
```

起動するのは backend の FastAPI 1つだけです。FastAPI が frontend の HTML を読み込んで画面を返します。

## 手元で動かす方法

### 必要なもの

- Python 3.11以上
- Git

### 1. リポジトリをコピー

```
git clone https://github.com/makun212/fall-hackathon-team4.git
cd fall-hackathon-team4
```

### 2. 起動

```
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Macは source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # .env にAPIキーなどを書く（下を参照）
uvicorn main:app --reload
```

`backend/.env` には、最低限つぎを書きます（値はチャットや Discord に貼らない）。

```
GEMINI_API_KEY=（Google AI Studio で作ったキー）
DEV_LOGIN=1                    # Google の設定なしで試す開発用ログイン。本番では書かない
TEACHER_CODE=（好きな合言葉）  # 先生として使うときに入力する
```

http://127.0.0.1:8000 を開き、ログイン画面の「（開発用）Googleを使わずにログイン」から入ればOKです。
先生として入るときは、教師コードの入力が出ます。教師が授業を開始すると、生徒のトップに教材セットが表示されます。

テストは `backend` で `python -m unittest discover -s tests` と `python -m unittest discover -s security_tests` で実行できます。

ターミナルを開き直したときは、毎回 `source .venv/Scripts/activate` を実行してから作業してください。

## デプロイ（Render）

リポジトリの直下にある `render.yaml` で、アプリ（Web サービス）と DB（Postgres）をまとめて作れます。

1. Render にログインし、「New → Blueprint」からこのリポジトリを選ぶ
2. 秘密の値（`GEMINI_API_KEY`、`GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`TEACHER_CODE`）を画面で入力する
3. できた URL（`https://〜.onrender.com`）の `/auth/google/callback` を、Google Cloud の OAuth クライアントの「承認済みのリダイレクト URI」に追加する
4. Google Cloud の OAuth 同意画面を「本番環境」にする（審査員がログインできるように）

注意

- 無料プランのアプリは、15分使われないと止まり、次に開いたときに起動まで1分ほどかかります。発表の前に一度開いておきます。
- 無料プランの Postgres は30日で期限が切れます（発表までは十分です）。
- DB は Postgres なので、再デプロイしてもデータは残ります。

## 開発ルール

- main に直接 push しない
- 作業は自分用のブランチで行う（例：feature/chat-ui）
- 終わったらプルリクエストを出し、1人以上が確認してからマージ
- 同じファイルを同時に編集しないよう、担当を分ける
- やることは GitHub の Issues に書き、担当者を割り当てる

## セキュリティ対策

このリポジトリは公開（Public）なので、特に注意します。

- APIキーは .env に書き、GitHub には絶対に上げない（.gitignore で除外済み）
- AIのAPIはサーバー（FastAPI）からのみ呼び出し、APIキーをブラウザ側に出さない
- 画面に表示する文字は Jinja2 の自動エスケープを使い、XSS（悪意のあるスクリプトの埋め込み）を防ぐ
- プロンプトインジェクション対策（「今までの指示を無視して」などへの対策、システムプロンプトの固定）
- 入力文字数の制限、連続リクエストの制限（API料金の使いすぎ防止）
- 想定した攻撃と、実際に試した結果を記録しておく

### 攻撃テストの記録（2026-11-09）

最新main `f8e049f` を、テスト用メモリDBと偽Geminiで確認しました。
既存テスト35件は成功。攻撃テストは10件中6件成功、4件失敗です。

| 攻撃・確認内容 | 結果 | 対応 |
| --- | --- | --- |
| 生徒・AI返答・ロール名へのHTML挿入 | エスケープを確認 | ブラウザ実行は未検証 |
| 指示無視・役割変更・指示開示・タグ/JSON偽装 | API引数でsystemとuserを分離 | 実Geminiの応答は未検証 |
| 201字/5000字の入力、連続送信 | 422/429、拒否後のAPI呼び出しなし | 確認済み |
| 作り話・範囲外の引用 | 根拠として表示されない | 確認済み |
| 未認証の教師画面、別ブラウザからの意見閲覧・感想保存 | 拒否されず、3テスト失敗 | [#77](https://github.com/makun212/fall-hackathon-team4/issues/77)、#33の認可実装後に再検証 |
| 同じ会話IDのまま教材Cookieを変更 | 範囲が変わり、1テスト失敗 | [#78](https://github.com/makun212/fall-hackathon-team4/issues/78) |

実行方法・全結果・未実施事項は [攻撃テスト記録](docs/security-test-results.md) を参照してください。
失敗を隠していません。本番環境の安全性を保証する結果ではありません。

## スケジュール

| 日付 | 内容 |
| --- | --- |
| 10/25（日） | キックオフ |
| 11/1（日）19:00〜 | 中間発表（背景・テーマ・開発計画・役割分担） |
| 11/14（土）13:00まで | 成果物提出（GitHubリポジトリのURL） |
| 11/15（日）13:00〜 | 最終発表・審査（動くデモ必須） |

## 審査基準（各10点×5項目）

- 新規性：独自の特徴があるか
- インパクト：学校生活や社会への実用的な効果
- セキュリティ：既知の攻撃を想定した対策・テスト
- AIの妥当性：課題に対してAIが有効か
- UI・UX：学生にとってわかりやすく、使いやすいか

## メンバー

| 名前 | 担当 |
| --- | --- |
| マーボー | バックエンド |
| Aya Ueda | フロントエンド |
| Atsuki | バックエンド |

## 用語メモ（わからない言葉があったらここを見る）

### GitHub・Git まわり

| 用語 | 意味 |
| --- | --- |
| リポジトリ | プロジェクトのファイルをまとめて置いておく場所。今回は fall-hackathon-team4 |
| clone（クローン） | GitHub のリポジトリを、自分のPCにまるごとコピーすること。最初に1回だけやる |
| commit（コミット） | 「ここまでの変更を記録する」こと。ゲームのセーブのようなもの |
| push（プッシュ） | 自分のPCで記録した変更を、GitHub に送ること |
| pull（プル） | GitHub にある最新の変更を、自分のPCに取り込むこと。作業を始める前にやる |
| main（メイン） | 完成版を置いておく、いちばん大事なブランチ |
| ブランチ | 作業用のコピー。main を壊さずに、自分の作業を進められる |
| プルリクエスト（PR） | 「自分のブランチの変更を main に入れていいですか？」とお願いすること。誰かが確認してから入れる |
| マージ | ブランチの変更を main に合体させること |
| コンフリクト | 2人が同じ場所を書き換えて、どちらを残すか Git が決められない状態。困ったらチャットで相談 |

### 作業の流れ（毎回これ）

```
git pull                         # 最新を取り込む
git checkout -b feature/作業名    # 作業用ブランチを作る
（ファイルを編集）
git add -A                       # 変更をまとめる
git commit -m "やったこと"        # 記録する
git push origin feature/作業名    # GitHub に送る
```

そのあと GitHub のページで「Compare & pull request」ボタンを押して、プルリクエストを出します。

### アプリの仕組みまわり

| 用語 | 意味 |
| --- | --- |
| FastAPI | Python でWebアプリのサーバーを作るための道具。今回のアプリの本体 |
| htmx | HTML に少し書き足すだけで、ページを再読み込みせずに画面の一部を書き換えられる道具。チャットの返事を追加表示するのに使っている |
| Jinja2 | HTML の中に Python の値を埋め込む道具。`{{ reply }}` のように書くと、そこに値が入る |
| Tailwind CSS | 見た目を整える道具。`class="bg-blue-600 rounded"` のように、HTML に短い単語を書くだけで色や形を変えられる |
| CDN | ネット上に置いてあるものを読み込んで使う仕組み。Tailwind と htmx はインストール不要で、HTML に1行書くだけで使える |
| localhost | 自分のPCのこと。`http://localhost:8000` は自分のPCで動いているアプリを開くURL |
| API | 別のサービスの機能を、自分のプログラムから呼び出す仕組み。今回は AI の API を使って返事を作る |
| APIキー | API を使うためのパスワードのようなもの。人に見られると勝手に使われてお金がかかるので、絶対に GitHub に上げない |
| .env | APIキーなどの秘密の情報を書いておくファイル。GitHub には上がらないように設定済み |

### Python まわり

| 用語 | 意味 |
| --- | --- |
| 仮想環境（.venv） | このプロジェクト専用の Python の部屋。ライブラリをPC全体ではなく、ここにだけ入れる |
| activate | 仮想環境に入ること。`source .venv/Scripts/activate` を実行して、行の頭に (.venv) が付けばOK |
| requirements.txt | このプロジェクトで使っている Python のライブラリ一覧。`pip install -r requirements.txt` で全部まとめて入る |
| uvicorn | FastAPI のアプリを動かすための道具。`uvicorn main:app --reload` で起動する |

### 公開まわり

| 用語 | 意味 |
| --- | --- |
| デプロイ | 作ったアプリをネット上に公開して、誰でも URL から開ける状態にすること |
| Render | デプロイ先のサービスの1つ。GitHub とつなぐと、push するたびに自動で公開し直してくれる |
