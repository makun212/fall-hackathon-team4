import re
import uuid

import ai
import db
from prompts import build_system_prompt

# 生徒とAIの対話（#19）。main.py の /chat から reply() を呼ぶ
# 会話の履歴は DB（db.py）に保存する（#20）

# AIに渡す過去のメッセージの数（生徒とAIの発言を合わせた数）。多いほど前の話を覚えているが、遅く高くなる
HISTORY_MAX = 20
# 会話の ID の形（uuid4 の16進数32文字）。それ以外は受け付けない
CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def new_conversation_id() -> str:
    # チャット画面を開いたときに1つ作る。推測されないように、ランダムな値にする
    return uuid.uuid4().hex


def is_valid_conversation_id(conversation_id) -> bool:
    return isinstance(conversation_id, str) and bool(CONVERSATION_ID_PATTERN.fullmatch(conversation_id))


def read_history(conversation_id: str):
    return db.read_messages(conversation_id, HISTORY_MAX)


def reply(conversation_id: str, role: dict, material_text: str, message: str) -> str:
    # 生徒の発言に、ロールの立場でAIが返事をする。失敗したら ai.AIError（履歴には残さない）
    system_prompt = build_system_prompt(role, material_text)
    messages = read_history(conversation_id) + [{"role": "user", "content": message}]
    reply_text = ai.generate(system_prompt, messages)
    db.save_messages(
        conversation_id,
        role["id"],
        [{"role": "user", "content": message}, {"role": "assistant", "content": reply_text}],
    )
    return reply_text
