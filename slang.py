"""网络用语预处理：把黑话/流行梗换成同语言的中性表达，再交给机器翻译。

机器翻译对 lol、amk、type shit 这类词基本无能为力，直接翻会得到
"类型狗屎"、"阿妈" 之类的错误结果。这里在翻译前先做同语言改写，
既保留原意，也不会把句子变成长串外语导致整句翻译失败。
"""

import re

# ---------- 拉丁语言行（目标：中文） ----------
_LATIN_RAW = [
    # 英语
    (r"\btype\s+shi{1,2}t?\b", "and stuff like that", re.I),
    (r"\bno\s+cap\b", "seriously", re.I),
    (r"\bthat'?s\s+cap\b", "that's a lie", re.I),
    (r"\bngl\b", "honestly", re.I),
    (r"\bicl\b", "I can't lie", re.I),
    (r"\bikr\b", "I know, right", re.I),
    (r"\btbh\b", "to be honest", re.I),
    (r"\bimo\b", "in my opinion", re.I),
    (r"\bidk\b", "I don't know", re.I),
    (r"\birl\b", "in real life", re.I),
    (r"\brn\b", "right now", re.I),
    (r"\batm\b", "at the moment", re.I),
    (r"\bsmh\b", "what a shame", re.I),
    (r"\bwtf\b", "what the hell", re.I),
    (r"\bomg\b", "oh my god", re.I),
    (r"\baf\b", "as hell", re.I),
    (r"\bfr\b", "for real", re.I),
    (r"\blmfao\b", "haha", re.I),
    (r"\blmao\b", "haha", re.I),
    (r"\blol\b", "haha", re.I),
    (r"\bmid\b", "mediocre", re.I),
    (r"\bsus\b", "suspicious", re.I),
    (r"\bgoated\b", "the greatest", re.I),
    (r"\bthe\s+goat\b", "the greatest", re.I),
    (r"\brizz\b", "charm", re.I),
    (r"\bbussin'?\b", "delicious", re.I),
    (r"\blowkey\b", "kind of", re.I),
    (r"\bhighkey\b", "very", re.I),
    (r"\bdelulu\b", "delusional", re.I),
    (r"\bfinna\b", "going to", re.I),
    (r"\btryna\b", "trying to", re.I),
    (r"\bglazing\b", "overpraising", re.I),
    (r"\byapping\b", "talking too much", re.I),
    (r"\bwe'?re\s+cooked\b", "we are in trouble", re.I),
    (r"\bgg\s+wp\b", "well done", re.I),
    (r"\bgg\b", "well done", re.I),
    (r"\bwp\b", "well done", re.I),
    (r"\bgj\b", "well done", re.I),
    (r"\bpush(?:ing)?\b", "advance", re.I),
    (r"\bfall\s+back\b", "retreat", re.I),
    (r"\bez\b", "easy", re.I),

    # 土耳其语
    (r"^\s*am[kq]\b", "Lanet olsun,", re.I),
    (r"\bam[kq]\s*$", ", lanet olsun", re.I),
    (r"\bam[kq]\b", "gerçekten", re.I),
    (r"\baq\b", "gerçekten", re.I),
    (r"^\s*lan\b", "Hey,", re.I),
    (r"\bkanka\b", "dostum", re.I),

    # 法语
    (r"\bmdr\b", "très drôle,", re.I),
    (r"\bptdr\b", "très drôle,", re.I),
    (r"\bxd\b", "très drôle,", re.I),
    (r"\bchelou\b", "bizarre", re.I),
    (r"\bmeuf\b", "femme", re.I),
    (r"\bkeuf\b", "policier", re.I),
    (r"\bouais\b", "oui", re.I),
    (r"\bwesh\b", "salut", re.I),
    (r"\bfr[ée]rots?\b", "frère", re.I),
    (r"\bkiffer\b", "aimer", re.I),
    (r"\bjsp\b", "je ne sais pas", re.I),
    (r"\bbof\b", "moyen", re.I),
    (r"\btrop\s+bien\b", "très bien", re.I),
    (r"\bouf\b", "fou", re.I),
    (r"\bteuf\b", "fête", re.I),

    # 西班牙语
    (r"\bno\s+mames\b", "no digas eso", re.I),
    (r"\bno\s+manches\b", "no digas eso", re.I),
    (r"\bno\s+mms\b", "no digas eso", re.I),
    (r"\s*\b(?:g[üu]ey|wey)\b", "", re.I),
    (r"\bchido\b", "genial", re.I),
    (r"\bchida\b", "genial", re.I),
    (r"\bmuy\s+padre\b", "muy bueno", re.I),
    (r"\bqu[ée]\s+padre\b", "qué bien", re.I),
    (r"\bneta\b", "de verdad", re.I),
    (r"\bchale\b", "qué lástima", re.I),
    (r"\b[óo]rale\b", "venga", re.I),
    (r"\bpinche\b", "maldito", re.I),
    (r"\bqu[ée]\s+pedo\b", "qué pasa", re.I),
    (r"\bculero\b", "horrible", re.I),

    # 德语
    (r"\bdigga\b", "Kumpel", re.I),
    (r"\bdiggi\b", "Kumpel", re.I),
    (r"\bwallah(i)?\b", "ich schwöre", re.I),
    (r"\bist\s+krass\b", "ist heftig gut", re.I),
    (r"\bkrass\b", "sehr", re.I),
    (r"\bist\s+geil\b", "ist heftig gut", re.I),
    (r"\bgeil\b", "super", re.I),
    (r"\bbock\b", "Lust", re.I),
]

