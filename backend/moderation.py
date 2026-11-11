"""Issue #21: 乱暴な言葉の確認候補を返す、API/DBに依存しない初期判定。

チーム確認前の規則案。送信の拒否や自動処罰には使わない。
保存・画面への接続は担当者との調整待ちで、このモジュールからは行わない。
"""

import re
import unicodedata

TARGET = r"(?:お前|おまえ|あなた|あんた|君|きみ|こいつ|あいつ)"
INSULT = r"(?:ばか|馬鹿|あほ|無能|くず)"
END = r"(?=$|[\s、。!?！？」』])"
QUOTED_ANALYSIS = re.compile(
    r"(?:「[^「」]*」|『[^『』]*』)"
    r"(?=という(?:言葉|表現|台詞|せりふ)|の(?:意味|表現|言葉)|と(?:書かれ|語られ|述べられ))"
)
THREAT = re.compile(
    rf"(?:^|[\s、。!?！？「『])(?:{TARGET}(?:は|なんか|なんて)?\s*)?"
    rf"(?:さっさと|今すぐ)?(?:死ね|しね|消えろ|きえろ)(?:よ|や)?{END}"
    rf"|{TARGET}(?:は|を)?\s*(?:絶対に|今すぐ)?(?:殺す|ころす){END}"
    rf"|^(?:殺す|ころす)[\s。!！]*$"
)
PERSONAL_INSULT = re.compile(
    rf"{TARGET}(?:は|って|なんて)?\s*{INSULT}(?!ではな|じゃな)(?:だ(?:よ|な|ね)?|です)?{END}"
    rf"|^{INSULT}[\s。!！]*$"
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if c not in "\u200b\u200c\u200d\ufeff")
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text).strip()


def detect(text: str) -> list[dict]:
    """[{type: rough_language, level: high|medium}] または [] を返す。

明示された攻撃表現のみを対象とする。未知の表現の安全性は保証しない。
入力文や検知した語は返さず、既存の教師画面が扱うキーだけを返す。
"""
    if not isinstance(text, str):
        raise TypeError("textは文字列にしてください。")
    normalized = QUOTED_ANALYSIS.sub("", normalize(text))
    if THREAT.search(normalized):
        return [{"type": "rough_language", "level": "high"}]
    if PERSONAL_INSULT.search(normalized):
        return [{"type": "rough_language", "level": "medium"}]
    return []
