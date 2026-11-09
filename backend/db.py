import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    insert,
    select,
    update,
)

# DB（#12）。テーブルの定義と、DBへの接続はこのファイルにまとめる
# 手元では backend/app.db（SQLite）を使う。本番では .env の DATABASE_URL に Postgres などの接続先を書く
load_dotenv(Path(__file__).resolve().parent / ".env")
DEFAULT_DATABASE_URL = "sqlite:///" + str(Path(__file__).resolve().parent / "app.db")

metadata = MetaData()

# 会話1つ分。生徒がチャット画面を開くたびに1件できる
conversations = Table(
    "conversations",
    metadata,
    Column("id", String(32), primary_key=True),  # chat.new_conversation_id() の値
    Column("role_id", String(64), nullable=False),
    Column("student_id", String(64)),  # Googleログイン（#33）ができたら入れる
    Column("material_set_id", String(64)),  # 教材セットが生徒の画面まで届いたら入れる
    Column("pre_opinion", Text),  # 対話前の考え（#46）
    Column("reflection", Text),  # 対話後の感想（#46）。一度保存したら書き換えない
    Column("reflected_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# 会話の中の発言。role は user（生徒）か assistant（AI）
chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conversation_id", String(32), ForeignKey("conversations.id"), nullable=False, index=True),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

_engine = None


def now():
    return datetime.now(timezone.utc)


def get_engine():
    # 最初に呼ばれたときに1回だけ作り、テーブルがなければ作る
    global _engine
    if _engine is None:
        url = os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
        _engine = create_engine(url)
        if url.startswith("sqlite"):
            # SQLite は、外部キーの確認を毎回オンにする必要がある
            event.listen(_engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        metadata.create_all(_engine)
    return _engine


def use_engine(engine):
    # テスト用：別のDB（メモリ上の SQLite など）に切り替える
    global _engine
    _engine = engine
    metadata.create_all(_engine)


# ---- 会話（#20）と、対話前の考え・感想（#46） ----


def create_conversation(conversation_id: str, role_id: str, pre_opinion: str):
    # 対話前の考えを送ったときに、会話を1件作る
    with get_engine().begin() as conn:
        conn.execute(
            insert(conversations).values(id=conversation_id, role_id=role_id, pre_opinion=pre_opinion, created_at=now())
        )


def get_conversation(conversation_id: str):
    # 会話1件を辞書で返す。なければ None
    with get_engine().connect() as conn:
        row = conn.execute(select(conversations).where(conversations.c.id == conversation_id)).first()
    return dict(row._mapping) if row else None


def save_reflection(conversation_id: str, reflection: str) -> bool:
    # 感想を保存する。まだ感想がない会話だけを更新し、保存できたら True（書き換えはしない）
    with get_engine().begin() as conn:
        result = conn.execute(
            update(conversations)
            .where(conversations.c.id == conversation_id, conversations.c.reflection.is_(None))
            .values(reflection=reflection, reflected_at=now())
        )
    return result.rowcount == 1


def read_messages(conversation_id: str, limit: int):
    # 会話の発言を、古い順に最大 limit 件返す（新しいほうから limit 件）
    query = (
        select(chat_messages.c.role, chat_messages.c.content)
        .where(chat_messages.c.conversation_id == conversation_id)
        .order_by(chat_messages.c.id.desc())
        .limit(limit)
    )
    with get_engine().connect() as conn:
        rows = conn.execute(query).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def save_messages(conversation_id: str, role_id: str, messages):
    # 発言をまとめて保存する。会話がまだなければ作る。1つのトランザクションで行う
    created = now()
    with get_engine().begin() as conn:
        exists = conn.execute(select(conversations.c.id).where(conversations.c.id == conversation_id)).first()
        if exists is None:
            conn.execute(insert(conversations).values(id=conversation_id, role_id=role_id, created_at=created))
        conn.execute(
            insert(chat_messages),
            [{"conversation_id": conversation_id, "role": m["role"], "content": m["content"], "created_at": created} for m in messages],
        )


# ---- 教師用：チャット一覧（#20） ----

JST = timezone(timedelta(hours=9))


def to_jst_text(value):
    # DBの日時を、ダミーデータと同じ「2026-11-04T10:15:00+09:00」の形にする
    # SQLite はタイムゾーンを保存しないので、タイムゾーンがなければ UTC として扱う
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST).isoformat(timespec="seconds")


def read_chat_records():
    # 教師のチャット一覧に使う、DBの会話と発言。main.py の read_chat_records() と同じキー名で返す
    with get_engine().connect() as conn:
        conversation_rows = conn.execute(select(conversations).order_by(conversations.c.created_at)).all()
        message_rows = conn.execute(select(chat_messages).order_by(chat_messages.c.id)).all()
    return {
        "conversations": [
            {
                "id": c.id,
                "material_set_id": c.material_set_id,
                "student_id": c.student_id,
                "role_id": c.role_id,
                "created_at": to_jst_text(c.created_at),
            }
            for c in conversation_rows
        ],
        "messages": [
            {
                "id": f"db{m.id}",
                "conversation_id": m.conversation_id,
                "text": m.content,
                "sent_at": to_jst_text(m.created_at),
            }
            for m in message_rows
        ],
    }
