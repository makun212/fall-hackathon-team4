# よみより セキュリティ調査・改善記録

## 対象と作業範囲

- 2026-11-12にGitHubからmainをfetchし、`4daa0db7db57310e8fc9b44778f99c6ec5175e58`を起点にした。
- 専用ブランチ `fix/security-hardening`、独立した作業フォルダ `yomiyori-security` を使用。既存の `pr117-review` の作業内容は変更していない。
- commit、push、PR作成、マージは実施していない。PR #117の変更を取り込む作業もしていない。
- Windows / Python 3.13.2、既存の `backend/requirements.txt` を専用 `.venv` にインストール。
- FastAPI TestClient、新規メモリSQLite、架空アカウント、偽AIのみを使用。本番・実ユーザー・Google/Geminiの実APIへの攻撃は行っていない。

## 攻撃別の調査結果

| 攻撃 | 調査時の対策と根拠 | 既存のテスト | 今回の対応と残るリスク |
| --- | --- | --- | --- |
| SQLインジェクション | `backend/db.py` 全体がSQLAlchemy Coreの `select` / `insert` / `update` / `delete` と値バインドを使用。`get_conversation`、`get_user`、`save_reflection`、`save_role_edit` 等を確認。生SQLは入力を含まない固定 `PRAGMA foreign_keys=ON` のみ。利用者入力のSQL文字列連結は見つからなかった | 専用の攻撃テストなし | SQL実装は変更せず、真偽条件・UNION・UPDATE・DELETE・DROPを含む入力の保存・検索・更新・HTTP経由の検査を追加。SQLiteで確認し、本番PostgreSQLでは未検証 |
| XSS | `backend/main.py` のJinja2TemplatesによるHTML自動エスケープ。`frontend/templates/_message.html`、チャット詳細、教師の提出状況に動的な値を出力。提出状況のポップアップは `textContent` を使用。利用者入力への `safe` 適用なし | `security_tests/test_security_acceptance.py` の生徒/AI返答、教師ロール名の攻撃テスト | 保存済みの名前・会話・感想が教師画面でもエスケープされるテストを追加。実ブラウザのJavaScript実行や外部CDNの供給網、CSPは未検証 |
| CSRF | SessionMiddlewareの `SameSite=Lax` はあるが、更新リクエストの送信元確認なし。GETでログアウト可能 | 専用テストなし | `backend/auth.py` の `access_control` にOrigin/Refererの厳密な同一オリジン確認を追加。両方欠落・null・異なるscheme/host/portを403で拒否。cross-site Fetch Metadataも拒否。ログアウトをPOST化し、同じ確認を適用。OAuth callbackのstate検証はAuthlibに任せ、今回変更しない |
| 認証・認可 | `auth.py` の `needs` / `access_control` が教師と生徒を分離。権限はCookie内のroleではなく `current_user` がDBから取得。`main.py` の `find_conversation` と `/chat` で所有者照合 | `tests/test_auth.py`、`tests/test_conversation_scope.py`、既存攻撃テストで未認証・生徒の教師操作・他人の閲覧/書込みを確認 | 署名付きCookieに偽のroleを追加しても教師になれないテストを追加。教師同士は現在の仕様上データを共有し、学校/クラス/担当教師ごとの分離は未実装。`DEV_LOGIN=1` や `TEACHER_EMAILS=*` は本番で使わないこと |
| セッションCookie改ざん | `main.py` のSessionMiddlewareによる署名、`auth.py` のランダムな鍵または環境設定の鍵、7日間の期限。HttpOnly / SameSite=Lax。本番用 `render.yaml` はSecure有効・鍵の自動生成を指定 | ログイン/ログアウトの機能テストあり。改ざん・期限切れの専用テストなし | payloadのすり替え、別鍵署名、期限切れを拒否するテストとCookie属性の検査を追加。Cookieは暗号化されない。盗まれた有効Cookieの再利用・サーバー側の個別失効は未対応。鍵の保管とHTTPSの運用は別途必要 |
| レート制限 | `chat.py` の会話ごとの上限と全体上限、`rate_limit.py` のロック付き時間窓。入力長も `/chat` で200字制限。ただし候補生成は制限対象外だった | `tests/test_rate_limit.py` と既存攻撃テストで429・時間窓・全体上限を検査 | `main.py` の `generated_candidates` でキャッシュ未生成時だけ全体枠を消費。会話IDの変更でも全体枠を回避できないこと、候補の再表示は消費しないことをテスト。プロセス内制限のため複数ワーカー/再起動で枠は共有・維持されない。通信帯域やHTTP全般のDoS防御、個人別の公平性は保証しない |
| プロンプトインジェクション | `prompts.py` が固定規則とJSON化したロール/本文を区別。`ai.py` がsystem_instructionとuser contentsを分離。`chat.py` は指定本文と一致する引用だけ表示。AIにDB操作や外部ツールの権限を渡していない | `tests/test_prompts.py` と既存攻撃テストで役割変更・指示開示要求・タグ/JSON偽装・本文内の命令・不正引用を検査 | 既存対策とテストを保持。偽AIでAPI引数の分離を確認した結果であり、実モデルがすべての誘導に耐える保証ではない。実モデルへの攻撃評価は未実施 |

