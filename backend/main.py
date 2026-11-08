from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI()
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
templates = Jinja2Templates(directory=FRONTEND_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/auth/google")
def auth_google(role: Literal["student", "teacher"]):
    # TODO: ここに Google ログイン（OAuth）を入れる。
    #       role（student / teacher）に応じて、ログイン後の移動先を分ける
    return PlainTextResponse("ログイン処理は未実装です", status_code=501)


@app.post("/chat", response_class=HTMLResponse)
def chat(request: Request, message: str = Form(..., max_length=500)):
    # TODO: ここをAIのAPI呼び出しに置き換える
    reply = f"（ダミー）「{message}」について、豊太郎として答えます。"
    return templates.TemplateResponse(
        request, "_message.html", {"user_message": message, "reply": reply}
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}