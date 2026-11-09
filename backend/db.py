import os
from datetime import datetime, timezone
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


# ---- 会話（#20） ----


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
