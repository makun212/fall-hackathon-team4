import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import chat as chat_service
import db
from ai import AIBusyError, AIError
from content import apply_maihime

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI()
# ログインと権限（#33）。access_control より後に足した SessionMiddleware が外側になり、先にセッションを読む
app.include_router(auth.router)
app.middleware("http")(auth.access_control)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.SESSION_SECRET,
    session_cookie=auth.SESSION_COOKIE,
    max_age=auth.SESSION_MAX_AGE,
    same_site="lax",
    https_only=auth.SESSION_HTTPS_ONLY,
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
templates = Jinja2Templates(directory=FRONTEND_DIR / "templates")

# ダミーデータ（起動時に1回だけ読み込む）
SEED_DATA_PATH = Path(__file__).resolve().parent / "data" / "seed_dummy_data.json"


def load_seed_data():
    if not SEED_DATA_PATH.exists():
        raise FileNotFoundError(
            f"backend/data/seed_dummy_data.json が見つかりません（探した場所: {SEED_DATA_PATH}）"
        )
    with SEED_DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# 『舞姫』は、厚樹さんが用意した本文と立場に置き換える（content.py）
SEED_DATA = apply_maihime(load_seed_data())



# 生徒が選んだ教材セットを覚えておく Cookie の名前（#44）
MATERIAL_SET_COOKIE = "material_set"


def current_material_set(request: Request | None):
    # 生徒が選んだ教材セット（get_material_set_for_chat の形）。選んでいなければ None
    material_set_id = request.cookies.get(MATERIAL_SET_COOKIE) if request is not None else None
    return get_material_set_for_chat(material_set_id) if material_set_id else None


def get_roles(request: Request | None = None):
    # 生徒が選べるロールの一覧。教材セットを選んでいれば、そのロール。なければ舞姫のプリセット
    material_set = current_material_set(request)
    if material_set is not None:
        return material_set["roles"]
    return SEED_DATA["preset_roles"]["roles"]


def find_role(role_id: str, request: Request | None = None):
    # get_roles() の中から ID が一致するロールを返す。なければ None
    for role in get_roles(request):
        if role["id"] == role_id:
            return role
    return None


def conversation_material_set(request: Request, conversation=None):
    # 継続時の教材はサーバーに保存したIDから取得する。Cookieは一致確認にだけ使う。
    # 未指定（None）の全文モードも固定し、途中のCookie変更や削除は400で拒否する。
    requested_id = request.cookies.get(MATERIAL_SET_COOKIE) or None
    material_set_id = conversation["material_set_id"] if conversation is not None else requested_id
    if conversation is not None and requested_id != material_set_id:
        raise HTTPException(status_code=400)
    if material_set_id is None:
        if conversation is not None and not db.has_conversation_context(conversation["id"]):
            # 旧直接チャットではNULLが保存漏れの場合もある。Cookieから範囲を補完しない。
            raise HTTPException(status_code=400)
        return None
    material_set = get_material_set_for_chat(material_set_id)
    if material_set is None:
        # 削除済み・不明なセットを、プリセット全文へ暗黙に切り替えない。
        raise HTTPException(status_code=400)
    return material_set


def find_conversation_role(role_id: str, material_set):
    roles = material_set["roles"] if material_set is not None else get_roles()
    return next((role for role in roles if role["id"] == role_id), None)


# ---- 生徒用：対話前の問いかけ ----

# 事前の考えの文字数の上限。この値は、チームで確認して決める（仮の値）
OPINION_MAX_LENGTH = 200


def get_question():
    # 対話の前に生徒に見せる問いかけ。問いかけを使うのは、この関数1か所だけ
    # ダミーデータに問いかけのキーはまだないため、いまは固定の文。教材セットごとの問いかけに差し替える
    return "豊太郎の行動を、あなたはどう思いますか？"


def render_student_question(request: Request, role, opinion: str = "", error: str | None = None, status_code: int = 200):
    # 問いかけ画面を返す（GET と、POST の入力エラーで共用）
    return templates.TemplateResponse(
        request,
        "student_question.html",
        {
            "role": role,
            "question": get_question(),
            "opinion": opinion,
            "error": error,
            "opinion_max_length": OPINION_MAX_LENGTH,
        },
        status_code=status_code,
    )


# ---- 生徒用：対話後の感想 ----

# 感想（対話後の考え）の文字数の上限。上限は、この定数1か所だけで定義する
REFLECTION_MAX_LENGTH = 200


# ダミー。事前の意見の保存ができたら、ログイン中の生徒の保存済みの値に差し替える
def current_student_id(request: Request):
    # ログインしている人の id（会話の持ち主として保存する）。auth.access_control が request.state.user に入れる
    user = getattr(request.state, "user", None)
    return user["id"] if user else None


def find_conversation(conversation_id, role, request: Request):
    # 会話 ID が正しい形で、DBにあり、ロールも持ち主も一致するときだけ会話を返す。それ以外は None
    # 持ち主が違う会話は、ないものとして扱う（ほかの人の考えを見せない・書き換えさせない。#33）
    if not chat_service.is_valid_conversation_id(conversation_id):
        return None
    conversation = db.get_conversation(conversation_id)
    if conversation is None or conversation["role_id"] != role["id"]:
        return None
    if conversation["student_id"] != current_student_id(request):
        return None
    return conversation


def get_pre_opinion(conversation):
    # 対話前の考えを取り出すのは、この関数1か所だけ。会話がなければダミーデータを使う
    if conversation and conversation.get("pre_opinion"):
        return conversation["pre_opinion"]
    reflections = SEED_DATA.get("reflections") or []
    if not reflections:
        return "（対話の前の考えが見つかりません）"
    return reflections[0]["pre_opinion"]


def render_student_reflection(
    request: Request, role, conversation=None, reflection: str = "", error: str | None = None, status_code: int = 200
):
    # 感想入力画面を返す（GET と、POST の入力エラーで共用）
    return templates.TemplateResponse(
        request,
        "student_reflection.html",
        {
            "role": role,
            "question": get_question(),
            "pre_opinion": get_pre_opinion(conversation),
            "conversation_id": conversation["id"] if conversation else None,
            "reflection": reflection,
            "error": error,
            "reflection_max_length": REFLECTION_MAX_LENGTH,
        },
        status_code=status_code,
    )


# ---- 教師用：生徒のチャット一覧 ----

# 検知の種類と強さの表示名。種類や強さが増えたら、ここに足す
DETECTION_TYPE_LABELS = {
    "rough_language": "乱暴な言葉",
    "off_topic": "無関係な話題",
    "prompt_injection": "AIへの不正な指示",
}
DETECTION_LEVEL_LABELS = {"high": "強", "medium": "中", "low": "弱"}
# 並べ替え用の強さの順位（大きいほど強い）
DETECTION_LEVEL_RANK = {"low": 1, "medium": 2, "high": 3}


