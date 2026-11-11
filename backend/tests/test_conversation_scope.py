"""#78: Cookieではなく保存済みの会話文脈が本文範囲を決める。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from google.genai import errors
from sqlalchemy import create_engine, update
from sqlalchemy.pool import StaticPool
import ai
import chat
import db
import main
from auth_helper import TEST_USER_ID, login  # noqa: E402
from rate_limit import RateLimiter


class FakeModels:
    def __init__(self):
        self.prompts = []
        self.error = False

    def generate_content(self, model, contents, config):
        self.prompts.append(config.system_instruction)
        if self.error:
            raise errors.APIError(500, {"error": {"message": "test", "status": "TEST"}})
        return type("Response", (), {"text": json.dumps({"reply": "返事", "evidence": []})})()


class ConversationScopeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        db.use_engine(self.engine)
        self.addCleanup(self.engine.dispose)
        self.models = FakeModels()
        for context in [patch.object(ai, "_client", type("Client", (), {"models": self.models})()),
                        patch.object(chat, "conversation_limiter", RateLimiter(1000, 60)),
                        patch.object(chat, "global_limiter", RateLimiter(1000, 60))]:
            context.start()
            self.addCleanup(context.stop)
        self.app = TestClient(main.app)
        login(self.app)
        self.addCleanup(self.app.close)
        self.conversation_id = "b" * 32

    def choose(self, material_set_id="ms001"):
        # 生徒が教材セットを選べるのは、授業が開始中のときだけ（#43）
        db.start_lesson(material_set_id)
        self.assertEqual(200, self.app.get(f"/student/roles?set={material_set_id}").status_code)

    def start(self):
        self.choose()
        response = self.app.post("/student/question", data={"role_id": "r002", "opinion": "元の考え"}, follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.conversation_id = re.search(r"conversation_id=([0-9a-f]{32})", response.headers["location"]).group(1)

    def send(self, **extra):
        return self.app.post("/chat", data={"message": "質問", "role_id": "r002", "conversation_id": self.conversation_id, **extra})

    def own_legacy_conversation(self):
        # 旧会話は持ち主（student_id）がない。範囲の確認を試すため、ログインしているテスト用のユーザーの会話にする（#33）
        with self.engine.begin() as conn:
            conn.execute(update(db.conversations).where(db.conversations.c.id == self.conversation_id).values(student_id=TEST_USER_ID))

    def assert_rejected_without_api_or_messages(self):
        self.assertEqual(400, self.send().status_code)
        self.assertEqual([], self.models.prompts)
        self.assertEqual([], db.read_messages(self.conversation_id, 20))

    def test_removed_cookie_cannot_expand_saved_range(self):
        self.start()
        self.app.cookies.delete("material_set")
        self.assert_rejected_without_api_or_messages()
        saved = db.get_conversation(self.conversation_id)
        self.assertEqual("ms001", saved["material_set_id"])
        self.assertEqual("元の考え", saved["pre_opinion"])

    def test_other_set_with_same_role_is_rejected(self):
        self.start()
        db.create_material_set("other", "m001", 40, 50, [{"role_id": "r002", "name": "別の立場", "features": ["別の特徴"]}])
        self.choose("other")
        self.assert_rejected_without_api_or_messages()
        self.assertEqual("ms001", db.get_conversation(self.conversation_id)["material_set_id"])

    def test_unchanged_context_uses_saved_range_and_role(self):
        self.start()
        self.assertEqual(200, self.send(material_set_id="other", range_start="40", range_end="50").status_code)
        prompt = self.models.prompts[-1]
        self.assertIn("[p001]", prompt)
        self.assertIn("[p008]", prompt)
        self.assertNotIn("[p009]", prompt)
        self.assertIn("エリスを狂気に追いやった責任を豊太郎自身に厳しく問う", prompt)

    def test_full_text_context_cannot_switch_to_a_set(self):
        db.create_conversation(self.conversation_id, "r002", "考え", student_id=TEST_USER_ID)
        self.choose()
        self.assert_rejected_without_api_or_messages()
        self.assertIsNone(db.get_conversation(self.conversation_id)["material_set_id"])

    def test_unknown_cookie_does_not_fall_back_to_full_text(self):
        self.app.cookies.set(main.MATERIAL_SET_COOKIE, "missing")
        self.assert_rejected_without_api_or_messages()
        self.assertIsNone(db.get_conversation(self.conversation_id))

    def test_missing_saved_set_does_not_fall_back_to_full_text(self):
        db.create_conversation(self.conversation_id, "r002", "考え", "missing", student_id=TEST_USER_ID)
        self.app.cookies.set(main.MATERIAL_SET_COOKIE, "missing")
        self.assert_rejected_without_api_or_messages()

    def test_direct_chat_page_binds_context_when_id_is_issued(self):
        self.choose()
        response = self.app.get("/student/chat?role_id=r002")
        self.assertEqual(200, response.status_code)
        self.conversation_id = re.search(r'name="conversation_id" value="([0-9a-f]{32})"', response.text).group(1)
        self.assertEqual("ms001", db.get_conversation(self.conversation_id)["material_set_id"])
        self.app.cookies.delete("material_set")
        self.assert_rejected_without_api_or_messages()

    def test_direct_post_binds_context_before_first_reply(self):
        self.choose()
        self.assertEqual(200, self.send().status_code)
        self.assertEqual("ms001", db.get_conversation(self.conversation_id)["material_set_id"])
        self.app.cookies.delete("material_set")
        self.assertEqual(400, self.send().status_code)
        self.assertEqual(1, len(self.models.prompts))
        self.assertEqual(2, len(db.read_messages(self.conversation_id, 20)))

    def test_failed_ai_call_does_not_unbind_context_or_save_messages(self):
        self.choose()
        self.models.error = True
        self.assertEqual(502, self.send().status_code)
        self.assertEqual("ms001", db.get_conversation(self.conversation_id)["material_set_id"])
        self.assertEqual([], db.read_messages(self.conversation_id, 20))
        self.app.cookies.delete("material_set")
        self.assertEqual(400, self.send().status_code)
        self.assertEqual(1, len(self.models.prompts))

    def test_chat_page_with_mismatched_cookie_is_rejected(self):
        self.start()
        self.app.cookies.delete("material_set")
        response = self.app.get(f"/student/chat?role_id=r002&conversation_id={self.conversation_id}")
        self.assertEqual(400, response.status_code)

    def test_question_with_invalid_cookie_does_not_create_conversation(self):
        self.app.cookies.set(main.MATERIAL_SET_COOKIE, "missing")
        response = self.app.post("/student/question", data={"role_id": "r002", "opinion": "考え"}, follow_redirects=False)
        self.assertEqual(400, response.status_code)
        self.assertEqual([], db.read_chat_records()["conversations"])

    def test_db_binding_never_changes_existing_role_set_or_opinion(self):
        db.create_conversation(self.conversation_id, "r002", "元の考え", "ms001", student_id=TEST_USER_ID)
        original = db.get_conversation(self.conversation_id)
        self.assertFalse(db.bind_conversation_context(self.conversation_id, "r002", None, TEST_USER_ID))
        self.assertFalse(db.bind_conversation_context(self.conversation_id, "r003", "ms001", TEST_USER_ID))
        self.assertTrue(db.bind_conversation_context(self.conversation_id, "r002", "ms001", TEST_USER_ID))
        self.assertEqual(original, db.get_conversation(self.conversation_id))

    def test_db_binding_conflict_is_rejected_before_api(self):
        with patch.object(db, "bind_conversation_context", return_value=False):
            self.assert_rejected_without_api_or_messages()

    def test_legacy_null_scope_is_not_assumed_to_mean_full_text(self):
        # 旧save_messagesの直接作成はセットID・固定記録がない。既存の発言を消さず拒否する。
        db.save_messages(self.conversation_id, "r002", [{"role": "user", "content": "旧会話の発言"}])
        self.own_legacy_conversation()
        original = db.read_messages(self.conversation_id, 20)
        self.assertFalse(db.has_conversation_context(self.conversation_id))
        self.assertEqual(400, self.send().status_code)
        self.assertEqual([], self.models.prompts)
        self.assertEqual(original, db.read_messages(self.conversation_id, 20))
        self.assertFalse(db.bind_conversation_context(self.conversation_id, "r002", None, TEST_USER_ID))

    def test_legacy_conversation_with_saved_set_can_continue(self):
        db.save_messages(self.conversation_id, "r002", [{"role": "user", "content": "旧会話の発言"}])
        self.own_legacy_conversation()
        with self.engine.begin() as conn:
            conn.execute(update(db.conversations).where(db.conversations.c.id == self.conversation_id).values(material_set_id="ms001"))
        self.assertFalse(db.has_conversation_context(self.conversation_id))
        self.choose()
        self.assertEqual(200, self.send().status_code)
        self.assertIn("[p008]", self.models.prompts[-1])
        self.assertNotIn("[p009]", self.models.prompts[-1])

    def test_old_database_gains_context_table_without_changing_existing_rows(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        try:
            old_tables = [table for table in db.metadata.sorted_tables if table is not db.conversation_contexts]
            db.metadata.create_all(engine, tables=old_tables)
            with engine.begin() as conn:
                conn.execute(db.conversations.insert().values(id=self.conversation_id, role_id="r002", pre_opinion="旧データ", created_at=db.now()))
            db.use_engine(engine)  # アプリと同じcreate_allで不足テーブルだけを追加。
            self.assertEqual("旧データ", db.get_conversation(self.conversation_id)["pre_opinion"])
            self.assertFalse(db.has_conversation_context(self.conversation_id))
            self.assertFalse(db.bind_conversation_context(self.conversation_id, "r002", None, TEST_USER_ID))
            db.create_conversation("c" * 32, "r002", "新しい会話", student_id=TEST_USER_ID)
            self.assertTrue(db.has_conversation_context("c" * 32))
        finally:
            engine.dispose()
            db.use_engine(self.engine)

    def test_simultaneous_first_requests_cannot_bind_two_different_sets(self):
        # ファイルSQLiteで別接続から競合させ、初回IDの取り合いでも文脈が上書きされないことを確認。
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine("sqlite:///" + str(Path(folder) / "scope.db"), connect_args={"timeout": 10})
            db.use_engine(engine)
            barrier = threading.Barrier(2)

            def bind(material_set_id):
                barrier.wait(timeout=10)
                return material_set_id, db.bind_conversation_context(self.conversation_id, "r002", material_set_id, TEST_USER_ID)

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = dict(executor.map(bind, ["ms001", "other"]))
                self.assertEqual(1, sum(outcomes.values()))
                winner = next(key for key, allowed in outcomes.items() if allowed)
                self.assertEqual(winner, db.get_conversation(self.conversation_id)["material_set_id"])
            finally:
                engine.dispose()
                db.use_engine(self.engine)


if __name__ == "__main__":
    unittest.main()
