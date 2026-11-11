# デザインの決まり（よみより）

教室のタブレット（横画面）で、中高生と先生が使う前提です。
画面の大きさは 1180×820（Safari で実際に使える範囲は約 1180×740）に合わせています。
本文の幅は `base.html` の `max-w-6xl`（左右の余白 32px）で、この幅では Tailwind の `lg` までが効きます。
基本は白、差し色は赤。細い線と余白で整理し、落ち着いた見た目にします。

色と文字の設定は `templates/base.html` の `@theme` にあります。
部品は `templates/_components.html` にあります。

## 色

| 名前 | 色 | 使いどころ | class の例 |
| --- | --- | --- | --- |
| paper | #FFFFFF | 画面の背景、カード | `bg-paper` |
| subtle | #F0F4FA | カードの中の区切り、表の行の区切り | `bg-subtle` |
| ink | #1F2A44 | ふつうの文字 | `text-ink` |
| muted | #5B6780 | 補足の文字（日付、説明など） | `text-muted` |
| line | #D5DCE8 | 枠線、区切り線 | `border-line` |
| accent | #C8102E | 差し色の赤。主なボタン、見出しの線、選択中 | `bg-accent` `text-accent` `border-accent` |
| accent-soft | #FDECEE | 赤の薄い背景（選択中の行など） | `bg-accent-soft` |
| warning | #B45309 | 軽い注意 | `text-warning` `border-warning` |

赤は「いちばん大事な1か所」に使います。1画面に主なボタン（赤い背景）は、できれば1つにします。

## 文字

- フォント：システムフォント（Hiragino Sans / Yu Gothic UI / Meiryo）
- 本文：18px（`base.html` で設定済み。何も書かなければ18px）
- 見出し：太字（`font-bold`）。本文：標準の太さ

## 部品

使う部品を、テンプレートの先頭で読み込みます。

```jinja
{% from "_components.html" import page_header, btn_primary, btn_secondary, card, badge %}
```

| 部品 | 用途 | 呼び出し方の例 |
| --- | --- | --- |
| `page_header(title)` | ページの見出し（下に赤い線） | `{{ page_header("作品をえらぶ") }}` |
| `btn_primary(label, href=None, busy_label=None)` | 主なボタン（赤い背景）。`busy_label` を渡すと、押せない間（disabled）だけその文字になる | `{{ btn_primary("チャット画面へ", href="/student/chat") }}` / `{{ btn_primary("送信", busy_label="送信中…") }}` |
| `btn_secondary(label, href=None)` | 補助のボタン（白い背景、赤い枠） | `{{ btn_secondary("トップに戻る", href="/") }}` |
| `card(title, href=None)` | 選択用のカード。中身は `call` で渡す | 下の例を見る |
| `radio_card(name, value, required=False)` | ラジオボタンで1つ選ぶカード（フォームの中で使う）。中身は `call` で渡す | `{% call radio_card("role_id", "r001", required=True) %}…{% endcall %}` |
| `avatar(text, image=None)` | ロールの丸いアイコン。`text` は名前の最初の1文字など。画像があれば上に重ねる | `{{ avatar(role_name[:1], role_image) }}` |
| `badge(text, kind)` | 小さなラベル。kind は `default` / `accent` / `warning` | `{{ badge("注意", "warning") }}` |

カードの例：

```jinja
{% call card("舞姫", href="/student/works/maihime") %}
  <p class="text-muted">森鷗外</p>
{% endcall %}
```

- ボタンは `href` を渡すとリンク（`<a>`）、渡さないと `<button>` になります。フォームの中に置くと送信ボタンになります
- `<button>` に属性を付けたいときは `attrs` に辞書で渡します（例: `btn_secondary("編集する", attrs={"type": "submit", "formaction": "/…", "formmethod": "get"})`）
- カードは `href` を渡すと、カード全体を押して移動できます。マウスを乗せると枠が赤になります

## 守ってほしいルール