def read_chat_records():
    # 一覧に使うデータを、ダミーデータとDBから取り出す。ダミーデータのキー名を参照するのはここだけ
    # デモで一覧が空にならないように、ダミーの会話の後ろに、DBの本物の会話を足す
    data = SEED_DATA
    records = {
        "conversations": [
            {
                "id": c["id"],
                "material_set_id": c["material_set_id"],
                "student_id": c["student_id"],
                "role_id": c["role_id"],
                "created_at": c["created_at"],
            }
            for c in data["conversations"]
        ],
        "user_names": {u["id"]: u["name"] for u in data["users"]},
        "role_names": {r["id"]: r["name"] for r in data["preset_roles"]["roles"]},
        "material_set_titles": {
            m["id"]: m["title_label"] for m in data["material_sets"]
        },
        "messages": [
            {
                "id": m["id"],
                "conversation_id": m["conversation_id"],
                "text": m["text"],
                "sent_at": m["sent_at"],
            }
            for m in data["chat_messages"]
        ],
        "detections": [
            {"message_id": d["message_id"], "type": d["type"], "level": d["level"]}
            for d in data["detections"]
        ],
    }
    from_db = db.read_chat_records()
    records["conversations"] += from_db["conversations"]
    records["messages"] += from_db["messages"]
    # ログインした人の名前（#33）。ログイン機能より前に作られた会話は「ゲスト」と出す
    records["user_names"].update(db.read_user_names())
    records["user_names"][None] = "ゲスト"
    return records


def get_material_set_title(material_set_id: str):
    # 教材セットのタイトル（一覧の見出し用）。見つからなければ None
    return read_chat_records()["material_set_titles"].get(material_set_id)


def get_conversation_summaries():
    # 会話1件ごとの一覧用の値を返す。検知がある会話（強い順）を先に、同じ順位の中では新しい順に並べる
    records = read_chat_records()

    messages_by_conversation = {}
    for message in records["messages"]:
        messages_by_conversation.setdefault(message["conversation_id"], []).append(message)
    conversation_by_message = {
        m["id"]: m["conversation_id"] for m in records["messages"]
    }

    # 検知は、メッセージを通して会話に結びつける。同じ種類が何件あっても、いちばん強いものだけを残す
    detections_by_conversation = {}
    for detection in records["detections"]:
        conversation_id = conversation_by_message.get(detection["message_id"])
        if conversation_id is None:
            continue
        by_type = detections_by_conversation.setdefault(conversation_id, {})
        current = by_type.get(detection["type"])
        if current is None or DETECTION_LEVEL_RANK.get(detection["level"], 0) > DETECTION_LEVEL_RANK.get(current["level"], 0):
            by_type[detection["type"]] = detection

    summaries = []
    for conversation in records["conversations"]:
        messages = sorted(
            messages_by_conversation.get(conversation["id"], []),
            key=lambda m: datetime.fromisoformat(m["sent_at"]),
        )
        last = messages[-1] if messages else None
        last_time = datetime.fromisoformat(last["sent_at"] if last else conversation["created_at"])

        detections = sorted(
            (
                {
                    "type": d["type"],
                    "level": d["level"],
                    "type_label": DETECTION_TYPE_LABELS.get(d["type"], d["type"]),
                    "level_label": DETECTION_LEVEL_LABELS.get(d["level"], d["level"]),
                }
                for d in detections_by_conversation.get(conversation["id"], {}).values()
            ),
            key=lambda d: DETECTION_LEVEL_RANK.get(d["level"], 0),
            reverse=True,
        )
        max_level = detections[0]["level"] if detections else None

        summaries.append(
            {
                "conversation_id": conversation["id"],
                "material_set_id": conversation["material_set_id"],
                "student_name": records["user_names"].get(conversation["student_id"], "不明"),
                "role_name": records["role_names"].get(conversation["role_id"], "不明"),
                "last_message": last["text"] if last else "",
                "message_count": len(messages),
                "last_sent_at": last_time.strftime("%H:%M") if last else None,
                "detections": detections,
                "max_level": max_level,
                "_sort_time": last_time,
            }
        )

    # 新しい順に並べてから、検知の強い順に並べ直す（sorted は同じ順位の並びを保つ）
    summaries.sort(key=lambda s: s["_sort_time"], reverse=True)
    summaries.sort(key=lambda s: DETECTION_LEVEL_RANK.get(s["max_level"], 0), reverse=True)
    for summary in summaries:
        del summary["_sort_time"]
    return summaries


def find_conversation_summary(conversation_id: str):
    # 一覧の中から ID が一致する会話を返す。なければ None
    for summary in get_conversation_summaries():
        if summary["conversation_id"] == conversation_id:
            return summary
    return None

# ---- 教師用：教材と使用範囲の選択 ----

# 教材と範囲を決めたあとの移動先（ロール候補の選択画面）。URLを変えるときはここだけ直す
NEXT_AFTER_MATERIAL = "/teacher/candidates"

# 使用範囲の種類。この3つ以外は受け付けない
RANGE_TYPES = ("all", "paragraph", "line")

# 範囲の数字は、半角の整数（6桁以内）だけを受け付ける
RANGE_NUMBER_PATTERN = re.compile(r"[0-9]{1,6}")

# 冒頭の文がない教材で、先頭の段落から切り出す文字数
OVERVIEW_FALLBACK_LENGTH = 60


def read_material_records():
    # 教材のデータを、ダミーデータから取り出す。教材のキー名を参照するのはここだけ
    # DBに変えるときは、この関数の中身だけを、DBから同じ形で読む処理に差し替える
    records = []
    for m in SEED_DATA.get("materials") or []:
        paragraphs = m.get("paragraphs") or []
        records.append(
            {
                "id": m["id"],
                "title": m["title"],
                "author": m["author"],
                "opening_preview": m.get("opening_preview"),
                # 形式段落は、並び順のまま第1、第2…段落とする
                "paragraph_texts": [p["text"] for p in paragraphs],
                "max_line": paragraphs[-1]["line_end"] if paragraphs else 0,
            }
        )
    return records


def get_materials():
    # 一覧用の教材（id、title、author、has_text）
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "author": r["author"],
            "has_text": bool(r["paragraph_texts"]),
        }
        for r in read_material_records()
    ]


def get_material(material_id: str):
    # 1件の教材の詳細を返す。なければ None
    for r in read_material_records():
        if r["id"] != material_id:
            continue
        overview = r["opening_preview"]
        if not overview and r["paragraph_texts"]:
            overview = r["paragraph_texts"][0][:OVERVIEW_FALLBACK_LENGTH] + "…"
        return {
            "id": r["id"],
            "title": r["title"],
            "author": r["author"],
            "overview": overview or "",
            "paragraph_count": len(r["paragraph_texts"]),
            "max_line": r["max_line"],
            "has_text": bool(r["paragraph_texts"]),
        }
    return None


