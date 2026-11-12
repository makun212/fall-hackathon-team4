"""Browser-shaped normal requests, real Authlib state checks, and fake AI only."""
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authlib.integrations.starlette_client import OAuth
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import ai
import auth
import chat
import db
import main
from auth_helper import login
from rate_limit import RateLimiter
from test_candidates import FakeModels


class SecurityCompatibilityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        previous = db._engine
        self.addCleanup(engine.dispose)
        self.addCleanup(setattr, db, "_engine", previous)
        db.use_engine(engine)
        self.models = FakeModels()
        for context in (
            patch.object(ai, "_client", type("Client", (), {"models": self.models})()),
            patch.object(chat, "conversation_limiter", RateLimiter(6, 60)),
            patch.object(chat, "global_limiter", RateLimiter(10, 60)),
        ):
            context.start()
            self.addCleanup(context.stop)

    def client(self, role, base_url="http://testserver", app=None):
        client = TestClient(app or main.app, base_url=base_url)
        self.addCleanup(client.close)
        login(client, role=role, user_id=f"test:{role}")
        # GETにOriginを付ける共通ヘルパーに頼らず、ブラウザの各POSTを再現する。
        client.headers.pop("Origin", None)
        return client

    def test_teacher_student_lesson_flow_with_origin_and_referer(self):
        for origin in ("http://testserver", "https://yomiyori.example.test"):
            with self.subTest(origin=origin):
                teacher = self.client("teacher", origin)
                student = self.client("student", origin)
                lesson = "/teacher/material-sets/ms001/lesson"
                self.assertEqual(200, teacher.get(lesson).status_code)
                self.assertEqual(303, teacher.post(lesson + "/start", headers={"Origin": origin}, follow_redirects=False).status_code)
                self.assertEqual(200, student.get("/student/roles?set=ms001").status_code)
                response = student.post("/student/question", data={"role_id": "r002", "opinion": "考え"}, headers={"Referer": origin + "/student/question?role_id=r002"}, follow_redirects=False)
                self.assertEqual(303, response.status_code)
                cid = re.search(r"conversation_id=([0-9a-f]{32})", response.headers["location"]).group(1)
                response = student.post("/chat", data={"role_id": "r002", "conversation_id": cid, "message": "質問"}, headers={"Origin": origin, "HX-Request": "true", "Sec-Fetch-Site": "same-origin"})
                self.assertEqual(200, response.status_code)
                response = student.post("/student/reflection", data={"role_id": "r002", "conversation_id": cid, "reflection": "学んだこと"}, headers={"Origin": origin}, follow_redirects=False)
                self.assertEqual(303, response.status_code)
                self.assertEqual("学んだこと", db.get_conversation(cid)["reflection"])
                self.assertEqual(200, teacher.get(f"/teacher/chats/{cid}").status_code)
                self.assertEqual(303, teacher.post(lesson + "/end", headers={"Referer": origin + lesson}, follow_redirects=False).status_code)
                self.assertIsNone(db.get_open_lesson("ms001"))

    def test_teacher_material_edit_and_confirm_with_referer_only(self):
        teacher = self.client("teacher")
        scope = {"material_id": "m001", "range_type": "paragraph", "start": "3", "end": "10"}
        headers = {"Referer": "http://testserver/teacher/candidates"}
        response = teacher.post("/teacher/candidates/r001/edit", data={**scope, "name": "編集した立場", "feature_1": "特徴1", "feature_2": "特徴2", "feature_3": "特徴3"}, headers=headers, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        response = teacher.post("/teacher/candidates/confirm", data={**scope, "selected": ["r001"], "question": "どう考えますか"}, headers=headers, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual("編集した立場", db.read_material_sets()[0]["roles"][0]["name"])

    def test_https_proxy_scheme_is_used_for_csrf_check(self):
        app = ProxyHeadersMiddleware(main.app, trusted_hosts="testclient")
        teacher = self.client("teacher", "http://yomiyori.example.test", app)
        headers = {"X-Forwarded-Proto": "https", "Origin": "https://yomiyori.example.test"}
        response = teacher.post("/teacher/material-sets/ms001/lesson/start", headers=headers, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        # 別オリジンを送ってもForwardedヘッダーでチェックを回避できない。
        headers["Origin"] = "https://attacker.example.test"
        response = teacher.post("/teacher/material-sets/ms001/lesson/end", headers=headers, follow_redirects=False)
        self.assertEqual(403, response.status_code)
        self.assertIsNotNone(db.get_open_lesson("ms001"))

    def oauth_client(self):
        oauth = OAuth()
        remote = oauth.register("google", client_id="test-client-not-a-real-client", authorize_url="https://identity.example.test/authorize", access_token_url="https://identity.example.test/token", client_kwargs={"scope": "openid email profile"})
        context = patch.object(auth, "oauth", oauth)
        context.start()
        self.addCleanup(context.stop)
        client = TestClient(main.app, base_url="https://yomiyori.example.test")
        self.addCleanup(client.close)
        return client, remote

    def begin_oauth(self, client, role="student"):
        response = client.get("/auth/google/login", params={"role": role}, follow_redirects=False)
        self.assertEqual(302, response.status_code)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(["https://yomiyori.example.test/auth/google/callback"], query["redirect_uri"])
        return query["state"][0]

    def test_oauth_state_checked_and_cross_site_callback_then_logout_works(self):
        client, remote = self.oauth_client()
        state = self.begin_oauth(client)
        # 本物のAuthlibのstate検証を通す。トークン交換だけを偽物にし、外部通信しない。
        fetch = AsyncMock(return_value={"userinfo": {"sub": "test-user", "email_verified": True, "name": "Test"}})
        with patch.object(remote, "fetch_access_token", fetch):
            response = client.get("/auth/google/callback", params={"state": state, "code": "test-code-not-usable"}, headers={"Sec-Fetch-Site": "cross-site", "Referer": "https://identity.example.test/"}, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual("/student", response.headers["location"])
        fetch.assert_awaited_once()
        self.assertEqual(200, client.get("/student").status_code)
        self.assertIn('action="/auth/logout" method="post"', client.get("/student").text)
        response = client.post("/auth/logout", headers={"Origin": "https://yomiyori.example.test"}, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual("/student/login", client.get("/student", follow_redirects=False).headers["location"])

    def test_oauth_wrong_missing_or_replayed_state_is_rejected(self):
        client, remote = self.oauth_client()
        state = self.begin_oauth(client)
        fetch = AsyncMock(return_value={"userinfo": {"sub": "test-user", "email_verified": True}})
        with patch.object(remote, "fetch_access_token", fetch):
            for params in ({"code": "test-code"}, {"state": "wrong-state", "code": "test-code"}):
                response = client.get("/auth/google/callback", params=params, follow_redirects=False)
                self.assertEqual("/student/login?error=google_mismatching_state", response.headers["location"])
            fetch.assert_not_awaited()
            response = client.get("/auth/google/callback", params={"state": state, "code": "test-code"}, follow_redirects=False)
            self.assertEqual("/student", response.headers["location"])
            response = client.get("/auth/google/callback", params={"state": state, "code": "test-code"}, follow_redirects=False)
            self.assertEqual("/student/login?error=google_mismatching_state", response.headers["location"])
            fetch.assert_awaited_once()

    def test_candidates_share_budget_with_chat_and_cache_survives_exhaustion(self):
        clock = [0.0]
        limiter = RateLimiter(2, 60, clock=lambda: clock[0])
        teacher = self.client("teacher")
        scope = {"material_id": "m002", "range_type": "paragraph", "start": "1", "end": "1"}
        with patch.object(chat, "global_limiter", limiter):
            # 偽候補の2件とも選択本文p101に収まるようにする。
            self.models.roles[1]["basis"] = self.models.roles[0]["basis"]
            self.assertEqual(200, teacher.get("/teacher/candidates", params=scope).status_code)
            cid = "c" * 32
            self.assertEqual(200, teacher.post("/chat", data={"role_id": "r002", "conversation_id": cid, "message": "質問"}, headers={"Origin": "http://testserver"}).status_code)
            self.assertEqual(2, len(self.models.calls))
            self.assertEqual(200, teacher.get("/teacher/candidates", params=scope).status_code)
            other = {**scope, "end": "2"}
            response = teacher.get("/teacher/candidates", params=other)
            self.assertEqual(429, response.status_code)
            self.assertIn("少し待って", response.text)
            self.assertEqual(2, len(self.models.calls))
            clock[0] = 60.0
            self.assertEqual(200, teacher.get("/teacher/candidates", params=other).status_code)
            self.assertEqual(3, len(self.models.calls))


if __name__ == "__main__":
    unittest.main()
