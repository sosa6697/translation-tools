"""轻工具：启动器。"""

import ctypes
import tkinter as tk
from tkinter import font as tkfont

MAGIC = "#010203"
PANEL = "#0F1116"
BORDER = "#242833"
CARD = "#171A22"
CARD_HOVER = "#1F2430"
FG = "#F2F4F8"
SUB = "#8A92A3"
ACCENT = "#6EA8FF"
DARK = "#0B0D12"

W, H = 480, 340

CARDS = [
    ("c1", "翻译模式", "拖到游戏聊天框\n屏幕文字原位翻译"),
]


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.configure(bg=MAGIC)
        self._center(W, H)

        self._canvas = tk.Canvas(self.root, bg=MAGIC, highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self._fonts = {}
        self._drag = None
        self._hover = None
        self._overlay = None

        self.root.update_idletasks()
        try:
            self.root.attributes("-transparentcolor", MAGIC)
        except tk.TclError:
            self.root.configure(bg=PANEL)

        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._release)
        self._canvas.bind("<Motion>", self._on_motion)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

        self._draw()

    def _font(self, size, bold=False):
        key = (size, bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(family="Microsoft YaHei UI", size=size,
                                           weight="bold" if bold else "normal")
        return self._fonts[key]

    def _center(self, w, h):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    def _round(self, x1, y1, x2, y2, r, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        c = self._canvas
        c.delete("all")
        self._round(3, 3, W - 3, H - 3, 24, fill=PANEL, outline=BORDER, tags="panelbg")

        c.create_text(32, 46, text="轻工具", anchor="w", fill=FG,
                      font=self._font(18, True), tags="panelbg")
        c.create_text(32, 76, text="拖到游戏聊天框即可自动翻译", anchor="w", fill=SUB,
                      font=self._font(9), tags="panelbg")

        self._round(W - 52, 22, W - 18, 46, 12, fill=CARD, outline="", tags=("close",))
        c.create_text(W - 35, 34, text="✕", fill=SUB, font=self._font(10), tags=("close",))

        left = (W - (len(CARDS) * 212 + (len(CARDS) - 1) * 12)) / 2
        for index, (tag, title, desc) in enumerate(CARDS):
            x1 = left + index * 224
            x2 = x1 + 212
            y1, y2 = 106, 262
            fill = CARD_HOVER if self._hover == tag else CARD
            self._round(x1, y1, x2, y2, 18, fill=fill, outline="", tags=(tag,))
            cx = (x1 + x2) / 2
            self._round(cx - 14, y1 + 38, cx + 14, y1 + 41, 2,
                        fill=ACCENT, outline="", tags=(tag,))
            c.create_text(cx, y1 + 66, text=title, fill=FG,
                          font=self._font(12, True), tags=(tag,))
            c.create_text(cx, y1 + 100, text=desc, fill=SUB, justify="center",
                          font=self._font(8), tags=(tag,))

    # ---------- 交互 ----------
    def _on_press(self, event):
        tags = self._canvas.gettags("current")
        if "close" in tags:
            self.root.destroy()
        elif "c1" in tags:
            self.open_translate()
        elif "panelbg" in tags:
            self._drag = (event.x_root, event.y_root,
                          self.root.winfo_rootx(), self.root.winfo_rooty())

    def _on_drag(self, event):
        if not self._drag:
            return
        x, y, gx, gy = self._drag
        self.root.geometry(f"+{gx + event.x_root - x}+{gy + event.y_root - y}")

    def _release(self, event):
        self._drag = None

    def _on_motion(self, event):
        tags = self._canvas.gettags("current")
        hover = next((t for t in ("c1",) if t in tags), None)
        if hover != self._hover:
            self._hover = hover
            self._draw()
        cursor = "hand2" if (hover or "close" in tags) else ""
        self._canvas.configure(cursor=cursor)

    # ---------- 模式 ----------
    def open_translate(self):
        if self._overlay:
            return
        self.root.withdraw()
        from translate_window import TranslateOverlay
        self._overlay = TranslateOverlay(self.root, self._back)

    def _back(self):
        self._overlay = None
        self.root.deiconify()
        self.root.lift()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    enable_dpi_awareness()
    App().run()
