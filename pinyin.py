"""拼音识别：把屏幕上的拼音（带声调或不带声调）还原成汉字，直接显示中文。

依赖 Pinyin2Hanzi（纯 Python，带离线词典），缺失时自动跳过，不影响其它功能。
"""

import re
import threading
import unicodedata

_LETTERS = re.compile(r"[A-Za-z\u00C0-\u024F]+")
# 聊天行的昵称与正文分隔符：`SgtPepper: ni gan ma`、`[Squad] SgtPepper: ...`
_SEPARATORS = re.compile(r"[:：>\]]\s*")

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


def _best_syllables(text):
    """整行能切成拼音音节就用整行；否则剥掉英文昵称前缀再试。

    聊天行常见 `SgtPepper: ni gan ma`、`[Squad] SgtPepper: chi fan le ma`，
    昵称切不成音节，整行会被误判为非拼音，这里只把正文交给还原。
    返回 (前缀, 正文, 音节)；切不开时音节为 None。
    """
    syllables = _syllables_of(text)
    if syllables:
        return "", text, syllables

    last = None
    for match in _SEPARATORS.finditer(text):
        last = match
    if last:
        tail = text[last.end():]
        syllables = _syllables_of(tail)
        if syllables:
            return text[:last.end()], tail, syllables

    first = _LETTERS.search(text)
    if first and not _split_token(_plain(first.group()), _syllable_set()):
        # 没有分隔符时的兜底：首词不是拼音，就当作昵称剥掉
        end = first.end()
        while end < len(text) and text[end] in " \t\u3000":
            end += 1
        syllables = _syllables_of(text[end:])
        if syllables:
            return text[:end], text[end:], syllables

    return "", text, None


def looks_like_pinyin(text, caps=False, lenient=False):
    """判断整行是否像拼音。

    带声调直接认可；开启 caps 时全大写字母行直接认可（聊天里常用全大写拼音）；
    lenient 用于「拼音→中」模式，只要整行能切成音节就认定。
    """
    if lenient:
        _prefix, body, syllables = _best_syllables(text)
    else:
        body, syllables = text, _syllables_of(text)
    if not syllables:
        return False
    if lenient and (len(syllables) == 1 or tuple(syllables) in _PHRASES):
        return True
    if _has_tone(body):
        return True
    if caps and _all_caps(body):
        return True
    if len(syllables) < 2:
        return False
    tokens = [_plain(t) for t in _LETTERS.findall(body)]
    return any(token not in _EN_WORDS for token in tokens)


def _all_caps(text):
    letters = _LETTERS.findall(text)
    return bool(letters) and all(token.isupper() for token in letters)


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


