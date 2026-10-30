"""各类对话框：文件夹、记录、设置、文件夹管理、关于。"""

from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from .. import dates, repo, scanner
from ..config import (
    COLOR_MUTED,
    COLOR_WARN,
    DEFAULT_EXTENSIONS,
    FONT_SMALL,
    FONT_UI,
    PERIOD_COLORS,
    normalize_extensions,
)


def center_on_parent(window: tk.Toplevel, parent: tk.Misc) -> None:
    window.update_idletasks()
    root = parent.winfo_toplevel()
    x = root.winfo_rootx() + (root.winfo_width() - window.winfo_width()) // 2
    y = root.winfo_rooty() + (root.winfo_height() - window.winfo_height()) // 3
    window.geometry(f"+{max(0, x)}+{max(0, y)}")


class FolderDialog(tk.Toplevel):
    """添加管理文件夹。"""

    def __init__(self, master: tk.Misc, initial_dir: str = "") -> None:
        super().__init__(master)
        self.result: dict | None = None
        self.title("添加管理文件夹")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="文件夹路径：").grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar(value=initial_dir)
        entry = ttk.Entry(body, textvariable=self.path_var, width=56)
        entry.grid(row=1, column=0, columnspan=2, sticky="we", pady=(2, 8))
        ttk.Button(body, text="浏览…", command=self._browse).grid(
            row=1, column=2, padx=(6, 0)
        )

        self.recursive_var = tk.BooleanVar(value=True)
        self.auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            body, text="包含子文件夹", variable=self.recursive_var
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            body, text="新发现的文件自动入库（不弹确认）", variable=self.auto_var
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="确定", command=self._ok).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")

        entry.focus_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self, title="选择要管理的文件夹", mustexist=True
        )
        if chosen:
            self.path_var.set(chosen)

    def _ok(self) -> None:
        raw = self.path_var.get().strip()
        if not raw:
            messagebox.showwarning("提示", "请选择文件夹。", parent=self)
            return
        path = Path(raw)
        if not path.is_dir():
            messagebox.showerror("路径无效", f"文件夹不存在：\n{path}", parent=self)
            return
        self.result = {
            "path": path,
            "recursive": self.recursive_var.get(),
            "auto_add": self.auto_var.get(),
        }
        self.destroy()


