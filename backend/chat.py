import os
import re
import uuid

import ai
import db
import moderation
from prompts import build_system_prompt
from rate_limit import RateLimiter

# 生徒とAIの対話（#19）。main.py の /chat から reply() を呼ぶ
# 会話の履歴は DB（db.py）に保存する（#20）

# AIに渡す過去のメッセージの数（生徒とAIの発言を合わせた数）。多いほど前の話を覚えているが、遅く高くなる
HISTORY_MAX = 20
# 会話の ID の形（uuid4 の16進数32文字）。それ以外は受け付けない
CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

# 連続送信の制限（#35）。上限を超えたら、/chat は 429 を返す
# 1つの会話で、1分に送れる回数。普通に考えて書けば超えない値にする
CONVERSATION_LIMIT_PER_MINUTE = 6
# アプリ全体で、1分にAIを呼べる回数。Gemini の上限（無料枠や有料の設定）に合わせて .env の CHAT_LIMIT_PER_MINUTE で変える
# 発表で審査員が同時に使っても足りるように、上限より少し低い値にしておく
GLOBAL_LIMIT_PER_MINUTE = int(os.getenv("CHAT_LIMIT_PER_MINUTE") or 30)

conversation_limiter = RateLimiter(CONVERSATION_LIMIT_PER_MINUTE, 60)
global_limiter = RateLimiter(GLOBAL_LIMIT_PER_MINUTE, 60)


def new_conversation_id() -> str:
    # チャット画面を開いたときに1つ作る。推測されないように、ランダムな値にする
    return uuid.uuid4().hex


def is_valid_conversation_id(conversation_id) -> bool:
    return isinstance(conversation_id, str) and bool(CONVERSATION_ID_PATTERN.fullmatch(conversation_id))


def allow_request(conversation_id: str) -> bool:
    # まだ送ってよければ True（回数を1つ数える）。会話ごとの上限を先に見て、1人の連打で全体の枠を使わせない
    return conversation_limiter.allow(conversation_id) and global_limiter.allow()


def read_history(conversation_id: str):
    return db.read_messages(conversation_id, HISTORY_MAX)


# 根拠（#45）。AIには、返事と一緒に「どの段落の、どの言葉をもとにしたか」を JSON で返してもらう
# 引用は、本文にそのまま書かれているものだけを画面に出す（AIが作った引用は捨てる）
EVIDENCE_MAX = 3
QUOTE_MAX_LENGTH = 80
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "生徒への返事の文"},
        "evidence": {
            "type": "array",
            "description": "返事のもとにした、今回の本文の箇所。本文から引用できないときは空にする",
            "items": {
                "type": "object",
                "properties": {
                    "paragraph_id": {"type": "string", "description": "本文の段落ID（p001 など）"},
                    "quote": {"type": "string", "description": "その段落から一字一句そのまま抜き出した短い引用"},
                },
                "required": ["paragraph_id", "quote"],
            },
        },
    },
    "required": ["reply", "evidence"],
}
EVIDENCE_INSTRUCTION = f"""

【出力の形】
reply に生徒への返事を書く。evidence には、返事のもとにした今回の本文（material_text）の箇所を最大{EVIDENCE_MAX}つ入れる。
paragraph_id は本文に書かれた段落ID（[p001] の p001）、quote はその段落から一字一句そのまま抜き出した{QUOTE_MAX_LENGTH}字以内の引用にする。
要約や言い換えを quote に入れない。今回の本文から引用できないとき（ロール付属の根拠だけのときも）は、evidence を空のリストにする。"""
PARAGRAPH_PATTERN = re.compile(r"^\[(p[0-9A-Za-z_-]+)\]\n(.*?)(?=\n\n\[p[0-9A-Za-z_-]+\]\n|\Z)", re.S | re.M)


def split_paragraphs(material_text: str):
    # main.get_material_text() の「[p001]（改行）本文」の形から、{段落ID: 本文} を作る
    return {m.group(1): m.group(2) for m in PARAGRAPH_PATTERN.finditer(material_text)}


def squash(text: str):
    # 空白・改行の違いは無視して比べる
    return re.sub(r"\s+", "", text)


def check_evidence(raw, paragraphs, paragraph_labels):
    # AIが返した根拠のうち、本文にそのまま書かれている引用だけを、画面の形 [{quote_text, location}] で返す
    checked = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        paragraph_id = item.get("paragraph_id")
        quote = item.get("quote")
        if not isinstance(paragraph_id, str) or not isinstance(quote, str):
            continue
        quote = quote.strip().strip("「」『』")
        if not quote or len(quote) > QUOTE_MAX_LENGTH or paragraph_id not in paragraphs:
            continue
        if squash(quote) not in squash(paragraphs[paragraph_id]):
            continue
        evidence = {"quote_text": quote, "location": paragraph_labels.get(paragraph_id, paragraph_id)}
        if evidence not in checked:
            checked.append(evidence)
    return checked[:EVIDENCE_MAX]


def reply(conversation_id: str, role: dict, material_text: str, message: str, paragraph_labels=None):
    # 生徒の発言に、ロールの立場でAIが返事をする。(返事の文, 根拠のリスト) を返す
    # 失敗したら ai.AIError（履歴には残さない）。paragraph_labels は {段落ID: "第3段落"} の表示名
    system_prompt = build_system_prompt(role, material_text) + EVIDENCE_INSTRUCTION
    messages = read_history(conversation_id) + [{"role": "user", "content": message}]
    result = ai.generate_json(system_prompt, messages, REPLY_SCHEMA)
    reply_text = result.get("reply") if isinstance(result, dict) else None
    if not isinstance(reply_text, str) or not reply_text.strip():
        raise ai.AIError("AIが返答を作れませんでした。言い方を変えて、もう一度送ってください。")
    evidence = check_evidence(result.get("evidence"), split_paragraphs(material_text), paragraph_labels or {})
    db.save_messages(
        conversation_id,
        role["id"],
        [
            # 生徒の発言だけ、乱暴な言葉を確認する（#21）。送信は止めず、教師の一覧に出すだけ
            {"role": "user", "content": message, "detections": moderation.detect(message)},
            {"role": "assistant", "content": reply_text.strip(), "evidence": evidence},
        ],
    )
    return reply_text.strip(), evidence
