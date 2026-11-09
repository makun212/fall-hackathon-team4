import re
import threading
import uuid
from collections import OrderedDict

import ai
from prompts import build_system_prompt

# 生徒とAIの対話（#19）。main.py の /chat から reply() を呼ぶ
# 会話の履歴は、DBが入るまでサーバーのメモリに置く（再起動すると消える）。DBに移すときは、下の3つの関数だけ直す

# AIに渡す過去のメッセージの数（生徒とAIの発言を合わせた数）。多いほど前の話を覚えているが、遅く高くなる
HISTORY_MAX = 20
# メモリに置いておく会話の数。超えたら、いちばん長く使われていない会話から消す
CONVERSATIONS_MAX = 500
# 会話の ID の形（uuid4 の16進数32文字）。それ以外は受け付けない
CONVERSATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

_histories = OrderedDict()
_lock = threading.Lock()


def new_conversation_id() -> str:
    # チャット画面を開いたときに1つ作る。推測されないように、ランダムな値にする
    return uuid.uuid4().hex


def is_valid_conversation_id(conversation_id) -> bool:
    return isinstance(conversation_id, str) and bool(CONVERSATION_ID_PATTERN.fullmatch(conversation_id))


def read_history(conversation_id: str):
    with _lock:
        return list(_histories.get(conversation_id, []))


def save_turn(conversation_id: str, user_message: str, reply_text: str):
    # 1往復分を、会話の最後に足す
    with _lock:
        history = _histories.setdefault(conversation_id, [])
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply_text})
        del history[:-HISTORY_MAX]
        _histories.move_to_end(conversation_id)
        while len(_histories) > CONVERSATIONS_MAX:
            _histories.popitem(last=False)


def reply(conversation_id: str, role: dict, material_text: str, message: str) -> str:
    # 生徒の発言に、ロールの立場でAIが返事をする。失敗したら ai.AIError（履歴には残さない）
    system_prompt = build_system_prompt(role, material_text)
    messages = read_history(conversation_id) + [{"role": "user", "content": message}]
    reply_text = ai.generate(system_prompt, messages)
    save_turn(conversation_id, message, reply_text)
    return reply_text