## 発見・修正した問題

1. **送信元確認不足**：Cookieが添付された更新要求なら外部のOriginでも処理された。特に同じサイト配下の別オリジンでは、SameSite Cookieだけに依存できない。Originを優先し、存在しない場合だけRefererで検証する。両方ない場合は安全側に拒否する。
2. **GETログアウト**：外部サイトからのリンク遷移でログアウトさせられる。画面上のログアウトを同じ見た目のPOSTボタンに変更し、外部送信を拒否する。
3. **候補生成のAI呼び出し制限不足**：教材範囲を変えて候補を生成すると、チャット用の全体上限を通らない。全体枠を共通利用し、キャッシュ取得は数えない。

SQLに関して、今回の確認範囲で危険な文字列連結は見つからず、DB構造・SQLの実装を変更する必要はなかった。

## 変更ファイル

- `backend/auth.py`：送信元確認、ログアウトのPOST化。
- `backend/main.py`：AI候補生成を全体レート制限に接続。
- `frontend/templates/base.html`、`student_home.html`、`teacher_base.html`：ログアウトをPOSTボタンに変更。
- `backend/tests/auth_helper.py`、`test_privacy.py`：通常ブラウザと同様のOriginを付ける。攻撃ケースでは明示的に上書き/削除する。
- `backend/tests/test_auth.py`：POSTによるログアウトを確認。
- `backend/tests/test_security_compatibility.py`：最終レビューで通常操作・OAuth state検証・HTTPS中継・レート制限の互換性テスト6件を追加。
- `backend/security_tests/test_security_hardening.py`：新しい攻撃テスト17件。
- この報告書。既存の攻撃テストと過去の結果記録は削除・上書きしていない。

## テスト結果

- 修正前：既存攻撃テスト12件は成功。新規テストにより、外部Origin・不正Referer・送信元ヘッダー欠落・cross-site要求が受理されることを再現した（25テストメソッドの実行で、subtestを含む10失敗）。
- 修正後：既存機能テスト115件成功。最終レビューで互換性テスト6件を追加し、通常テスト計121件成功、セキュリティテスト29件成功（既存12件＋新規17件）。
- SQLテストでは攻撃文字列の正確な保存、別ユーザーの会話・発言・権限が不変であることを確認。実際にDBへ送られるSQLとparametersも取得し、攻撃文字列がSQL文ではなくparametersに入ることを確認した。
- 既存テストでSQLite接続の未クローズを示すResourceWarningが出るが、テストは成功。既存テストの接続後始末は今回の変更対象外。
- 再実行：`backend` で `.venv\Scripts\python.exe -m unittest discover -s tests -v` と `.venv\Scripts\python.exe -m unittest discover -s security_tests -v`。

## 未対策・未検証と互換性

