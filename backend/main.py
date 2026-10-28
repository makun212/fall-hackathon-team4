from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


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
