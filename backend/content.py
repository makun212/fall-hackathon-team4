import json
from pathlib import Path

# 厚樹さんが用意した『舞姫』のデータ（#42 の本文、#14 の立場）を、ダミーデータに重ねる
# main.py はこのファイルの apply_maihime() を1回呼ぶだけ。データの形が変わったら、ここだけ直す
PREPARATION_DIR = Path(__file__).resolve().parent / "data" / "preparation"
MAIHIME_MATERIAL_PATH = PREPARATION_DIR / "maihime_material.json"
MAIHIME_ROLES_PATH = PREPARATION_DIR / "maihime_preset_roles.json"

# 本文に行番号がないときに、1行を何文字として数えるか（教科書の縦書き1行くらい）
# 本文の JSON に line_start / line_end が入ったら、そちらを優先する（#42）
LINE_LENGTH = 40

# 立場で、厚樹さんのデータから取り込む項目（id と image はダミーデータのものを残す）
ROLE_FIELDS = ("name", "summary", "features", "basis", "prompt_hint")


def read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def add_line_numbers(paragraphs):
    # 各段落に line_start / line_end を付ける。すでに付いている段落は、その値を使う
    result = []
    next_line = 1
    for paragraph in paragraphs:
        paragraph = dict(paragraph)
        if "line_start" not in paragraph or "line_end" not in paragraph:
            line_count = max(1, -(-len(paragraph["text"].strip()) // LINE_LENGTH))
            paragraph["line_start"] = next_line
            paragraph["line_end"] = next_line + line_count - 1
        next_line = paragraph["line_end"] + 1
        result.append(paragraph)
    return result


def merge_roles(seed_roles, new_roles):
    # 同じ順番の立場どうしで、中身だけを入れ替える
    # ID（r001 など）は、教材セットや会話のダミーデータが参照しているので変えない
    merged = []
    for i, seed_role in enumerate(seed_roles):
        role = dict(seed_role)
        if i < len(new_roles):
            role.update({key: new_roles[i][key] for key in ROLE_FIELDS})
            # 画像はまだ選んでいないので、空ならダミーデータの画像を使う
            if new_roles[i].get("image"):
                role["image"] = new_roles[i]["image"]
        merged.append(role)
    return merged


def apply_maihime(seed_data):
    # ダミーデータの『舞姫』（m001）を、本物の本文と立場に置き換えた seed_data を返す
    # 準備用のファイルがなければ、ダミーデータのまま返す
    if not MAIHIME_MATERIAL_PATH.exists() or not MAIHIME_ROLES_PATH.exists():
        return seed_data

    material = read_json(MAIHIME_MATERIAL_PATH)["material"]
    material = {**material, "paragraphs": add_line_numbers(material["paragraphs"])}
    roles = read_json(MAIHIME_ROLES_PATH)

    data = dict(seed_data)
    data["materials"] = [material if m["id"] == material["id"] else m for m in seed_data.get("materials") or []]
    preset = seed_data.get("preset_roles") or {}
    if preset.get("material_id") == roles.get("material_id"):
        data["preset_roles"] = {**preset, "roles": merge_roles(preset.get("roles") or [], roles["roles"])}
    return data