- 実ブラウザ・スマートフォン・公開URLでの操作、実Google OAuth、実Gemini、本番PostgreSQL、同時接続/複数ワーカーの負荷試験は未実施。
- OriginもRefererも消すブラウザ設定や独自クライアントの更新要求は403になる。同じオリジンから送る必要がある。リバースプロキシは正しいscheme/hostをアプリへ渡す必要がある。外部ヘッダーを無条件に信頼する追加処理は入れていない。
- 候補生成がGETでキャッシュを作る既存設計は維持している。全体レート制限で回数は抑えるが、将来は明示的な生成POSTへ分ける余地がある。
- 教師間・学校/クラス間の閲覧範囲分離、盗まれたセッションの個別失効、分散レート制限、CSP/依存ライブラリ脆弱性の網羅的検査は今回完了としない。
- プロンプトインジェクションは確率的なAIへの対策であり、完全防御とは説明しない。

## 審査員向けの説明例

「SQLAlchemyのパラメータ化されたクエリで利用者入力とSQL命令を分離しています。SQLの読出し・改ざん・削除を狙う文字列をテスト用DBに送り、命令として実行されず他人のデータが変化しないことを確認しました。HTMLのエスケープ、会話の所有者と教師権限の確認、署名付きCookie、外部サイトからの更新要求の拒否、AI呼び出しのレート制限もテストしました。通常機能・互換性121件と攻撃テスト29件が成功しています。AIの命令すり替えは入力構造の分離を確認しており、実モデルへの完全な耐性を保証するものではありません。」

## PR前の最終レビュー

最新mainを再取得し、mainと作業ブランチの起点はいずれも `4daa0db7db57310e8fc9b44778f99c6ec5175e58` であることを確認した。main側の追加変更がないため、確認時点で取り込み競合・重複修正はない。`git diff --check` も成功。別作業フォルダの未コミット変更はないまま維持されている。

追加した6件は以下を確認する。共通ログインヘルパーのOrigin設定を削除し、リクエストごとに通常のブラウザ相当のヘッダーを明示した。

1. HTTPとHTTPSで教師の授業開始・終了、生徒の教材選択・意見送信・htmxチャット・感想提出・教師の会話閲覧が成功。
2. Originなし・正しいRefererだけでも、教師のロール編集・教材セット確定が成功。
3. Uvicornの実際のProxyHeadersMiddlewareを通し、HTTPS中継後も同一オリジンは成功し、異なるOriginは拒否。
4. Authlib自身が生成・照合するstateでログインし、外部サイトからのGET callbackを受け付け、POSTログアウトでセッションを終了できる。ネットワークに出るトークン交換のみAsyncMockに置き換えた。
5. state欠落・不一致・再利用はトークン交換前に拒否。
6. 候補生成とチャットが全体枠を共用し、上限時は追加生成だけ429となる。キャッシュ済み候補は表示でき、時計を60秒進めると生成を再開できる。

最終実行は通常テスト121件（9.796秒）・攻撃テスト29件（2.547秒）、両方とも終了コード0。今回のレビューでは追加のアプリ修正が必要な問題は見つからず、テストとこの記録を追加・更新した。

変更・新規ファイル11件を読み、秘密鍵、Google APIキー、GitHub/AWSトークン、認証情報の文字列代入を検出するパターン検索も実施し、該当0件。テスト内の `test:*`、`example.test`、`test-code` 等は偽サービス用の値で、実際のログインやAPIに使用できる認証情報ではない。セッション署名は実行時に生成し、鍵やCookieをファイルに固定保存していない。パターン検索だけであらゆる形式の秘密情報不在を保証するものではない。

上限は既存の `CHAT_LIMIT_PER_MINUTE` を共有する（既定30、Render設定10）。全体上限到達時には通常操作も一時的に429になることは仕様として残る。実ブラウザの目視、Googleの実トークン交換・IDトークン署名検証、本番プロキシ環境は未検証であり、今回の互換性テストを実サービスでの接続確認とは扱わない。

## 参考

- [OWASP SQL Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)：SQL文と値を分けるパラメータ化。
- [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)：SameSiteの限界、Origin/Refererによる送信元確認。
