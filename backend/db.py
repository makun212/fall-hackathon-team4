import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Text,
    create_engine,
    event,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

# DB（#12）。テーブルの定義と、DBへの接続はこのファイルにまとめる
# 手元では backend/app.db（SQLite）を使う。本番では .env の DATABASE_URL に Postgres などの接続先を書く
load_dotenv(Path(__file__).resolve().parent / ".env")
DEFAULT_DATABASE_URL = "sqlite:///" + str(Path(__file__).resolve().parent / "app.db")

metadata = MetaData()

# ログインした人（#33）。id は "google:<GoogleのユーザーID>"。role は student か teacher
# 教師になるには、教師コードを入力する（auth.py）。生徒が自分で role を変えることはできない
users = Table(
    "users",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("email", Text),
    Column("name", Text),
    Column("role", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

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

# 新しい経路で本文範囲を固定したことの記録。旧会話のNULLが「全文」か保存漏れかは推測しない。
# create_allで追加できる独立テーブルにし、既存テーブル・行の書き換えは不要にする。
conversation_contexts = Table(
    "conversation_contexts",
    metadata,
    Column("conversation_id", String(32), ForeignKey("conversations.id"), primary_key=True),
)

# 会話の中の発言。role は user（生徒）か assistant（AI）
chat_messages = Table(
    "chat_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conversation_id", String(32), ForeignKey("conversations.id"), nullable=False, index=True),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("evidence", JSON),  # AIの返事の根拠 [{quote_text, location}]（#45）
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# 生徒の発言で見つかった乱暴な言葉の検知（#21）。moderation.detect() の結果をそのまま保存する
# 送信を止めたり、発言を書き換えたりはしない。教師が確認する順番の目安にだけ使う
detections = Table(
    "detections",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("message_id", Integer, ForeignKey("chat_messages.id"), nullable=False, index=True),
    Column("type", String(32), nullable=False),
    Column("level", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# AI が作った立場の候補（#16）。教材と範囲（第何段落から何段落まで）ごとに1回だけ作り、使い回す
# roles は [{name, summary, features, basis, prompt_hint}]。候補の ID は candidates.candidate_id() で作る
candidate_sets = Table(
    "candidate_sets",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("material_id", String(64), nullable=False),
    Column("range_start", Integer, nullable=False),
    Column("range_end", Integer, nullable=False),
    Column("roles", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("material_id", "range_start", "range_end"),
)

# 教師がロールの編集画面で保存した内容（#13）。name が None なら「元に戻した」
role_edits = Table(
    "role_edits",
    metadata,
    Column("material_id", String(64), primary_key=True),
    Column("candidate_id", String(64), primary_key=True),
    Column("name", Text),
    Column("features", JSON),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# 教師が確定した教材セット（#13）。範囲は、第何段落から第何段落か（1から数える）
material_sets = Table(
    "material_sets",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("teacher_id", String(64)),  # Googleログイン（#33）ができたら入れる
    Column("material_id", String(64), nullable=False),
    Column("range_start", Integer, nullable=False),
    Column("range_end", Integer, nullable=False),
    Column("question", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# 教材セットで使うロール。確定したときの名前と特徴を、そのまま残す（あとで候補が変わっても、教材セットは変わらない）
material_set_roles = Table(
    "material_set_roles",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("material_set_id", String(32), ForeignKey("material_sets.id"), nullable=False, index=True),
    Column("position", Integer, nullable=False),
    Column("role_id", String(64), nullable=False),  # 元のロール（プリセットの r001 や、候補の rc001-1 など）
    Column("name", Text, nullable=False),
    Column("summary", Text),
    Column("features", JSON, nullable=False),
    Column("image", Text),
    Column("edited", Integer, nullable=False),
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


def create_conversation(
    conversation_id: str, role_id: str, pre_opinion: str, material_set_id: str | None = None, student_id: str | None = None
):
    # 対話前の考えを送ったときに、会話を1件作る。student_id は会話の持ち主（ログインした人の id）
    with get_engine().begin() as conn:
        conn.execute(
            insert(conversations).values(
                id=conversation_id,
                role_id=role_id,
                pre_opinion=pre_opinion,
                material_set_id=material_set_id,
                student_id=student_id,
                created_at=now(),
            )
        )
        conn.execute(insert(conversation_contexts).values(conversation_id=conversation_id))


def get_conversation(conversation_id: str):
    # 会話1件を辞書で返す。なければ None
    with get_engine().connect() as conn:
        row = conn.execute(select(conversations).where(conversations.c.id == conversation_id)).first()
    return dict(row._mapping) if row else None


def has_conversation_context(conversation_id: str) -> bool:
    with get_engine().connect() as conn:
        return conn.execute(
            select(conversation_contexts.c.conversation_id).where(conversation_contexts.c.conversation_id == conversation_id)
        ).first() is not None


def bind_conversation_context(
    conversation_id: str, role_id: str, material_set_id: str | None, student_id: str | None = None
) -> bool:
    # 初回の直接POSTでも、AIを呼ぶ前に会話のロールと教材セットをDBに固定する。
    # 既存の会話を書き換えず、同じ文脈の要求だけを許す。固定記録のあるNoneだけが全文を表す。
    # 持ち主（student_id）も一致しなければ False（ほかの人の会話には書き込ませない。#33）
    def matches(row):
        return row["role_id"] == role_id and row["material_set_id"] == material_set_id and row["student_id"] == student_id

    try:
        with get_engine().begin() as conn:
            row = conn.execute(select(conversations).where(conversations.c.id == conversation_id)).mappings().first()
            if row is not None:
                scope_known = row["material_set_id"] is not None or conn.execute(
                    select(conversation_contexts.c.conversation_id).where(conversation_contexts.c.conversation_id == conversation_id)
                ).first() is not None
                return scope_known and matches(row)
            conn.execute(
                insert(conversations).values(
                    id=conversation_id,
                    role_id=role_id,
                    material_set_id=material_set_id,
                    student_id=student_id,
                    created_at=now(),
                )
            )
            conn.execute(insert(conversation_contexts).values(conversation_id=conversation_id))
        return True
    except IntegrityError:
        # 同じ未使用IDへの同時送信は、先に保存された文脈と再照合する。
        existing = get_conversation(conversation_id)
        if existing is None:
            raise
        return matches(existing) and (existing["material_set_id"] is not None or has_conversation_context(conversation_id))


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
        for m in messages:
            result = conn.execute(
                insert(chat_messages).values(
                    conversation_id=conversation_id,
                    role=m["role"],
                    content=m["content"],
                    evidence=m.get("evidence"),
                    created_at=created,
                )
            )
            # 発言に検知の結果があれば、一緒に保存する（#21）
            message_id = result.inserted_primary_key[0]
            for d in m.get("detections") or []:
                conn.execute(
                    insert(detections).values(message_id=message_id, type=d["type"], level=d["level"], created_at=created)
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
        detection_rows = conn.execute(select(detections).order_by(detections.c.id)).all()
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
        "detections": [{"message_id": f"db{d.message_id}", "type": d.type, "level": d.level} for d in detection_rows],
    }


# ---- ロールの編集と教材セット（#13） ----


def read_role_edits(material_id: str):
    # {candidate_id: {"name", "features"} または None（元に戻した）}
    with get_engine().connect() as conn:
        rows = conn.execute(select(role_edits).where(role_edits.c.material_id == material_id)).all()
    return {r.candidate_id: (None if r.name is None else {"name": r.name, "features": list(r.features)}) for r in rows}


def save_role_edit(material_id: str, candidate_id: str, value):
    # value は {"name", "features"} か None（元に戻した）。同じ候補の前の内容は置き換える
    row = {
        "material_id": material_id,
        "candidate_id": candidate_id,
        "name": value["name"] if value else None,
        "features": list(value["features"]) if value else None,
        "updated_at": now(),
    }
    with get_engine().begin() as conn:
        conn.execute(
            delete(role_edits).where(role_edits.c.material_id == material_id, role_edits.c.candidate_id == candidate_id)
        )
        conn.execute(insert(role_edits).values(**row))


def create_material_set(material_set_id: str, material_id: str, range_start: int, range_end: int, roles, question=None):
    # 教材セットと、そのロールを1つのトランザクションで保存する
    # roles は [{"role_id", "name", "summary", "features", "image", "edited"}]（並び順のまま保存する）
    with get_engine().begin() as conn:
        conn.execute(
            insert(material_sets).values(
                id=material_set_id,
                material_id=material_id,
                range_start=range_start,
                range_end=range_end,
                question=question,
                created_at=now(),
            )
        )
        conn.execute(
            insert(material_set_roles),
            [
                {
                    "material_set_id": material_set_id,
                    "position": i,
                    "role_id": r["role_id"],
                    "name": r["name"],
                    "summary": r.get("summary") or "",
                    "features": list(r["features"]),
                    "image": r.get("image"),
                    "edited": int(bool(r.get("edited"))),
                }
                for i, r in enumerate(roles)
            ],
        )


def read_material_sets():
    # 保存した教材セットを、ロールつきで返す（作った順）
    with get_engine().connect() as conn:
        set_rows = conn.execute(select(material_sets).order_by(material_sets.c.created_at)).all()
        role_rows = conn.execute(select(material_set_roles).order_by(material_set_roles.c.position)).all()
    roles_by_set = {}
    for r in role_rows:
        roles_by_set.setdefault(r.material_set_id, []).append(
            {
                "role_id": r.role_id,
                "name": r.name,
                "summary": r.summary or "",
                "features": list(r.features),
                "image": r.image,
                "edited": bool(r.edited),
            }
        )
    return [
        {**dict(row._mapping), "created_at": to_jst_text(row.created_at), "roles": roles_by_set.get(row.id, [])}
        for row in set_rows
    ]


# ---- ログインした人（#33） ----


def upsert_user(user_id: str, email: str, name: str):
    # ログインのたびに呼ぶ。初めての人は生徒として作る。名前とメールは最新にする（role は変えない）
    with get_engine().begin() as conn:
        exists = conn.execute(select(users.c.id).where(users.c.id == user_id)).first()
        if exists is None:
            conn.execute(insert(users).values(id=user_id, email=email, name=name, role="student", created_at=now()))
        else:
            conn.execute(update(users).where(users.c.id == user_id).values(email=email, name=name))
    return get_user(user_id)


def get_user(user_id: str):
    # ユーザー1人を辞書で返す。なければ None
    with get_engine().connect() as conn:
        row = conn.execute(select(users).where(users.c.id == user_id)).first()
    return dict(row._mapping) if row else None


def set_user_role(user_id: str, role: str):
    # 教師コードが正しかったときに、teacher にする
    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(role=role))


def read_user_names():
    # {ユーザーの id: 名前}。教師のチャット一覧で、生徒の名前を出すのに使う
    with get_engine().connect() as conn:
        return {r.id: r.name or r.email or "名前なし" for r in conn.execute(select(users.c.id, users.c.name, users.c.email))}


# ---- AI が作った立場の候補（#16） ----


def find_candidate_set(material_id: str, range_start: int, range_end: int):
    # 教材と範囲の候補のまとまりを返す。まだ作っていなければ None
    with get_engine().connect() as conn:
        row = conn.execute(
            select(candidate_sets).where(
                candidate_sets.c.material_id == material_id,
                candidate_sets.c.range_start == range_start,
                candidate_sets.c.range_end == range_end,
            )
        ).first()
    return dict(row._mapping) if row else None


def save_candidate_set(candidate_set_id: str, material_id: str, range_start: int, range_end: int, roles):
    # 候補を保存して、保存されている候補のまとまりを返す
    # 同じ教材・範囲を同時に作ったときは、先に保存されたほうを使う
    try:
        with get_engine().begin() as conn:
            conn.execute(
                insert(candidate_sets).values(
                    id=candidate_set_id,
                    material_id=material_id,
                    range_start=range_start,
                    range_end=range_end,
                    roles=roles,
                    created_at=now(),
                )
            )
    except IntegrityError:
        pass
    return find_candidate_set(material_id, range_start, range_end)


def get_candidate_set(candidate_set_id: str):
    with get_engine().connect() as conn:
        row = conn.execute(select(candidate_sets).where(candidate_sets.c.id == candidate_set_id)).first()
    return dict(row._mapping) if row else None
