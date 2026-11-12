import base64
import logging
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import TimestampSigner

import db

# Googleログインと権限（#33）
# - 先生も生徒も Google でログインする。初めての人は生徒として登録する
# - .env の TEACHER_EMAILS に書いた Google アカウントでログインすると、先生になる（生徒は先生になれない）
# - /teacher 以下は教師だけ、生徒の画面と /chat はログインした人だけが使える（access_control）
# - 会話は作った本人だけが見られる（main.py で conversation の student_id と照らし合わせる）
load_dotenv(Path(__file__).resolve().parent / ".env")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID") or ""
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET") or ""
# 先生として使う Google アカウントのメールアドレス（カンマ区切り）。大文字・小文字は区別しない
# 「*」と書くと、先生用のログインから入った人は全員先生になる（審査員のアドレスがわからない発表の日だけ使う）
TEACHER_EMAILS = {e.strip().lower() for e in (os.getenv("TEACHER_EMAILS") or "").split(",") if e.strip()}
# 開発用ログイン。Google の設定がなくても手元で動かせるようにする。本番では絶対に 1 にしない
DEV_LOGIN = os.getenv("DEV_LOGIN") == "1"
# セッション（ログイン状態）を署名する鍵。本番では必ず .env に長いランダムな文字列を書く
# 書かなければ起動のたびに作り直す（再起動するとログアウトされる）
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)
# 本番（https）では 1 にする。Cookie が https のときだけ送られるようになる
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY") == "1"
SESSION_COOKIE = "session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60

LOGIN_PAGES = {"teacher": "/teacher/login", "student": "/student/login"}
HOME_PAGES = {"teacher": "/teacher", "student": "/student"}
# 先生として登録されていない人が、先生の画面を開こうとしたときの戻り先
NOT_TEACHER_PAGE = "/teacher/login?error=not_teacher"

oauth = OAuth()
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        "google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def current_user(request: Request):
    # ログインしている人（db.users の1行）。していなければ None。role は毎回DBから読む
    user_id = request.session.get("user_id") if "session" in request.scope else None
    return db.get_user(user_id) if user_id else None


def is_teacher(user) -> bool:
    return user is not None and user["role"] == "teacher"


def start_session(request: Request, user):
    # セッションを作り直してからログインする（ログイン前のセッションを使い回さない）
    request.session.clear()
    request.session["user_id"] = user["id"]


def teacher_role_for(email: str, wanted_role: str):
    # このアカウントの役割。"teacher"、"student"、None（今のまま）のどれかを返す
    # メールアドレスは判定に使うだけで、保存しない（#36）
    if email and email.lower() in TEACHER_EMAILS:
        return "teacher"
    if "*" in TEACHER_EMAILS:
        # 全員先生にできる日は、先生用から入った人だけ先生にする。生徒用から入った人は今のまま
        return "teacher" if wanted_role == "teacher" else None
    # リストから外れたアカウントは、生徒に戻す
    return "student"


def after_login_url(user, wanted_role: str) -> str:
    # 先生用から入ったのに先生でなければ、先生用のログイン画面に「登録されていません」と出す
    if wanted_role == "teacher":
        return HOME_PAGES["teacher"] if is_teacher(user) else NOT_TEACHER_PAGE
    return HOME_PAGES["student"]


# ---- ログイン・ログアウト ----

# ログインの失敗の理由として画面に出してよい、決まった文字（Google から届いた文字をそのまま画面に出さない）
LOGIN_FAILURE_REASONS = {"invalid_client", "mismatching_state", "access_denied", "invalid_grant", "email_not_verified"}


def login_failed(wanted_role: str, reason: str):
    reason = reason if reason in LOGIN_FAILURE_REASONS else "unknown"
    return RedirectResponse(f"{LOGIN_PAGES.get(wanted_role, LOGIN_PAGES['student'])}?error=google_{reason}", status_code=303)


