"""Googleログインと教師コード（#33）のテスト。本物の Google は使わない。backend フォルダで python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from auth_helper import login  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402


class FakeGoogle:
    # authlib の oauth.google の代わり。ログイン画面への移動と、ログイン後の情報を偽物にする
    def __init__(self, userinfo):
        self.userinfo = userinfo

    async def authorize_redirect(self, request, redirect_uri):
        return RedirectResponse("https://accounts.google.com/fake", status_code=302)

    async def authorize_access_token(self, request):
        return {"userinfo": self.userinfo}


GOOGLE_USER = {"sub": "12345", "email": "student@example.test", "email_verified": True, "name": "山田 太郎"}


class AuthTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.app = TestClient(main.app)
        for context in [
            patch.object(auth, "code_limiter", RateLimiter(5, 600)),
            patch.object(auth, "TEACHER_CODE", "correct-code"),
            patch.object(auth, "DEV_LOGIN", False),
        ]:
            context.start()
            self.addCleanup(context.stop)

    def google_login(self, role, userinfo=GOOGLE_USER):
        with patch.object(auth.oauth, "google", FakeGoogle(userinfo), create=True):
            first = self.app.get(f"/auth/google/login?role={role}", follow_redirects=False)
            self.assertEqual(302, first.status_code)
            return self.app.get("/auth/google/callback", follow_redirects=False)

    # ---- ログインしていない人 ----

    def test_public_pages_are_open(self):
        for path in ["/", "/teacher/login", "/student/login", "/api/health"]:
            self.assertEqual(200, self.app.get(path).status_code, path)

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual("/student/login", self.app.get("/student", follow_redirects=False).headers["location"])
        self.assertEqual("/teacher/login", self.app.get("/teacher", follow_redirects=False).headers["location"])
        self.assertEqual(401, self.app.post("/chat", data={"message": "x"}).status_code)
        self.assertEqual(401, self.app.get("/student/roles", headers={"HX-Request": "true"}).status_code)

    # ---- Google ログイン ----

    def test_student_google_login(self):
        response = self.google_login("student")
        self.assertEqual("/student", response.headers["location"])
        user = db.get_user("google:12345")
        self.assertEqual(("student", "山田 太郎"), (user["role"], user["name"]))
        self.assertEqual(200, self.app.get("/student").status_code)
        self.assertIn("ログアウト", self.app.get("/student").text)

    def test_teacher_login_asks_for_code_until_correct(self):
        response = self.google_login("teacher")
        self.assertEqual("/teacher/code", response.headers["location"])
        self.assertEqual("/teacher/code", self.app.get("/teacher", follow_redirects=False).headers["location"])
        self.assertEqual(403, self.app.post("/teacher/code", data={"code": "wrong"}).status_code)
        self.assertEqual("student", db.get_user("google:12345")["role"])
        response = self.app.post("/teacher/code", data={"code": "correct-code"}, follow_redirects=False)
        self.assertEqual("/teacher", response.headers["location"])
        self.assertEqual("teacher", db.get_user("google:12345")["role"])
        self.assertEqual(200, self.app.get("/teacher").status_code)
        # 次からは、先生用から入るとそのまま先生のトップへ
        self.app.get("/auth/logout")
        self.assertEqual("/teacher", self.google_login("teacher").headers["location"])

    def test_teacher_code_is_rate_limited(self):
        self.google_login("teacher")
        statuses = [self.app.post("/teacher/code", data={"code": "wrong"}).status_code for _ in range(6)]
        self.assertEqual([403] * 5 + [429], statuses)
        # 上限のあとは、正しいコードでも受け付けない
        self.assertEqual(429, self.app.post("/teacher/code", data={"code": "correct-code"}).status_code)

    def test_teacher_code_not_configured(self):
        self.google_login("teacher")
        with patch.object(auth, "TEACHER_CODE", ""):
            self.assertEqual(503, self.app.post("/teacher/code", data={"code": ""}).status_code)
        self.assertEqual("student", db.get_user("google:12345")["role"])

    def test_unverified_email_is_rejected(self):
        response = self.google_login("student", {**GOOGLE_USER, "email_verified": False})
        self.assertEqual("/student/login", response.headers["location"])
        self.assertIsNone(db.get_user("google:12345"))

    def test_role_param_cannot_make_a_teacher(self):
        # ?role=teacher で入っても、教師コードなしでは教師にならない
        self.google_login("teacher")
        self.assertEqual(303, self.app.get("/teacher/chats", follow_redirects=False).status_code)
        self.assertEqual("student", db.get_user("google:12345")["role"])

    def test_logout(self):
        self.google_login("student")
        self.app.get("/auth/logout")
        self.assertEqual("/student/login", self.app.get("/student", follow_redirects=False).headers["location"])

    def test_google_not_configured(self):
        self.assertFalse(hasattr(auth.oauth, "google"))
        self.assertEqual(503, self.app.get("/auth/google/login?role=student").status_code)

    # ---- 開発用ログイン ----

    def test_dev_login_is_off_by_default(self):
        self.assertEqual(404, self.app.get("/auth/dev/login?role=student").status_code)
        self.assertNotIn("/auth/dev/login", self.app.get("/student/login").text)

    def test_dev_login_when_enabled(self):
        with patch.object(auth, "DEV_LOGIN", True):
            self.assertIn("/auth/dev/login?role=teacher", self.app.get("/teacher/login").text)
            response = self.app.get("/auth/dev/login?role=teacher", follow_redirects=False)
            self.assertEqual("/teacher/code", response.headers["location"])
            self.app.post("/teacher/code", data={"code": "correct-code"})
            self.assertEqual(200, self.app.get("/teacher/chats").status_code)

    def test_teacher_sees_student_names(self):
        login(self.app, role="teacher")
        db.upsert_user("google:999", "s@example.test", "佐藤 花子")
        db.create_conversation("d" * 32, "r001", "考え", student_id="google:999")
        db.save_messages("d" * 32, "r001", [{"role": "user", "content": "質問"}])
        self.assertIn("佐藤 花子", self.app.get("/teacher/chats").text)


if __name__ == "__main__":
    unittest.main()