def find_selectable_material(material_id: str):
    # get_materials() にあり、本文が登録されている教材だけを返す。それ以外は None
    if material_id not in {m["id"] for m in get_materials()}:
        return None
    material = get_material(material_id)
    if material is None or not material["has_text"]:
        return None
    return material


def parse_range_number(value: str):
    # 半角の整数（6桁以内）なら int を返す。それ以外は None
    if not isinstance(value, str) or not RANGE_NUMBER_PATTERN.fullmatch(value):
        return None
    return int(value)


def validate_range(material, range_type: str, start: str, end: str):
    # 使用範囲を検証する。正しければ (True, None)、不正なら (False, エラーの文章)
    if range_type not in RANGE_TYPES:
        return False, "使用範囲の種類を選んでください。"
    if range_type == "all":
        return True, None

    start_number = parse_range_number(start)
    end_number = parse_range_number(end)
    if start_number is None or end_number is None:
        return False, "開始と終了に、半角の数字を入れてください。"

    if range_type == "paragraph":
        upper = material["paragraph_count"]
        out_of_range = f"形式段落は、1〜{upper}の間で指定してください。"
    else:
        upper = material["max_line"]
        out_of_range = f"行番号は、1〜{upper}の間で指定してください。"

    if start_number > end_number:
        return False, "開始は、終了以下の数字にしてください。"
    # 0 は範囲外として扱う（「1〜」の案内のほうが直しやすいため）
    if start_number < 1 or end_number > upper:
        return False, out_of_range
    return True, None


def range_label(range_type: str, start: str, end: str):
    # 検証済みの使用範囲を、画面に出す文にする
    if range_type == "all":
        return "全文"
    if range_type == "paragraph":
        return f"第{int(start)}〜{int(end)}段落"
    return f"{int(start)}〜{int(end)}行"


def build_next_after_material_url(material, range_type: str, start: str, end: str):
    # 次の画面へのURL。ユーザーの入力ではなく、検証済みの値から組み立てる
    return build_candidates_url(material, range_type, start, end)


def build_candidates_url(material, range_type: str, start: str, end: str, selected=(), back: bool = False):
    # ロールの選択画面（/teacher/candidates）へのURL。この画面へのリンクは、すべてこの関数で作る
    # 引数は、検証済みの値だけを渡す（教材は find_selectable_material()、範囲は validate_range()、selected は候補にあるIDだけ）
    query = {"material_id": material["id"], "range_type": range_type}
    if range_type != "all":
        query["start"] = str(int(start))
        query["end"] = str(int(end))
    if selected:
        query["selected"] = list(selected)
    if back:
        query["back"] = "1"
    return f"{NEXT_AFTER_MATERIAL}?{urlencode(query, doseq=True)}"


def render_teacher_materials(
    request: Request,
    material=None,
    range_type: str = "all",
    start: str = "",
    end: str = "",
    error: str | None = None,
    status_code: int = 200,
):
    # 教材選択画面を返す（GET と、POST の入力エラーで共用）
    return templates.TemplateResponse(
        request,
        "teacher_materials.html",
        {
            "materials": get_materials(),
            "material": material,
            # 不正な種類は画面に戻さない（どのラジオも選ばれていない状態にする）
            "range_type": range_type if range_type in RANGE_TYPES else "",
            "start": start,
            "end": end,
            "error": error,
        },
        status_code=status_code,
    )


# ---- 教師用：ロールの候補の選択 ----

# ロールを確定したあとの移動先。本来は教材セットの選択画面（仮で教師用ホーム）。URLを変えるときはここだけ直す
NEXT_AFTER_CONFIRM = "/teacher"

# 1つの教材で表示する候補の数の上限
ROLE_CANDIDATES_MAX = 6

# 教師が選べるロールの数の上限。上限は、この定数1か所だけで定義する（画面の表示とJavaScriptも、この値を使う）
ROLE_SELECT_MAX = 3

STATIC_DIR = FRONTEND_DIR / "static"


def find_static_image(path):
    # /static/ の中に実在する画像のパスだけを返す。なければ（または static の外を指していれば）None
    if not isinstance(path, str) or not path:
        return None
    static_root = STATIC_DIR.resolve()
    target = (static_root / path).resolve()
    if not target.is_relative_to(static_root) or not target.is_file():
        return None
    return path


# ダミー。AIの候補生成と、保存した編集内容に、あとで差し替える
def get_role_candidates(material_id: str):
    # 教材の候補を、同じ形の辞書のリストで返す（最大6つ）。候補のキー名を参照するのはここだけ
    # 形: candidate_id、name、summary、features（文字列のリスト）、image（なければ None）、edited（True / False）、
    #     original（元のAIの案。編集を適用する前の、編集できる項目 {"name", "features"}）
    # summary（立場の一言）は編集できないため、いつもAIの元の値
    preset = SEED_DATA.get("preset_roles") or {}
    if material_id == preset.get("material_id"):
        # 舞姫（m001）は、プリセットのロールを候補として使う。id を candidate_id として使う
        candidates = [
            {
                "candidate_id": r["id"],
                "name": r["name"],
                "summary": r.get("summary") or "",
                "features": list(r.get("features") or []),
                "image": find_static_image(r.get("image")),
                "edited": False,
            }
            for r in preset.get("roles") or []
        ]
    else:
        candidates = []
        for group in SEED_DATA.get("role_candidates") or []:
            if group.get("material_id") != material_id:
                continue
            candidates = [
                {
                    "candidate_id": c["candidate_id"],
                    "name": c["name"],
                    "summary": c.get("summary") or "",
                    "features": list(c.get("features") or []),
                    "image": find_static_image(c.get("image")),
                    "edited": False,
                }
                for c in group.get("candidates") or []
            ]
            break

    # 元のAIの案（編集を適用する前の値）を残す
    for candidate in candidates:
        candidate["original"] = role_fields(candidate)

    # 1. 確定済みの教材セットに、編集後の特徴があれば置き換える
    edited_features = {}
    for material_set in SEED_DATA.get("material_sets") or []:
        if material_set.get("material_id") != material_id or material_set.get("status") != "confirmed":
            continue
        for role in material_set.get("roles") or []:
            features = (role.get("edited") or {}).get("features")
            if features:
                edited_features[role["role_id"]] = list(features)
    for candidate in candidates:
        if candidate["candidate_id"] in edited_features:
            candidate["features"] = edited_features[candidate["candidate_id"]]
            candidate["edited"] = True

    # 2. 編集画面で保存した内容（DB の role_edits）があれば、上書きする。None は「元に戻した」
    saved_edits = db.read_role_edits(material_id)
    for candidate in candidates:
        if candidate["candidate_id"] not in saved_edits:
            continue
        saved = saved_edits[candidate["candidate_id"]]
        fields = candidate["original"] if saved is None else saved
        candidate["name"] = fields["name"]
        candidate["features"] = list(fields["features"])
        candidate["edited"] = role_fields(candidate) != candidate["original"]

    return candidates[:ROLE_CANDIDATES_MAX]


