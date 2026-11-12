"""立場の候補を AI で作る（#16）のテスト。本物の Gemini は呼ばず、偽物の client を使う。backend フォルダで python -m unittest discover -s tests"""

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from google.genai import errors  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import ai  # noqa: E402
import candidates  # noqa: E402
import chat  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from auth_helper import login  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402

# 羅生門（m002）はダミーの本文。p101「【ダミー】羅生門の冒頭の段落。」、p102「【ダミー】下人と老婆のやり取りに関する段落。」
RANGE = {"material_id": "m002", "range_type": "all", "start": "", "end": ""}


def role(name, quote="羅生門の冒頭の段落", paragraph_id="p101", **extra):
    return {
        "name": name,
        "summary": f"{name}の説明",
        "features": ["特徴1", "特徴2", "特徴3"],
        "basis": [{"paragraph_id": paragraph_id, "quote": quote, "reading": "この立場の読み"}],
        "prompt_hint": "穏やかに話す",
        **extra,
    }


class FakeModels:
    def __init__(self):
        self.calls = []
        self.roles = [role("下人の生きる必要を見る読者"), role("老婆の言い分を問う読者", "下人と老婆のやり取り", "p102")]
        self.error_code = None

    def generate_content(self, model, contents, config):
        self.calls.append(config)
        if self.error_code:
            raise errors.APIError(self.error_code, {"error": {"message": "test", "status": "TEST"}})
        if config.response_json_schema is candidates.CANDIDATES_SCHEMA:
            text = json.dumps({"roles": self.roles}, ensure_ascii=False)
        else:
            text = json.dumps({"reply": "返事", "evidence": []}, ensure_ascii=False)
        return type("Response", (), {"text": text})()


class CandidateTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.models = FakeModels()
        for context in [
            patch.object(ai, "_client", type("Client", (), {"models": self.models})()),
            patch.object(chat, "conversation_limiter", RateLimiter(1000, 60)),
            patch.object(chat, "global_limiter", RateLimiter(1000, 60)),
        ]:
            context.start()
            self.addCleanup(context.stop)
        self.app = TestClient(main.app)
        login(self.app)

    def open_candidates(self):
        return self.app.get("/teacher/candidates", params=RANGE)

    def test_candidates_are_generated_once_and_reused(self):
        page = self.open_candidates()
        self.assertEqual(200, page.status_code)
        self.assertIn("下人の生きる必要を見る読者", page.text)
        self.assertIn("老婆の言い分を問う読者", page.text)
        self.open_candidates()
        self.assertEqual(1, len(self.models.calls))
        # 候補づくりは待ち時間を長くする
        self.assertEqual(candidates.GENERATE_TIMEOUT_SECONDS * 1000, self.models.calls[0].http_options.timeout)

    def test_unverifiable_or_too_long_roles_are_dropped(self):
        self.models.roles = [
            role("本文にない引用の立場", quote="作り話の引用"),
            role("名前が長すぎる立場" * 3),
            role("特徴が足りない立場", features=["1", "2"]),
            role("下人の生きる必要を見る読者"),
            role("老婆の言い分を問う読者", "下人と老婆のやり取り", "p102"),
            role("下人の生きる必要を見る読者"),  # 同じ名前は1つにする
        ]
        roles = candidates.generate(main.get_material_text("m002"), main.get_paragraph_labels())
        self.assertEqual(["下人の生きる必要を見る読者", "老婆の言い分を問う読者"], [r["name"] for r in roles])
        self.assertIn("引用：「羅生門の冒頭の段落」", roles[0]["basis"][0]["note"])

    def test_too_few_roles_shows_error_on_material_page(self):
        self.models.roles = [role("一つだけの立場")]
        page = self.open_candidates()
        self.assertEqual(502, page.status_code)
        self.assertIn("立場の候補を作れませんでした", page.text)
        self.assertIsNone(db.find_candidate_set("m002", 1, 2))

    def test_busy_shows_wait_message(self):
        self.models.error_code = 429
        page = self.open_candidates()
        self.assertEqual(429, page.status_code)
        self.assertIn("混み合っています", page.text)

    def test_maihime_uses_presets_without_ai(self):
        page = self.app.get("/teacher/candidates", params={"material_id": "m001", "range_type": "all"})
        self.assertEqual(200, page.status_code)
        self.assertIn("豊太郎の葛藤を重く見る読者", page.text)
        self.assertEqual([], self.models.calls)

    def test_generated_role_can_be_edited_confirmed_and_used_in_chat(self):
        self.open_candidates()
        first = main.get_role_candidates("m002")[0]["candidate_id"]
        self.assertRegex(first, candidates.CANDIDATE_ID_PATTERN)
        data = {**RANGE, "name": "編集した立場", "feature_1": "a", "feature_2": "b", "feature_3": "c"}
        self.assertEqual(303, self.app.post(f"/teacher/candidates/{first}/edit", data=data, follow_redirects=False).status_code)
        self.app.post("/teacher/candidates/confirm", data={**RANGE, "selected": [first], "question": "問い"})
        material_set_id = db.read_material_sets()[0]["id"]
        self.assertEqual("編集した立場", db.read_material_sets()[0]["roles"][0]["name"])

        # 生徒が、その教材セットのロールで話す。根拠と口調は、AI が作った元のロールから取る
        db.start_lesson(material_set_id)
        self.app.get(f"/student/roles?set={material_set_id}")
        response = self.app.post("/student/question", data={"role_id": first, "opinion": "考え"}, follow_redirects=False)
        conversation_id = re.search(r"conversation_id=([0-9a-f]{32})", response.headers["location"]).group(1)
        self.assertEqual(200, self.app.post("/chat", data={"message": "質問", "role_id": first, "conversation_id": conversation_id}).status_code)
        system_prompt = self.models.calls[-1].system_instruction
        self.assertIn("編集した立場", system_prompt)
        self.assertIn("羅生門の冒頭の段落", system_prompt)

    def test_unknown_generated_id_is_not_a_role(self):
        self.assertIsNone(main.find_generated_role("g" + "0" * 32 + "-1"))
        self.assertIsNone(main.find_generated_role("r001"))


if __name__ == "__main__":
    unittest.main()