class RecordDialog(tk.Toplevel):
    """新增/编辑一条日期记录。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        day: str,
        note: str = "",
        file_mtime: str | None = None,
    ) -> None:
        super().__init__(master)
        self.result: dict | None = None
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="日期（YYYY-MM-DD）：").grid(row=0, column=0, sticky="w")
        self.day_var = tk.StringVar(value=day)
        entry = ttk.Entry(body, textvariable=self.day_var, width=16, font=FONT_UI)
        entry.grid(row=1, column=0, sticky="w", pady=(2, 6))
        quick = ttk.Frame(body)
        quick.grid(row=1, column=1, sticky="w", padx=(8, 0))
        ttk.Button(
            quick, text="今天", width=8, command=lambda: self.day_var.set(dates.today_str())
        ).pack(side="left")
        if file_mtime:
            ttk.Button(
                quick,
                text="文件修改日期",
                width=12,
                command=lambda: self.day_var.set(file_mtime[:10]),
            ).pack(side="left", padx=(4, 0))

        ttk.Label(body, text="备注（类似 commit message，可留空）：").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(8, 2)
        )
        self.text = tk.Text(body, width=58, height=7, wrap="word", font=FONT_UI, undo=True)
        self.text.grid(row=3, column=0, columnspan=2, sticky="we")
        self.text.insert("1.0", note)
        self.text.focus_set()

        hint = ttk.Label(
            body,
            text="Ctrl+Enter 保存；日期可以改成任意一天，用来补录历史记录。",
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
        )
        hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")

        entry.select_range(0, "end")
        entry.focus_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Control-Return>", lambda _e: self._save())
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _save(self) -> None:
        day = dates.try_parse_day(self.day_var.get())
        if day is None:
            messagebox.showerror(
                "日期无效", "请按 YYYY-MM-DD 格式填写日期，例如 2026-10-29。", parent=self
            )
            self.day_var.set(dates.today_str())
            return
        self.result = {
            "day": day,
            "note": self.text.get("1.0", "end").strip(),
        }
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """设置页：文件类型、日期默认值、自动记录、扫描提示、日历与备份。"""

    def __init__(self, master: tk.Misc, conn) -> None:
        super().__init__(master)
        self.conn = conn
        self.result: dict | None = None
        self.title("设置")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="管理的文件类型（逗号分隔）：").grid(row=0, column=0, sticky="w")
        self.ext_var = tk.StringVar(
            value=repo.get_setting(conn, "doc.extensions", ",".join(DEFAULT_EXTENSIONS)) or ""
        )
        ttk.Entry(body, textvariable=self.ext_var, width=46).grid(
            row=1, column=0, sticky="we", pady=(2, 4)
        )
        ttk.Label(
            body,
            text="例：" + ", ".join(DEFAULT_EXTENSIONS) + "（修改后下次扫描生效）",
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
        ).grid(row=2, column=0, sticky="w", pady=(0, 10))

        ttk.Label(body, text="导入新文件时，日期默认取：").grid(row=3, column=0, sticky="w")
        self.source_var = tk.StringVar(
            value="文件修改日期"
            if repo.get_setting(conn, "default.date_source", "mtime") != "added"
            else "添加日期"
        )
        ttk.Combobox(
            body,
            textvariable=self.source_var,
            values=["文件修改日期", "添加日期"],
            state="readonly",
            width=18,
        ).grid(row=4, column=0, sticky="w", pady=(2, 10))

        ttk.Label(body, text="日历每周从哪天开始：").grid(row=5, column=0, sticky="w")
        firstweekday = repo.get_int(conn, "calendar.firstweekday", 0)
        self.weekday_var = tk.StringVar(value="周日" if firstweekday == 6 else "周一")
        ttk.Combobox(
            body,
            textvariable=self.weekday_var,
            values=["周一", "周日"],
            state="readonly",
            width=18,
        ).grid(row=6, column=0, sticky="w", pady=(2, 10))

        ttk.Label(body, text="启动时默认视图：").grid(row=7, column=0, sticky="w")
        current_view = repo.get_setting(conn, "view.default", "calendar") or "calendar"
        self.view_var = tk.StringVar(
            value="关系图"
            if current_view == "graph"
            else ("列表" if current_view == "list" else "日历")
        )
        ttk.Combobox(
            body,
            textvariable=self.view_var,
            values=["日历", "列表", "关系图"],
            state="readonly",
            width=18,
        ).grid(row=8, column=0, sticky="w", pady=(2, 10))

        self.auto_log_var = tk.BooleanVar(
            value=repo.get_bool(conn, "auto.log_on_open", False)
        )
        ttk.Checkbutton(
            body,
            text="打开文档时自动记一条记录（可能产生较多记录，默认关闭）",
            variable=self.auto_log_var,
        ).grid(row=9, column=0, sticky="w")

        self.prompt_mtime_var = tk.BooleanVar(
            value=repo.get_bool(conn, "scan.prompt_mtime", True)
        )
        ttk.Checkbutton(
            body,
            text="扫描发现文档修改日期变化时，提示一键补记录",
            variable=self.prompt_mtime_var,
        ).grid(row=10, column=0, sticky="w", pady=(0, 10))

        ttk.Label(body, text="数据库每日备份保留份数：").grid(row=11, column=0, sticky="w")
        self.keep_var = tk.IntVar(value=repo.get_int(conn, "backup.keep", 7))
        ttk.Spinbox(body, from_=0, to=30, textvariable=self.keep_var, width=6).grid(
            row=12, column=0, sticky="w", pady=(2, 12)
        )

        ttk.Label(body, text="关系图字号（6–18，也可在关系图里 Ctrl+滚轮缩放）：").grid(
            row=13, column=0, sticky="w"
        )
        self.graph_font_var = tk.IntVar(
            value=max(6, min(18, repo.get_int(conn, "graph.font_size", 9)))
        )
        ttk.Spinbox(
            body,
            from_=6,
            to=18,
            textvariable=self.graph_font_var,
            width=6,
        ).grid(row=14, column=0, sticky="w", pady=(2, 12))

        buttons = ttk.Frame(body)
        buttons.grid(row=15, column=0, sticky="e")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")

        self.bind("<Escape>", lambda _e: self.destroy())
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _save(self) -> None:
        exts = normalize_extensions(self.ext_var.get())
        if not exts:
            messagebox.showerror("提示", "至少保留一种文件类型。", parent=self)
            return
        try:
            keep = max(0, int(self.keep_var.get() or 0))
        except (tk.TclError, ValueError):
            keep = 7
        try:
            graph_font = max(6, min(18, int(self.graph_font_var.get() or 9)))
        except (tk.TclError, ValueError):
            graph_font = 9
        self.result = {
            "doc.extensions": ",".join(exts),
            "default.date_source": "added"
            if self.source_var.get() == "添加日期"
            else "mtime",
            "auto.log_on_open": "1" if self.auto_log_var.get() else "0",
            "scan.prompt_mtime": "1" if self.prompt_mtime_var.get() else "0",
            "calendar.firstweekday": "6" if self.weekday_var.get() == "周日" else "0",
            "view.default": {
                "列表": "list",
                "关系图": "graph",
            }.get(self.view_var.get(), "calendar"),
            "backup.keep": str(keep),
            "graph.font_size": str(graph_font),
        }
        self.destroy()


class PeriodDialog(tk.Toplevel):
    """新建/编辑时间段：名称、颜色、包含的日期。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str = "新建时间段",
        name: str = "",
        color: str | None = None,
        note: str = "",
        days: list[str] | tuple[str, ...] = (),
    ) -> None:
        super().__init__(master)
        self.result: dict | None = None
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.color = color or PERIOD_COLORS[0]
        self._swatches: dict[str, tk.Frame] = {}

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="时间段名称：").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=name)
        name_entry = ttk.Entry(body, textvariable=self.name_var, width=40)
        name_entry.grid(row=1, column=0, columnspan=3, sticky="we", pady=(2, 10))

        ttk.Label(body, text="颜色（预设，或点「自定义…」）：").grid(
            row=2, column=0, columnspan=3, sticky="w"
        )
        swatches = ttk.Frame(body)
        swatches.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 10))
        for index, swatch_color in enumerate(PERIOD_COLORS):
            swatch = tk.Frame(
                swatches,
                width=22,
                height=22,
                bg=swatch_color,
                cursor="hand2",
                highlightthickness=2,
                highlightbackground="#e5e7eb",
            )
            swatch.grid(
                row=index // 10,
                column=index % 10,
                padx=(0, 6),
                pady=(0, 4),
                sticky="w",
            )
            swatch.bind("<Button-1>", lambda _e, c=swatch_color: self._pick(c))
            self._swatches[swatch_color] = swatch

        self.custom_swatch = tk.Frame(
            swatches,
            width=22,
            height=22,
            bg=self.color,
            cursor="hand2",
            highlightthickness=2,
            highlightbackground="#111827",
        )
        self.custom_swatch.grid(row=2, column=0, padx=(0, 6), pady=(2, 0), sticky="w")
        self.custom_swatch.bind("<Button-1>", lambda _e: self._choose_custom())
        ttk.Button(swatches, text="自定义…", command=self._choose_custom).grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(2, 0)
        )
        self.custom_hex_label = ttk.Label(
            swatches, text=self.color, foreground=COLOR_MUTED, font=FONT_SMALL
        )
        self.custom_hex_label.grid(
            row=2, column=3, columnspan=4, sticky="w", pady=(2, 0)
        )
        self._refresh_swatches()

        ttk.Label(body, text="包含的日期（每行或逗号分隔，YYYY-MM-DD）：").grid(
            row=4, column=0, columnspan=3, sticky="w"
        )
        self.days_text = tk.Text(
            body, width=52, height=7, wrap="word", font=FONT_UI, undo=True
        )
        self.days_text.grid(row=5, column=0, columnspan=3, sticky="we", pady=(2, 2))
        self.days_text.insert("1.0", "\n".join(days))
        ttk.Label(
            body,
            text="提示：在日历上按住 Shift 连选多日再右键，可把选中的日期直接带进来。",
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(body, text="备注（可留空）：").grid(row=7, column=0, sticky="w")
        self.note_var = tk.StringVar(value=note)
        ttk.Entry(body, textvariable=self.note_var, width=40).grid(
            row=8, column=0, columnspan=3, sticky="we", pady=(2, 12)
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")

        name_entry.focus_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Control-Return>", lambda _e: self._save())
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _pick(self, color: str) -> None:
        self.color = color
        self._refresh_swatches()

    def _choose_custom(self) -> None:
        chosen = colorchooser.askcolor(
            color=self.color, parent=self, title="选择时间段颜色"
        )
        if chosen and chosen[1]:
            self.color = chosen[1]
            self._refresh_swatches()

    def _refresh_swatches(self) -> None:
        for color, swatch in self._swatches.items():
            swatch.configure(
                highlightbackground="#111827" if color == self.color else "#e5e7eb"
            )
        self.custom_swatch.configure(
            bg=self.color,
            highlightbackground="#111827" if self.color not in self._swatches else "#e5e7eb",
        )
        self.custom_hex_label.config(text=self.color)

    def _save(self) -> None:
        title = self.name_var.get().strip()
        if not title:
            messagebox.showwarning("提示", "请填写时间段名称。", parent=self)
            return
        raw = self.days_text.get("1.0", "end")
        parsed: list[str] = []
        invalid: list[str] = []
        for token in re.split(r"[,;，；\s]+", raw):
            text = token.strip()
            if not text:
                continue
            day = dates.try_parse_day(text)
            if day is None:
                invalid.append(text)
            elif day not in parsed:
                parsed.append(day)
        if invalid:
            messagebox.showerror(
                "日期无效",
                "以下内容不是有效日期（请用 YYYY-MM-DD）：\n" + "、".join(invalid[:8]),
                parent=self,
            )
            return
        if not parsed:
            messagebox.showwarning("提示", "请至少填写一个日期。", parent=self)
            return
        parsed.sort()
        self.result = {
            "name": title,
            "color": self.color,
            "note": self.note_var.get().strip(),
            "days": parsed,
        }
        self.destroy()


class ColorChoiceDialog(tk.Toplevel):
    """选择颜色：预设色板 + 自定义取色 + 可选的“自动/默认”选项。"""

    AUTO = "__auto__"

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str = "选择颜色",
        message: str = "",
        current: str | None = None,
        allow_auto: bool = False,
        auto_label: str = "跟随时间段颜色",
    ) -> None:
        super().__init__(master)
        self.result: str | None = None
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.color = current or PERIOD_COLORS[0]
        self._swatches: dict[str, tk.Frame] = {}

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        if message:
            ttk.Label(
                body,
                text=message,
                foreground=COLOR_MUTED,
                wraplength=500,
                justify="left",
            ).pack(anchor="w", pady=(0, 8))

        grid = ttk.Frame(body)
        grid.pack(anchor="w", pady=(0, 8))
        for index, swatch_color in enumerate(PERIOD_COLORS):
            swatch = tk.Frame(
                grid,
                width=24,
                height=24,
                bg=swatch_color,
                cursor="hand2",
                highlightthickness=2,
                highlightbackground="#e5e7eb",
            )
            swatch.grid(
                row=index // 10,
                column=index % 10,
                padx=(0, 6),
                pady=(0, 4),
                sticky="w",
            )
            swatch.bind("<Button-1>", lambda _e, c=swatch_color: self._pick(c))
            self._swatches[swatch_color] = swatch

        custom_row = ttk.Frame(body)
        custom_row.pack(fill="x", pady=(2, 8))
        self.custom_swatch = tk.Frame(
            custom_row,
            width=24,
            height=24,
            bg=self.color,
            highlightthickness=2,
            highlightbackground="#111827",
        )
        self.custom_swatch.pack(side="left")
        ttk.Button(custom_row, text="自定义…", command=self._choose_custom).pack(
            side="left", padx=(6, 0)
        )
        self.hex_label = ttk.Label(
            custom_row, text=self.color, foreground=COLOR_MUTED, font=FONT_SMALL
        )
        self.hex_label.pack(side="left", padx=(8, 0))
        if allow_auto:
            ttk.Button(
                custom_row, text=auto_label, command=self._choose_auto
            ).pack(side="left", padx=(16, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="确定", command=self._ok).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 6)
        )

        self._refresh()
        self.bind("<Escape>", lambda _e: self.destroy())
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _pick(self, color: str) -> None:
        self.color = color
        self._refresh()

    def _choose_custom(self) -> None:
        chosen = colorchooser.askcolor(
            color=self.color, parent=self, title="选择颜色"
        )
        if chosen and chosen[1]:
            self.color = chosen[1]
            self._refresh()

    def _choose_auto(self) -> None:
        self.result = self.AUTO
        self.destroy()

    def _refresh(self) -> None:
        for color, swatch in self._swatches.items():
            swatch.configure(
                highlightbackground="#111827" if color == self.color else "#e5e7eb"
            )
        self.custom_swatch.configure(bg=self.color)
        self.hex_label.config(text=self.color)

    def _ok(self) -> None:
        self.result = self.color
        self.destroy()


