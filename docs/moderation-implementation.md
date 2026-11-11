# 乱暴な言葉の独立判定（Issue #21）

`backend/moderation.py` の `detect(text)` を追加した。
`main.py`、`chat.py`、`db.py`、テンプレート、依存ライブラリは変更していない。

```python
from moderation import detect

detect("お前は死ね")  # [{"type": "rough_language", "level": "high"}]
detect("お前は馬鹿")  # [{"type": "rough_language", "level": "medium"}]
detect("豊太郎の行動は無責任だ")  # []
```

規則はチーム確認前のIssue #5の基準案。明示的な命令・相手への侮辱を対象とし、
作品についての異論や死・病気などの話題語を単独では検知しない。
定型の引用分析を除外し、Unicodeの表記揺れを一部正規化する。
未知の攻撃表現を網羅するものではなく、教師が文脈を確認するための候補を返す。

## 互換性

既存の `DETECTION_TYPE_LABELS` / `DETECTION_LEVEL_LABELS` が持つ
`rough_language` と `high` / `medium` のみを使う。問題がなければ空のリスト。
返却値は呼び出しごとに新規生成する。非文字列はTypeError。外部通信・DB・ファイル書き込みを行わない。

## 検証

backendで `python -m unittest discover -s tests -v`：44件成功（既存35、新規9）。
新規テストは脅し・侮辱・表記揺れ・引用・否定・重複抑止・不正な型に加え、
舞姫73段落および6ロールの説明・特徴・応答方針・根拠で誤検知がないことを確認する。
初回は引用符だけで囲んだ命令を見逃すテストが1件失敗したが、開始境界を修正し、最終実行は全件成功した。

## 未完了

- #5の基準案のチーム承認。
- チャットからの呼び出し、発言との一括保存、教師画面への接続。
- 保存・画面の結合テスト。

ユーザーの明示指示により接続作業を保留している。現在のアプリで自動検知が有効になったとは扱わない。
Issue #21の保存まで含む完了条件には未達であり、このPRからIssueをCloseしない。
