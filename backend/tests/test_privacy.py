"""個人情報の保護（#36）のテスト。backend フォルダで python -m unittest discover -s tests"""

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import ai  # noqa: E402
import auth  # noqa: E402
import chat  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402

NAME = "山田 太郎"
EMAIL = "taro.yamada@example.test"


class FakeGoogle:
    async def authorize_redirect(self, request, redirect_uri):
        return RedirectResponse("https://accounts.google.com/fake", status_code=302)

    async def authorize_access_token(self, request):
        return {"userinfo": {"sub": "777", "email": EMAIL, "email_verified": True, "name": NAME}}


class FakeModels:
    def __init__(self):
        self.sent = []

    def generate_content(self, model, contents, config):
        self.sent.append(str(config.system_instruction) + json.dumps([c.parts[0].text for c in contents], ensure_ascii=False))
        return type("Response", (), {"text": json.dumps({"reply": "返事", "evidence": []})})()


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.models = FakeModels()
        for context in [
            patch.object(ai, "_client", type("Client", (), {"models": self.models})()),
            patch.object(chat, "conversation_limiter", RateLimiter(1000, 60)),
            patch.object(chat, "global_limiter", RateLimiter(1000, 60)),
            patch.object(auth.oauth, "google", FakeGoogle(), create=True),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.app = TestClient(main.app)
        self.app.get("/auth/google/login?role=student")
        self.app.get("/auth/google/callback")

    def test_email_is_not_stored(self):
        user = db.get_user("google:777")
        self.assertEqual(NAME, user["name"])
        self.assertFalse(user["email"])

    def test_name_and_email_are_not_sent_to_ai(self):
        response = self.app.post("/student/question", data={"role_id": "r001", "opinion": "考え"}, follow_redirects=False)
        conversation_id = re.search(r"conversation_id=([0-9a-f]{32})", response.headers["location"]).group(1)
        self.app.post("/chat", data={"message": "質問", "role_id": "r001", "conversation_id": conversation_id})
        self.assertEqual(1, len(self.models.sent))
        self.assertNotIn(NAME, self.models.sent[0])
        self.assertNotIn(EMAIL, self.models.sent[0])
        self.assertNotIn("google:777", self.models.sent[0])


if __name__ == "__main__":
    unittest.main()