@router.get("/auth/google/login")
async def google_login(request: Request, role: str = "student"):
    wanted_role = role if role in LOGIN_PAGES else "student"
    if not hasattr(oauth, "google"):
        if DEV_LOGIN:
            return RedirectResponse(f"/auth/dev/login?role={wanted_role}", status_code=303)
        return HTMLResponse("Googleログインの設定が終わっていません。管理者に連絡してください。", status_code=503)
    request.session["login_role"] = wanted_role
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request):
    wanted_role = request.session.get("login_role", "student")
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        # 理由（invalid_client＝シークレットが違う、mismatching_state＝セッションが切れた など）をログと画面に出す
        # 理由の名前だけを出し、ID・シークレット・トークンは出さない
        reason = getattr(e, "error", None) or "unknown"
        logger.warning("Googleログインに失敗しました: %s", reason)
        return login_failed(wanted_role, reason)
    info = token.get("userinfo") or {}
    if not info.get("sub") or not info.get("email_verified"):
        logger.warning("Googleログインに失敗しました: email_not_verified")
        return login_failed(wanted_role, "email_not_verified")
    # 保存するのは、Google のユーザー ID と名前だけ（#36）。メールアドレスは先生かどうかの判定にだけ使う
    user = db.upsert_user(f"google:{info['sub']}", "", info.get("name") or "")
    role = teacher_role_for(info.get("email") or "", wanted_role)
    if role is not None and role != user["role"]:
        db.set_user_role(user["id"], role)
        user = db.get_user(user["id"])
    start_session(request, user)
    return RedirectResponse(after_login_url(user, wanted_role), status_code=303)


@router.get("/auth/dev/login")
def dev_login(request: Request, role: str = "student"):
    # 開発用ログイン（DEV_LOGIN=1 のときだけ）。先生用から入ると、そのまま開発用の先生になる
    if not DEV_LOGIN:
        return HTMLResponse("見つかりません", status_code=404)
    wanted_role = role if role in LOGIN_PAGES else "student"
    user = db.upsert_user(f"dev:{wanted_role}", "", "開発用の" + ("先生" if wanted_role == "teacher" else "生徒"))
    db.set_user_role(user["id"], wanted_role)
    user = db.get_user(user["id"])
    start_session(request, user)
    return RedirectResponse(after_login_url(user, wanted_role), status_code=303)


@router.post("/auth/logout")
def logout(request: Request):
    # ログアウトも状態変更。外部ページのリンクや画像では実行させない。
    if not same_origin_request(request):
        return JSONResponse({"detail": "送信元を確認できません"}, status_code=403)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---- 権限の確認（すべてのリクエストの前に通る） ----

# ログインしなくても開けるページ
PUBLIC_PATHS = {"/", "/teacher/login", "/student/login", "/api/health", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/", "/auth/")


def needs(path: str):
    # このページに必要な権限。None（誰でも）、"login"（ログインした人）、"teacher"（教師だけ）
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return None
    if path == "/teacher" or path.startswith("/teacher/"):
        return "teacher"
    return "login"


def deny(request: Request, login_page: str):
    # 画面の移動ならログイン画面へ。htmx（チャットの送信など）やPOSTには 401 を返す
    if request.method == "GET" and request.headers.get("HX-Request") != "true":
        return RedirectResponse(login_page, status_code=303)
    return JSONResponse({"detail": "ログインしてください"}, status_code=401)


async def access_control(request: Request, call_next):
    required = needs(request.url.path)
    if required is None:
        return await call_next(request)
    user = current_user(request)
    is_teacher_page = required == "teacher"
    if user is None:
        return deny(request, LOGIN_PAGES["teacher" if is_teacher_page else "student"])
    if is_teacher_page and not is_teacher(user):
        if request.method == "GET":
            return RedirectResponse(NOT_TEACHER_PAGE, status_code=303)
        return JSONResponse({"detail": "教師だけが使えます"}, status_code=403)
    # Cookieが送られてきても、別サイトのフォームからの更新は認めない。
    # OriginがないブラウザはRefererで確認し、両方なければ安全側に拒否する。
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin_request(request):
        return JSONResponse({"detail": "送信元を確認できません。アプリの画面から再送してください。"}, status_code=403)
    request.state.user = user
    return await call_next(request)


def origin_tuple(value: str):
    """scheme・host・実効portを比較する。文字列の前方一致では確認しない。"""
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            return None
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        return parts.scheme, parts.hostname.lower(), port
    except ValueError:
        return None


def same_origin_request(request: Request) -> bool:
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    # Originが存在するなら、不正でもRefererへフォールバックさせない。
    source = request.headers.get("origin") if "origin" in request.headers else request.headers.get("referer", "")
    origin = origin_tuple(source)
    return origin is not None and origin == origin_tuple(str(request.url))


# ---- テスト用 ----


def session_cookie(data: dict) -> str:
    # SessionMiddleware と同じ形で、署名したセッションの Cookie の値を作る（テストでログインした状態にするため）
    payload = base64.b64encode(json.dumps(data).encode("utf-8"))
    return TimestampSigner(SESSION_SECRET).sign(payload).decode("utf-8")
