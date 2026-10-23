"""拼音识别：把屏幕上的拼音（带声调或不带声调）还原成汉字，直接显示中文。

依赖 Pinyin2Hanzi（纯 Python，带离线词典），缺失时自动跳过，不影响其它功能。
"""

import re
import threading
import unicodedata

_LETTERS = re.compile(r"[A-Za-z\u00C0-\u024F]+")

# 既像拼音又像英文的常见词：没有声调时，至少要有一个词不在此列才判定为拼音。
# 前一组本身就是一个合法拼音音节，后一组是常见英文功能词（多数切不成拼音）。
_EN_WORDS = {
    "a", "an", "he", "she", "me", "men", "can", "man", "ban", "fan", "pan",
    "you", "ran", "tan", "wan", "hang", "long", "song", "sun", "hen", "ping", "ma",
    "na", "pa", "ta", "da", "ba", "sha", "cha", "zha", "xia", "hua", "gua",
    "like", "make", "take", "same", "name", "nine", "line", "sale", "male",
    "pine", "site", "data", "sea", "die", "rice", "mice", "nice", "face",
    "race", "base", "case", "gate", "late", "date", "rate", "page", "made",
    "side", "rule", "open", "one", "human", "woman", "women", "banana",
    "camera", "mango", "tango", "panda", "china", "fine", "mine", "dine",
    "more", "core", "time", "home",
    "the", "is", "are", "was", "were", "be", "been", "to", "of", "and",
    "or", "but", "in", "on", "at", "it", "this", "that", "these", "those",
    "for", "with", "from", "by", "as", "not", "no", "yes", "do", "does",
    "did", "have", "has", "had", "will", "would", "should", "could", "i",
    "we", "they", "them", "his", "her", "its", "our", "your", "my", "up",
    "down", "out", "off", "over", "under", "again", "just", "very", "too",
    "so", "if", "then", "than", "there", "here", "when", "where", "why",
    "how", "what", "who", "which", "all", "any", "some", "such", "only",
    "also", "into", "about", "after", "before", "between", "through",
    "during",
}

_lock = threading.Lock()
_syllables = None
_dag_params = None


def _plain(text):
    """去声调并转小写：nǐ hǎo -> ni hao，ü 记为 v。"""
    text = text.replace("ü", "v").replace("Ü", "v")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _has_tone(text):
    decomposed = unicodedata.normalize("NFD", text)
    return any(unicodedata.combining(ch) for ch in decomposed)


def _syllable_set():
    global _syllables
    if _syllables is None:
        with _lock:
            if _syllables is None:
                try:
                    from Pinyin2Hanzi import all_pinyin, simplify_pinyin
                except Exception:
                    _syllables = frozenset()
                else:
                    raw = set(all_pinyin())
                    base = {simplify_pinyin(s) for s in raw}
                    base |= raw
                    # 输入法习惯：xue / lue 这类用 u 代替 ü（库里记为 v）
                    base |= {s.replace("v", "u") for s in base}
                    base |= {_plain(s) for s in base}
                    _syllables = frozenset(base)
    return _syllables


def _split_token(token, syllables):
    """把一个连写词按最长匹配切成拼音音节，切不开返回 None。"""
    out = []
    i = 0
    while i < len(token):
        for n in (6, 5, 4, 3, 2, 1):
            chunk = token[i:i + n]
            if chunk in syllables:
                out.append(chunk)
                i += n
                break
        else:
            return None
    return out


def _syllables_of(text):
    syllables = _syllable_set()
    if not syllables:
        return None
    tokens = _LETTERS.findall(text)
    if not tokens:
        return None
    result = []
    for token in tokens:
        part = _split_token(_plain(token), syllables)
        if not part:
            return None
        result.extend(part)
    return result


def looks_like_pinyin(text):
    """判断整行是否像拼音。带声调直接认可，无声调需两个音节且有非英文词。"""
    syllables = _syllables_of(text)
    if not syllables:
        return False
    if _has_tone(text):
        return True
    if len(syllables) < 2:
        return False
    tokens = [_plain(t) for t in _LETTERS.findall(text)]
    return any(token not in _EN_WORDS for token in tokens)


def _params():
    global _dag_params
    if _dag_params is None:
        with _lock:
            if _dag_params is None:
                try:
                    from Pinyin2Hanzi import DefaultDagParams
                except Exception:
                    return None
                _dag_params = DefaultDagParams()
    return _dag_params


def to_hanzi(text):
    """拼音转汉字，失败返回 None。"""
    syllables = _syllables_of(text)
    params = _params()
    if not syllables or params is None:
        return None
    from Pinyin2Hanzi import dag, simplify_pinyin

    # 库内部用 xve 表示 xue（ü），转写后再查
    syllables = [simplify_pinyin(s) for s in syllables]
    try:
        result = dag(params, syllables, path_num=1)
    except Exception:
        return None
    if not result:
        return None
    text = _tidy(syllables, "".join(result[0].path))
    return text or None


_TIDY = {"ni": "你", "ma": "吗", "le": "了", "de": "的"}


def _tidy(syllables, hanzi):
    """修正几个高频代词的选字，并处理句末语气词。"""
    if len(syllables) != len(hanzi):
        return hanzi
    chars = list(hanzi)
    last = len(chars) - 1
    for i, syllable in enumerate(syllables):
        if syllable == "ni":
            chars[i] = "你"
        elif i == last and syllable in ("ma", "le", "de"):
            chars[i] = _TIDY[syllable]
    return "".join(chars)
