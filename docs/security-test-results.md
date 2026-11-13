# 攻撃テスト記録（Issue #37）

実施日：2026-11-09。対象：`origin/main` の `f8e049f`。
このPRはアプリの認証・保存・プロンプトの実装を変更しない。

## 実行方法

Python 3.12、Windows、既存の `backend/requirements.txt` とテスト用 `httpx` を利用。

```powershell
cd backend
python -m unittest discover -s tests -v
python -m unittest discover -s security_tests -v
```

既存テスト35件は成功。攻撃テストは10件中6件成功、4件失敗、終了コード1。
失敗を `skip` / `expectedFailure` に置き換えていない。
通常の `tests` ディレクトリとは別にし、未完成の認可要件の受け入れテストとして明示的に実行する。

すべてFastAPI TestClient、テストごとに新規のメモリSQLite、偽Geminiで実施。
DBファイル、実ユーザー、実API、公開サイトを操作していない。
攻撃文字列はテスト専用で、APIキーやパスワードを含めていない。

| ケース | 期待 | 実結果 | 判定・対応 |
| --- | --- | --- | --- |
| 未認証で教師チャット一覧を取得 | ログイン要求または401/403 | 200で表示 | 失敗。[#77](https://github.com/makun212/fall-hackathon-team4/issues/77)、認可実装 #33待ち |
| 別ブラウザで会話IDを指定し事前意見を取得 | 拒否・原文非表示 | 架空の事前意見が表示 | 失敗。#77に記録 |
| 別ブラウザで会話IDを指定し感想を送信 | 拒否・DB未変更 | 感想が保存された | 失敗。#77に記録 |
| 教材セットのCookieを外して同じ会話を継続 | 拒否、または作成時の範囲を復元 | 200、全文の文脈へ変更 | 失敗。[#78](https://github.com/makun212/fall-hackathon-team4/issues/78)に記録 |
| 生徒とAIの返答にimgのonerror属性を含める | HTMLとして表示しない | 両方ともエスケープ | 成功。ブラウザでの実行検証は未実施 |
| ロール名にsvgのonload属性を保存 | 教師の一覧でHTMLとして表示しない | エスケープ | 成功。許容文字数内の入力で確認 |
| 役割変更・指示開示・タグ偽装・JSON偽装の4入力 | 入力がsystem引数を置き換えない | 固定systemとuserメッセージが分離 | API引数の構造検査は成功。実モデルの指示遵守は未検証 |
| 201字・5000字の発言 | API・DB処理前に拒否 | 422、API呼び出し0、保存0 | 成功 |
| 連続送信（テスト時の上限2） | 3回目を拒否 | 200、200、429、API呼び出し2 | 成功 |
| 範囲外の段落の引用・作り話の引用 | 根拠パネルに出さない | ボタン・不正引用とも非表示 | 成功 |

## 未実施と限界

- 本物のGeminiに対するプロンプトインジェクション、秘密の開示要求、教材内命令への応答。
- OAuth、教師・生徒の認可、他ユーザーとの分離の完成後の再検証。
- 本番URL、Googleアカウント、スマートフォン、ブラウザでのJavaScript実行。
- 実際のAPI上限・同時利用・マルチワーカーでのレート制限。

保存された会話IDはランダムだが、漏れたIDの所有権確認を代替しない。
今回の失敗は公開前に確認すべき項目であり、「攻撃に耐える」とは評価しない。
修正は他担当のIssueに属するため、このPRでは行わない。#37全体の完了宣言やIssueのCloseはしない。

## 2026-11-11 再検証

上記は2026-11-09の履歴であり、削除・置き換えはしていない。この節が最新mainでの再検証結果である。

### 対象と既存成果物

- main：`e7d397810699cd1b18353d981357012f07e4caa2`。GitHubの `refs/heads/main` と手元の対象SHAを照合した。
- 作業ブランチ：`feature/issue-37-security-recheck`。追加テストと記録は未コミット。
- PR #82はマージ済み。最初の攻撃テスト10件は認証の変更に合わせて更新され、今回の開始時点では12件だった。既存の再現テストを再利用し、削除・skip・expectedFailureへの変更はしていない。
- Issue #77（認可不足）・#78（教材範囲の変更）はいずれもClosed/completed。今回は既存テストで修正後の挙動を確認し、新しい問題として再登録していない。

### 実行環境と結果

Windows、Python 3.12、既存の検証用仮想環境 `.atsuki-work/.venv` を利用した。認証関連のAuthlibは1.9.5。
すべてFastAPI TestClient・テストごとのメモリSQLite・偽Geminiで実施。Googleログインは既存のテスト用署名セッション／偽Google応答を使う。
アプリの認証仕様は変更せず、`frontend/` にも変更を加えていない。

| 実行 | 成功 | 失敗 | skip | 終了コード |
| --- | --- | --- | --- | --- |
| 追加前：`python -m unittest discover -s tests -q` | 109 | 0 | 0 | 0 |
| 追加前：`python -m unittest discover -s security_tests -v` | 12 | 0 | 0 | 0 |
| 追加後：`python -m unittest discover -s security_tests -v` | 18 | 0 | 0 | 0 |

追加前後の攻撃テストは重複するため、12件と18件を合算しない。通常テストの再実行は追加前に実施した。

再実行する場合は、backendを作業ディレクトリとする。元の作業場所にある古いmainのコードと取り違えないこと。

```powershell
cd .atsuki-work/pr-review-20261111/backend
../../.venv/Scripts/python.exe -m unittest discover -s tests -q
../../.venv/Scripts/python.exe -m unittest discover -s security_tests -v
```

### 追加した6件と既存テストの補強

対象ファイル：`backend/security_tests/test_security_acceptance.py`。

| テスト名 | 確認したこと | 結果 |
| --- | --- | --- |
| `test_injection_in_history_cannot_replace_system_or_message_roles` | 生徒のタグ偽装と偽AIのJSON役割偽装を保存し、次の往復でも固定systemとuser/modelの役割を維持する | 成功 |
| `test_stored_html_stays_escaped_in_teacher_list_and_detail` | 生徒と偽AIのimg/onerror文字列を保存後、教師の一覧・会話詳細でエスケープする | 成功 |
| `test_tampered_session_cannot_impersonate_a_teacher` | 実在するテスト教師IDにCookie内容を書き換えても、元の署名では認証されない。教師画面はログインへ、POSTは401 | 成功 |
| `test_all_teacher_routes_require_authentication_and_teacher_role` | ログイン画面を除く登録済み教師用ルートのGET/POSTを列挙し、匿名／生徒を拒否。授業開始・終了、感想提出状況も対象 | 成功 |
| `test_message_boundary_and_rejection_leave_saved_history_unchanged` | 200文字は200、201文字は422、空白だけは400。拒否後も保存済み履歴とAI呼び出し数が変わらない | 成功 |
| `test_global_limit_rejects_new_conversation_without_saving_messages` | 全体上限を2に下げて3件だけ送る。別会話でも3件目は429となり、その会話には発言を保存しない | 成功 |
| 既存 `test_repeated_sends_are_limited_before_api` の補強 | 会話単位の3件目拒否後も、保存は成功した2往復分の4メッセージだけ | 成功 |

### 4分野と既知問題の整理

| 分野 | 実施済み | 未実施・結果の限界 |
| --- | --- | --- |
| プロンプトインジェクション | 初回の4入力と複数往復の履歴について、固定system・役割・入力の分離を検査 | 実AIへの攻撃と応答品質は今回未実施。偽AIでの構造検査から実モデルの耐性は判断しない |
| XSS | チャット部分HTML、保存後の教師一覧・詳細、ロール名。通常テストでは感想ダイアログも検査 | ブラウザDOMやイベント属性の実行、クライアント側の即時表示処理は未検証 |
| 権限チェック | 匿名・生徒・別の生徒・改ざんCookieを検査。#77の3再現ケースと、別のログイン済み生徒による横取りを拒否 | 実Google OAuth、実アカウント、本番Cookie・HTTPS設定は未検証 |
| 連続送信・長文入力 | 200/201/5000文字、空白、会話単位・全体の少数要求、拒否後のAI呼び出し・保存件数 | 外部サービスへの大量送信なし。有料AI APIの呼び出しなし。本番の同時利用・複数プロセスの制限は未検証 |
| 既知問題 #78 | 教材Cookieを外した要求は400となり、AIは呼ばれない | 既存の本文範囲固定テストも通常テストに含む。Issueの再作成は不要 |

### 発見した問題・対応状況

- 今回の追加テストで新たな不具合は再現しなかった。新規Issueの作成、既存Issueの再オープンはしていない。
- READMEの「4件失敗」は過去の結果として維持し、今回の修正後の結果を別節で追記する案を作成した。
- 前の中断時に作られた未追跡の `backend/security_tests/browser_fixture.py` は今回の追加6件に含めていない。専用サーバーは停止し、ブラウザでの成功実績はない。
- アプリの認証・保存・プロンプト実装の変更、commit・push・PR作成は行っていない。
- この記録だけで本番環境の安全性やIssue #37全体の完了を宣言せず、IssueはOpenのままとする。
