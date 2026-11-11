"""予備のモデルでのやり直し（ai.py）のテスト。本物の Gemini は呼ばない。backend フォルダで python -m unittest discover -s tests"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import errors  # noqa: E402

import ai  # noqa: E402


class FakeModels:
    def __init__(self, failures):
        self.failures = failures  # {モデル名: エラーコード}
        self.models = []

    def generate_content(self, model, contents, config):
        self.models.append(model)
        if model in self.failures:
            raise errors.APIError(self.failures[model], {"error": {"message": "test", "status": "TEST"}})
        return type("Response", (), {"text": f"{model}の返事"})()


class FallbackTests(unittest.TestCase):
    def run_with(self, failures, env=None):
        models = FakeModels(failures)
        environ = {k: v for k, v in os.environ.items() if k not in ("GEMINI_MODEL", "GEMINI_FALLBACK_MODEL")}
        environ.update(env or {})
        with patch.object(ai, "_client", type("Client", (), {"models": models})()), patch.dict(os.environ, environ, clear=True):
            try:
                return ai.generate("指示", [{"role": "user", "content": "こんにちは"}]), models.models
            except ai.AIError as e:
                return e, models.models

    def test_main_model_first(self):
        result, models = self.run_with({})
        self.assertEqual("gemini-3.6-flash の返事".replace(" ", ""), result)
        self.assertEqual(["gemini-3.6-flash"], models)

    def test_busy_main_model_falls_back(self):
        for code in (429, 500, 503):
            result, models = self.run_with({"gemini-3.6-flash": code})
            self.assertEqual("gemini-3.5-flash-liteの返事", result)
            self.assertEqual(["gemini-3.6-flash", "gemini-3.5-flash-lite"], models)

    def test_other_errors_do_not_fall_back(self):
        result, models = self.run_with({"gemini-3.6-flash": 400})
        self.assertIsInstance(result, ai.AIError)
        self.assertEqual(["gemini-3.6-flash"], models)

    def test_both_busy(self):
        result, _ = self.run_with({"gemini-3.6-flash": 503, "gemini-3.5-flash-lite": 429})
        self.assertIsInstance(result, ai.AIBusyError)

    def test_fallback_can_be_turned_off(self):
        result, models = self.run_with({"gemini-3.6-flash": 503}, {"GEMINI_FALLBACK_MODEL": ""})
        self.assertIsInstance(result, ai.AIError)
        self.assertEqual(["gemini-3.6-flash"], models)


if __name__ == "__main__":
    unittest.main()
