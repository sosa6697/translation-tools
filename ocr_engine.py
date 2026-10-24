"""屏幕取词 OCR：本地 ONNX 模型（RapidOCR），中/英/德/法/土/西等拉丁语种与俄语。"""

import os
import re
import sys
import threading

import numpy as np

_THREADS = 2        # 限制 ONNX 推理线程，避免推理时占满所有核心
_DET_SIDE = 640     # 检测边长上限：聊天字号偏小时 480 容易漏检
_MIN_SCORE = 0.45   # 置信度过低的框多半是误识别，宁可丢掉也不送去乱翻译
_RAW_SCORE = 0.2    # 先收下低分候选，交给第二个识别模型复核后再决定去留

_CYR = re.compile(r"[\u0400-\u04FF]")
_CJK = re.compile(r"[\u3400-\u9FFF]")
_LATIN = re.compile(r"[A-Za-z]")

_CYR_MODEL = "cyrillic_v5.onnx"
_CYR_KEYS = "cyrillic_v5_dict.txt"


def _resource(name):
    """模型文件位置：源码运行取项目 models/，打包后在临时解包目录。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "models", name)


class OcrError(RuntimeError):
    pass


class ScreenOcr:
    def __init__(self):
        self._engine = None
        self._rec = None        # 西里尔/拉丁识别模型（PP-OCRv5），用于复核
        self._lock = threading.Lock()

    def _ensure(self):
        if self._engine is None:
            try:
                import onnxruntime as ort
                import rapidocr_onnxruntime.utils as ru
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise OcrError("缺少依赖，请执行 pip install rapidocr-onnxruntime") from exc

            def options(*args, **kwargs):
                opt = ort.SessionOptions(*args, **kwargs)
                opt.intra_op_num_threads = _THREADS
                opt.inter_op_num_threads = 1
                return opt

            ru.SessionOptions = options
            # 聊天文字都是水平的，关掉方向分类模型；检测边长与线程数一并压低
            try:
                self._engine = RapidOCR(use_angle_cls=False,
                                        det_limit_side_len=_DET_SIDE,
                                        text_score=_RAW_SCORE,
                                        det_model_path=None)
            except Exception:
                self._engine = RapidOCR(use_angle_cls=False)
        return self._engine

    def _recognizer(self):
        """按需加载西里尔识别模型；模型缺失时返回 None，俄语以外不受影响。"""
        if self._rec is not None:
            return self._rec or None
        model, keys = _resource(_CYR_MODEL), _resource(_CYR_KEYS)
        if not (os.path.exists(model) and os.path.exists(keys)):
            self._rec = False
            return None
        try:
            from rapidocr_onnxruntime.ch_ppocr_v3_rec.text_recognize import TextRecognizer
            self._rec = TextRecognizer({
                "use_cuda": False,
                "model_path": model,
                "keys_path": keys,
                "rec_batch_num": 6,
                "rec_img_shape": [3, 48, 320],
            })
        except Exception:
            self._rec = False
            return None
        return self._rec

    @staticmethod
    def _pick(ch_text, ch_score, alt_text, alt_score):
        """同一行分别用中英模型和西里尔模型识别，取更可信的那个。"""
        alt_text = (alt_text or "").strip()
        if not alt_text or _CJK.search(ch_text):
            return ch_text, ch_score
        if _CYR.search(alt_text) and alt_score >= max(0.5, ch_score - 0.25):
            return alt_text, alt_score
        if (not _CYR.search(ch_text) and _LATIN.search(alt_text)
                and alt_score >= ch_score - 0.05):
            # 拉丁文本两个模型都能认，西里尔模型通常更完整（词间空格不容易丢）
            return alt_text, alt_score
        return ch_text, ch_score

    def recognize_lines(self, image):
        """返回按行切分的结果：[{text, x, y, w, h}]，坐标与传入图片像素一致。"""
        with self._lock:
            engine = self._ensure()
            array = np.array(image.convert("RGB"))
            if float(array.mean()) < 110:
                array = 255 - array  # 深色背景反色，便于模型识别
            result, _ = engine(array)
            if not result:
                return []

            alt = None
            rec = self._recognizer()
            # 全是中文的屏幕（国内游戏）不必再跑西里尔模型，省一次约 0.2 秒的推理
            if rec is not None and any(not _CJK.search(str(item[1])) for item in result):
                try:
                    boxes = [np.array(item[0], dtype=np.float32) for item in result]
                    crops = engine.get_crop_img_list(array, boxes)
                    alt, _ = rec(crops)
                except Exception:
                    alt = None

        lines = []
        for index, item in enumerate(result):
            box, text = item[0], item[1]
            score = float(item[2]) if len(item) > 2 else 1.0
            if alt is not None and index < len(alt):
                text, score = self._pick(str(text), score, alt[index][0],
                                         float(alt[index][1]))
            if not text or not str(text).strip() or score < _MIN_SCORE:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            x, y = min(xs), min(ys)
            w, h = max(xs) - x, max(ys) - y
            if w < 4 or h < 4:
                continue
            lines.append({"text": str(text), "x": x, "y": y, "w": w, "h": h})
        return lines

    def close(self):
        self._engine = None
        self._rec = None
