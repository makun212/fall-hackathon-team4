"""授業の開始・終了（#43）と提出状況（#49）のテスト。backend フォルダで python -m unittest discover -s tests"""

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import ai  # noqa: E402
import chat  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from auth_helper import login  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402

LESSON = "/teacher/material-sets/ms001/lesson"


class FakeModels:
    def generate_content(self, model, contents, config):
        return type("Response", (), {"text": json.dumps({"reply": "返事", "evidence": []})})()


class LessonTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        for context in [
            patch.object(ai, "_client", type("Client", (), {"models": FakeModels()})()),
            patch.object(chat, "conversation_limiter", RateLimiter(1000, 60)),
            patch.object(chat, "global_limiter", RateLimiter(1000, 60)),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.teacher = TestClient(main.app)
        login(self.teacher, role="teacher", user_id="test:teacher", name="山田先生")
        self.student = TestClient(main.app)
        login(self.student, role="student", user_id="test:student", name="佐藤 花子")

    def student_starts(self, opinion="豊太郎は弱い"):
        # 生徒が教材セットを選んで、考えを送り、1回話す。会話 ID を返す
        self.assertEqual(200, self.student.get("/student/roles?set=ms001").status_code)
        response = self.student.post("/student/question", data={"role_id": "r002", "opinion": opinion}, follow_redirects=False)
        conversation_id = re.search(r"conversation_id=([0-9a-f]{32})", response.headers["location"]).group(1)
        self.assertEqual(200, self.chat(conversation_id).status_code)
        return conversation_id

    def chat(self, conversation_id):
        return self.student.post("/chat", data={"message": "質問", "role_id": "r002", "conversation_id": conversation_id})

    def test_before_start_students_cannot_see_the_set(self):
        page = self.teacher.get(LESSON)
        self.assertEqual(200, page.status_code)
        self.assertIn("授業を開始する", page.text)
        self.assertNotIn("book-ms001", self.student.get("/student").text)
        self.assertEqual("/student", self.student.get("/student/roles?set=ms001", follow_redirects=False).headers["location"])

    def test_start_shows_the_set_to_students(self):
        self.assertEqual(303, self.teacher.post(f"{LESSON}/start", follow_redirects=False).status_code)
        self.teacher.post(f"{LESSON}/start")  # 二重に開始しても、授業は1つ
        self.assertIn("授業中", self.teacher.get(LESSON).text)
        self.assertIn("book-ms001", self.student.get("/student").text)
        self.assertEqual({"ms001"}, db.open_lesson_set_ids())

    def test_material_shelf_links_to_lesson_page(self):
        # 教材セット選択・教材セット一覧からは開始せず、授業の画面へ進む（授業の開始・終了は、授業の画面だけで行う）
        for path in ["/teacher", "/teacher/material-sets"]:
            page = self.teacher.get(path)
            self.assertIn(f'href="{LESSON}"', page.text)
            self.assertNotIn(f'action="{LESSON}/start"', page.text)
        self.assertIn("授業前", self.teacher.get("/teacher/material-sets").text)
        # 授業の画面から開始すると、同じ授業の画面に戻る
        response = self.teacher.post(f"{LESSON}/start", follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual(LESSON, response.headers["location"])
        self.assertIn("book-ms001", self.student.get("/student").text)
        # 教材セット一覧の札も「授業中」に変わる
        self.assertIn("授業中", self.teacher.get("/teacher/material-sets").text)

    def test_submissions_are_listed(self):
        self.teacher.post(f"{LESSON}/start")
        conversation_id = self.student_starts()
        page = self.teacher.get(LESSON).text
        self.assertIn("佐藤 花子", page)
        self.assertIn("未提出", page)
        self.assertIn("（提出 0 / 1 人）", page)
        self.student.post("/student/reflection", data={"role_id": "r002", "conversation_id": conversation_id, "reflection": "考えが変わった"})
        page = self.teacher.get(LESSON).text
        self.assertIn("考えが変わった", page)
        self.assertIn("（提出 1 / 1 人）", page)
        self.assertIn("発言 1 回", page)

    def test_after_end_no_new_conversations(self):
        self.teacher.post(f"{LESSON}/start")
        conversation_id = self.student_starts()
        self.assertEqual(303, self.teacher.post(f"{LESSON}/end", follow_redirects=False).status_code)
        self.assertIn("終了しました", self.teacher.get(LESSON).text)
        self.assertNotIn("book-ms001", self.student.get("/student").text)
        # 新しい会話は始められない
        response = self.student.post("/student/question", data={"role_id": "r002", "opinion": "別の考え"}, follow_redirects=False)
        self.assertEqual("/student", response.headers["location"])
        self.assertEqual("/student", self.student.get("/student/chat?role_id=r002", follow_redirects=False).headers["location"])
        self.assertEqual(403, self.chat("e" * 32).status_code)
        self.assertEqual(1, len(db.read_lesson_conversations("ms001")))
        # 始めていた会話は続けられ、感想も出せる
        self.assertEqual(200, self.chat(conversation_id).status_code)
        self.student.post("/student/reflection", data={"role_id": "r002", "conversation_id": conversation_id, "reflection": "終了後の感想"})
        self.assertEqual("終了後の感想", db.get_conversation(conversation_id)["reflection"])

    def test_restart_after_end(self):
        self.teacher.post(f"{LESSON}/start")
        self.teacher.post(f"{LESSON}/end")
        self.teacher.post(f"{LESSON}/start")
        self.assertEqual({"ms001"}, db.open_lesson_set_ids())

    def test_students_cannot_start_or_end(self):
        self.assertEqual(403, self.student.post(f"{LESSON}/start").status_code)
        self.teacher.post(f"{LESSON}/start")
        self.assertEqual(403, self.student.post(f"{LESSON}/end").status_code)
        self.assertEqual({"ms001"}, db.open_lesson_set_ids())

    def test_unknown_set_is_404(self):
        self.assertEqual(404, self.teacher.post("/teacher/material-sets/nope/lesson/start").status_code)

    def test_full_text_mode_does_not_need_a_lesson(self):
        response = self.student.post("/student/question", data={"role_id": "r001", "opinion": "考え"}, follow_redirects=False)
        self.assertIn("/student/chat", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