# ---------- 中文行（目标：英文） ----------
_ZH_RAW = [
    # 中文网络用语
    (r"yyds", "非常棒", re.I),
    (r"绝绝子", "太棒了", re.I),
    (r"牛[逼批]", "很厉害", re.I),
    (r"破防(了)?", "心里很难受", re.I),
    (r"社死", "非常丢脸", re.I),
    (r"芭比Q", "完蛋了", re.I),
    (r"栓Q", "真是谢谢你", re.I),
    (r"233+", "哈哈", re.I),
    (r"(?<![A-Za-z0-9])666(?![0-9])", "厉害", re.I),
    (r"(?<![A-Za-z])emo(?![A-Za-z])", "情绪低落", re.I),
    (r"awsl", "太可爱了", re.I),
    (r"xswl", "笑死我了", re.I),
    (r"打call", "加油", re.I),
    (r"泪目", "感动得想哭", re.I),
    (r"这波操作", "这个举动", re.I),
    (r"内卷", "激烈竞争", re.I),
    (r"躺平", "放弃奋斗", re.I),

    # 中文句子里夹带的英文黑话（替换成中文，避免中英混排）
    (r"(?<![A-Za-z])type\s+shi{1,2}t?(?![A-Za-z])", "之类的东西", re.I),
    (r"(?<![A-Za-z])no\s+cap(?![A-Za-z])", "真的", re.I),
    (r"(?<![A-Za-z])ngl(?![A-Za-z])", "说实话", re.I),
    (r"(?<![A-Za-z])tbh(?![A-Za-z])", "说实话", re.I),
    (r"(?<![A-Za-z])imo(?![A-Za-z])", "我觉得", re.I),
    (r"(?<![A-Za-z])idk(?![A-Za-z])", "不知道", re.I),
    (r"(?<![A-Za-z])wtf(?![A-Za-z])", "搞什么", re.I),
    (r"(?<![A-Za-z])omg(?![A-Za-z])", "我的天", re.I),
    (r"(?<![A-Za-z])(lmfao|lmao|lol)(?![A-Za-z])", "哈哈", re.I),
    (r"(?<![A-Za-z])mid(?![A-Za-z])", "一般", re.I),
    (r"(?<![A-Za-z])sus(?![A-Za-z])", "可疑", re.I),
    (r"(?<![A-Za-z])lowkey(?![A-Za-z])", "有点", re.I),
    (r"(?<![A-Za-z])fr(?![A-Za-z])", "真的", re.I),
]

_LATIN_RULES = [(re.compile(p, f), r) for p, r, f in _LATIN_RAW]
_ZH_RULES = [(re.compile(p, f), r) for p, r, f in _ZH_RAW]


def normalize(text, target="zh"):
    """把黑话替换成同语言的中性表达；target 为 'en' 时按中文行处理。"""
    rules = _ZH_RULES if target == "en" else _LATIN_RULES
    for pattern, repl in rules:
        text = pattern.sub(repl, text)
    return text
