"""生徒が選んだ教材セットのロールと範囲で対話する（#44）のテスト。backend フォルダで python -m unittest discover -s tests"""

import re
import sys
import unittest
from pathlib import Path

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


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(config.system_instruction)
        return type("Response", (), {"text": '{"reply": "返事", "evidence": []}'})()


class StudentMaterialSetTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.models = FakeModels()
        ai._client = type("Client", (), {"models": self.models})()
        chat.conversation_limiter = RateLimiter(1000, 60)
        chat.global_limiter = RateLimiter(1000, 60)
        self.app = TestClient(main.app)
        login(self.app)

    def tearDown(self):
        ai._client = None

    def talk(self, role_id):
        # 問いかけ → チャット → 1回送る。AIに渡したシステムプロンプトを返す
        response = self.app.post("/student/question", data={"role_id": role_id, "opinion": "考え"}, follow_redirects=False)
        self.assertEqual(303, response.status_code, response.text[:200])
        conversation_id = re.search(r"conversation_id=([0-9a-f]{32})", response.headers["location"]).group(1)
        self.assertEqual(200, self.app.post("/chat", data={"message": "質問", "role_id": role_id, "conversation_id": conversation_id}).status_code)
        return conversation_id, self.models.calls[-1]

    def test_without_material_set_uses_preset_and_full_text(self):
        page = self.app.get("/student/roles").text
        self.assertEqual(6, page.count('name="role_id"'))
        _, prompt = self.talk("r004")
        self.assertIn("[p073]", prompt)

    def test_seed_material_set_limits_roles_and_text(self):
# 生徒が教材セットを選べるのは、授業が開始中のときだけ（#43）
        db.start_lesson("ms001")
        page = self.app.get("/student/roles?set=ms001").text
        self.assertEqual(3, page.count('name="role_id"'))
        # ms001 にないロールは選べない
        self.assertEqual(303, self.app.get("/student/question?role_id=r004", follow_redirects=False).status_code)
        conversation_id, prompt = self.talk("r002")
        self.assertIn("[p001]", prompt)
        self.assertIn("[p008]", prompt)
        self.assertNotIn("[p009]", prompt)
        # 教師が編集した特徴が、AIに渡る
        self.assertIn("エリスを狂気に追いやった責任を豊太郎自身に厳しく問う", prompt)
        self.assertEqual("ms001", db.get_conversation(conversation_id)["material_set_id"])

    def test_db_material_set_uses_saved_range(self):
        self.app.post(
            "/teacher/candidates/confirm",
            data={"material_id": "m001", "range_type": "paragraph", "start": "40", "end": "50", "selected": ["r005"], "question": "問い"},
        )
        material_set_id = db.read_material_sets()[0]["id"]
        db.start_lesson(material_set_id)
        page = self.app.get(f"/student/roles?set={material_set_id}").text
        self.assertEqual(1, page.count('name="role_id"'))
        _, prompt = self.talk("r005")
        self.assertIn("[p040]", prompt)
        self.assertIn("[p050]", prompt)
        self.assertNotIn("[p039]", prompt)
        self.assertNotIn("[p051]", prompt)

    def test_unknown_material_set_goes_back_to_home(self):
        response = self.app.get("/student/roles?set=nope", follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual("/student", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