def role_fields(role):
    # 編集できる項目（立場・思考の特徴）だけを取り出す。項目が決まったら、ここと role_edit_problems() を直す
    return {"name": role["name"], "features": list(role["features"])}


# ---- 教師用：ロールの編集 ----

# 入力の上限。仮の値。チームで確認して決める。AIの指示文にそのまま入るので、長さを制限している
# あわせて、ロールの選択画面のカード（1180×820 で3列×2段、1画面に収める）で崩れない長さにしている
# - 立場: カードでは2行まで（1行 約14字。行頭に句読点を置かない送りを見込んで 20字）
# - 特徴: カードでは1つ2行まで（1行 約18字。同じく送りを見込んで 30字）
# AIの候補生成でも、この長さに収めるように指示する。値を変えたら、teacher_candidates.html のカードの見た目も確かめる
ROLE_NAME_MAX = 20
ROLE_FEATURE_MAX = 30
ROLE_FEATURE_COUNT = 3


def has_line_break(value: str):
    # 改行（\n、\r のほか、Python が改行として扱う文字）を含むか。前後の空白は取り除いてから呼ぶ
    return len(value.splitlines()) > 1


def role_edit_problems(name: str, features):
    # 入力の検証。(整えた値, [(欄の名前, エラーの文章), ...]) を返す。欄の名前は、フォームの name と同じ
    cleaned_name = (name or "").strip()
    cleaned_features = [(f or "").strip() for f in list(features)[:ROLE_FEATURE_COUNT]]
    cleaned_features += [""] * (ROLE_FEATURE_COUNT - len(cleaned_features))

    problems = []
    if not cleaned_name:
        problems.append(("name", "立場（ロールの名前）を入力してください。"))
    elif len(cleaned_name) > ROLE_NAME_MAX:
        problems.append(("name", f"立場は{ROLE_NAME_MAX}文字以内にしてください。"))
    for n, feature in enumerate(cleaned_features, start=1):
        if not feature:
            problems.append((f"feature_{n}", f"思考の特徴{n}を入力してください。"))
        elif len(feature) > ROLE_FEATURE_MAX:
            problems.append((f"feature_{n}", f"思考の特徴{n}は{ROLE_FEATURE_MAX}文字以内にしてください。"))

    values = {"name": cleaned_name}
    values.update({f"feature_{n}": f for n, f in enumerate(cleaned_features, start=1)})
    for field, value in values.items():
        if has_line_break(value):
            problems.append((field, "改行は使えません。"))

    cleaned = {"name": cleaned_name, "features": cleaned_features}
    return cleaned, problems


def validate_role_edit(name: str, features):
    # 入力を検証する。(整えた値, エラーの文章のリスト) を返す。同じ文章は1回だけにする
    cleaned, problems = role_edit_problems(name, features)
    return cleaned, list(dict.fromkeys(message for _, message in problems))


def save_role_edit(material_id: str, candidate, cleaned):
    # 編集内容を保存する。元のAIの案と同じなら None（元に戻した）を保存する
    # TODO: 教師ごとに分ける（Googleログイン #33 の後）。今は、同じ教材の編集を全員で共有する
    value = None if cleaned == candidate["original"] else cleaned
    db.save_role_edit(material_id, candidate["candidate_id"], value)


def render_teacher_candidate_edit(
    request: Request,
    material,
    range_type: str,
    start: str,
    end: str,
    candidate,
    selected_ids,
    form,
    is_reset: bool = False,
    errors=None,
    invalid_fields=None,
    status_code: int = 200,
):
    # ロールの編集画面を返す（GET と、POST の入力エラーで共用）。form は {"name", "features"（3つ）}
    original = candidate["original"]
    return templates.TemplateResponse(
        request,
        "teacher_candidate_edit.html",
        {
            "material": material,
            "range_type": range_type,
            "start": start,
            "end": end,
            "candidate": candidate,
            "original": original,
            "form": form,
            "is_reset": is_reset,
            "errors": errors or [],
            "invalid_fields": set(invalid_fields or []),
            "selected_ids": selected_ids,
            "back_url": build_candidates_url(material, range_type, start, end, selected_ids, back=True),
            "name_max": ROLE_NAME_MAX,
            "feature_max": ROLE_FEATURE_MAX,
        },
        status_code=status_code,
    )


def pad_features(features):
    # 思考の特徴を ROLE_FEATURE_COUNT 個にそろえる（足りない分は空文字）
    features = list(features)[:ROLE_FEATURE_COUNT]
    return features + [""] * (ROLE_FEATURE_COUNT - len(features))


def resolve_candidates_request(material_id: str, range_type: str, start: str, end: str):
    # 教材と範囲を検証する。正しければ (教材, 範囲の種類, 開始, 終了)、不正なら None
    # 開始・終了は、検証済みの数字を整えた文字列にする（"all" のときは空）
    material = find_selectable_material(material_id)
    if material is None:
        return None
    ok, _ = validate_range(material, range_type, start, end)
    if not ok:
        return None
    if range_type == "all":
        return material, range_type, "", ""
    return material, range_type, str(int(start)), str(int(end))


def filter_selected(candidates, selected):
    # selected のうち、候補にあるIDだけを、候補の並び順で返す（重複は1つにする）
    chosen = set(selected or [])
    return [c["candidate_id"] for c in candidates if c["candidate_id"] in chosen]


def render_teacher_candidates(
    request: Request,
    material,
    range_type: str,
    start: str,
    end: str,
    candidates,
    selected_ids,
    error: str | None = None,
    status_code: int = 200,
):
    # ロールの選択画面を返す（GET と、POST の入力エラーで共用）。値はすべて検証済みのものを渡す
    return templates.TemplateResponse(
        request,
        "teacher_candidates.html",
        {
            "material": material,
            "range_type": range_type,
            "start": start,
            "end": end,
            "range_label": range_label(range_type, start, end),
            "candidates": candidates,
            "selected_ids": selected_ids,
            "select_max": ROLE_SELECT_MAX,
            "error": error,
        },
        status_code=status_code,
    )


# ---- 教師用：作成した教材セットの一覧 ----


