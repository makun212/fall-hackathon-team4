import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# backend/.env から APIキーなどを読み込む（.env は GitHub に上げない）
load_dotenv(Path(__file__).resolve().parent / ".env")

# 使うモデル。.env の GEMINI_MODEL で変えられる（本番で別のモデルにするときも、ここか .env だけ直す）
DEFAULT_MODEL = "gemini-3.6-flash"

# 1回の呼び出しで待つ最大の時間（秒）。これを超えたら AIError にする
# チャット画面は30秒で待つのをやめるので、それより短くして、先にエラーを返せるようにする
TIMEOUT_SECONDS = 25

# 返答の長さの上限（トークン数）。長すぎる返答で料金と待ち時間が増えるのを防ぐ
MAX_OUTPUT_TOKENS = 1024

# 会話履歴の役割。ダミーデータの chat_messages と同じ user / assistant を使う
ROLE_TO_GEMINI = {"user": "user", "assistant": "model"}


class AIError(Exception):
    # AIの呼び出しに失敗したとき。message は、そのまま画面に出してよい文にする
    pass


class AIBusyError(AIError):
    # 混み合っている・回数の上限に達したとき（429）。少し待てば直る
    pass


_client = None


def get_client():
    # 最初に呼ばれたときに1回だけ作る。APIキーがなければ AIError
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise AIError("AIの設定が終わっていません。管理者に連絡してください。")
        _client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000),
        )
    return _client


def to_contents(messages):
    # [{"role": "user" | "assistant", "content": "..."}] を Gemini の形に変える
    contents = []
    for message in messages:
        role = ROLE_TO_GEMINI.get(message["role"])
        if role is None:
            raise ValueError(f"role は user か assistant にしてください: {message['role']}")
        contents.append(types.Content(role=role, parts=[types.Part(text=message["content"])]))
    return contents


def call_gemini(system_prompt: str, messages, config_extra=None):
    # 呼び出しの本体。失敗はすべて AIError にそろえる（生徒の入力やAPIキーはエラー文に入れない）
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        **(config_extra or {}),
    )
    try:
        response = get_client().models.generate_content(
            model=os.getenv("GEMINI_MODEL") or DEFAULT_MODEL,
            contents=to_contents(messages),
            config=config,
        )
    except errors.APIError as e:
        if e.code == 429:
            raise AIBusyError("AIが混み合っています。少し待ってから、もう一度送ってください。") from e
        raise AIError("AIが応答できませんでした。もう一度送ってください。") from e
    except AIError:
        raise
    except Exception as e:
        # タイムアウトや通信の失敗など
        raise AIError("AIが応答できませんでした。もう一度送ってください。") from e

    if not response.text:
        # 安全フィルターで止められたときなど
        raise AIError("AIが返答を作れませんでした。言い方を変えて、もう一度送ってください。")
    return response.text


def generate(system_prompt: str, messages) -> str:
    # 返答の文章を返す。対話（#19）で使う
    # messages は [{"role": "user" | "assistant", "content": "..."}]（古い順）。最後は user にする
    return call_gemini(system_prompt, messages)


def generate_json(system_prompt: str, messages, schema: dict):
    # schema（JSON Schema）の形の JSON を、Python の値にして返す。立場の候補（#16）や根拠（#45）で使う
    text = call_gemini(
        system_prompt,
        messages,
        {"response_mime_type": "application/json", "response_json_schema": schema},
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("AIの返答を読み取れませんでした。もう一度試してください。") from e


if __name__ == "__main__":
    # 動作確認用: python ai.py "こんにちは"
    import sys

    text = sys.argv[1] if len(sys.argv) > 1 else "こんにちは。一言で自己紹介してください。"
    print(generate("あなたは高校の国語の授業を手伝うアシスタントです。短く答えてください。", [{"role": "user", "content": text}]))