1. **新しい色を増やさない。** `bg-blue-600` のような Tailwind の標準色や、`#xxxxxx` の直書きは使わない。上の表の色だけを使う。足りないときはチームで相談して `@theme` に追加する
2. **押す場所は高さ48px以上**（部品は対応済み。自分で作るときは `min-h-12`）
3. **色だけに頼らない。** 状態や注意は「送信済み」「注意」などの文字も付ける
4. **派手にしない。** グラデーション、強い影、たくさんの角丸や太い枠は使わない。細い線（`border border-line`）と余白で区切る
5. **表示する文字は Jinja2 の自動エスケープに任せる。** `|safe` は使わない
6. CSSファイルは作らない。見た目は class で書く

## 教師用の画面の例外

教師用の画面は、`templates/teacher_base.html` を継承します（上部バーに「教師用」のバッジとナビが付きます）。
教師用のトップ（教材セット選択、`teacher_home.html`）は、本棚の見た目にするため、上のルールの例外として次を使います。教材選択（`teacher_materials.html`、見た目の基準は「教材選択 M2」）、ロールの選択（`teacher_candidates.html`、見た目の基準は「ロール候補の選択 K1」）、ロールの編集（`teacher_candidate_edit.html`、見た目の基準は「ロールの編集 E1改」）も同じ例外を使います。

- 色：`base.html` の `@theme` の「教師用の画面だけで使う」色（`crimson`、`kinari`、`cream`、`gold`、`wood`、`book-*` など）。教師用の上部バー・上に挙げた教師用の画面、下の「生徒の画面の例外」以外では使わない。入力欄・チェック・「決定」の使えないときの色（`#F1EADB`・`#7A6A58`・`#D9CDB8`・`#C9B99C`）と、丸やチェックの印の枠（`#8A7A68`）だけは、基準HTMLの値を直書きしている
- 教材選択・ロールの選択・ロールの編集の画面では、「生徒のチャット一覧」のナビを `hide_nav` で出さない（教材の制作には不要なため）
- 文字：見出しと背表紙に明朝（`font-serif`。システムフォント）。外部フォントは読み込まない
- 影：選択中の本と棚板、ロールの選択の選択中のカードだけに付ける
- ボタン：「新規作成」「セットを編集」「授業開始」は部品を使わず、画面に合わせて class で書く（高さ48px以上は守る）

CSSファイルは作らないルールは、教師用の画面でも同じです（縦書きや持ち上がりも class で書いています）。

## 生徒の画面の例外（ロール選択・問いかけ・チャット・ログイン）

`role_select.html`・`student_question.html`・`index.html`（チャット）・`login.html`（教師用・生徒用で共用のログイン、見た目の基準は「生徒用ログイン L2」）は、それぞれの見た目の基準HTMLに合わせて、上のルールの例外として次を使います。

- 色：教師用の画面と同じ `crimson`、`kinari`、`cream`、`gold` など。チャットの「送信」の書く前の色（`#D9CDB8`・`#C9B99C`）と入力欄の placeholder（`#7A6A58`）だけは、基準HTMLの値を直書きしている
- 文字：Google Fonts の Noto Sans JP（本文）／Noto Serif JP（題名・見出し）を、その画面の `head` ブロックで読み込む
- ボタン：部品を使わず、画面に合わせて class で書く（高さ48px以上は守る）。「送信」などの見た目の切り替えは `:placeholder-shown` と `group-has-*` だけで行い、JS の `disabled` は使わない
- チャットの発言1件は `_components.html` の `chat_msg(kind, who, text, avatar_text=None, image=None)`、頭文字の丸は `chat_avatar(text, image=None)` で出す。htmx で追加される `_message.html` もこれを使う

## ウェルカム画面の例外

ウェルカム画面（`templates/welcome.html`、URL は `/`）だけは、最初に目に入る「顔」として、上のルールとは別の見た目にしています。

- 背景は深紅、文字は生成り、差し色は金。書体は Google Fonts の Noto Sans JP / Noto Serif JP
- 色は `login-` で始まる名前で、`welcome.html` の中の `@theme` に定義している（ほかの画面では使わない）
- 飾りの絵（SVG）は `templates/partials/_login_objects.html` にある
- ボタンもこの画面専用の形なので、部品（`btn_primary` など）は使っていない

ほかの画面では、この例外の色を使わないでください。