def read_material_set_records():
    # 確定済みの教材セットを、ダミーデータから取り出す。教材セットとロールのキー名を参照するのはここだけ
    # TODO（バックエンド担当）: 教材セットの保存ができたら、この関数の中身を、保存した教材セットを同じ形で読む処理に差し替える
    # 形: id、title、author、range_start・range_end（第何段落か。1から数える）、paragraph_count、question、created_at、
    #     roles（name、features、image、edited の辞書のリスト。edited は教師が編集したかどうか）
    materials = {m["id"]: m for m in SEED_DATA.get("materials") or []}
    sources = {
        "preset": {r["id"]: r for r in (SEED_DATA.get("preset_roles") or {}).get("roles") or []},
        "candidate": {
            c["candidate_id"]: c
            for group in SEED_DATA.get("role_candidates") or []
            for c in group.get("candidates") or []
        },
    }

    records = []
    for ms in SEED_DATA.get("material_sets") or []:
        material = materials.get(ms.get("material_id"))
        if ms.get("status") != "confirmed" or material is None:
            continue
        paragraph_ids = [p["id"] for p in material.get("paragraphs") or []]
        range_ = ms.get("range") or {}
        if range_.get("from") not in paragraph_ids or range_.get("to") not in paragraph_ids:
            continue

        roles = []
        for role in ms.get("roles") or []:
            source = sources.get(role.get("origin"), {}).get(role.get("role_id"))
            if source is None:
                continue
            edited = role.get("edited") or {}
            roles.append(
                {
                    "role_id": role["role_id"],
                    "name": edited.get("name") or source["name"],
                    "features": list(edited.get("features") or source.get("features") or []),
                    "image": find_static_image(source.get("image")),
                    "edited": bool(edited),
                }
            )

        records.append(
            {
                "id": ms["id"],
                "material_id": material["id"],
                "title": ms.get("title_label") or material["title"],
                "author": material.get("author") or "",
                "range_start": paragraph_ids.index(range_["from"]) + 1,
                "range_end": paragraph_ids.index(range_["to"]) + 1,
                "paragraph_count": len(paragraph_ids),
                "question": ms.get("question") or "",
                "created_at": ms["created_at"],
                "roles": roles,
            }
        )

    # DBに保存した教材セット（#13）を後ろに足す
    for ms in db.read_material_sets():
        material = materials.get(ms["material_id"])
        if material is None:
            continue
        records.append(
            {
                "id": ms["id"],
                "material_id": material["id"],
                "title": material["title"],
                "author": material.get("author") or "",
                "range_start": ms["range_start"],
                "range_end": ms["range_end"],
                "paragraph_count": len(material.get("paragraphs") or []),
                "question": ms["question"] or "",
                "created_at": ms["created_at"],
                "roles": [
                    {
                        "role_id": r["role_id"],
                        "name": r["name"],
                        "features": r["features"],
                        "image": find_static_image(r["image"]),
                        "edited": r["edited"],
                    }
                    for r in ms["roles"]
                ],
            }
        )
    return records


def get_confirmed_material_sets():
    # 一覧用の値（新しい順）。範囲と作成日は、画面に出す文にする
    sets = []
    for r in read_material_set_records():
        if r["range_start"] == 1 and r["range_end"] == r["paragraph_count"]:
            label = range_label("all", "", "")
        else:
            label = range_label("paragraph", str(r["range_start"]), str(r["range_end"]))
        created = datetime.fromisoformat(r["created_at"])
        sets.append(
            {
                "id": r["id"],
                "title": r["title"],
                "author": r["author"],
                "range_label": label,
                "question": r["question"],
                "created_label": f"{created.year}年{created.month}月{created.day}日",
                "roles": r["roles"],
                "_sort_time": created,
            }
        )
    sets.sort(key=lambda s: s["_sort_time"], reverse=True)
    for s in sets:
        del s["_sort_time"]
    return sets


# Googleログインの入口（処理はバックエンド担当が作る）。URLを変えるときはここだけ直す
GOOGLE_LOGIN_URL = "/auth/google/login"

# ログイン画面の役割ごとの設定。role は teacher / student の2種類だけ
LOGIN_ROLES = {
    "teacher": {"heading": "教師用ログイン", "home_url": "/teacher"},
    "student": {"heading": "生徒用ログイン", "home_url": "/student"},
}


def render_login(request: Request, role: str):
    # 役割に合わせてログイン画面を返す（role はユーザー入力ではなく固定の値）
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "heading": LOGIN_ROLES[role]["heading"],
            "home_url": LOGIN_ROLES[role]["home_url"],
            "login_url": f"{GOOGLE_LOGIN_URL}?role={role}",
            # 開発用ログイン（.env の DEV_LOGIN=1 のときだけ出す）
            "dev_login_url": f"/auth/dev/login?role={role}" if auth.DEV_LOGIN else None,
        },
    )


# ウェルカムページ（先生／生徒の選択）
@app.get("/", response_class=HTMLResponse)
def welcome(request: Request):
    return templates.TemplateResponse(request, "welcome.html")


# 教師用のログイン画面
@app.get("/teacher/login", response_class=HTMLResponse)
def teacher_login(request: Request):
    return render_login(request, "teacher")


# 生徒用のログイン画面
@app.get("/student/login", response_class=HTMLResponse)
def student_login(request: Request):
    return render_login(request, "student")


def render_teacher_home(request: Request, material_set_id: str, partial: bool = False):
    # 教材セット選択の画面を返す。ID が見つからない・未指定のときは、1冊目（一覧の先頭）を選ぶ
    # partial が True なら、htmx 用に、右の詳細と本棚（hx-swap-oob）だけを返す
    material_sets = get_confirmed_material_sets()
    selected = next((s for s in material_sets if s["id"] == material_set_id), None)
    if selected is None and material_sets:
        selected = material_sets[0]
    response = templates.TemplateResponse(
        request,
        "teacher_home_detail.html" if partial else "teacher_home.html",
        {"material_sets": material_sets, "selected": selected},
    )
    # 同じURLで、htmx かどうかによって返す中身が変わるため
    response.headers["Vary"] = "HX-Request"
    return response


# 教師用のトップ：教材セット選択。?set= で選択中のセットを指定できる（共有・再読み込みのため）
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher", response_class=HTMLResponse)
def teacher_home(request: Request, material_set_id: str = Query("", alias="set")):
    return render_teacher_home(request, material_set_id)


# 教師用：教材と使用範囲の選択画面。material_id は get_materials() にある、本文が登録済みのIDだけを選択中にする
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/materials", response_class=HTMLResponse)
def teacher_materials(request: Request, material_id: str = ""):
    # 不明なIDや未指定は、エラーにせず「選択なし」の状態にする
    return render_teacher_materials(request, find_selectable_material(material_id))


# 教師用：教材選択の「決定」の送信先。正しければロール候補の選択画面へ進む
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.post("/teacher/materials", response_class=HTMLResponse)
def teacher_materials_submit(
    request: Request,
    material_id: str = Form(""),
    range_type: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
):
    material = find_selectable_material(material_id)
    if material is None:
        return RedirectResponse("/teacher/materials", status_code=303)

    ok, error = validate_range(material, range_type, start, end)
    if not ok:
        # 選んだ教材・範囲の種類・入力した数字は、消さずに画面に戻す
        return render_teacher_materials(
            request, material, range_type=range_type, start=start, end=end, error=error, status_code=422
        )

    # TODO: ここで、題材の保存と「題材から立場の候補を返す処理」の呼び出しを行う（バックエンド担当）
    return RedirectResponse(
        build_next_after_material_url(material, range_type, start, end), status_code=303
    )


