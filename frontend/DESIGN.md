# デザインの決まり（よみより）

教室のタブレット（横画面）で、中高生と先生が使う前提です。
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

- フォント：システムフォント（Hiragino Sans / Yu Gothic UI / Meiryo）。外部フォントは読み込まない
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
- カードは `href` を渡すと、カード全体を押して移動できます。マウスを乗せると枠が赤になります

## 守ってほしいルール

1. **新しい色を増やさない。** `bg-blue-600` のような Tailwind の標準色や、`#xxxxxx` の直書きは使わない。上の表の色だけを使う。足りないときはチームで相談して `@theme` に追加する
2. **ボタン・カード・ラベル・見出しは部品を使う。** 似たものを毎回 class で書かない
3. **押す場所は高さ48px以上**（部品は対応済み。自分で作るときは `min-h-12`）
4. **色だけに頼らない。** 状態や注意は「送信済み」「注意」などの文字も付ける
5. **派手にしない。** グラデーション、強い影、たくさんの角丸や太い枠は使わない。細い線（`border border-line`）と余白で区切る
6. **表示する文字は Jinja2 の自動エスケープに任せる。** `|safe` は使わない
7. CSSファイルは作らない。見た目は class で書く

## ログイン画面の例外

ログイン画面（`templates/login.html`）だけは、最初に目に入る「顔」として、上のルールとは別の見た目にしています。

- 背景は深紅、文字は生成り、差し色は金。書体は Google Fonts の Noto Sans JP / Noto Serif JP
- 色は `login-` で始まる名前で、`login.html` の中の `@theme` に定義している（ほかの画面では使わない）
- 飾りの絵（SVG）は `templates/partials/_login_objects.html` にある
- ボタンもこの画面専用の形なので、部品（`btn_primary` など）は使っていない

ほかの画面では、この例外の色・書体を使わないでください。
