import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI()
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


SEED_DATA = load_seed_data()

# 仮の保存場所（サーバーのメモリ）。再起動すると消える。DBが入ったら、ロールの保存・取得の処理に差し替える
# キーは (material_id, candidate_id)。値は {"name": ..., "features": [...]}、または None（元に戻した）
ROLE_EDITS = {}
ROLE_EDITS_LOCK = threading.Lock()


def get_roles():
    # 生徒が選べるロールの一覧。教師が確定したロールに差し替えるときは、ここだけ直す
    return SEED_DATA["preset_roles"]["roles"]


def find_role(role_id: str):
    # get_roles() の中から ID が一致するロールを返す。なければ None
    for role in get_roles():
        if role["id"] == role_id:
            return role
    return None


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
def get_pre_opinion(role_id: str):
    # 対話前の考えを取り出すのは、この関数1か所だけ
    reflections = SEED_DATA.get("reflections") or []
    if not reflections:
        return "（対話の前の考えが見つかりません）"
    return reflections[0]["pre_opinion"]


def render_student_reflection(request: Request, role, reflection: str = "", error: str | None = None, status_code: int = 200):
    # 感想入力画面を返す（GET と、POST の入力エラーで共用）
    return templates.TemplateResponse(
        request,
        "student_reflection.html",
        {
            "role": role,
            "question": get_question(),
            "pre_opinion": get_pre_opinion(role["id"]),
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
    # 一覧に使うデータを、ダミーデータから取り出す。キー名を参照するのはここだけ
    # DBに変えるときは、この関数の中身だけを、DBから同じ形で読む処理に差し替える
    data = SEED_DATA
    return {
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

    # 2. 編集画面で保存した内容（ROLE_EDITS）があれば、上書きする。None は「元に戻した」
    for candidate in candidates:
        key = (material_id, candidate["candidate_id"])
        if key not in ROLE_EDITS:
            continue
        saved = ROLE_EDITS[key]
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
    # TODO: DBに保存する。教材セットの保存（issue: ロールの保存・取得）と合わせる
    value = None if cleaned == candidate["original"] else cleaned
    with ROLE_EDITS_LOCK:
        ROLE_EDITS[(material_id, candidate["candidate_id"])] = value


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
                    "name": edited.get("name") or source["name"],
                    "features": list(edited.get("features") or source.get("features") or []),
                    "image": find_static_image(source.get("image")),
                    "edited": bool(edited),
                }
            )

        records.append(
            {
                "id": ms["id"],
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

    # TODO: ここで、選んだロール（と編集内容）を、教材セットとして保存する。保存後は、教材セットの選択画面に移動する
    return RedirectResponse(NEXT_AFTER_CONFIRM, status_code=303)


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
    return templates.TemplateResponse(request, "student_home.html")


# 生徒用のロール選択画面
@app.get("/student/roles", response_class=HTMLResponse)
def student_roles(request: Request):
    return templates.TemplateResponse(
        request, "role_select.html", {"roles": get_roles()}
    )


# 生徒用：対話前の問いかけ画面。role_id は get_roles() にあるIDだけを受け付ける
@app.get("/student/question", response_class=HTMLResponse)
def student_question(request: Request, role_id: str | None = None):
    role = find_role(role_id) if role_id else None
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
    role = find_role(role_id) if role_id else None
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

    # TODO: ここで、事前の意見を保存する（バックエンド担当が、DBの導入後に作る）

    # リダイレクト先は、ユーザーの入力ではなく、検証したロールのIDから組み立てる
    return RedirectResponse(f"/student/chat?role_id={role['id']}", status_code=303)


# 既存のチャット画面。role_id があれば、選んだロールを表示する
@app.get("/student/chat", response_class=HTMLResponse)
def student_chat(request: Request, role_id: str | None = None):
    if role_id is None:
        return templates.TemplateResponse(request, "index.html")
    role = find_role(role_id)
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"role": role})


# 生徒用：対話後の感想入力画面。role_id は get_roles() にあるIDだけを受け付ける
@app.get("/student/reflection", response_class=HTMLResponse)
def student_reflection(request: Request, role_id: str | None = None):
    role = find_role(role_id) if role_id else None
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)
    return render_student_reflection(request, role)


# 生徒用：感想入力画面のフォームの送信先。正しければ終了画面へ進む
@app.post("/student/reflection", response_class=HTMLResponse)
def student_reflection_submit(
    request: Request,
    role_id: str = Form(""),
    reflection: str = Form(""),
):
    role = find_role(role_id) if role_id else None
    if role is None:
        return RedirectResponse("/student/roles", status_code=303)

    # ブラウザは送信時に改行を「\r\n」（2文字）にするため、画面の maxlength と数え方をそろえる
    text = reflection.replace("\r\n", "\n").strip()
    error = None
    if not text:
        error = "感想を書いてから、送信してください。"
    elif len(text) > REFLECTION_MAX_LENGTH:
        error = f"文章が長すぎます。{REFLECTION_MAX_LENGTH}文字以内にしてください。"
    if error:
        # 入力した文章は消さずに、入力欄に戻す
        return render_student_reflection(request, role, reflection=reflection, error=error, status_code=422)

    # TODO: ここで、対話後の意見を保存し、教師に届くようにする。保存後は、書き換えられないようにする

    return RedirectResponse("/student/finish", status_code=303)


# 生徒用：対話の終了画面（仮）
@app.get("/student/finish", response_class=HTMLResponse)
def student_finish(request: Request):
    return templates.TemplateResponse(request, "student_finish.html")


@app.post("/chat", response_class=HTMLResponse)
def chat(
    request: Request,
    message: str = Form(..., max_length=200),
    role_id: str | None = Form(None),
):
    # role_id はチャット画面のフォームから届く。見つからなければ None（アイコンは「AI」になる）
    role = find_role(role_id) if role_id else None
    # TODO: ここをAIのAPI呼び出しに置き換える
    reply = f"（ダミー）「{message}」について、豊太郎として答えます。"
    return templates.TemplateResponse(
        request,
        "_message.html",
        {"user_message": message, "reply": reply, "role": role},
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}