# 教師用：ロールの選択画面（Figma 5）。selected は候補にあるIDだけを選択済みにする。back=1 は編集画面などから戻ったとき
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/candidates", response_class=HTMLResponse)
def teacher_candidates(
    request: Request,
    material_id: str = "",
    range_type: str = "",
    start: str = "",
    end: str = "",
    selected: list[str] = Query([]),
    back: str = "",
):
    resolved = resolve_candidates_request(material_id, range_type, start, end)
    if resolved is None:
        return RedirectResponse("/teacher/materials", status_code=303)
    material, range_type, start, end = resolved

    # ダミーの待ち時間。AI接続後に削除する（待ち時間の表示の動作確認用）。戻ってきたとき（back=1）は待たせない
    if back != "1":
        time.sleep(2)

    candidates = get_role_candidates(material["id"])
    return render_teacher_candidates(
        request, material, range_type, start, end, candidates, filter_selected(candidates, selected)
    )


# 教師用：ロールの選択画面の「この内容で追加する」の送信先。正しければ NEXT_AFTER_CONFIRM へ進む
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.post("/teacher/candidates/confirm", response_class=HTMLResponse)
def teacher_candidates_confirm(
    request: Request,
    material_id: str = Form(""),
    range_type: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
    selected: list[str] = Form([]),
):
    resolved = resolve_candidates_request(material_id, range_type, start, end)
    if resolved is None:
        return RedirectResponse("/teacher/materials", status_code=303)
    material, range_type, start, end = resolved

    candidates = get_role_candidates(material["id"])
    selected_ids = filter_selected(candidates, selected)
    error = None
    if not selected:
        error = "使うロールを1つ以上選んでください。"
    elif len(set(selected)) != len(selected_ids):
        error = "選んだロールが正しくありません。もう一度選んでください。"
    elif len(selected_ids) > ROLE_SELECT_MAX:
        error = f"選べるロールは{ROLE_SELECT_MAX}つまでです。選ぶロールを減らしてください。"
    if error:
        # 選んだ状態（候補にあるIDだけ）は、消さずに画面に戻す
        return render_teacher_candidates(
            request, material, range_type, start, end, candidates, selected_ids, error=error, status_code=422
        )

    # 選んだロール（編集後の内容）を、教材セットとして保存する（#13）
    by_id = {c["candidate_id"]: c for c in candidates}
    range_start, range_end = paragraph_range(material["id"], range_type, start, end)
    db.create_material_set(
        chat_service.new_conversation_id(),
        material["id"],
        range_start,
        range_end,
        [
            {
                "role_id": candidate_id,
                "name": by_id[candidate_id]["name"],
                "summary": by_id[candidate_id]["summary"],
                "features": by_id[candidate_id]["features"],
                "image": by_id[candidate_id]["image"],
                "edited": by_id[candidate_id]["edited"],
            }
            for candidate_id in selected_ids
        ],
        question=get_question(),
    )
    return RedirectResponse(NEXT_AFTER_CONFIRM, status_code=303)


def paragraph_range(material_id: str, range_type: str, start: str, end: str):
    # 検証済みの使用範囲を、(第何段落から, 第何段落まで) にする（1から数える）
    # 行で指定したときは、その行を含む段落の範囲にする
    paragraphs = next((m.get("paragraphs") or [] for m in SEED_DATA.get("materials") or [] if m["id"] == material_id), [])
    if range_type == "paragraph":
        return int(start), int(end)
    if range_type == "line":
        first, last = int(start), int(end)
        hit = [i for i, p in enumerate(paragraphs, start=1) if p["line_start"] <= last and p["line_end"] >= first]
        if hit:
            return hit[0], hit[-1]
    return 1, len(paragraphs)


