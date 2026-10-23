"""翻译模式的透明悬浮窗：按原文位置覆盖显示译文（类似微信翻译）。

窗口分三层，自下而上：
- 背景层（shade）：只画可调透明度的圆角底色，鼠标穿透、不进入截图
- 工具条层（bar）：顶部 80px 的玻璃底，固定 40% 透明，不随“透明”滑块变化
- 界面层（main）：工具条控件 / 滑块 / 边框 / 译文块，始终完全不透明
"""

import ctypes
import math
import os
import threading
import tkinter as tk
from tkinter import font as tkfont

import numpy as np
from PIL import ImageGrab

from ocr_engine import OcrError, ScreenOcr
from translate_engine import TranslateError, Translator, clean_text

WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

# 三层窗口用同一个 DeferWindowPos 批次更新，位置与大小严格同步
_USER32 = ctypes.windll.user32
_USER32.GetParent.restype = ctypes.c_void_p
_USER32.GetParent.argtypes = [ctypes.c_void_p]
_USER32.BeginDeferWindowPos.restype = ctypes.c_void_p
_USER32.BeginDeferWindowPos.argtypes = [ctypes.c_int]
_USER32.DeferWindowPos.restype = ctypes.c_void_p
_USER32.DeferWindowPos.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
_USER32.EndDeferWindowPos.restype = ctypes.c_int
_USER32.EndDeferWindowPos.argtypes = [ctypes.c_void_p]

MAGIC = "#010203"          # 透明色，窗口除绘制内容外全部透明
BG = "#1B1B1F"             # 背景层底色（透明度由“透明”滑块控制）
BAR_BG = "#2C2C2E"         # iOS 次级背景
BAR_LINE = "#3A3A3C"
TRACK = "#48484A"
PILL = "#3A3A3C"
ACCENT = "#0A84FF"         # iOS 系统蓝
GREEN = "#30D158"          # iOS 系统绿
FG = "#FFFFFF"
SUB = "#8E8E93"            # iOS 次级文字
BAR_TEXT = "#D8D8DC"       # 玻璃底上的次级文字，保证可读
OUTLINE = "#3A3A3C"
GRIP = "#4A4A4E"
DARK_TEXT = "#0B0D12"

RADIUS = 20                 # 窗口圆角
BAR_H = 80                  # 工具条高度（与窗口顶部齐平）
BAR_ALPHA = 0.6             # 工具条底色：40% 透明（不透明度 60%）
DEFAULT_W, DEFAULT_H = 620, 300
MIN_W, MIN_H = 320, 160
DEFAULT_ALPHA = 85          # 0-100，100 为完全不透明
INTERVAL_MS = 900
FRAME_SIZE = (64, 40)       # 画面比对用的缩略图尺寸
FRAME_DELTA = 16            # 灰度差超过该值才算画面变化


