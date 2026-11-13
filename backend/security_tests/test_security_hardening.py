"""Isolated SQLite + fake AI; never use deployed services or real accounts."""
import base64
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from itsdangerous import TimestampSigner
import ai
import auth
import chat
import db
import main
from auth_helper import TEST_USER_ID, login
from rate_limit import RateLimiter
from test_security_acceptance import FakeModels


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        previous = db._engine
        self.addCleanup(setattr, db, "_engine", previous)
        db.use_engine(engine)
        self.engine = engine
        self.models = FakeModels()
        for context in (
            patch.object(ai, "_client", type("Client", (), {"models": self.models})()),
            patch.object(chat, "conversation_limiter", RateLimiter(1000, 60)),
            patch.object(chat, "global_limiter", RateLimiter(1000, 60)),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.app = TestClient(main.app)
        self.addCleanup(self.app.close)
        login(self.app, role="student")
        self.cid = "a" * 32
        self.victim = "b" * 32
        db.upsert_user("test:victim", "", "Victim")
        db.create_conversation(self.victim, "r002", "PRIVATE_SENTINEL", student_id="test:victim")
        db.save_messages(self.victim, "r002", [{"role": "user", "content": "PRIVATE_MESSAGE"}])

    def send(self, payload="question", cid=None, **kwargs):
        return self.app.post("/chat", data={"message": payload, "role_id": "r002", "conversation_id": cid or self.cid}, **kwargs)

    def assert_victim_unchanged(self):
        victim = db.get_conversation(self.victim)
        self.assertEqual("PRIVATE_SENTINEL", victim["pre_opinion"])
        self.assertIsNone(victim["reflection"])
        self.assertEqual([{"role": "user", "content": "PRIVATE_MESSAGE"}], db.read_messages(self.victim, 100))
        self.assertEqual("student", db.get_user(TEST_USER_ID)["role"])

    def test_sql_payloads_are_stored_as_values_not_commands(self):
        captured = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            captured.append((statement, parameters))
        event.listen(self.engine, "before_cursor_execute", capture)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", capture)
        payloads = ["' OR '1'='1' --", "'; DROP TABLE users; --", "'; DELETE FROM conversations; --",
                    "'; UPDATE users SET role='teacher'; --", "' UNION SELECT id FROM users --"]
        for i, payload in enumerate(payloads):
            with self.subTest(payload=payload):
                cid = f"{i + 20:032x}"
                response = self.send(payload, cid=cid)
                self.assertEqual(200, response.status_code)
                self.assertEqual(payload, db.read_messages(cid, 100)[0]["content"])
                self.assertNotIn("PRIVATE_SENTINEL", response.text)
                self.assertNotIn("PRIVATE_MESSAGE", response.text)
                self.assert_victim_unchanged()
        self.assertTrue(any(payloads[0] in str(params) for _, params in captured))
        for statement, _ in captured:
            for payload in payloads:
                self.assertNotIn(payload, statement)

    def test_sql_lookup_and_update_do_not_match_other_rows(self):
        payload = "' OR 1=1 --"
        self.assertIsNone(db.get_conversation(payload))
        self.assertIsNone(db.get_user(payload))
        self.assertEqual([], db.read_messages(payload, 100))
        self.assertFalse(db.save_reflection(payload, "ATTACK"))
        db.set_user_role(payload, "teacher")
        self.assertEqual([], db.read_lesson_conversations(payload))
        self.assertIsNone(db.get_candidate_set(payload))
        self.assertIsNone(db.find_candidate_set(payload, 1, 3))
        self.assertEqual({}, db.read_role_edits(payload))
        db.save_role_edit(payload, payload, {"name": payload, "features": [payload]})
        self.assertEqual(payload, db.read_role_edits(payload)[payload]["name"])
        self.assert_victim_unchanged()

    def test_sql_injection_in_http_identifier_cannot_read_or_modify(self):
        payload = "' OR 1=1 --"
        self.assertEqual(400, self.send(cid=payload).status_code)
        page = self.app.get("/student/reflection", params={"role_id": "r002", "conversation_id": payload})
        self.assertNotIn("PRIVATE_SENTINEL", page.text)
        self.assertEqual([], self.models.calls)
        self.assert_victim_unchanged()

    def test_sql_payload_in_reflection_is_literal_and_only_updates_owner(self):
        payload = "'; DELETE FROM conversations; --"
        db.create_conversation(self.cid, "r002", "opinion", student_id=TEST_USER_ID)
        response = self.app.post("/student/reflection", data={"role_id": "r002", "conversation_id": self.cid, "reflection": payload}, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual(payload, db.get_conversation(self.cid)["reflection"])
        self.assert_victim_unchanged()

    def test_cross_origin_post_is_rejected_before_ai_or_db(self):
        for origin in ("https://attacker.test", "http://testserver.attacker.test", "null", "https://testserver", "http://testserver:8080"):
            with self.subTest(origin=origin):
                self.assertEqual(403, self.send(headers={"Origin": origin, "Referer": "http://testserver/"}).status_code)
        self.assertEqual([], self.models.calls)
        self.assertIsNone(db.get_conversation(self.cid))

    def test_missing_origin_and_referer_is_rejected(self):
        self.app.headers.pop("Origin", None)
        self.assertEqual(403, self.send().status_code)
        self.assertIsNone(db.get_conversation(self.cid))
        self.assertEqual([], self.models.calls)

    def test_referer_fallback_checks_exact_origin(self):
        self.app.headers.pop("Origin", None)
        self.assertEqual(403, self.send(headers={"Referer": "http://testserver.attacker.test/"}).status_code)
        self.assertEqual(200, self.send(headers={"Referer": "http://testserver/student/chat"}).status_code)

    def test_csrf_cannot_start_teacher_lesson_or_save_reflection(self):
        db.create_conversation(self.cid, "r002", "opinion", student_id=TEST_USER_ID)
        response = self.app.post("/student/reflection", data={"role_id": "r002", "conversation_id": self.cid, "reflection": "FORGED"}, headers={"Origin": "https://evil.test"})
        self.assertEqual(403, response.status_code)
        self.assertIsNone(db.get_conversation(self.cid)["reflection"])
        login(self.app, role="teacher")
        response = self.app.post("/teacher/material-sets/ms001/lesson/start", headers={"Origin": "https://evil.test"}, follow_redirects=False)
        self.assertEqual(403, response.status_code)
        self.assertIsNone(db.get_open_lesson("ms001"))
        response = self.app.post("/teacher/material-sets/ms001/lesson/start", follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertIsNotNone(db.get_open_lesson("ms001"))

    def test_cross_site_metadata_cannot_override_origin_check(self):
        self.assertEqual(403, self.send(headers={"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"}).status_code)
        self.assertIsNone(db.get_conversation(self.cid))

    def test_malformed_origins_are_denied_and_default_port_is_allowed(self):
        for origin in ("", "file:///etc/passwd", "http://[", "http://testserver:invalid", "http://user@testserver"):
            with self.subTest(origin=origin):
                self.assertEqual(403, self.send(headers={"Origin": origin}).status_code)
        self.assertEqual([], self.models.calls)
        self.assertEqual(200, self.send(headers={"Origin": "http://testserver:80"}).status_code)

    def test_session_cookie_has_browser_protection_flags(self):
        # 参照だけではCookieを再発行しないため、テスト用ログインで発行させる。
        with patch.object(auth, "DEV_LOGIN", True):
            response = self.app.get("/auth/dev/login?role=student", follow_redirects=False)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn(f"max-age={auth.SESSION_MAX_AGE}", cookie)
        if auth.SESSION_HTTPS_ONLY:
            self.assertIn("secure", cookie)

    def test_tampered_and_expired_session_cannot_impersonate_teacher(self):
        db.upsert_user("test:teacher", "", "Teacher")
        db.set_user_role("test:teacher", "teacher")
        valid = auth.session_cookie({"user_id": TEST_USER_ID})
        _, timestamp, signature = valid.rsplit(".", 2)
        forged_payload = base64.b64encode(json.dumps({"user_id": "test:teacher"}).encode()).decode()
        with patch("itsdangerous.timed.time.time", return_value=1):
            expired = auth.session_cookie({"user_id": "test:teacher"})
        wrong_key = TimestampSigner("wrong-test-key").sign(forged_payload.encode()).decode()
        for cookie in (f"{forged_payload}.{timestamp}.{signature}", wrong_key, expired):
            with self.subTest(cookie=cookie), TestClient(main.app) as attacker:
                attacker.cookies.set(auth.SESSION_COOKIE, cookie)
                response = attacker.get("/teacher/chats", follow_redirects=False)
                self.assertEqual(303, response.status_code)
                self.assertEqual("/teacher/login", response.headers["location"])
                self.assertEqual(401, attacker.post("/chat", data={"message": "forged"}).status_code)
        self.assert_victim_unchanged()

    def test_role_is_read_from_database_not_cookie(self):
        cookie = auth.session_cookie({"user_id": TEST_USER_ID, "role": "teacher"})
        self.app.cookies.clear()
        self.app.cookies.set(auth.SESSION_COOKIE, cookie)
        self.assertEqual(403, self.app.post("/teacher/material-sets/ms001/lesson/start").status_code)
        self.assertIsNone(db.get_open_lesson("ms001"))

    def test_logout_requires_same_origin_post(self):
        # ブラウザ同様にホストを限定する（テスト用のdomain無しCookieを残さない）。
        self.app.cookies.clear()
        self.app.cookies.set(auth.SESSION_COOKIE, auth.session_cookie({"user_id": TEST_USER_ID}), domain="testserver.local", path="/")
        self.assertEqual(405, self.app.get("/auth/logout", follow_redirects=False).status_code)
        self.assertEqual(403, self.app.post("/auth/logout", headers={"Origin": "https://evil.test"}).status_code)
        self.assertEqual(200, self.app.get("/student").status_code)
        page = self.app.get("/student")
        self.assertIn('action="/auth/logout" method="post"', page.text)
        self.assertEqual(303, self.app.post("/auth/logout", follow_redirects=False).status_code)
        self.assertEqual("/student/login", self.app.get("/student", follow_redirects=False).headers["location"])

    def test_stored_xss_in_teacher_views_is_escaped(self):
        payload = '<img src=x onerror="alert(1)">'
        db.upsert_user(TEST_USER_ID, "", payload)
        db.create_conversation(self.cid, "r002", payload, student_id=TEST_USER_ID)
        db.save_messages(self.cid, "r002", [{"role": "user", "content": payload}])
        db.save_reflection(self.cid, payload)
        login(self.app, role="teacher", user_id="test:teacher")
        for path in ("/teacher/chats", f"/teacher/chats/{self.cid}", "/teacher/submissions"):
            with self.subTest(path=path):
                page = self.app.get(path)
                self.assertEqual(200, page.status_code)
                self.assertNotIn(payload, page.text)
                self.assertIn("&lt;img", page.text)

    def test_global_rate_limit_cannot_be_bypassed_by_rotating_conversation_ids(self):
        with patch.object(chat, "global_limiter", RateLimiter(2, 60)):
            statuses = [self.send(cid=f"{i + 40:032x}").status_code for i in range(3)]
        self.assertEqual([200, 200, 429], statuses)
        self.assertEqual(2, len(self.models.calls))
        self.assertIsNone(db.get_conversation(f"{42:032x}"))

    def test_candidate_generation_uses_global_budget_but_cached_results_do_not(self):
        with patch.object(chat, "global_limiter", RateLimiter(1, 60)), patch.object(main.candidate_service, "generate", return_value=[]) as generate:
            main.generated_candidates("m002", "paragraph", "1", "1")
            main.generated_candidates("m002", "paragraph", "1", "1")
            self.assertEqual(1, generate.call_count)
            with self.assertRaises(ai.AIBusyError):
                main.generated_candidates("m002", "paragraph", "2", "2")
            self.assertEqual(1, generate.call_count)
            self.assertIsNone(db.find_candidate_set("m002", 2, 2))


if __name__ == "__main__":
    unittest.main()