def resolve_candidate_edit_request(candidate_id: str, material_id: str, range_type: str, start: str, end: str):
    # 編集画面の共通の検証。不正な教材・範囲は None（リダイレクト）。候補にない candidate_id は 404
    # 正しければ (教材, 範囲の種類, 開始, 終了, 候補の一覧, 候補)
    resolved = resolve_candidates_request(material_id, range_type, start, end)
    if resolved is None:
        return None
    material, range_type, start, end = resolved
    candidates = get_role_candidates(material["id"])
    candidate = next((c for c in candidates if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise HTTPException(status_code=404, detail="ロールの候補が見つかりません")
    return material, range_type, start, end, candidates, candidate


# 教師用：ロールの編集画面（Figma 6）。クエリはロールの選択画面と同じ。選んでいた候補（selected）は、戻り先に引き継ぐ
# reset=1 のときは、元のAIの案を入力欄に表示する（保存はしない。「決定」を押すと保存される）
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/candidates/{candidate_id}/edit", response_class=HTMLResponse)
def teacher_candidate_edit(
    request: Request,
    candidate_id: str,
    material_id: str = "",
    range_type: str = "",
    start: str = "",
    end: str = "",
    selected: list[str] = Query([]),
    reset: str = "",
):
    resolved = resolve_candidate_edit_request(candidate_id, material_id, range_type, start, end)
    if resolved is None:
        return RedirectResponse("/teacher/materials", status_code=303)
    material, range_type, start, end, candidates, candidate = resolved

    is_reset = reset == "1"
    source = candidate["original"] if is_reset else role_fields(candidate)
    form = {"name": source["name"], "features": pad_features(source["features"])}
    return render_teacher_candidate_edit(
        request, material, range_type, start, end, candidate,
        filter_selected(candidates, selected), form, is_reset=is_reset,
    )


# 教師用：ロールの編集画面の「決定」の送信先。正しければ保存して、ロールの選択画面に戻る
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.post("/teacher/candidates/{candidate_id}/edit", response_class=HTMLResponse)
def teacher_candidate_edit_submit(
    request: Request,
    candidate_id: str,
    name: str = Form(""),
    feature_1: str = Form(""),
    feature_2: str = Form(""),
    feature_3: str = Form(""),
    material_id: str = Form(""),
    range_type: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
    selected: list[str] = Form([]),
):
    resolved = resolve_candidate_edit_request(candidate_id, material_id, range_type, start, end)
    if resolved is None:
        return RedirectResponse("/teacher/materials", status_code=303)
    material, range_type, start, end, candidates, candidate = resolved
    selected_ids = filter_selected(candidates, selected)

    features = [feature_1, feature_2, feature_3]
    cleaned, errors = validate_role_edit(name, features)
    if errors:
        # 入力した値は、消さずに入力欄に戻す
        _, problems = role_edit_problems(name, features)
        form = {"name": name, "features": features}
        return render_teacher_candidate_edit(
            request, material, range_type, start, end, candidate, selected_ids, form,
            errors=errors, invalid_fields=[field for field, _ in problems], status_code=422,
        )

    save_role_edit(material["id"], candidate, cleaned)

    # 戻り先は、ユーザーの入力ではなく、検証済みの値から組み立てる
    return RedirectResponse(
        build_candidates_url(material, range_type, start, end, selected_ids, back=True), status_code=303
    )


# 教師用：作成した（確定済みの）教材セットの一覧
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/material-sets", response_class=HTMLResponse)
def teacher_material_sets(request: Request):
    return templates.TemplateResponse(
        request, "teacher_material_sets.html", {"material_sets": get_confirmed_material_sets()}
    )


# 教師用：教材セット選択で本を押したときの詳細。htmx からは詳細と本棚だけ、通常のアクセスでは画面全体を返す
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/material-sets/{material_set_id}", response_class=HTMLResponse)
def teacher_material_set_detail(request: Request, material_set_id: str):
    is_htmx = request.headers.get("HX-Request") == "true"
    return render_teacher_home(request, material_set_id, partial=is_htmx)


# 教師用：教材セットの編集（準備中）。確定済みの教材セットの ID だけを受け付ける
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/material-sets/{material_set_id}/edit", response_class=HTMLResponse)
def teacher_material_set_edit(request: Request, material_set_id: str):
    if material_set_id not in {s["id"] for s in get_confirmed_material_sets()}:
        raise HTTPException(status_code=404, detail="教材セットが見つかりません")
    return templates.TemplateResponse(request, "teacher_placeholder.html", {"heading": "セットを編集"})


# 教師用：授業の画面（仮）。中身はまだない。確定済みの教材セットの ID だけを受け付ける
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/material-sets/{material_set_id}/lesson", response_class=HTMLResponse)
def teacher_lesson(request: Request, material_set_id: str):
    if material_set_id not in {s["id"] for s in get_confirmed_material_sets()}:
        raise HTTPException(status_code=404, detail="教材セットが見つかりません")
    return templates.TemplateResponse(request, "teacher_lesson.html")


# 教師用：生徒のチャット一覧。only_detected=1 なら、検知がある会話だけにする
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/chats", response_class=HTMLResponse)
def teacher_chats(request: Request, only_detected: int = 0):
    summaries = get_conversation_summaries()
    detected = [s for s in summaries if s["detections"]]
    # 見出し用の教材セットのタイトル（会話に出てくる教材セットを、出てきた順に並べる）
    material_set_ids = dict.fromkeys(s["material_set_id"] for s in summaries)
    titles = [t for t in (get_material_set_title(i) for i in material_set_ids) if t]
    return templates.TemplateResponse(
        request,
        "teacher_chats.html",
        {
            "conversations": detected if only_detected == 1 else summaries,
            "material_set_title": "、".join(titles),
            "detected_count": len(detected),
            "total_count": len(summaries),
            "only_detected": only_detected == 1,
        },
    )


# 教師用：チャット閲覧画面（仮）。次のissue「チャット閲覧画面と検知結果の表示」で本物にする
# TODO: 教師かどうかの確認（ログイン・権限）は、別issue「Googleログインと権限を実装する」でここに足す。今は誰でも開ける
@app.get("/teacher/chats/{conversation_id}", response_class=HTMLResponse)
def teacher_chat_detail(request: Request, conversation_id: str):
    # conversations にある ID だけを受け付ける
    conversation = find_conversation_summary(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会話が見つかりません")
    return templates.TemplateResponse(
        request, "teacher_chat_detail.html", {"conversation": conversation}
    )


# 生徒用のトップ（仮）
@app.get("/student", response_class=HTMLResponse)
def student_home(request: Request):
    return templates.TemplateResponse(
        request, "student_home.html", {"material_sets": get_confirmed_material_sets()}
    )


# 生徒用のロール選択画面
@app.get("/student/roles", response_class=HTMLResponse)
def student_roles(request: Request, material_set_id: str = Query("", alias="set")):
    # ?set= で教材セットを選んだときは、その教材セットを Cookie に覚えて、ロールを出す（#44）
    if material_set_id:
        material_set = get_material_set_for_chat(material_set_id)
        if material_set is None:
            return RedirectResponse("/student", status_code=303)
        response = templates.TemplateResponse(request, "role_select.html", {"roles": material_set["roles"]})
        response.set_cookie(MATERIAL_SET_COOKIE, material_set_id, httponly=True, samesite="lax")
        return response
    return templates.TemplateResponse(
        request, "role_select.html", {"roles": get_roles(request)}
    )


# 生徒用：対話前の問いかけ画面。role_id は get_roles() にあるIDだけを受け付ける
@app.get("/student/question", response_class=HTMLResponse)
def student_question(request: Request, role_id: str | None = None):
    role = find_role(role_id, request) if role_id else None
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)
    return render_student_question(request, role)


# 生徒用：問いかけ画面のフォームの送信先。正しければチャット画面へ進む
@app.post("/student/question", response_class=HTMLResponse)
def student_question_submit(
    request: Request,
    role_id: str = Form(""),
    opinion: str = Form(""),
):
    material_set = conversation_material_set(request)
    role = find_conversation_role(role_id, material_set) if role_id else None
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)

    # ブラウザは送信時に改行を「\r\n」（2文字）にするため、画面の maxlength と数え方をそろえる
    text = opinion.replace("\r\n", "\n").strip()
    error = None
    if not text:
        error = "考えを書いてから、対話を始めてください。"
    elif len(text) > OPINION_MAX_LENGTH:
        error = f"文章が長すぎます。{OPINION_MAX_LENGTH}文字以内にしてください。"
    if error:
        # 入力した文章は消さずに、入力欄に戻す
        return render_student_question(request, role, opinion=opinion, error=error, status_code=422)

    # 対話前の考えを保存して、会話を始める（#46）
    conversation_id = chat_service.new_conversation_id()
    db.create_conversation(
        conversation_id,
        role["id"],
        text,
        request.cookies.get(MATERIAL_SET_COOKIE) if material_set else None,
        student_id=current_student_id(request),
    )

    # リダイレクト先は、ユーザーの入力ではなく、検証したロールのIDと、作った会話 ID から組み立てる
    return RedirectResponse(
        f"/student/chat?role_id={role['id']}&conversation_id={conversation_id}", status_code=303
    )


# 既存のチャット画面。role_id があれば、選んだロールを表示する
@app.get("/student/chat", response_class=HTMLResponse)
def student_chat(request: Request, role_id: str | None = None, conversation_id: str | None = None):
    if role_id is None:
        return templates.TemplateResponse(request, "index.html")
    conversation = db.get_conversation(conversation_id) if chat_service.is_valid_conversation_id(conversation_id) else None
    if conversation is not None and (
        conversation["role_id"] != role_id or conversation["student_id"] != current_student_id(request)
    ):
        # 従来どおり、別ロールの会話IDを新しい会話へ引き継がない。ほかの人の会話も引き継がない（#33）
        conversation = None
    material_set = conversation_material_set(request, conversation)
    role = find_conversation_role(role_id, material_set)
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)
    # 問いかけ画面から来たときは、その会話を続ける。なければ新しい会話にする
    conversation_id = conversation["id"] if conversation else chat_service.new_conversation_id()
    if conversation is None:
        # 問いかけ画面を通らずに開く場合も、IDを発行した時点の教材を固定する。
        db.create_conversation(
            conversation_id,
            role["id"],
            "",
            request.cookies.get(MATERIAL_SET_COOKIE) if material_set else None,
            student_id=current_student_id(request),
        )
    return templates.TemplateResponse(request, "index.html", {"role": role, "conversation_id": conversation_id})