class TranslateOverlay:
    def __init__(self, root, on_close):
        self.root = root
        self.on_close = on_close
        self._closed = False
        self._busy = False
        self._signature = None
        self._shown = []             # 当前已显示的译文（用于识别截图里的自身回显）
        self._blocks = []            # 当前译文块的位置（同上，按坐标兜底）
        self._draw_key = None        # 上一次绘制的译文（内容没变就不重绘，防闪烁）
        self._frame = None
        self._drag = None
        self._pinned = True
        self._status = "准备中…"
        self._fonts = {}
        self._alpha = DEFAULT_ALPHA
        self._mode = "auto"          # auto: 外语→中；pinyin: 拼音→中；zh_en: 中→英
        self._sliders = {}
        self._slider_items = {}
        self._size = (DEFAULT_W, DEFAULT_H)
        self._geom = (0, 0, DEFAULT_W, DEFAULT_H)
        self._drawn_size = None

        self._ocr = ScreenOcr()
        self._translator = Translator()

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x = (sw - DEFAULT_W) // 2
        y = (sh - DEFAULT_H) // 2

        # 背景层：先建，位于界面层下方
        self._shade = tk.Toplevel(root)
        self._shade.overrideredirect(True)
        self._shade.attributes("-topmost", True)
        self._shade.configure(bg=MAGIC)
        self._shade_canvas = tk.Canvas(self._shade, bg=MAGIC, highlightthickness=0, bd=0)
        self._shade_canvas.pack(fill="both", expand=True)

        # 工具条底色层：单独一层做半透明，控件仍画在界面层上保持清晰
        self._bar = tk.Toplevel(root)
        self._bar.overrideredirect(True)
        self._bar.attributes("-topmost", True)
        self._bar.configure(bg=MAGIC)
        self._bar_canvas = tk.Canvas(self._bar, bg=MAGIC, highlightthickness=0, bd=0)
        self._bar_canvas.pack(fill="both", expand=True)

        # 界面层
        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=MAGIC)
        self._canvas = tk.Canvas(self._win, bg=MAGIC, highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)

        # 先让 Tk 记住初始几何，映射时才不会被默认尺寸覆盖
        self._shade.geometry(f"{DEFAULT_W}x{max(1, DEFAULT_H - BAR_H)}+{x}+{y + BAR_H}")
        self._bar.geometry(f"{DEFAULT_W}x{BAR_H}+{x}+{y}")
        self._win.geometry(f"{DEFAULT_W}x{DEFAULT_H}+{x}+{y}")
        self._set_geometry(x, y, DEFAULT_W, DEFAULT_H)
        self._shade.update_idletasks()
        self._bar.update_idletasks()
        self._win.update_idletasks()

        self._shade.attributes("-transparentcolor", MAGIC)
        self._bar.attributes("-transparentcolor", MAGIC)
        self._win.attributes("-transparentcolor", MAGIC)
        self._bar.attributes("-alpha", BAR_ALPHA)
        self._exclude_from_capture(self._shade)
        self._exclude_from_capture(self._bar)
        self._exclude_from_capture(self._win)
        self._click_through(self._shade)
        self._no_activate(self._bar)
        self._apply_alpha()

        self._shade_canvas.bind("<Configure>", self._on_shade_configure)
        self._bar_canvas.bind("<Configure>", self._on_bar_configure)
        self._bar_canvas.bind("<ButtonPress-1>", self._bar_press)
        self._bar_canvas.bind("<B1-Motion>", self._on_drag)
        self._bar_canvas.bind("<ButtonRelease-1>", self._on_release)
        self._bar_canvas.bind("<Motion>", self._bar_motion)
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Motion>", self._on_cursor)
        self._canvas.bind("<Configure>", self._on_configure)
        self._win.bind("<Escape>", lambda e: self.close())

        self._win.lift()
        self._draw_shade(self._size[0], max(1, self._size[1] - BAR_H))
        self._draw_bar(self._size[0], BAR_H)
        self._draw_ui(*self._size)
        self._win.after(500, self._tick)

    # ---------- 窗口属性 ----------
    def _exclude_from_capture(self, win):
        """让窗口不出现在屏幕截图里，避免把自己显示的译文再识别一遍。"""
        if os.environ.get("LIGHT_TOOL_CAPTURE_OVERLAY") == "1":
            return
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(self._hwnd(win), WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

    def _click_through(self, win):
        """背景层不接收鼠标，点击直接落到下层窗口。"""
        self._set_ex_style(win, WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)

    def _no_activate(self, win):
        """不抢焦点，也不因点击改变层级。"""
        self._set_ex_style(win, WS_EX_NOACTIVATE)

    def _set_ex_style(self, win, bits):
        try:
            hwnd = self._hwnd(win)
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | bits)
        except Exception:
            pass

    def _hwnd(self, win):
        return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()

    def _apply_alpha(self):
        try:
            self._shade.attributes("-alpha", max(0.02, self._alpha / 100))
        except tk.TclError:
            pass

    def _set_geometry(self, x, y, w, h):
        """三层窗口放在同一个批次里同步更新，拖拽时边框不会错位抖动。"""
        self._geom = (x, y, w, h)
        self._size = (w, h)
        rects = (
            (self._shade, (x, y + BAR_H, w, max(1, h - BAR_H))),
            (self._bar, (x, y, w, BAR_H)),
            (self._win, (x, y, w, h)),
        )
        if self._place(rects):
            return
        for win, (gx, gy, gw, gh) in rects:
            win.geometry(f"{gw}x{gh}+{gx}+{gy}")

    def _place(self, rects):
        """一次 DeferWindowPos 提交所有窗口的位置与大小（原子更新）。"""
        try:
            batch = _USER32.BeginDeferWindowPos(len(rects))
            if not batch:
                return False
            for win, (x, y, w, h) in rects:
                batch = _USER32.DeferWindowPos(
                    batch, self._hwnd(win), None, x, y, w, h,
                    SWP_NOZORDER | SWP_NOACTIVATE)
                if not batch:
                    return False
            _USER32.EndDeferWindowPos(batch)
            return True
        except Exception:
            return False

    def _win_rect(self):
        x, y, w, h = self._geom
        return (x, y, x + w, y + h)

    # ---------- 绘图基础 ----------
    @staticmethod
    def _round_pts(x1, y1, x2, y2, r, steps=6):
        """按圆弧采样生成圆角矩形轮廓，保证圆角半径真实可见。"""
        r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        if r <= 0:
            return [x1, y1, x2, y1, x2, y2, x1, y2]
        n = max(2, min(10, int(round(r))))
        pts = []
        for cx, cy, start in ((x1 + r, y1 + r, 180), (x2 - r, y1 + r, 270),
                              (x2 - r, y2 - r, 0), (x1 + r, y2 - r, 90)):
            for i in range(n + 1):
                angle = math.radians(start + 90 * i / n)
                pts.extend((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        return pts

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        return self._canvas.create_polygon(
            self._round_pts(x1, y1, x2, y2, r), **kw)

    def _font(self, size):
        if size not in self._fonts:
            self._fonts[size] = tkfont.Font(family="Microsoft YaHei UI", size=size)
        return self._fonts[size]

    def _fit_font(self, text, max_w, max_h):
        """先按行高定字号，再按宽度微调，必要时允许少量横向溢出。"""
        base = 6
        for size in range(16, 5, -1):
            if self._font(size).metrics("linespace") <= max_h:
                base = size
                break
        for size in range(base, 5, -1):
            if self._font(size).measure(text) <= max_w * 1.7:
                return self._font(size)
        return self._font(6)

    # ---------- 背景层 ----------
    def _on_shade_configure(self, event):
        self._draw_shade(event.width, event.height)

    def _draw_shade(self, w, h):
        if self._closed or w < 8 or h < 8:
            return
        c = self._shade_canvas
        c.delete("all")
        # 下方圆角、上方直角（与工具条无缝衔接）
        c.create_polygon(self._round_pts(0, 0, w, h, RADIUS),
                         fill=BG, outline="")
        c.create_rectangle(0, 0, w, RADIUS + 1, fill=BG, outline="")

    # ---------- 工具条底色层 ----------
    def _on_bar_configure(self, event):
        self._draw_bar(event.width, event.height)

    def _draw_bar(self, w, h):
        if self._closed or w < 8 or h < 8:
            return
        c = self._bar_canvas
        c.delete("all")
        # 上方圆角、下方直角，整条为 40% 透明的玻璃底
        c.create_polygon(self._round_pts(0, 0, w, h, RADIUS),
                         fill=BAR_BG, outline="")
        c.create_rectangle(0, RADIUS, w, h, fill=BAR_BG, outline="")

    def _bar_press(self, event):
        if self._closed:
            return
        # 工具条底色层被点击时会浮到最上层，因此这里直接按界面层上的控件做命中判断，
        # 关闭 / 置顶 / 滑块 / 缩放在任何层级顺序下都能点中
        if self._press_with_tags(event, self._hit_tags(event.x, event.y)):
            return
        gx, gy = self._geom[0], self._geom[1]
        self._drag = {"mode": "move", "x": event.x_root, "y": event.y_root,
                      "gx": gx, "gy": gy}

    def _hit_tags(self, x, y):
        """在界面层上查找该点最上层控件的标签（坐标与工具条层一致）。"""
        items = self._canvas.find_overlapping(x, y, x + 1, y + 1)
        return self._canvas.gettags(items[-1]) if items else ()

    def _bar_motion(self, event):
        if self._drag or self._closed:
            return
        tags = self._hit_tags(event.x, event.y)
        control = any(t in tags for t in
                      ("close", "pin", "mode", "size_slide", "alpha_slide"))
        self._bar_canvas.configure(cursor="hand2" if control else "fleur")

    def _raise_main(self):
        """界面层始终压在工具条底色层之上，控件才不会被玻璃遮住。"""
        if self._closed:
            return
        try:
            ctypes.windll.user32.SetWindowPos(
                self._hwnd(self._win), -1 if self._pinned else 0, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0010,  # NOSIZE | NOMOVE | NOACTIVATE
            )
        except Exception:
            pass

    # ---------- 界面层 ----------
    def _on_configure(self, event):
        if self._closed:
            return
        size = (event.width, event.height)
        if size == self._drawn_size:
            return
        self._size = size
        self._draw_ui(*size)

    def _size_pct(self):
        """0 = 最小窗口，100 = 满屏宽度。"""
        if self._drag and self._drag.get("mode") == "size":
            return self._drag.get("value", 0)
        sw = max(MIN_W + 1, self.root.winfo_screenwidth())
        pct = (self._size[0] - MIN_W) / (sw - MIN_W) * 100
        return max(0, min(100, round(pct)))

    def _draw_switch(self, x, y, on):
        c = self._canvas
        w, h, r = 38, 22, 11
        self._round_rect(x, y, x + w, y + h, r,
                         fill=ACCENT if on else PILL, outline="",
                         tags=("ui", "barbg", "pin"))
        kx = x + w - r if on else x + r
        c.create_oval(kx - 9, y + 2, kx + 9, y + h - 2, fill=FG, outline="",
                      tags=("ui", "barbg", "pin"))

    def _draw_slider(self, tag, label, x, y, width, value):
        c = self._canvas
        value = int(value)
        c.create_text(x, y, text=label, anchor="w", fill=BAR_TEXT,
                      font=self._font(9), tags=("ui", "barbg", tag))
        x1 = x + 34
        x2 = max(x1 + 24, x + width - 40)
        self._round_rect(x1, y - 2, x2, y + 2, 2, fill=TRACK, outline="",
                         tags=("ui", "barbg", tag))
        kx = x1 + (x2 - x1) * value / 100
        fill = c.create_polygon(self._round_pts(x1, y - 2, max(x1 + 1, kx), y + 2, 2),
                                fill=ACCENT, outline="",
                                tags=("ui", "barbg", tag))
        knob = c.create_oval(kx - 7, y - 7, kx + 7, y + 7, fill=FG, outline="",
                             tags=("ui", "barbg", tag))
        pct = c.create_text(x + width, y, text=f"{value}%", anchor="e", fill=FG,
                            font=self._font(9), tags=("ui", "barbg", tag))
        self._sliders[tag] = (x1, x2)
        self._slider_items[tag] = {"y": y, "x1": x1, "x2": x2,
                                   "fill": fill, "knob": knob, "pct": pct}

    def _set_slider(self, tag, value):
        """拖动时只挪滑块图形，不整体重绘。"""
        items = self._slider_items.get(tag)
        if not items:
            return
        value = max(0, min(100, int(value)))
        x1, x2, y = items["x1"], items["x2"], items["y"]
        kx = x1 + (x2 - x1) * value / 100
        self._canvas.coords(items["fill"],
                            *self._round_pts(x1, y - 2, max(x1 + 1, kx), y + 2, 2))
        self._canvas.coords(items["knob"], kx - 7, y - 7, kx + 7, y + 7)
        self._canvas.itemconfigure(items["pct"], text=f"{value}%")

    def _draw_ui(self, w=None, h=None):
        if self._closed:
            return
        w = w or self._size[0]
        h = h or self._size[1]
        if w < 40 or h < 40:
            return
        self._size = (w, h)
        self._drawn_size = (w, h)
        c = self._canvas

        c.delete("ui")
        # 边框（不随透明度变化）
        self._round_rect(1, 1, w - 1, h - 1, RADIUS - 1, fill="", outline=OUTLINE,
                         tags=("ui", "backdrop"))

        # 关闭按钮
        cx = w - 28
        c.create_oval(cx - 12, 15, cx + 12, 39, fill=PILL, outline="",
                      tags=("ui", "barbg", "close"))
        c.create_text(cx, 27, text="✕", fill=SUB, font=self._font(9),
                      tags=("ui", "barbg", "close"))

        # 置顶开关
        sw_x = cx - 12 - 10 - 38
        self._draw_switch(sw_x, 16, self._pinned)
        c.create_text(sw_x - 8, 27, text="置顶", anchor="e", fill=BAR_TEXT,
                      font=self._font(8), tags=("ui", "barbg", "pin"))

        # 翻译方向：外语→中 / 拼音→中 / 中→英
        labels = (("auto", "外语→中"), ("pinyin", "拼音→中"), ("zh_en", "中→英"))
        f8 = self._font(8)
        widths = [f8.measure(text) + 14 for _tag, text in labels]
        mode_right = sw_x - 34
        mode_left = mode_right - (sum(widths) + 4 * (len(labels) - 1))
        x = mode_left
        for (tag, text), seg_w in zip(labels, widths):
            self._round_rect(x, 16, x + seg_w, 38, 11,
                             fill=ACCENT if self._mode == tag else PILL, outline="",
                             tags=("ui", "barbg", "mode", f"mode_{tag}"))
            c.create_text(x + seg_w / 2, 27, text=text, fill=FG, font=f8,
                          tags=("ui", "barbg", "mode", f"mode_{tag}"))
            x += seg_w + 4

        # 状态：指示灯常亮（窗口过窄时让位给模式按钮）
        if mode_left >= 50:
            c.create_oval(20, 22, 30, 32, fill=GREEN, outline="",
                          tags=("ui", "barbg", "led"))
        limit = int((mode_left - 50) / 15)
        if limit >= 4:
            status = self._status
            if len(status) > limit:
                status = status[:limit - 1] + "…"
            c.create_text(40, 27, text=status, anchor="w", fill=FG,
                          font=self._font(10), tags=("ui", "barbg"))

        # 两行滑块
        gap = 18
        group_w = max(120, (w - 32 - gap) / 2)
        self._sliders = {}
        self._slider_items = {}
        self._draw_slider("size_slide", "大小", 18, 62, group_w, self._size_pct())
        self._draw_slider("alpha_slide", "透明", 18 + group_w + gap, 62, group_w, self._alpha)

        # 边缘抓手：右边缘只调宽、下边缘只调高、右下角同时调
        self._round_rect(w - 8, h / 2 - 26, w - 3, h / 2 + 26, 2, fill=GRIP,
                         outline="", tags=("ui", "grip"))
        self._round_rect(w / 2 - 26, h - 8, w / 2 + 26, h - 3, 2, fill=GRIP,
                         outline="", tags=("ui", "grip"))
        c.create_oval(w - 26, h - 26, w - 8, h - 8, fill=ACCENT, outline="",
                      tags=("ui", "knob"))
        c.create_oval(w - 20, h - 20, w - 14, h - 14, fill=FG, outline="",
                      tags=("ui", "knob"))

        c.tag_lower("backdrop")

    def _draw_blocks(self, items):
        if self._closed:
            return
        c = self._canvas
        c.delete("blocks")
        win_w = self._size[0]
        for item in items:
            x, y, w, h = item["rect"]
            pad = 3
            f = self._fit_font(item["text"], w + pad * 2 - 4, h + 4)
            tw = f.measure(item["text"])
            if tw <= win_w - 12:
                left = max(6, min(x + w / 2 - tw / 2, win_w - tw - 6))
                c.create_text(left, y + h / 2, text=item["text"], fill=item["fg"],
                              font=f, anchor="w", tags="blocks")
            else:
                c.create_text(win_w / 2, y + h / 2, text=item["text"], fill=item["fg"],
                              font=f, width=max(12, win_w - 12), tags="blocks")
        c.tag_raise("ui")
        c.tag_lower("backdrop")

    # ---------- 交互 ----------
    def _start_resize(self, event, mode):
        x, y, w, h = self._geom
        self._drag = {"mode": mode, "x": event.x_root, "y": event.y_root,
                      "gx": x, "gy": y, "w": w, "h": h}

    def _begin_slider(self, mode, event):
        self._drag = {
            "mode": mode,
            "base_w": self._size[0],
            "base_h": self._size[1],
            "value": self._size_pct() if mode == "size" else self._alpha,
        }
        tag = "size_slide" if mode == "size" else "alpha_slide"
        bounds = self._sliders.get(tag)
        if bounds:
            # 轨道固定为按下时的屏幕坐标，窗口变大时不会来回跳
            gx = self._geom[0]
            self._drag["slide"] = (bounds[0] + gx, bounds[1] + gx)
        self._drag_slider(event.x_root)

    def _drag_slider(self, x_root):
        d = self._drag
        slide = d.get("slide")
        if not slide:
            return
        x1, x2 = slide
        value = max(0, min(100, round((x_root - x1) / max(1, x2 - x1) * 100)))
        if value == d["value"]:
            return
        d["value"] = value

        if d["mode"] == "alpha":
            self._alpha = value
            self._apply_alpha()
            self._set_slider("alpha_slide", value)
            return

        sw = max(MIN_W + 1, self.root.winfo_screenwidth())
        target_w = max(MIN_W, round(MIN_W + (sw - MIN_W) * value / 100))
        ratio = d["base_h"] / max(1, d["base_w"])
        target_h = max(MIN_H, round(target_w * ratio))
        self._set_geometry(self._geom[0], self._geom[1], target_w, target_h)
        self._set_slider("size_slide", value)

    def _on_press(self, event):
        if self._closed:
            return
        self._press_with_tags(event, self._canvas.gettags("current"))

    def _press_with_tags(self, event, tags):
        """按命中到的控件标签处理点击，返回是否已处理。"""
        if self._closed:
            return True
        if "close" in tags:
            self.close()
            return True
        if "pin" in tags:
            self._toggle_pin()
            return True
        if "mode" in tags:
            for tag in ("mode_auto", "mode_pinyin", "mode_zh_en"):
                if tag in tags:
                    self._set_mode(tag[5:])
                    break
            return True
        if "size_slide" in tags or "alpha_slide" in tags:
            self._begin_slider("size" if "size_slide" in tags else "alpha", event)
            return True

        x, y = event.x, event.y
        w, h = self._size
        if "knob" in tags or (x >= w - 10 and y >= h - 10):
            self._start_resize(event, "resize_wh")
            return True
        if x >= w - 10:
            self._start_resize(event, "resize_w")
            return True
        if y >= h - 10:
            self._start_resize(event, "resize_h")
            return True
        if "barbg" in tags or "backdrop" in tags or "blocks" in tags:
            gx, gy, _w, _h = self._geom
            self._drag = {"mode": "move", "x": event.x_root, "y": event.y_root,
                          "gx": gx, "gy": gy}
            return True
        return False

    def _on_drag(self, event):
        if not self._drag or self._closed:
            return
        d = self._drag
        if d["mode"] in ("size", "alpha"):
            self._drag_slider(event.x_root)
            return
        dx, dy = event.x_root - d["x"], event.y_root - d["y"]
        if d["mode"] == "move":
            self._set_geometry(d["gx"] + dx, d["gy"] + dy, self._size[0], self._size[1])
            return

        w, h = d["w"], d["h"]
        if d["mode"] in ("resize_w", "resize_wh"):
            w = max(MIN_W, w + dx)
        if d["mode"] in ("resize_h", "resize_wh"):
            h = max(MIN_H, h + dy)
        self._set_geometry(d["gx"], d["gy"], w, h)

    def _on_release(self, event):
        self._drag = None
        if not self._closed:
            self._win.after(0, self._raise_main)

    def _on_cursor(self, event):
        if self._drag:
            return
        tags = self._canvas.gettags("current")
        x, y = event.x, event.y
        w, h = self._size
        if any(t in tags for t in
               ("close", "pin", "mode", "size_slide", "alpha_slide")):
            cursor = "hand2"
        elif "knob" in tags or (x >= w - 10 and y >= h - 10):
            cursor = "size_nw_se"
        elif "grip" in tags and x >= w - 10:
            cursor = "sb_h_double_arrow"
        elif "grip" in tags:
            cursor = "sb_v_double_arrow"
        else:
            cursor = ""
        self._canvas.configure(cursor=cursor)

    def _set_mode(self, mode):
        if self._mode == mode:
            return
        self._mode = mode
        self._signature = None
        self._frame = None
        self._shown = []
        self._blocks = []
        self._draw_key = None
        self._status = "切换中…"
        self._canvas.delete("blocks")
        self._draw_ui(*self._size)

    def _toggle_pin(self):
        self._pinned = not self._pinned
        for win in (self._shade, self._bar, self._win):
            win.attributes("-topmost", self._pinned)
        if self._pinned:
            self._win.lift()
        self._draw_ui(*self._size)

    # ---------- 自动识别 + 翻译 ----------
    def _tick(self):
        if self._closed:
            return
        if not self._busy:
            self._busy = True
            threading.Thread(target=self._work, args=(self._win_rect(),), daemon=True).start()
        self._win.after(INTERVAL_MS, self._tick)

    def _frame_changed(self, image):
        """缩略图对比：画面基本没动时跳过 OCR，避免持续占用 CPU。"""
        view = np.asarray(image.convert("L").resize(FRAME_SIZE), dtype=np.int16)
        last = self._frame
        self._frame = view
        if last is None or last.shape != view.shape:
            return True
        changed = int((np.abs(view - last) > FRAME_DELTA).sum())
        return changed > view.size // 400

    def _work(self, bbox):
        try:
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            if not self._frame_changed(image):
                return
            lines = []
            echoed = False
            for line in self._ocr.recognize_lines(image):
                text = clean_text(line["text"])
                if not text or line["w"] <= 4 or line["h"] <= 4:
                    continue
                if line["y"] < BAR_H:
                    # 工具条区域被自己的界面遮住，忽略这部分识别结果
                    continue
                if self._is_echo(text) or self._echo_at(text, line):
                    echoed = True
                    continue
                lines.append({"rect": (line["x"], line["y"], line["w"], line["h"]), "text": text})

            if not lines:
                if echoed:
                    # 画面里只剩自己刚显示的译文，保持现有结果，避免闪一下就清屏
                    return
                self._signature = None
                self._ui(self._apply, None, [], "未识别到文字")
                return

            signature = (self._mode, tuple(l["text"] for l in lines))
            if signature == self._signature:
                return

            try:
                results = self._translator.translate_lines(
                    [l["text"] for l in lines], self._mode)
            except TranslateError:
                if not echoed:
                    self._ui(self._apply, None, [], "翻译失败")
                return

            items = []
            for line, (text, _target) in zip(lines, results):
                if not text:
                    continue
                fg = self._swatch(image, line["rect"])
                items.append({"rect": line["rect"], "text": text, "fg": fg})

            targets = {t for _text, t in results if t}
            if not items:
                if echoed:
                    return
                status = "无可翻译文字" if not targets else "翻译失败"
                self._ui(self._apply, None, [], status)
                return

            if self._mode == "pinyin":
                status = "拼音 → 中文"
            elif self._mode == "zh_en":
                status = "中 → 英"
            else:
                status = "→ 中文"

            self._signature = signature
            self._ui(self._apply, signature, items, status)
        except OcrError as exc:
            self._ui(self._apply, None, [], f"识别失败：{exc}")
        except Exception as exc:
            self._ui(self._apply, None, [], f"出错：{exc}")
        finally:
            self._busy = False

    def _apply(self, signature, items, status):
        if self._closed:
            return
        if signature is None:
            self._signature = None
        # OCR 每轮的行坐标会有 1–2px 抖动，坐标按 8px 取整后再比对，
        # 内容没变就完全不重绘，避免删除/重建造成的闪烁
        key = tuple((item["text"], round(item["rect"][0] / 8),
                     round(item["rect"][1] / 8)) for item in items)
        if key != self._draw_key:
            self._draw_key = key
            self._draw_blocks(items)
            self._shown = [self._norm_text(item["text"]) for item in items]
            self._blocks = [item["rect"] for item in items]
        elif status == self._status:
            return
        self._status = status
        self._draw_ui(*self._size)

    @staticmethod
    def _norm_text(text):
        return "".join(ch for ch in text.lower() if ch.isalnum())

    def _is_echo(self, text):
        """截图里读到自己刚画的译文时忽略，避免把译文当成原文再识别一遍。"""
        if not self._shown:
            return False
        value = self._norm_text(text)
        if len(value) < 2:
            return False
        for shown in self._shown:
            if value == shown or (len(shown) >= 2 and (value in shown or shown in value)):
                return True
        return False

    def _echo_at(self, text, line):
        """译文块所在位置读到的文字：按语种判断是不是自己画的译文。

        OCR 对自身译文偶尔会读错一两个字，文本比对会漏掉，这里按位置兜底：
        外语/拼音模式下这块位置显示的只能是中文，「中→英」下只能是英文。
        """
        if not self._blocks:
            return False
        cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
        for bx, by, bw, bh in self._blocks:
            if (bx - 6 <= line["x"] <= bx + bw + 6
                    and by - 6 <= line["y"] <= by + bh + 6):
                return (not cjk) if self._mode == "zh_en" else cjk
        return False

    def _ui(self, func, *args):
        if not self._closed:
            try:
                self._win.after(0, lambda: func(*args))
            except Exception:
                pass

    @staticmethod
    def _swatch(image, rect):
        """按文字行上下边缘的亮度挑字色，不画底色时译文也能看清。"""
        x, y, w, h = (int(v) for v in rect)
        samples = []
        for y1, y2 in ((y - 6, y - 1), (y + h + 1, y + h + 6)):
            if y2 <= 0 or y1 >= image.height:
                continue
            crop = image.crop((max(0, x), max(0, y1), min(image.width, x + w),
                               min(image.height, y2))).convert("RGB")
            samples.extend(crop.getdata())
        if not samples:
            rgb = (18, 20, 26)
        else:
            n = len(samples)
            rgb = (int(sum(p[0] for p in samples) / n),
                   int(sum(p[1] for p in samples) / n),
                   int(sum(p[2] for p in samples) / n))
        lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        return DARK_TEXT if lum > 140 else FG

    # ---------- 关闭 ----------
    def close(self):
        if self._closed:
            return
        self._closed = True
        self._ocr.close()
        for win in (self._win, self._bar, self._shade):
            try:
                win.destroy()
            except Exception:
                pass
        self.on_close()
