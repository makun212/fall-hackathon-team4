"""教師用「感想の提出状況」のテスト。backend フォルダで python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import db  # noqa: E402
import main  # noqa: E402
from auth_helper import login  # noqa: E402

CONVERSATION_ID = "b" * 32


class TeacherSubmissionTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.app = TestClient(main.app)
        login(self.app, role="teacher")

    def test_only_teachers_can_view_submissions(self):
        anonymous = TestClient(main.app)
        response = anonymous.get("/teacher/submissions", follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual("/teacher/login", response.headers["location"])
        login(anonymous, role="student", user_id="test:student")
        response = anonymous.get("/teacher/submissions", follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertIn("/teacher/login", response.headers["location"])

    def test_seed_reflection_is_submitted(self):
        status = main.get_submission_status()
        ids = [s["conversation_id"] for s in status["submitted"]]
        self.assertIn("cv001", ids)
        self.assertEqual("10:38", status["submitted"][0]["submitted_at"])
        self.assertTrue(status["submitted"][0]["reflection"])
        response = self.app.get("/teacher/submissions")
        self.assertEqual(200, response.status_code)
        self.assertIn('aria-current="page"', response.text)
        self.assertIn("提出が早い順", response.text)

    def test_db_reflection_moves_to_submitted(self):
        db.create_conversation(CONVERSATION_ID, "r003", "考え")
        status = main.get_submission_status()
        self.assertIn(CONVERSATION_ID, [s["conversation_id"] for s in status["not_submitted"]])
        db.save_reflection(CONVERSATION_ID, "感想")
        status = main.get_submission_status()
        mine = next(s for s in status["submitted"] if s["conversation_id"] == CONVERSATION_ID)
        self.assertRegex(mine["submitted_at"], r"^\d\d:\d\d$")
        self.assertEqual("考え", mine["pre_opinion"])
        self.assertEqual("感想", mine["reflection"])

    def test_reflection_text_is_escaped_in_dialog(self):
        db.create_conversation(CONVERSATION_ID, "r003", "<b>前</b>")
        db.save_reflection(CONVERSATION_ID, "1行目\n<script>x</script>")
        html = self.app.get("/teacher/submissions").text
        self.assertIn('<dialog id="sb-dialog"', html)
        self.assertIn('data-pre="&lt;b&gt;前&lt;/b&gt;"', html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        self.assertIn(f'data-chat-url="/teacher/chats/{CONVERSATION_ID}"', html)
        self.assertEqual(200, self.app.get(f"/teacher/chats/{CONVERSATION_ID}").status_code)

    def render(self, submitted, not_submitted):
        status = {"material_set_title": "舞姫", "submitted": submitted, "not_submitted": not_submitted}
        with mock.patch.object(main, "get_submission_status", return_value=status):
            return self.app.get("/teacher/submissions").text

    def test_empty_states(self):
        self.assertIn("この教材では、まだ生徒の記録がありません。", self.render([], []))
        all_done = self.render([{"conversation_id": "x", "student_name": "上野", "submitted_at": None}], [])
        self.assertIn("全員が提出しました。", all_done)
        self.assertNotIn("提出が早い順", all_done)
        none_done = self.render([], [{"conversation_id": "x", "student_name": "上野"}])
        self.assertIn("まだ提出した人はいません。", none_done)
        self.assertIn('aria-label="0人が提出、1人が未提出"', none_done)
        self.assertNotIn("<dialog", none_done)


if __name__ == "__main__":
    unittest.main()