# 生徒用：対話後の感想入力画面。role_id は get_roles() にあるIDだけを受け付ける
@app.get("/student/reflection", response_class=HTMLResponse)
def student_reflection(request: Request, role_id: str | None = None, conversation_id: str | None = None):
    role = find_role(role_id, request) if role_id else None
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)
    return render_student_reflection(request, role, find_conversation(conversation_id, role, request))


# 生徒用：感想入力画面のフォームの送信先。正しければ終了画面へ進む
@app.post("/student/reflection", response_class=HTMLResponse)
def student_reflection_submit(
    request: Request,
    role_id: str = Form(""),
    reflection: str = Form(""),
    conversation_id: str = Form(""),
):
    role = find_role(role_id, request) if role_id else None
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)
    conversation = find_conversation(conversation_id, role, request)

    # ブラウザは送信時に改行を「\r\n」（2文字）にするため、画面の maxlength と数え方をそろえる
    text = reflection.replace("\r\n", "\n").strip()
    error = None
    if not text:
        error = "感想を書いてから、送信してください。"
    elif len(text) > REFLECTION_MAX_LENGTH:
        error = f"文章が長すぎます。{REFLECTION_MAX_LENGTH}文字以内にしてください。"
    if error:
        # 入力した文章は消さずに、入力欄に戻す
        return render_student_reflection(
            request, role, conversation, reflection=reflection, error=error, status_code=422
        )

    # 対話後の感想を保存する（#46）。すでに保存した会話は書き換えない（save_reflection が何もしない）
    # TODO: 教師の画面に届くようにする（教師のチャット一覧を DB から読むようにしたとき）
    if conversation:
        db.save_reflection(conversation["id"], text)

    return RedirectResponse("/student/finish", status_code=303)


# 生徒用：対話の終了画面（仮）
@app.get("/student/finish", response_class=HTMLResponse)
def student_finish(request: Request):
    return templates.TemplateResponse(request, "student_finish.html")


def get_material_text(material_id: str, range_start: int | None = None, range_end: int | None = None):
    # AIに渡す本文。段落ごとに [p001] のような段落IDを付ける（AIが根拠の場所を示せるように）
    # range_start・range_end（第何段落か。1から数える）を渡すと、その範囲の段落だけにする（#44）
    for m in SEED_DATA.get("materials") or []:
        if m["id"] == material_id:
            paragraphs = m.get("paragraphs") or []
            if range_start is not None and range_end is not None:
                paragraphs = paragraphs[range_start - 1 : range_end]
            return "\n\n".join(f"[{p['id']}]\n{p['text']}" for p in paragraphs)
    return ""


def get_paragraph_labels():
    # 根拠の場所の表示名 {段落ID: "第3段落"}。段落の番号は、教材の中で1から数える
    return {
        p["id"]: f"第{i}段落"
        for m in SEED_DATA.get("materials") or []
        for i, p in enumerate(m.get("paragraphs") or [], start=1)
    }


def find_role_source(role_id: str):
    # プリセットのロールか、AIの候補から、元のロールを探す。なければ None
    for role in (SEED_DATA.get("preset_roles") or {}).get("roles") or []:
        if role["id"] == role_id:
            return role
    for group in SEED_DATA.get("role_candidates") or []:
        for candidate in group.get("candidates") or []:
            if candidate["candidate_id"] == role_id:
                return candidate
    return None


def get_material_set_for_chat(material_set_id: str):
    # 生徒の対話に使う教材セット。{"material_id", "material_text", "roles"} を返す。なければ None
    # roles は prompts.build_system_prompt に渡せる形（教師が編集した名前・特徴を使い、根拠と口調は元のロールから取る）
    record = next((r for r in read_material_set_records() if r["id"] == material_set_id), None)
    if record is None:
        return None
    roles = []
    for r in record["roles"]:
        source = find_role_source(r["role_id"]) or {}
        roles.append(
            {
                "id": r["role_id"],
                "name": r["name"],
                "summary": source.get("summary") or "",
                "features": list(r["features"]),
                "basis": list(source.get("basis") or []),
                "prompt_hint": source.get("prompt_hint") or "",
                "image": r["image"],
            }
        )
    return {
        "material_id": record["material_id"],
        "material_text": get_material_text(record["material_id"], record["range_start"], record["range_end"]),
        "roles": roles,
    }


@app.post("/chat", response_class=HTMLResponse)
def chat(
    request: Request,
    message: str = Form(..., max_length=200),
    role_id: str | None = Form(None),
    conversation_id: str | None = Form(None),
):
    # role_id と conversation_id は、チャット画面（/student/chat?role_id=...）のフォームから届く
    text = message.strip()
    if not text or not chat_service.is_valid_conversation_id(conversation_id):
        raise HTTPException(status_code=400)
    existing = db.get_conversation(conversation_id)
    if existing is not None and existing["student_id"] != current_student_id(request):
        # ほかの人の会話には書き込ませない（#33）
        raise HTTPException(status_code=403)
    material_set = conversation_material_set(request, existing)
    role = find_conversation_role(role_id, material_set) if role_id else None
    if role is None:
        raise HTTPException(status_code=400)
    # すでにある会話は、同じロールでしか続けられない（ほかのロールの会話に混ぜない）
    if existing is not None and existing["role_id"] != role["id"]:
        raise HTTPException(status_code=400)
    if not chat_service.allow_request(conversation_id):
        # 送信が多すぎる（#35）。画面は 429 を見て「少し待ってから」の文を出す
        raise HTTPException(status_code=429)

    material_set_id = existing["material_set_id"] if existing is not None else (
        request.cookies.get(MATERIAL_SET_COOKIE) if material_set is not None else None
    )
    if not db.bind_conversation_context(conversation_id, role["id"], material_set_id, current_student_id(request)):
        raise HTTPException(status_code=400)

    # 教材セットを選んでいれば、その使用範囲の本文だけを渡す（#44）
    if material_set is not None:
        material_text = material_set["material_text"]
    else:
        material_text = get_material_text(SEED_DATA["preset_roles"]["material_id"])
    try:
        reply, evidence = chat_service.reply(conversation_id, role, material_text, text, get_paragraph_labels())
    except AIBusyError:
        # 画面は、状態コードを見て決まった文を出す（index.html の MESSAGES）
        raise HTTPException(status_code=429)
    except AIError:
        raise HTTPException(status_code=502)
    return templates.TemplateResponse(
        request,
        "_message.html",
        {"user_message": message, "reply": reply, "role": role, "evidence": evidence},
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}
