"""中英等多语言互译：腾讯交互翻译（免 key）为主，MyMemory 兜底；俄语走 MyMemory。"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from pinyin import looks_like_pinyin, to_hanzi
from slang import normalize

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_LATIN = re.compile(r"[A-Za-z]")
_CYR = re.compile(r"[\u0400-\u04FF]")
_CJK_SPACE = re.compile(
    r"(?<=[\u3400-\u9fff\u3000-\u303f\uff00-\uffef])[ \t\u3000]+"
    r"(?=[\u3400-\u9fff\u3000-\u303f\uff00-\uffef])"
)
_NAME_SEP = re.compile(r"^(?P<head>[^\n]{1,40}?)[:：]\s+(?P<body>\S.*)$")


def clean_text(text):
    """整理 OCR 结果：合并断行、去掉中文之间的空格。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = " ".join(lines)
    text = _CJK_SPACE.sub("", text)
    return text.strip()


def split_name(text):
    """拆分「昵称: 内容」：冒号前的基本是玩家昵称，原样保留，只翻后面的内容。"""
    match = _NAME_SEP.match(text)
    if not match or len(match.group("body")) < 2:
        return "", text
    return text[:match.start("body")], match.group("body")


def detect_lang(text):
    """粗略判断语言，返回 'zh' / 'en' / None。"""
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    total = cjk + latin
    if total == 0:
        return None
    return "zh" if cjk / total >= 0.25 else "en"


class TranslateError(RuntimeError):
    pass


class Translator:
    def __init__(self):
        self._local = threading.local()
        self._cache = {}

    def _session(self):
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": UA})
            self._local.session = session
        return session

    def translate(self, text, target, caps=False, lenient=False):
        """target: 'zh' 或 'en'。返回译文（「昵称: 内容」只翻冒号后的内容）。"""
        key = (target, caps, lenient, text)
        if key in self._cache:
            return self._cache[key]

        head, source = split_name(text)
        if looks_like_pinyin(source, caps=caps, lenient=lenient):
            hanzi = to_hanzi(source)
            if hanzi and target == "zh":
                # 拼音直接还原成汉字显示，不再送去机器翻译
                result = head + hanzi
                self._cache[key] = result
                if len(self._cache) > 500:
                    self._cache.clear()
                return result
            source = hanzi or source
        cyr = bool(_CYR.search(source))
        source = normalize(source, target)
        try:
            # 腾讯接口没有俄语引擎，直接问 MyMemory
            result = None if cyr else self._tencent(source, target)
        except Exception:
            result = None
        if not result:
            if target not in ("zh", "en"):
                raise TranslateError("翻译失败")
            result = self._mymemory(source, target, "ru" if cyr else None)

        if not result:
            raise TranslateError("翻译失败")
        result = head + self._finish(result, target, source, "ru" if cyr else None)
        self._cache[key] = result
        if len(self._cache) > 500:
            self._cache.clear()
        return result

    def _finish(self, result, target, source, src=None):
        """「外语→中」只允许中文：接口返回英文时再兜底翻一次。"""
        if (target == "zh" and not _CJK.search(result) and result != source
                and any(ch.isalpha() for ch in result)):
            return self._retry_chinese(result, src) or result
        return result

    def translate_lines(self, texts, mode="auto"):
        """逐行自动判断语言并翻译，返回 [(译文, 目标语言)]。

        mode="auto"：外语转中文，拼音行与中文行原样保留；
        mode="pinyin"：只把拼音还原成中文（大小写均可），其他行原样保留；
        mode="zh_en"：只翻译中文（含拼音还原后）为英文，其他语言原样保留。
        跳过或翻译失败的行返回空字符串。
        """
        caps = mode == "pinyin"
        lenient = mode == "pinyin"
        targets = []
        for text in texts:
            _head, body = split_name(text)
            pinyin = looks_like_pinyin(body, caps=caps, lenient=lenient)
            if mode == "pinyin":
                targets.append("zh" if pinyin else None)
            elif pinyin or detect_lang(body) == "zh":
                targets.append("en" if mode == "zh_en" else None)
            else:
                targets.append("zh" if mode != "zh_en" else None)

        results = {}
        for target in ("zh", "en"):
            pending = []
            for text, want in zip(texts, targets):
                if want != target or (target, caps, lenient, text) in self._cache:
                    continue
                if text not in pending:
                    pending.append(text)
            if pending:
                results.update(self._translate_batch(pending, target, caps, lenient))

        out = []
        for text, target in zip(texts, targets):
            if not target:
                out.append(("", target))
                continue
            value = results.get((text, target))
            if value is None:
                value = self._cache.get((target, caps, lenient, text), "")
            out.append((value, target))
        return out

    def _translate_batch(self, texts, target, caps, lenient):
        """并发逐条翻译：不同语种混排时合并请求会让接口只按一种语言识别，故逐条发送。"""
        def job(text):
            try:
                return text, self.translate(text, target, caps=caps, lenient=lenient)
            except TranslateError:
                return text, ""

        done = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for text, value in pool.map(job, texts):
                done[(text, target)] = value
        return done

    def _retry_chinese(self, text, src=None):
        """英文兜底结果再翻成中文；仍然没有中文就返回 None。"""
        engines = (self._mymemory,) if src else (self._tencent, self._mymemory)
        for engine in engines:
            try:
                out = engine(text, "zh", src) if engine is self._mymemory else engine(text, "zh")
            except Exception:
                out = None
            if out and _CJK.search(out):
                return out
        return None

    def _tencent_many(self, texts, target):
        """一次请求翻译多行（接口支持 text_list），减少请求数与偶发限流。"""
        body = {
            "header": {
                "fn": "auto_translation",
                "client_key": "browser-chrome-131.0.0-Win32-1.0.0-00000000",
                "user": "",
            },
            "type": "plain",
            "model_category": "normal",
            "source": {"lang": "auto", "text_list": list(texts)},
            "target": {"lang": target},
        }
        resp = self._session().post(
            "https://transmart.qq.com/api/imt",
            json=body,
            headers={"Referer": "https://transmart.qq.com/zh-CN/index"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("header", {}).get("ret_code") != "succ":
            return None
        parts = data.get("auto_translation") or []
        if len(parts) != len(texts):
            return None
        return [str(part).strip() for part in parts]

    def _tencent(self, text, target):
        parts = self._tencent_many([text], target)
        return parts[0] if parts else None

    def _mymemory(self, text, target, src=None):
        if src:
            pair = f"{src}|en" if target == "en" else f"{src}|zh-CN"
        else:
            pair = "zh-CN|en" if target == "en" else "en|zh-CN"
        out = []
        for chunk in _split(text, 450):
            resp = self._session().get(
                "https://api.mymemory.translated.net/get",
                params={"q": chunk, "langpair": pair},
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
            translated = (data.get("responseData") or {}).get("translatedText") or ""
            if not translated or translated.upper().startswith("MYMEMORY WARNING"):
                raise TranslateError("翻译失败")
            out.append(translated.strip())
        return " ".join(out) if out else None


def _split(text, size):
    return [text[i:i + size] for i in range(0, len(text), size)] or [text]
