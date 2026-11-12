"""今日备忘 — Windows desktop calendar, editable or click-through."""
from __future__ import annotations

import calendar
import ctypes as ct
from ctypes import wintypes as wt
from datetime import date
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, filedialog, font as tkfont
import winreg

from PIL import Image, ImageDraw, ImageTk, ImageOps
import pystray
from storage import Store, MIN_WIDTH, MIN_HEIGHT
from wallpaper import read_wallpaper, keep_wallpaper

APP_NAME = "今日备忘"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DesktopMemoTemplate"
BG, PANEL, TEXT, MUTED = "#F5F4EE", "#FFFFFF", "#233F35", "#7B877D"
ACCENT, SOFT, LINE = "#32765B", "#DFEAE0", "#E3E6DD"
FONT = "Microsoft YaHei UI"

user32 = ct.WinDLL("user32", use_last_error=True)
kernel32 = ct.WinDLL("kernel32", use_last_error=True)
user32.GetAncestor.argtypes, user32.GetAncestor.restype = [wt.HWND, wt.UINT], wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ct.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ct.c_int, ct.c_long]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.UINT]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.RegisterHotKey.argtypes = [wt.HWND, ct.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ct.c_int]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetMessageW.argtypes = [ct.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
kernel32.CreateMutexW.argtypes, kernel32.CreateMutexW.restype = [ct.c_void_p, wt.BOOL, wt.LPCWSTR], wt.HANDLE
kernel32.CreateEventW.argtypes, kernel32.CreateEventW.restype = [ct.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR], wt.HANDLE
kernel32.SetEvent.argtypes = [wt.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def icon_image():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((3, 6, 60, 61), radius=13, fill=ACCENT)
    draw.rounded_rectangle((10, 19, 53, 53), radius=6, fill=BG)
    draw.line((22, 4, 22, 15), fill=BG, width=5)
    draw.line((42, 4, 42, 15), fill=BG, width=5)
    draw.line((21, 35, 29, 43, 44, 28), fill=ACCENT, width=5)
    return image


class App:
    def __init__(self, root, store, activation=None, testing=False):
        self.root, self.store, self.activation = root, store, activation
        self.testing = testing
        self.editing = False
        self.compact = False
        self.week_only = False
        self.layout_signature = None
        self.preview_settings = None
        self.wallpaper_source = None
        self.wallpaper_image = None
        self.wallpaper_cache = None
        self.daily_photo = None
        self.current_day = date.today()
        self.commands = queue.Queue()
        self.undo_record = None
        self.settings_window = None
        self.tray = None
        self.hotkey_thread_id = None
        self.hotkey_registered = False
        self.status_after = None
        self.root.title(APP_NAME)
        self.window_icon = ImageTk.PhotoImage(icon_image(), master=root)
        self.root.iconphoto(True, self.window_icon)
        self.root.withdraw()
        self.root.configure(bg=LINE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.daily)
        self.root.bind("<Escape>", lambda event: self.daily())
        self.root.bind("<Control-Return>", lambda event: self.add_task())
        self.root.report_callback_exception = self.callback_error
        self.normal_font = tkfont.Font(family=FONT, size=10)
        self.done_font = tkfont.Font(family=FONT, size=10, overstrike=True)
        self.build()
        self.apply_size()
        self.render_calendar()
        self.render_tasks()
        self.root.deiconify()
        self.root.update_idletasks()
        self.set_mode(False)
        if not testing:
            self.start_tray()
            threading.Thread(target=self.hotkeys, daemon=True).start()
            self.root.after(100, self.poll)
        if store.recovery_message:
            self.root.after(500, lambda: (self.set_mode(True), messagebox.showwarning(APP_NAME, store.recovery_message, parent=root)))

    def label(self, parent, text="", size=10, color=TEXT, bg=BG, **kwargs):
        return tk.Label(parent, text=text, font=(FONT, size), fg=color, bg=bg, **kwargs)

    def button(self, parent, text, command, primary=False, **kwargs):
        return tk.Button(parent, text=text, command=command, font=(FONT, 9),
                         bg=ACCENT if primary else SOFT, fg="white" if primary else ACCENT,
                         activebackground=TEXT if primary else LINE,
                         activeforeground="white" if primary else TEXT,
                         relief="flat", bd=0, cursor="hand2", padx=11, pady=6, **kwargs)

    def build(self):
        self.body = tk.Frame(self.root, bg=BG, padx=18, pady=14)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self.heading = tk.Frame(self.body, bg=BG)
        self.heading.pack(fill="x")
        self.brand = self.label(self.heading, "今 日 备 忘", 11)
        self.brand.pack(side="left")
        self.mode_tag = self.label(self.heading, "日常 · 点击穿透", 8, MUTED)
        self.mode_tag.pack(side="right")
        self.date_panel = tk.Frame(self.body, bg=BG)
        self.date_panel.pack(fill="x", pady=(8, 4))
        self.day_label = self.label(self.date_panel, size=36)
        self.day_label.pack(side="left")
        self.month_label = self.label(self.date_panel, justify="left", size=10)
        self.month_label.pack(side="left", padx=14)
        for widget in (self.heading, self.brand, self.date_panel, self.day_label, self.month_label):
            widget.bind("<ButtonPress-1>", self.drag_start)
            widget.bind("<B1-Motion>", self.drag_move)
        self.calendar_frame = tk.Frame(self.body, bg=BG)
        self.calendar_frame.pack(fill="x", pady=(0, 9))
        summary = self.summary = tk.Frame(self.body, bg=BG)
        summary.pack(fill="x", pady=(4, 8))
        self.summary_title = self.label(summary, "当天计划", 11)
        self.summary_title.pack(side="left")
        self.count_label = self.label(summary, color=MUTED, size=9)
        self.count_label.pack(side="right")
        self.progress_canvas = tk.Canvas(self.body, height=3, bg=LINE, highlightthickness=0)
        self.progress_canvas.pack(fill="x", pady=(0, 10))
        self.progress_canvas.bind("<Configure>", lambda event: self.draw_progress())
        # Bottom controls are packed before the task area to keep them visible when resizing.
        self.footer = tk.Frame(self.body, bg=BG)
        self.footer.pack(side="bottom", fill="x", pady=(10, 0))
        self.hint = self.label(self.footer, "Ctrl + Alt + M  切换编辑", 8, MUTED, anchor="w")
        self.hint.pack(side="left", fill="x", expand=True)
        self.grip = self.label(self.footer, "◢", 12, MUTED, cursor="sizing")
        self.grip.bind("<ButtonPress-1>", self.resize_start)
        self.grip.bind("<B1-Motion>", self.resize_move)
        self.grip.bind("<ButtonRelease-1>", self.resize_end)
        self.editor = tk.Frame(self.body, bg=BG)
        self.label(self.editor, "写下今天要做的事", 9, MUTED, anchor="w").pack(fill="x", pady=(8, 5))
        self.entry = tk.Entry(self.editor, font=(FONT, 10), bg=PANEL, fg=TEXT, relief="flat",
                              insertbackground=ACCENT, highlightthickness=1, highlightbackground=LINE,
                              highlightcolor=ACCENT)
        self.entry.pack(fill="x", ipady=7)
        self.entry.bind("<Return>", lambda event: self.add_task())
        row = tk.Frame(self.editor, bg=BG)
        row.pack(fill="x", pady=(7, 7))
        self.label(row, "时间（选填）", 8, MUTED).pack(side="left")
        self.time_entry = tk.Entry(row, width=6, font=(FONT, 9), bg=PANEL, relief="flat", fg=TEXT)
        self.time_entry.pack(side="left", padx=(5, 0), ipady=5)
        self.time_entry.bind("<Return>", lambda event: self.add_task())
        self.button(row, "＋ 添加", self.add_task, primary=True).pack(side="right")
        controls = tk.Frame(self.editor, bg=BG)
        controls.pack(fill="x")
        self.button(controls, "外观", self.open_settings).pack(side="left")
        self.undo_button = self.button(controls, "撤销删除", self.undo_delete)
        self.undo_button.pack(side="left", padx=5)
        self.undo_button.configure(state="disabled")
        self.button(controls, "收起 · 日常", self.daily, primary=True).pack(side="right")
        self.status = self.label(self.editor, "修改后自动保存", 8, MUTED, anchor="w", wraplength=340)
        self.status.pack(fill="x", pady=(4, 0))
        self.task_area = tk.Frame(self.body, bg=BG)
        self.task_area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.task_area, bg=BG, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(self.task_area, orient="vertical", command=self.canvas.yview)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.task_frame = tk.Frame(self.canvas, bg=BG)
        self.canvas_window = self.canvas.create_window(0, 0, anchor="nw", window=self.task_frame)
        self.task_frame.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.task_width)
        self.root.bind_all("<MouseWheel>", self.wheel)
        self.root.bind("<Configure>", self.adapt_layout)
        # A canvas lets the daily view display the wallpaper behind all text.
        # The regular widgets remain the opaque, interactive task editor.
        self.daily_canvas = tk.Canvas(self.root, bg=BG, highlightthickness=1, highlightbackground=LINE)

    def active_settings(self):
        return self.preview_settings if self.preview_settings is not None else self.store.data["settings"]

    def adapt_layout(self, event):
        if event.widget is not self.root:
            return
        self.update_layout(event.width, event.height)
        self.render_daily()

    def update_layout(self, width, height):
        compact = not self.editing
        week_only = not self.editing and self.active_settings().get("calendar_mode", "date") == "week"
        signature = (compact, week_only, self.editing)
        if signature == self.layout_signature:
            return
        self.layout_signature = signature
        self.compact, self.week_only = compact, week_only
        self.body.configure(padx=10 if compact else 18, pady=8 if compact else 14)
        if compact:
            self.heading.pack_forget()
        else:
            self.heading.pack(fill="x", before=self.date_panel)
        self.date_panel.pack_configure(pady=(0, 2) if compact else (8, 4))
        self.day_label.configure(font=(FONT, 12 if compact else 36))
        self.month_label.configure(font=(FONT, 8 if compact else 10))
        self.month_label.pack_configure(padx=7 if compact else 14)
        self.summary_title.configure(font=(FONT, 9 if compact else 11))
        self.count_label.configure(font=(FONT, 8 if compact else 9))
        self.summary.pack_configure(pady=(2, 4) if compact else (4, 8))
        self.progress_canvas.pack_configure(pady=(0, 5) if compact else (0, 10))
        self.footer.pack_configure(pady=(3, 0) if compact else (10, 0))
        if week_only:
            self.calendar_frame.pack_configure(pady=(0, 3) if compact else (0, 9))
        else:
            self.calendar_frame.pack_forget()
        self.update_hint()
        self.render_calendar()
        if hasattr(self, "task_labels"):
            self.render_tasks()

    def update_hint(self):
        if self.settings_window and self.settings_window.winfo_exists():
            text = "预览中 · 请保存或取消" if self.compact else "正在实时预览 · 在设置窗口保存或取消"
        elif self.editing:
            text = "拖动标题移动 · 右下角调整大小"
        else:
            text = "Ctrl+Alt+M  编辑" if self.compact else "Ctrl + Alt + M  切换编辑"
        self.hint.configure(text=text)

    def task_width(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)
        for widget in getattr(self, "task_labels", []):
            widget.configure(wraplength=max(60, event.width - (150 if self.editing else 90)))

    def render_calendar(self):
        today = self.current_day
        self.day_label.configure(text=f"{today.month}月{today.day}日" if self.compact else f"{today.day:02d}")
        self.month_label.configure(text=f"星期{'一二三四五六日'[today.weekday()]}" if self.compact else
                                   f"{today.year} 年 {today.month} 月\n星期{'一二三四五六日'[today.weekday()]}")
        for widget in self.calendar_frame.winfo_children():
            widget.destroy()
        for col, day_name in enumerate("一二三四五六日"):
            self.calendar_frame.columnconfigure(col, weight=1)
            self.label(self.calendar_frame, day_name, 8, MUTED).grid(row=0, column=col, sticky="ew")
        weeks = calendar.monthcalendar(today.year, today.month)
        if self.week_only:
            weeks = [week for week in weeks if today.day in week]
        for row, week in enumerate(weeks, start=1):
            for col, day in enumerate(week):
                selected = day == today.day
                self.label(self.calendar_frame, str(day) if day else "", 8 if self.compact else 9,
                           "white" if selected else (MUTED if col > 4 else TEXT),
                           ACCENT if selected else BG).grid(row=row, column=col, sticky="ew", pady=0 if self.compact else 1)

    def render_tasks(self):
        for widget in self.task_frame.winfo_children():
            widget.destroy()
        self.task_labels = []
        tasks = self.store.tasks(self.current_day.isoformat())
        done = sum(task["done"] for task in tasks)
        self.count_label.configure(text=f"已完成 {done} / {len(tasks)}")
        self.draw_progress()
        if not tasks:
            empty = tk.Frame(self.task_frame, bg=BG, pady=2 if self.compact else 16)
            empty.pack(fill="both", expand=True)
            self.label(empty, "今天，慢慢来" if self.compact else "留一点空间，给今天",
                       10 if self.compact else 12).pack(pady=(0, 2) if self.compact else (5, 8))
            if not self.compact:
                self.label(empty, "添加你的第一条计划\n按 Ctrl + Alt + M 开始", 9, MUTED, justify="center").pack()
        for task in sorted(tasks, key=lambda t: (t["done"], t.get("time", "") or "99:99")):
            row = tk.Frame(self.task_frame, bg=SOFT if task["done"] else PANEL, padx=9, pady=8)
            row.pack(fill="x", pady=(0, 6))
            row_bg = row.cget("bg")
            actions = tk.Frame(row, bg=row_bg)
            actions.pack(side="right", padx=(5, 0))
            complete = self.button(actions, "撤回" if task["done"] else "完成",
                                   lambda tid=task["id"]: self.toggle_task(tid))
            complete.configure(padx=7, pady=3, state="normal" if self.editing else "disabled",
                               disabledforeground=MUTED)
            complete.pack(side="left")
            if self.editing:
                delete = self.button(actions, "×", lambda tid=task["id"]: self.delete_task(tid))
                delete.configure(bg=row_bg, fg=MUTED, padx=6, pady=3)
                delete.pack(side="left", padx=(3, 0))
            content = tk.Frame(row, bg=row_bg)
            content.pack(side="left", fill="both", expand=True)
            if task.get("time"):
                self.label(content, task["time"], 8, ACCENT, row_bg, anchor="w").pack(fill="x")
            label = tk.Label(content, text=task["text"], bg=row_bg, fg=MUTED if task["done"] else TEXT,
                             font=self.done_font if task["done"] else self.normal_font,
                             anchor="w", justify="left", wraplength=max(60, self.canvas.winfo_width() - (150 if self.editing else 90)))
            label.pack(fill="x")
            self.task_labels.append(label)
        self.root.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.render_daily()

    def render_daily(self):
        if not hasattr(self, "daily_canvas") or self.editing:
            return
        canvas = self.daily_canvas
        canvas.place(x=1, y=1, relwidth=1, relheight=1, width=-2, height=-2)
        self.root.tk.call("raise", canvas._w)
        width, height = self.root.winfo_width() - 2, self.root.winfo_height() - 2
        if width < 10 or height < 10:
            return
        canvas.delete("all")
        settings = self.active_settings()
        path = settings.get("wallpaper", "")
        if path != self.wallpaper_source:
            self.wallpaper_source = path
            self.wallpaper_cache = None
            try:
                self.wallpaper_image = read_wallpaper(path) if path else None
            except (OSError, ValueError, Image.DecompressionBombError):
                self.wallpaper_image = None
                logging.warning("Wallpaper unavailable; using the plain background")
        if self.wallpaper_image is not None:
            key = (path, width, height)
            if self.wallpaper_cache != key:
                fitted = ImageOps.fit(self.wallpaper_image, (width, height), method=Image.Resampling.LANCZOS)
                # A light veil keeps text legible on both dark and busy photos.
                fitted = Image.blend(fitted, Image.new("RGB", fitted.size, BG), .55)
                self.daily_photo = ImageTk.PhotoImage(fitted, master=self.root)
                self.wallpaper_cache = key
            canvas.create_image(0, 0, image=self.daily_photo, anchor="nw")
        else:
            self.daily_photo = None
        margin = 12
        today = self.current_day
        canvas.create_text(margin, 11, anchor="nw", fill=TEXT, font=(FONT, 11, "bold"),
                           text=f"{today.month}月{today.day}日  星期{'一二三四五六日'[today.weekday()]}")
        y = 43
        if settings.get("calendar_mode", "date") == "week":
            week = next(week for week in calendar.monthcalendar(today.year, today.month) if today.day in week)
            column = (width - 2 * margin) / 7
            for index, day in enumerate(week):
                x = margin + column * (index + .5)
                selected = day == today.day
                if selected:
                    canvas.create_rectangle(x - column / 2 + 1, y, x + column / 2 - 1, y + 38,
                                            fill=ACCENT, outline="")
                color = "white" if selected else TEXT
                canvas.create_text(x, y + 9, text="一二三四五六日"[index], font=(FONT, 8), fill=color)
                canvas.create_text(x, y + 27, text=str(day) if day else "", font=(FONT, 9), fill=color)
            y += 45
        tasks = sorted(self.store.tasks(self.current_day.isoformat()),
                       key=lambda task: (task["done"], task.get("time", "") or "99:99"))
        completed = sum(task["done"] for task in tasks)
        canvas.create_text(margin, y, anchor="nw", text="当天计划", fill=TEXT, font=(FONT, 10))
        canvas.create_text(width - margin, y, anchor="ne", text=f"{completed} / {len(tasks)}", fill=TEXT, font=(FONT, 9))
        y += 25
        canvas.create_rectangle(margin, y, width - margin, y + 2, fill=LINE, outline="")
        if tasks:
            canvas.create_rectangle(margin, y, margin + (width - 2 * margin) * completed / len(tasks), y + 2,
                                    fill=ACCENT, outline="")
        y += 10
        limit = height - 48
        if not tasks:
            canvas.create_text(margin, y + 6, anchor="nw", text="今天，慢慢来\n按 Ctrl+Alt+M 添加计划",
                               fill=TEXT, font=(FONT, 9))
        shown = 0
        for task in tasks:
            if y + 40 > limit:
                break
            text = (f"{task['time']}  " if task.get("time") else "") + task["text"]
            text_id = canvas.create_text(margin + 9, y + 8, anchor="nw", text=text,
                                         width=max(60, width - 2 * margin - 75), fill=MUTED if task["done"] else TEXT,
                                         font=self.done_font if task["done"] else self.normal_font)
            bounds = canvas.bbox(text_id)
            row_height = max(40, bounds[3] - bounds[1] + 16)
            if y + row_height > limit:
                # Keep even a long first plan visible in the smallest window.
                canvas.itemconfigure(text_id, text=text[:max(5, int((width - 100) / 14))] + "…")
                row_height = 40
            card = canvas.create_rectangle(margin, y, width - margin, y + row_height, fill=SOFT if task["done"] else PANEL, outline="")
            canvas.tag_lower(card, text_id)
            canvas.create_text(width - margin - 10, y + row_height / 2, anchor="e", text="已完成" if task["done"] else "完成",
                               fill=ACCENT, font=(FONT, 9))
            y += row_height + 6
            shown += 1
        if shown < len(tasks):
            canvas.create_text(margin, height - 44, anchor="nw", text=f"还有 {len(tasks) - shown} 条 · 编辑查看全部",
                               fill=TEXT, font=(FONT, 8))
        canvas.create_text(margin, height - 24, anchor="nw", text="预览中 · 请保存或取消" if self.settings_window and self.settings_window.winfo_exists()
                           else "Ctrl+Alt+M  编辑", fill=TEXT, font=(FONT, 8))

    def draw_progress(self):
        tasks = self.store.tasks(self.current_day.isoformat())
        fraction = sum(task["done"] for task in tasks) / len(tasks) if tasks else 0
        self.progress_canvas.delete("all")
        self.progress_canvas.create_rectangle(0, 0, self.progress_canvas.winfo_width() * fraction, 3, fill=ACCENT, outline="")

    def hwnd(self):
        return user32.GetAncestor(self.root.winfo_id(), 2)

    def set_mode(self, editing):
        # Leaving the settings preview restores saved geometry before changing mode.
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()
            self.settings_window = None
        self.preview_settings = None
        self.editing = editing
        if editing:
            self.daily_canvas.place_forget()
        self.apply_size()
        self.root.deiconify()
        self.root.attributes("-alpha", 1.0 if editing else self.store.data["settings"]["opacity"] / 100)
        if editing:
            self.calendar_frame.pack_forget()
            self.editor.pack(side="bottom", fill="x", before=self.task_area)
            self.grip.pack(side="right")
            self.scrollbar.pack(side="right", fill="y")
        else:
            if self.week_only:
                self.calendar_frame.pack(fill="x", pady=(0, 3), before=self.summary)
            self.editor.pack_forget()
            self.grip.pack_forget()
            self.scrollbar.pack_forget()
            self.anchor_corner()
            self.canvas.yview_moveto(0)
        self.mode_tag.configure(text="编辑中" if editing else "日常 · 点击穿透")
        self.update_hint()
        self.root.update_idletasks()
        hwnd = self.hwnd()
        style = user32.GetWindowLongW(hwnd, -20)
        style |= 0x80000  # layered
        if editing:
            style = (style | 0x40000) & ~(0x20 | 0x08000000 | 0x80)
        else:
            style = (style | 0x80 | 0x20 | 0x08000000) & ~0x40000
        user32.SetWindowLongW(hwnd, -20, style)
        user32.SetWindowPos(hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x20)
        self.render_tasks()
        if editing:
            user32.SetForegroundWindow(hwnd)
            self.entry.focus_force()

    def daily(self):
        self.save()
        self.set_mode(False)

    def work_area(self):
        rect = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ct.byref(rect), 0)
        return rect

    def apply_size(self, settings=None):
        settings = settings if settings is not None else self.store.data["settings"]
        area = self.work_area()
        # Editing temporarily needs room for the input controls. Keep the saved
        # daily dimensions intact so returning to daily restores the small card.
        width = max(340 if self.editing else MIN_WIDTH, min(int(settings["width"]), area.right - area.left - 24))
        height = max(530 if self.editing else MIN_HEIGHT, min(int(settings["height"]), area.bottom - area.top - 24))
        self.root.geometry(f"{width}x{height}")
        self.root.update_idletasks()
        self.update_layout(self.root.winfo_width(), self.root.winfo_height())
        self.anchor_corner()

    def anchor_corner(self):
        area = self.work_area()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        self.root.geometry(f"+{max(area.left, area.right - width - 16)}+{max(area.top, area.bottom - height - 16)}")

    def drag_start(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag_move(self, event):
        if self.editing:
            x, y, left, top = self.drag_origin
            self.root.geometry(f"+{max(0, left + event.x_root - x)}+{max(0, top + event.y_root - y)}")

    def resize_start(self, event):
        self.resize_origin = (event.x_root, event.y_root, self.root.winfo_width(), self.root.winfo_height())

    def resize_move(self, event):
        if self.editing:
            x, y, width, height = self.resize_origin
            area = self.work_area()
            width = max(340, min(800, width + event.x_root - x, area.right - self.root.winfo_x()))
            height = max(530, min(1100, height + event.y_root - y, area.bottom - self.root.winfo_y()))
            self.root.geometry(f"{width}x{height}")

    def resize_end(self, event):
        self.store.data["settings"].update(width=self.root.winfo_width(), height=self.root.winfo_height())
        self.save()

    def wheel(self, event):
        if self.editing and self.canvas.winfo_height() < self.task_frame.winfo_reqheight():
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def save(self):
        try:
            self.store.save()
            return True
        except OSError:
            logging.exception("Unable to save memo")
            self.show_status("保存失败，请检查数据文件夹权限和磁盘空间。", error=True)
            messagebox.showerror(APP_NAME, "本次修改未能保存到磁盘。请检查磁盘空间和数据文件夹权限。", parent=self.root)
            return False

    def sync_day(self):
        today = date.today()
        if today != self.current_day:
            self.current_day = today
            self.undo_record = None
            self.undo_button.configure(state="disabled")
            self.render_calendar()
            self.render_tasks()
            self.show_status("已切换到今天，过去的计划仍保存在本机。")

    def add_task(self):
        if not self.editing:
            return
        self.sync_day()
        try:
            self.store.add(self.entry.get(), self.time_entry.get(), self.current_day.isoformat())
        except ValueError as error:
            self.show_status(str(error), error=True)
            return
        if self.save():
            self.entry.delete(0, "end")
            self.time_entry.delete(0, "end")
            self.show_status("已添加并保存")
        self.render_tasks()
        self.entry.focus_set()

    def toggle_task(self, task_id):
        self.sync_day()
        try:
            self.store.toggle(task_id, self.current_day.isoformat())
        except KeyError:
            return
        self.save()
        self.render_tasks()

    def delete_task(self, task_id):
        self.sync_day()
        try:
            index, task = self.store.delete(task_id, self.current_day.isoformat())
        except KeyError:
            return
        self.undo_record = (self.current_day.isoformat(), index, task)
        self.undo_button.configure(state="normal")
        self.save()
        self.render_tasks()
        self.show_status("已删除，可点击“撤销删除”恢复。")

    def undo_delete(self):
        if self.undo_record:
            day, index, task = self.undo_record
            self.store.data["days"].setdefault(day, []).insert(index, task)
            self.undo_record = None
            self.undo_button.configure(state="disabled")
            self.save()
            self.render_tasks()
            self.show_status("已恢复上一条删除的计划")

    def show_status(self, text, error=False):
        self.status.configure(text=text, fg="#B04B3B" if error else MUTED)

    def startup_command(self):
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        return f'"{pythonw}" "{Path(__file__).resolve()}"'

    def startup_enabled(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                return winreg.QueryValueEx(key, "DesktopMemoTemplate")[0] == self.startup_command()
        except OSError:
            return False

    def set_startup(self, enabled):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, "DesktopMemoTemplate", 0, winreg.REG_SZ, self.startup_command())
            else:
                try:
                    winreg.DeleteValue(key, "DesktopMemoTemplate")
                except FileNotFoundError:
                    pass

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        return_editing = self.editing
        self.set_mode(False)
        dialog = self.settings_window = tk.Toplevel(self.root)
        dialog.title("今日备忘 · 外观")
        dialog.configure(bg=BG, padx=24, pady=20)
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.attributes("-alpha", 1.0)
        dialog.transient(self.root)
        self.label(dialog, "让今天，舒服一点", 15).pack(anchor="w", pady=(0, 14))
        variables = self.appearance_variables = {}
        for key, title, low, high in (("opacity", "日常不透明度（越低越透明）", 25, 95),
                                      ("width", "日常窗口宽度", MIN_WIDTH, 800), ("height", "日常窗口高度", MIN_HEIGHT, 1100)):
            self.label(dialog, title, 9).pack(anchor="w")
            variables[key] = tk.IntVar(value=self.store.data["settings"][key])
            tk.Scale(dialog, from_=low, to=high, orient="horizontal", variable=variables[key],
                     bg=BG, fg=TEXT, troughcolor=SOFT, highlightthickness=0, length=300,
                     activebackground=ACCENT, font=(FONT, 9)).pack(fill="x", pady=(0, 8))
        self.label(dialog, "日历显示", 9).pack(anchor="w")
        variables["calendar_mode"] = tk.StringVar(value=self.store.data["settings"].get("calendar_mode", "date"))
        calendar_choices = tk.Frame(dialog, bg=BG)
        calendar_choices.pack(fill="x", pady=(2, 8))
        for title, value in (("仅日期 · 最省空间", "date"), ("当周日历", "week")):
            tk.Radiobutton(calendar_choices, text=title, value=value, variable=variables["calendar_mode"],
                           font=(FONT, 9), bg=BG, fg=TEXT, activebackground=BG).pack(side="left")
        variables["wallpaper"] = tk.StringVar(value=self.store.data["settings"].get("wallpaper", ""))
        wallpaper_controls = tk.Frame(dialog, bg=BG)
        wallpaper_controls.pack(fill="x", pady=(0, 5))

        def choose_wallpaper():
            path = filedialog.askopenfilename(parent=dialog, title="选择备忘录背景图片",
                                              filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp"), ("所有文件", "*.*")])
            if path:
                try:
                    self.set_wallpaper_preview(path)
                except (OSError, ValueError, Image.DecompressionBombError) as error:
                    messagebox.showerror(APP_NAME, f"这张图片无法读取，请换一张。\n{error}", parent=dialog)

        self.button(wallpaper_controls, "选择背景图片…", choose_wallpaper).pack(side="left")
        self.button(wallpaper_controls, "恢复纯色", lambda: variables["wallpaper"].set("")).pack(side="right")
        wallpaper_hint = self.label(dialog, "", 8, MUTED)
        wallpaper_hint.pack(anchor="w", pady=(0, 5))
        initial_startup = self.startup_enabled()
        startup = tk.BooleanVar(value=initial_startup)
        tk.Checkbutton(dialog, text="登录 Windows 后自动运行", variable=startup,
                       font=(FONT, 9), bg=BG, fg=TEXT, activebackground=BG).pack(anchor="w", pady=5)
        self.label(dialog, "调整后立即预览，保存后保留。\n壁纸仅用于备忘录；编辑时临时放大。", 9, MUTED,
                   justify="left").pack(anchor="w", pady=8)

        def preview(*unused):
            candidate = {key: value.get() for key, value in variables.items()}
            self.preview_settings = candidate
            self.apply_size(candidate)
            self.root.attributes("-alpha", candidate["opacity"] / 100)
            self.mode_tag.configure(text="外观预览")
            self.update_hint()
            wallpaper_hint.configure(text="已选择图片 · 保存后自动保留本机副本" if candidate["wallpaper"] else "当前为默认纯色背景")
            self.render_daily()

        def cancel():
            self.set_mode(return_editing)

        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", lambda event: cancel())

        def apply():
            previous = self.store.data["settings"].copy()
            candidate = {key: value.get() for key, value in variables.items()}
            try:
                candidate["wallpaper"] = keep_wallpaper(candidate["wallpaper"], self.store.path.parent)
                if startup.get() != initial_startup:
                    self.set_startup(startup.get())
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                messagebox.showerror(APP_NAME, f"外观设置未能保存：{error}", parent=dialog)
                return
            self.store.data["settings"].update(candidate)
            if self.save():
                self.set_mode(False)
                self.show_status("外观已保存")
            else:
                self.store.data["settings"] = previous

        controls = tk.Frame(dialog, bg=BG)
        controls.pack(fill="x", pady=(6, 0))
        self.settings_cancel_button = self.button(controls, "取消", cancel)
        self.settings_cancel_button.pack(side="left")
        self.settings_apply_button = self.button(controls, "保存外观", apply, primary=True)
        self.settings_apply_button.pack(side="right")
        dialog.update_idletasks()
        area = self.work_area()
        # Reserve room for the widest preview so the sliders never move while dragged.
        left = max(area.left + 16, area.right - 800 - 16 - dialog.winfo_width() - 18)
        top = max(area.top + 16, area.bottom - dialog.winfo_height() - 60)
        dialog.geometry(f"+{left}+{top}")
        for variable in variables.values():
            variable.trace_add("write", preview)
        preview()
        dialog.lift()
        dialog.focus_force()

    def set_wallpaper_preview(self, path):
        image = read_wallpaper(path)
        self.wallpaper_source = path
        self.wallpaper_image = image
        self.wallpaper_cache = None
        self.appearance_variables["wallpaper"].set(path)

    def start_tray(self):
        def enqueue(command):
            return lambda icon, item: self.commands.put(command)
        self.tray = pystray.Icon("DesktopMemoTemplate", icon_image(), "今日备忘 · 双击编辑 / Ctrl+Alt+M",
                                pystray.Menu(
                                    pystray.MenuItem("编辑今天的计划", enqueue("edit"), default=True),
                                    pystray.MenuItem("回到半透明日常", enqueue("daily")),
                                    pystray.MenuItem("外观与自动运行", enqueue("settings")),
                                    pystray.MenuItem("打开数据文件夹", enqueue("folder")),
                                    pystray.Menu.SEPARATOR,
                                    pystray.MenuItem("退出", enqueue("quit"))))
        threading.Thread(target=self.tray.run, daemon=True).start()

    def hotkeys(self):
        self.hotkey_thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, 0x4000 | 0x2 | 0x1, ord("M")):
            self.commands.put("hotkey_failed")
            return
        self.hotkey_registered = True
        message = wt.MSG()
        try:
            while user32.GetMessageW(ct.byref(message), None, 0, 0) > 0:
                if message.message == 0x312:
                    self.commands.put("toggle")
        finally:
            user32.UnregisterHotKey(None, 1)
            self.hotkey_registered = False

    def poll(self):
        if self.activation and kernel32.WaitForSingleObject(self.activation, 0) == 0:
            self.set_mode(True)
        while not self.commands.empty():
            command = self.commands.get_nowait()
            if command == "quit":
                if self.quit():
                    return
            if command == "toggle":
                self.daily() if self.editing else self.set_mode(True)
            elif command == "edit":
                self.set_mode(True)
            elif command == "daily":
                self.daily()
            elif command == "settings":
                self.open_settings()
            elif command == "folder":
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(DATA_DIR)
            elif command == "hotkey_failed":
                self.set_mode(True)
                self.show_status("快捷键已被占用，请双击桌面快捷方式或托盘图标进入编辑。", error=True)
        self.sync_day()
        self.root.after(150, self.poll)

    def callback_error(self, exc, value, traceback):
        logging.error("Interface error", exc_info=(exc, value, traceback))
        messagebox.showerror(APP_NAME, f"操作未完成：{value}\n详细信息已写入本机日志。", parent=self.root)

    def quit(self):
        if not self.save():
            return
        if self.hotkey_thread_id:
            user32.PostThreadMessageW(self.hotkey_thread_id, 0x12, 0, 0)
        if self.tray:
            self.tray.stop()
        self.root.destroy()
        return True


def smoke_test(report_path):
    """Exercise the packaged runtime with temporary data, including tray and hotkey."""
    import json
    import tempfile
    import traceback
    with tempfile.TemporaryDirectory() as directory:
        root = tk.Tk()
        app = App(root, Store(Path(directory) / "data.json"))
        result = {}

        def finish():
            try:
                result["hotkey_registered"] = app.hotkey_registered
                result["tray_visible"] = bool(app.tray.visible)
                app.set_mode(True)
                root.update_idletasks()
                app.entry.insert(0, "封装运行验证")
                app.add_task()
                task_id = app.store.tasks()[0]["id"]
                app.toggle_task(task_id)
                result["saved_completed_task"] = Store(app.store.path).tasks()[0]["done"]
                result["editor_task_area_height"] = app.canvas.winfo_height()
                app.delete_task(task_id)
                app.undo_delete()
                result["delete_undo"] = len(app.store.tasks()) == 1
                app.daily()
                result["daily_click_through"] = bool(user32.GetWindowLongW(app.hwnd(), -20) & 0x20)
                result["daily_alpha"] = root.attributes("-alpha")
                result["window_geometry"] = root.geometry()
                original = app.store.data["settings"].copy()
                app.open_settings()
                app.appearance_variables["opacity"].set(40)
                app.appearance_variables["width"].set(520)
                root.update_idletasks()
                result["live_preview"] = root.winfo_width() == 520 and abs(root.attributes("-alpha") - .40) < .01
                result["settings_panel_opaque"] = app.settings_window.attributes("-alpha") == 1.0
                result["preview_not_saved"] = app.store.data["settings"] == original
                app.settings_cancel_button.invoke()
                result["preview_cancel_restores"] = root.winfo_width() == original["width"]
                app.open_settings()
                app.appearance_variables["opacity"].set(55)
                app.appearance_variables["width"].set(480)
                app.settings_apply_button.invoke()
                result["preview_save_persists"] = Store(app.store.path).data["settings"]["opacity"] == 55
                app.open_settings()
                app.appearance_variables["width"].set(MIN_WIDTH)
                app.appearance_variables["height"].set(MIN_HEIGHT)
                app.appearance_variables["calendar_mode"].set("week")
                app.settings_apply_button.invoke()
                root.update_idletasks()
                result["small_daily_window"] = root.winfo_width() == MIN_WIDTH and root.winfo_height() == MIN_HEIGHT
                result["compact_calendar"] = app.week_only and len(app.calendar_frame.winfo_children()) == 14
                app.set_mode(True)
                root.update_idletasks()
                result["small_window_edit_accessible"] = bool(app.entry.winfo_ismapped()) and app.canvas.winfo_height() > 80
                app.daily()
                result["small_size_restored"] = root.winfo_width() == MIN_WIDTH and root.winfo_height() == MIN_HEIGHT
                sample = Path(directory) / "sample-wallpaper.png"
                Image.new("RGB", (480, 640), "#3479AA").save(sample)
                app.open_settings()
                app.appearance_variables["calendar_mode"].set("date")
                app.set_wallpaper_preview(str(sample))
                root.update_idletasks()
                result["wallpaper_preview"] = app.daily_photo is not None
                result["date_header_compact"] = not app.week_only and not app.calendar_frame.winfo_ismapped()
                app.settings_apply_button.invoke()
                saved_wallpaper = Store(app.store.path).data["settings"]["wallpaper"]
                result["wallpaper_copy_saved"] = Path(saved_wallpaper).is_file() and saved_wallpaper != str(sample)
                app.open_settings()
                app.appearance_variables["wallpaper"].set("")
                app.settings_cancel_button.invoke()
                result["wallpaper_cancel_restores"] = app.daily_photo is not None
                assert all(result[key] for key in ("hotkey_registered", "tray_visible", "saved_completed_task", "delete_undo", "daily_click_through"))
                assert all(result[key] for key in ("live_preview", "settings_panel_opaque", "preview_not_saved", "preview_cancel_restores", "preview_save_persists"))
                assert all(result[key] for key in ("small_daily_window", "compact_calendar", "small_window_edit_accessible", "small_size_restored"))
                assert all(result[key] for key in ("wallpaper_preview", "date_header_compact", "wallpaper_copy_saved", "wallpaper_cancel_restores"))
                result["passed"] = True
            except Exception:
                result["passed"] = False
                result["error"] = traceback.format_exc()
            finally:
                Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                app.quit()

        root.after(1800, finish)
        root.mainloop()


def main():
    if "--smoke-test" in sys.argv:
        smoke_test(sys.argv[sys.argv.index("--smoke-test") + 1])
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA_DIR / "app.log", level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    mutex = kernel32.CreateMutexW(None, False, r"Local\DesktopMemoTemplate.SingleInstance.v1")
    exists = ct.get_last_error() == 183
    activation = kernel32.CreateEventW(None, False, False, r"Local\DesktopMemoTemplate.Activate.v1")
    if exists:
        kernel32.SetEvent(activation)
        kernel32.CloseHandle(activation)
        kernel32.CloseHandle(mutex)
        return
    try:
        try:
            user32.SetProcessDPIAware()
        except AttributeError:
            pass
        root = tk.Tk()
        app = App(root, Store(DATA_DIR / "data.json"), activation)
        if "--settings" in sys.argv:
            root.after(300, app.open_settings)
        elif "--edit" in sys.argv:
            root.after(300, lambda: app.set_mode(True))
        root.mainloop()
    except Exception:
        logging.exception("Startup failed")
        user32.MessageBoxW(None, "今日备忘未能启动。请查看本机数据文件夹中的 app.log。", APP_NAME, 0x10)
    finally:
        kernel32.CloseHandle(activation)
        kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