_PHRASE_SRC = """
ni hao|你好
ni hao ma|你好吗
ni gan ma|你干嘛
gan ma|干嘛
gan xie|感谢
zai gan ma|在干嘛
ni zai gan ma|你在干嘛
gan sha|干啥
gan shen me|干什么
gan sha ne|干啥呢
chi fan le ma|吃饭了吗
chi fan|吃饭
chi le ma|吃了吗
zai na|在哪
zai na li|在哪里
na li|哪里
shen me|什么
wei shen me|为什么
zen me|怎么
zen me yang|怎么样
zen me le|怎么了
shen me gui|什么鬼
shen me qing kuang|什么情况
fa sheng shen me|发生什么
shi de|是的
bu shi|不是
mei you|没有
you mei you|有没有
xie xie|谢谢
bu ke qi|不客气
dui bu qi|对不起
mei guan xi|没关系
zai jian|再见
hao de|好的
zhi dao|知道
bu zhi dao|不知道
wo bu zhi dao|我不知道
ke yi|可以
bu ke yi|不可以
lai ba|来吧
zou ba|走吧
kuai dian|快点
man dian|慢点
xiao xin|小心
jiu wo|救我
bang wo|帮我
deng wo|等我
gen wo lai|跟我来
che tui|撤退
tui hou|退后
ying le|赢了
shu le|输了
si le|死了
qiang|枪
zi dan|子弹
yao|药
di ren|敌人
dui you|队友
ni men|你们
wo men|我们
ta men|他们
da jia|大家
lao da|老大
xiong di|兄弟
peng you|朋友
mei wen ti|没问题
you bing|有病
shen jing bing|神经病
sha bi|傻逼
niu bi|牛逼
wo cao|我操
cao ni ma|操你妈
ma de|妈的
ta ma de|他妈的
wan le|完了
ma fan|麻烦
xi wang|希望
jue de|觉得
yin wei|因为
suo yi|所以
dan shi|但是
ru guo|如果
xian zai|现在
gang cai|刚才
yi hou|以后
yi qian|以前
jin tian|今天
ming tian|明天
zuo tian|昨天
shang ban|上班
xia ban|下班
shui jiao|睡觉
qi chuang|起床
you xi|游戏
da you xi|打游戏
kai xin|开心
hao wan|好玩
lei le|累了
e le|饿了
kun le|困了
you kong|有空
mei kong|没空
wo de|我的
ni de|你的
ta de|他的
hao xiang|好像
yi qi|一起
deng yi xia|等一下
hui lai|回来
guo qu|过去
chu qu|出去
jin lai|进来
zou le|走了
wo lai|我来
ni lai|你来
bie pa|别怕
bu yao|不要
bu xing|不行
mei shi|没事
dui ma|对吗
shi ma|是吗
you ma|有吗
ke yi ma|可以吗
zai ma|在吗
shui le ma|睡了吗
zao shang hao|早上好
wan an|晚安
sheng ri kuai le|生日快乐
xin nian kuai le|新年快乐
da jia hao|大家好
tian qi|天气
bu cuo|不错
hao chi|好吃
xue xi|学习
xue sheng|学生
yin yue|音乐
que ding|确定
que shi|确实
nv ren|女人
nv sheng|女生
shang mian|上面
xia mian|下面
qian mian|前面
hou mian|后面
zuo bian|左边
you bian|右边
li mian|里面
wai mian|外面
pang bian|旁边
he he|呵呵
ha ha|哈哈
kuai le|快乐
zai jian le|再见了
ni|你
wo|我
ta|他
de|的
le|了
ma|吗
ne|呢
ba|吧
a|啊
o|哦
en|嗯
hao|好
bu|不
shi|是
zai|在
you|有
ren|人
da|打
kan|看
kan dao|看到
shuo|说
ting|听
ting dao|听到
lai|来
qu|去
zou|走
kai|开
kuai|快
man|慢
shang|上
xia|下
zuo|做
chi|吃
he|喝
shui|睡
xiang|想
yao|要
xie|写
ma ma|妈妈
ba ba|爸爸
ge ge|哥哥
jie jie|姐姐
di di|弟弟
mei mei|妹妹
"""

_PHRASES = {}
try:
    from Pinyin2Hanzi import simplify_pinyin as _simplify_key
except Exception:
    _simplify_key = None

for _line in _PHRASE_SRC.strip().splitlines():
    _py, _hz = _line.split("|")
    _parts = _py.split()
    if _simplify_key:
        _parts = [_simplify_key(p) for p in _parts]
    _PHRASES[tuple(_parts)] = _hz
_MAX_PHRASE = max(len(key) for key in _PHRASES)


def _phrase_hanzi(syllables):
    """按词表最长匹配还原；词表覆盖不到的片段交给 DAG。"""
    parts = []
    run = []
    i = 0
    while i < len(syllables):
        matched = None
        for n in range(min(_MAX_PHRASE, len(syllables) - i), 0, -1):
            word = _PHRASES.get(tuple(syllables[i:i + n]))
            if word:
                matched = (word, n)
                break
        if matched:
            if run:
                parts.append(_dag_hanzi(run))
                run = []
            parts.append(matched[0])
            i += matched[1]
        else:
            run.append(syllables[i])
            i += 1
    if run:
        parts.append(_dag_hanzi(run))
    return "".join(parts)


def _dag_hanzi(syllables):
    params = _params()
    if params is None or not syllables:
        return ""
    from Pinyin2Hanzi import dag
    try:
        result = dag(params, syllables, path_num=1)
    except Exception:
        return ""
    if not result:
        return ""
    return _tidy(syllables, "".join(result[0].path))


def to_hanzi(text):
    """拼音转汉字，失败返回 None；带英文昵称前缀时保留前缀。"""
    prefix, _body, syllables = _best_syllables(text)
    params = _params()
    if not syllables or params is None:
        return None
    from Pinyin2Hanzi import simplify_pinyin

    # 库内部用 xve 表示 xue（ü），转写后再查
    syllables = [simplify_pinyin(s) for s in syllables]
    hanzi = _phrase_hanzi(syllables)
    if not hanzi:
        return None
    return prefix + hanzi


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
