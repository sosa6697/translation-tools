"""中英等多语言互译：腾讯交互翻译（免 key）为主，MyMemory 兜底。"""

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
_CJK_SPACE = re.compile(
    r"(?<=[\u3400-\u9fff\u3000-\u303f\uff00-\uffef])[ \t\u3000]+"
    r"(?=[\u3400-\u9fff\u3000-\u303f\uff00-\uffef])"
)


def clean_text(text):
    """整理 OCR 结果：合并断行、去掉中文之间的空格。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    text = " ".join(lines)
    text = _CJK_SPACE.sub("", text)
    return text.strip()


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

    def translate(self, text, target):
        """target: 'zh' 或 'en'。返回译文（网络用语会先做同语言改写）。"""
        key = (target, text)
        if key in self._cache:
            return self._cache[key]

        source = text
        if looks_like_pinyin(text):
            hanzi = to_hanzi(text)
            if hanzi and target == "zh":
                # 拼音直接还原成汉字显示，不再送去机器翻译
                self._cache[key] = hanzi
                if len(self._cache) > 500:
                    self._cache.clear()
                return hanzi
            source = hanzi or text
        source = normalize(source, target)
        try:
            result = self._tencent(source, target)
        except Exception:
            result = None
        if not result:
            if target not in ("zh", "en"):
                raise TranslateError("翻译失败")
            result = self._mymemory(source, target)

        if not result:
            raise TranslateError("翻译失败")
        self._cache[key] = result
        if len(self._cache) > 500:
            self._cache.clear()
        return result

    def translate_lines(self, texts, mode="auto"):
        """逐行自动判断语言并翻译，返回 [(译文, 目标语言)]。

        mode="auto"：只翻译非中文（含拼音），中文行原样保留；
        mode="zh_en"：只翻译中文（含拼音）为英文，其他语言原样保留。
        跳过或翻译失败的行返回空字符串。
        """
        targets = []
        for text in texts:
            pinyin = looks_like_pinyin(text)
            if pinyin or detect_lang(text) == "zh":
                targets.append("en" if mode == "zh_en" else ("zh" if pinyin else None))
            else:
                targets.append("zh" if mode != "zh_en" else None)

        unique = {}
        for text, target in zip(texts, targets):
            if target:
                unique.setdefault((text, target), text)

        def job(key):
            text, target = key
            cached = self._cache.get(key)
            if cached:
                return key, cached
            try:
                return key, self.translate(text, target)
            except TranslateError:
                return key, ""

        results = {}
        if unique:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for key, out in pool.map(job, list(unique)):
                    results[key] = out

        return [(results.get((text, target), "") if target else "", target)
                for text, target in zip(texts, targets)]

    def _tencent(self, text, target):
        body = {
            "header": {
                "fn": "auto_translation",
                "client_key": "browser-chrome-131.0.0-Win32-1.0.0-00000000",
                "user": "",
            },
            "type": "plain",
            "model_category": "normal",
            "source": {"lang": "auto", "text_list": [text]},
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
        return parts[0].strip() if parts else None

    def _mymemory(self, text, target):
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
