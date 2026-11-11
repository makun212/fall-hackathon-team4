"""テストで「ログインした状態」を作る（#33）。本物の Google は使わない"""

import auth
import db

TEST_USER_ID = "test:user"


def login(client, role="teacher", user_id=TEST_USER_ID, name="テストの人"):
    # DB にユーザーを作り、署名したセッションの Cookie を client に入れる
    # role は teacher なら先生の画面も生徒の画面も使える。student なら生徒の画面だけ
    db.upsert_user(user_id, "", name)
    db.set_user_role(user_id, role)
    client.cookies.set(auth.SESSION_COOKIE, auth.session_cookie({"user_id": user_id}))
    return user_id
