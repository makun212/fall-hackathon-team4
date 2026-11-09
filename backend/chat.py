import os
import re
import uuid

import ai
import db
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
