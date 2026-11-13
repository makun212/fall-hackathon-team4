"""教師画面のリンクをたどって教材セットの選択が維持されることを確認する。"""

import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import db
import main
from auth_helper import login


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


class TeacherNavigationTests(unittest.TestCase):
    def setUp(self):
        db.use_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
        self.client = TestClient(main.app)
        login(self.client)
        self.conversation_id = "c" * 32

    def follow(self, response, path, material_set_id=None, only_detected=None):
        hrefs = [href for href in Links(response.text).hrefs if urlsplit(href).path == path]
        if only_detected is not None:
            hrefs = [href for href in hrefs if parse_qs(urlsplit(href).query).get("only_detected") == only_detected]
        self.assertTrue(hrefs, f"Missing link to {path}")
        for href in hrefs:
            query = parse_qs(urlsplit(href).query)
            self.assertEqual([material_set_id] if material_set_id else None, query.get("set"))
            if material_set_id:
                self.assertEqual({"set", "only_detected"} if "only_detected" in query else {"set"}, set(query))
        result = self.client.get(hrefs[0])
        self.assertEqual(200, result.status_code)
        return result

    def assert_selected_chats(self, response):
        self.assertEqual(1, response.context["total_count"])
        self.assertEqual([self.conversation_id], [c["conversation_id"] for c in response.context["conversations"]])

    def exercise_selected_navigation(self, material_set_id):
        # 教材の表示情報だけを差し替え、会話の取得・絞り込みとテンプレートは実処理を使う。
        material_set = dict(main.get_confirmed_material_sets()[0], id=material_set_id, title="羅生門")
        db.start_lesson(material_set_id, "test:user")
        db.create_conversation(self.conversation_id, "r003", "考え", material_set_id=material_set_id)
        db.create_conversation("d" * 32, "r003", "別教材", material_set_id="ms001")
        with patch.object(main, "get_confirmed_material_sets", return_value=[material_set]):
            lesson = self.client.get(f"/teacher/material-sets/{quote(material_set_id, safe='')}/lesson")
            self.assertEqual(200, lesson.status_code)
            chats = self.follow(lesson, "/teacher/chats", material_set_id)
            self.assert_selected_chats(chats)
            submissions = self.follow(chats, "/teacher/submissions", material_set_id)
            chats = self.follow(submissions, "/teacher/chats", material_set_id)
            self.assert_selected_chats(chats)
            detected = self.follow(chats, "/teacher/chats", material_set_id, ["1"])
            self.assertEqual(1, detected.context["total_count"])
            self.assertEqual([], detected.context["conversations"])
            # 「すべて」のリンクを明示的に選ぶ。
            all_url = next(h for h in Links(detected.text).hrefs
                           if urlsplit(h).path == "/teacher/chats" and "only_detected" not in parse_qs(urlsplit(h).query))
            chats = self.client.get(all_url)
            self.assert_selected_chats(chats)
            detail = self.follow(chats, f"/teacher/chats/{self.conversation_id}", material_set_id)
            chats = self.follow(detail, "/teacher/chats", material_set_id)
            self.assert_selected_chats(chats)

    def test_lesson_submissions_and_chat_navigation_preserves_selection(self):
        self.exercise_selected_navigation("rashomon")

    def test_reserved_characters_in_material_id_are_encoded(self):
        self.exercise_selected_navigation("rashomon&part=1 +?#")

    def test_navigation_without_material_keeps_all_conversations(self):
        db.create_conversation(self.conversation_id, "r003", "考え", material_set_id="rashomon")
        chats = self.client.get("/teacher/chats")
        expected_count = len(main.get_conversation_summaries())
        self.assertGreater(expected_count, 1)
        submissions = self.follow(chats, "/teacher/submissions")
        chats = self.follow(submissions, "/teacher/chats")
        self.assertEqual(expected_count, chats.context["total_count"])
        detail = self.follow(chats, f"/teacher/chats/{self.conversation_id}")
        chats = self.follow(detail, "/teacher/chats")
        self.assertEqual(expected_count, chats.context["total_count"])
