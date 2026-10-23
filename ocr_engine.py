"""屏幕取词 OCR：本地 ONNX 模型（RapidOCR），支持中/英/德/法/土/西等拉丁语种。"""

import threading

import numpy as np

_THREADS = 2        # 限制 ONNX 推理线程，避免推理时占满所有核心
_DET_SIDE = 480     # 检测边长上限，窗口聊天区无需放大到 736


class OcrError(RuntimeError):
    pass


class ScreenOcr:
    def __init__(self):
        self._engine = None
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
                                        det_model_path=None)
            except Exception:
                self._engine = RapidOCR(use_angle_cls=False)
        return self._engine

    def recognize_lines(self, image):
        """返回按行切分的结果：[{text, x, y, w, h}]，坐标与传入图片像素一致。"""
        with self._lock:
            engine = self._ensure()
            array = np.array(image.convert("RGB"))
            if float(array.mean()) < 110:
                array = 255 - array  # 深色背景反色，便于模型识别
            result, _ = engine(array)

        lines = []
        for item in result or []:
            box, text = item[0], item[1]
            if not text or not str(text).strip():
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
