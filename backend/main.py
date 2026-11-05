import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
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


# 教師用のトップ（仮）
@app.get("/teacher", response_class=HTMLResponse)
def teacher_home(request: Request):
    return templates.TemplateResponse(request, "teacher_home.html")


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