class NoteDialog(tk.Toplevel):
    """编辑图板注释块的多行文本。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str = "添加注释",
        text: str = "",
    ) -> None:
        super().__init__(master)
        self.result: str | None = None
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="注释内容（与文档无关，可自由写说明/待办/章节提要）：",
            foreground=COLOR_MUTED,
        ).pack(anchor="w")
        self.text = tk.Text(
            body, width=52, height=8, wrap="word", font=FONT_UI, undo=True
        )
        self.text.pack(fill="both", expand=True, pady=(4, 6))
        self.text.insert("1.0", text)
        self.text.focus_set()
        ttk.Label(
            body,
            text="Ctrl+Enter 保存；Esc 取消。",
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
        ).pack(anchor="w")

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 6)
        )

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Control-Return>", lambda _e: self._save())
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _save(self) -> None:
        value = self.text.get("1.0", "end").strip()
        if not value:
            messagebox.showwarning("提示", "注释内容不能为空。", parent=self)
            return
        self.result = value
        self.destroy()


class DropMoveDialog(tk.Toplevel):
    """拖入文件后的确认框：选择目标文件夹与要移动的文件。

    同时拖入多个文件时默认全部选中，可按住 Ctrl / Shift 调整；移动会改变
    文件在磁盘上的位置，但不会覆盖同名文件（自动改名）。
    """

    def __init__(
        self,
        master: tk.Misc,
        paths: list[str],
        folders: list,
        initial_folder_id: int | None = None,
    ) -> None:
        super().__init__(master)
        self.result: dict | None = None
        self._paths = list(paths)
        self._folders = list(folders)
        self.title("移动拖入的文件")
        self.transient(master.winfo_toplevel())
        self.geometry("760x500")
        self.minsize(600, 380)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.rowconfigure(3, weight=1)
        body.columnconfigure(0, weight=1)

        ttk.Label(
            body,
            text=(
                "把拖入的文件移动到目标文件夹。移动会改变文件在磁盘上的位置；"
                "同名文件会自动改名，不会覆盖。"
            ),
            foreground=COLOR_MUTED,
            wraplength=710,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        target_row = ttk.Frame(body)
        target_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 8))
        ttk.Label(target_row, text="目标文件夹：").pack(side="left")
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(
            target_row,
            textvariable=self.target_var,
            state="readonly",
            width=42,
        )
        self.target_combo.pack(side="left")
        self.target_combo.configure(values=[str(folder["name"]) for folder in folders])
        initial_index = 0
        for index, folder in enumerate(folders):
            if int(folder["id"]) == initial_folder_id:
                initial_index = index
                break
        if folders:
            self.target_combo.current(initial_index)

        ttk.Label(
            body,
            text="选择要移动的文件（默认全选）：",
            foreground=COLOR_MUTED,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self.tree = ttk.Treeview(
            body,
            columns=("name", "source"),
            show="headings",
            selectmode="extended",
        )
        self.tree.heading("name", text="文件名")
        self.tree.heading("source", text="来源文件夹")
        self.tree.column("name", width=240, anchor="w")
        self.tree.column("source", width=440, anchor="w", stretch=True)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=3, column=0, sticky="nsew")
        scrollbar.grid(row=3, column=1, sticky="ns")
        for index, raw in enumerate(self._paths):
            path = Path(raw)
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(path.name, str(path.parent)),
            )
        self.tree.selection_set(self.tree.get_children())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_count())

        self.count_label = ttk.Label(body, text="", foreground=COLOR_MUTED, font=FONT_SMALL)
        self.count_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        footer = ttk.Frame(body)
        footer.grid(row=5, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttk.Button(footer, text="全选", command=self._select_all).pack(side="left")
        ttk.Button(footer, text="全不选", command=self._select_none).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(footer, text="移动选中", command=self._ok).pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 6)
        )

        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._ok())
        self._update_count()
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _select_all(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def _select_none(self) -> None:
        self.tree.selection_remove(*self.tree.selection())

    def _update_count(self) -> None:
        self.count_label.config(
            text=f"共 {len(self._paths)} 个文件，已选 {len(self.tree.selection())} 个"
        )

    def _ok(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请至少选择一个要移动的文件。", parent=self)
            return
        index = self.target_combo.current()
        if index < 0 or index >= len(self._folders):
            messagebox.showinfo("提示", "请选择目标文件夹。", parent=self)
            return
        folder = self._folders[index]
        self.result = {
            "folder_id": int(folder["id"]),
            "paths": [self._paths[int(iid)] for iid in selection],
        }
        self.destroy()


class PeriodManagerDialog(tk.Toplevel):
    """管理全部时间段：查看、编辑、删除。"""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master)
        self.app = app
        self.conn = app.conn
        self.title("时间段管理")
        self.transient(master.winfo_toplevel())
        self.geometry("680x400")
        self.minsize(520, 300)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            body,
            columns=("name", "range", "days", "docs", "note"),
            show="headings",
            selectmode="browse",
        )
        for col, label, width in (
            ("name", "时间段", 150),
            ("range", "起止", 150),
            ("days", "天数", 60),
            ("docs", "文档数", 70),
            ("note", "备注", 200),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w", stretch=col == "note")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(buttons, text="新建…", command=self._create).pack(side="left")
        ttk.Button(buttons, text="编辑…", command=self._edit).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="删除", command=self._delete).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")

        self.tree.bind("<Double-1>", lambda _e: self._edit())
        self._reload()
        center_on_parent(self, master)

    def _reload(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for period in repo.list_periods(self.conn, folder_id=self.app.current_folder_id):
            color = period["color"] or COLOR_MUTED
            tag = f"color{color}"
            self.tree.tag_configure(tag, foreground=color)
            first, last = period["first_day"], period["last_day"]
            span = (
                f"{dates.short_day(first)} ~ {dates.short_day(last)}"
                if first and last
                else "（未设置日期）"
            )
            self.tree.insert(
                "",
                "end",
                iid=str(period["id"]),
                values=(
                    f"● {period['name']}",
                    span,
                    int(period["day_count"]),
                    int(period["doc_count"]),
                    period["note"] or "",
                ),
                tags=(tag,),
            )

    def _selected_id(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _create(self) -> None:
        self.app.create_period_from_days([])
        self._reload()

    def _edit(self) -> None:
        period_id = self._selected_id()
        if period_id is None:
            messagebox.showinfo("提示", "请先选择一个时间段。", parent=self)
            return
        self.app.edit_period_dialog(period_id)
        self._reload()

    def _delete(self) -> None:
        period_id = self._selected_id()
        if period_id is None:
            messagebox.showinfo("提示", "请先选择一个时间段。", parent=self)
            return
        self.app.delete_period(period_id)
        self._reload()


class FolderManagerDialog(tk.Toplevel):
    """管理已登记文件夹：查看、扫描、移除。"""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master)
        self.app = app
        self.conn = app.conn
        self.title("管理文件夹")
        self.transient(master.winfo_toplevel())
        self.geometry("720x380")
        self.minsize(560, 300)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        columns = ("name", "path", "recursive", "auto", "state", "docs")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        for col, label, width in (
            ("name", "名称", 150),
            ("path", "路径", 330),
            ("recursive", "子文件夹", 70),
            ("auto", "自动入库", 70),
            ("state", "状态", 60),
            ("docs", "文档数", 60),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w", stretch=col == "path")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(buttons, text="切换到此文件夹", command=self._switch).pack(side="left")
        ttk.Button(buttons, text="添加文件夹…", command=self._add).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="扫描全部文件夹", command=self._scan_all).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="移除选中", command=self._remove).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")

        self.tree.bind("<Double-1>", lambda _e: self._switch())

        self._reload()
        center_on_parent(self, master)

    def _reload(self) -> None:
        self.tree.delete(*self.tree.get_children())
        counts = repo.document_counts_by_folder(self.conn)
        for row in repo.list_folders(self.conn):
            marker = "● " if int(row["id"]) == self.app.current_folder_id else ""
            online = scanner.folder_accessible(row["path"])
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    f"{marker}{row['name']}",
                    row["path"],
                    "是" if row["recursive"] else "否",
                    "是" if row["auto_add"] else "否",
                    "在线" if online else "离线",
                    counts.get(int(row["id"]), 0),
                ),
                tags=("offline",) if not online else (),
            )
        self.tree.tag_configure("offline", foreground=COLOR_WARN)

    def _selected_folder(self) -> int | None:
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _add(self) -> None:
        self.app.add_folder_dialog()
        self._reload()

    def _scan_all(self) -> None:
        self.app.start_scan(scan_all=True)

    def _switch(self) -> None:
        folder_id = self._selected_folder()
        if folder_id is None:
            messagebox.showinfo("提示", "请先选择一个文件夹。", parent=self)
            return
        self.app.switch_folder(folder_id)
        self.destroy()

    def _remove(self) -> None:
        folder_id = self._selected_folder()
        if folder_id is None:
            messagebox.showinfo("提示", "请先选择一个文件夹。", parent=self)
            return
        folder = repo.get_folder(self.conn, folder_id)
        if folder is None:
            return
        count = repo.document_counts_by_folder(self.conn).get(folder_id, 0)
        if not messagebox.askyesno(
            "移除文件夹",
            f"将移除对该文件夹的索引（不会删除任何文件）。\n\n"
            f"{folder['path']}\n共 {count} 篇文档及它们的日期记录。\n\n确定继续？",
            parent=self,
        ):
            return
        self.app.remove_folder(folder_id, confirm=False)
        self._reload()


class DataLocationDialog(tk.Toplevel):
    """查看/更改数据目录；更改成功后程序退出，重启生效。"""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master)
        self.app = app
        self.title("数据位置")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="当前数据目录：").grid(row=0, column=0, sticky="nw")
        ttk.Label(
            body,
            text=str(app.paths.data_dir),
            foreground=COLOR_MUTED,
            wraplength=560,
            justify="left",
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(
            body,
            text=f"来源：{app.data_source or '默认位置'}",
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
        ).grid(row=1, column=1, sticky="w", pady=(0, 4))
        ttk.Button(body, text="打开当前目录", command=self.app.open_data_dir).grid(
            row=2, column=1, sticky="w", pady=(0, 12)
        )

        ttk.Label(body, text="新数据目录：").grid(row=3, column=0, sticky="w")
        self.path_var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.path_var, width=56)
        entry.grid(row=3, column=1, sticky="we")
        ttk.Button(body, text="浏览…", command=self._browse).grid(
            row=3, column=2, padx=(6, 0)
        )

        self.copy_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            body,
            text="复制现有数据到新位置（数据库、备份、日志）",
            variable=self.copy_var,
            command=self._sync_delete_state,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self.delete_var = tk.BooleanVar(value=False)
        self.delete_check = ttk.Checkbutton(
            body,
            text="复制成功后删除原位置的数据文件（不可恢复）",
            variable=self.delete_var,
        )
        self.delete_check.grid(row=5, column=0, columnspan=3, sticky="w")

        ttk.Label(
            body,
            text=(
                "说明：C 盘只保留 location.json 路径指针，数据库/备份/日志都写到新位置。\n"
                "切换成功后会退出程序，重新打开即可使用新位置。"
            ),
            foreground=COLOR_MUTED,
            font=FONT_SMALL,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="应用并退出", command=self._apply).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 6)
        )

        entry.focus_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        center_on_parent(self, master)
        self.grab_set()

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self, title="选择新的数据目录（可先选一个磁盘/父目录）", mustexist=True
        )
        if chosen:
            self.path_var.set(chosen)

    def _sync_delete_state(self) -> None:
        if self.copy_var.get():
            self.delete_check.state(["!disabled"])
        else:
            self.delete_var.set(False)
            self.delete_check.state(["disabled"])

    def _apply(self) -> None:
        raw = self.path_var.get().strip()
        if not raw:
            messagebox.showwarning("提示", "请选择新的数据目录。", parent=self)
            return
        target = Path(raw).expanduser()
        if not target.is_absolute():
            try:
                target = target.resolve()
            except OSError:
                target = target.absolute()
        copy = self.copy_var.get()
        delete_old = self.delete_var.get() and copy

        current = self.app.paths.data_dir
        try:
            same = target.resolve() == current.resolve()
        except OSError:
            same = str(target).lower() == str(current).lower()
        if same:
            messagebox.showinfo("提示", "新位置与当前数据目录相同。", parent=self)
            return

        if copy and (target / "app.db").exists():
            messagebox.showerror(
                "目标已有数据",
                "该目录中已经存在 EasyDocument 数据库。\n"
                "请选择空目录，或关闭「复制现有数据」后直接打开现有数据。",
                parent=self,
            )
            return
        if not copy and not (target / "app.db").exists():
            if not messagebox.askyesno(
                "新位置没有数据库",
                "新位置为空，重启后会创建一个空数据库，当前索引不会带过去。\n\n确定继续？",
                parent=self,
            ):
                return
        if delete_old:
            if not messagebox.askyesno(
                "确认删除原数据",
                "复制成功后删除原位置的数据文件，删除后无法恢复。\n\n确定继续？",
                parent=self,
            ):
                return

        try:
            self.app.change_data_location(target, copy, delete_old)
        except tk.TclError:
            pass


class BoardPickerDialog(tk.Toplevel):
    """从当前作用域的文档中挑选加入图板的文档（可多选、可筛选）。"""

    def __init__(self, master: tk.Misc, documents: list) -> None:
        super().__init__(master)
        self.result: list[int] | None = None
        self._documents = list(documents)
        self.title("添加文档到图板")
        self.transient(master.winfo_toplevel())
        self.geometry("780x540")
        self.minsize(600, 380)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.rowconfigure(2, weight=1)
        body.columnconfigure(0, weight=1)

        ttk.Label(
            body,
            text="勾选需要放进关系图的文档（可按住 Ctrl / Shift 多选）。图板只记录布局与连线，不影响文档本身。",
            foreground=COLOR_MUTED,
            wraplength=720,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        filter_row = ttk.Frame(body)
        filter_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 6))
        ttk.Label(filter_row, text="筛选：").pack(side="left")
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(filter_row, textvariable=self.filter_var, width=36)
        entry.pack(side="left")
        entry.bind("<KeyRelease>", lambda _e: self._fill())
        self.count_label = ttk.Label(
            filter_row, text="", foreground=COLOR_MUTED, font=FONT_SMALL
        )
        self.count_label.pack(side="left", padx=(12, 0))

        self.tree = ttk.Treeview(
            body,
            columns=("name", "path", "status"),
            show="headings",
            selectmode="extended",
        )
        for col, label, width in (
            ("name", "文件名", 220),
            ("path", "路径", 440),
            ("status", "状态", 70),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w", stretch=col == "path")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=2, column=0, sticky="nsew")
        scrollbar.grid(row=2, column=1, sticky="ns")

        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(footer, text="全选", command=lambda: self._select_all(True)).pack(
            side="left"
        )
        ttk.Button(footer, text="全不选", command=lambda: self._select_all(False)).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(footer, text="添加选中", command=self._ok).pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy).pack(
            side="right", padx=(0, 6)
        )

        self.bind("<Escape>", lambda _e: self.destroy())
        self._fill()
        entry.focus_set()
        center_on_parent(self, master)
        self.grab_set()
        self.wait_window(self)

    def _fill(self) -> None:
        keyword = self.filter_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for doc in self._documents:
            name = str(doc["name"])
            path = str(doc["path"])
            if keyword and keyword not in name.lower() and keyword not in path.lower():
                continue
            self.tree.insert(
                "",
                "end",
                iid=str(doc["id"]),
                values=(
                    name,
                    path,
                    "缺失" if doc["status"] == "missing" else "",
                ),
            )
            shown += 1
        self.count_label.config(text=f"共 {shown} 篇可添加")

    def _select_all(self, selected: bool) -> None:
        if selected:
            self.tree.selection_set(self.tree.get_children())
        else:
            self.tree.selection_remove(*self.tree.selection())

    def _ok(self) -> None:
        selection = [int(iid) for iid in self.tree.selection()]
        if not selection:
            messagebox.showinfo("提示", "请至少选择一篇文档。", parent=self)
            return
        self.result = selection
        self.destroy()
