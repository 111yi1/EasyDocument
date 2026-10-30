"""通用界面组件：可复用列表页签、悬浮提示、格式化函数。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..config import FONT_SMALL


def human_size(num: int | None) -> str:
    if not num:
        return "0 B"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{num} B"


def tint_color(color: str | None, ratio: float = 0.85) -> str:
    """把 #rrggbb 颜色与白色混合成浅色背景；ratio 为颜色占比（0~1）。

    颜色非法（或为空）时返回白色，保证 Canvas 不会因颜色值报错。
    """
    value = (color or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return "#ffffff"
    try:
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
    except ValueError:
        return "#ffffff"
    ratio = max(0.0, min(1.0, ratio))
    mixed = tuple(
        round(channel * ratio + 255 * (1 - ratio))
        for channel in (red, green, blue)
    )
    return "#{:02x}{:02x}{:02x}".format(*mixed)


class Tooltip:
    """轻量悬浮提示：由调用方在鼠标移动时 schedule()。"""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None

    def schedule(self, text: str, x_root: int, y_root: int, delay: int = 450) -> None:
        self.hide()
        if not text:
            return
        self._after_id = self.widget.after(
            delay, lambda: self._show(text, x_root, y_root)
        )

    def _show(self, text: str, x_root: int, y_root: int) -> None:
        self._after_id = None
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x_root + 14}+{y_root + 18}")
        label = tk.Label(
            tip,
            text=text,
            justify="left",
            background="#ffffe1",
            foreground="#1f2937",
            relief="solid",
            borderwidth=1,
            font=FONT_SMALL,
            padx=6,
            pady=3,
        )
        label.pack()
        self._tip = tip

    def hide(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class ListTab(ttk.Frame):
    """带滚动条、空状态提示与悬浮提示的列表页签。

    columns: [(列 id, 表头, 宽度), ...]
    on_select(iid)  选中行变化时回调
    on_activate(iid) 双击行时回调
    """

    def __init__(
        self,
        master: tk.Misc,
        columns: list[tuple[str, str, int]],
        *,
        empty_text: str = "（暂无数据）",
        on_select=None,
        on_activate=None,
    ) -> None:
        super().__init__(master)
        self._on_select = on_select
        self._on_activate = on_activate
        self._suspend = False
        self._hover_row: str | None = None
        self.tooltips: dict[str, str] = {}

        self.tree = ttk.Treeview(
            self,
            columns=[c[0] for c in columns],
            show="headings",
            selectmode="browse",
        )
        for index, (col_id, label, width) in enumerate(columns):
            self.tree.heading(col_id, text=label)
            self.tree.column(
                col_id,
                width=width,
                minwidth=50,
                anchor="w",
                stretch=index == len(columns) - 1,
            )
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.empty_label = ttk.Label(
            self, text=empty_text, foreground="#9aa0a6", justify="center"
        )
        self.tooltip = Tooltip(self.tree)

        self.tree.bind("<<TreeviewSelect>>", self._handle_select)
        self.tree.bind("<Double-1>", self._handle_activate)
        self.tree.bind("<Motion>", self._handle_motion)
        self.tree.bind("<Leave>", lambda _e: self._hide_tip())

    # -- 数据 ---------------------------------------------------------------

    def set_rows(self, rows: list[tuple[str, tuple, str]]) -> None:
        """rows: [(iid, values, tooltip_text), ...]"""
        self._suspend = True
        try:
            self.tree.delete(*self.tree.get_children())
            self.tooltips.clear()
            for iid, values, tip in rows:
                self.tree.insert("", "end", iid=iid, values=values)
                self.tooltips[iid] = tip or ""
        finally:
            self._suspend = False
        if rows:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.38, anchor="center")

    def select_iid(self, iid: str) -> None:
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def current_iid(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def clear_selection(self) -> None:
        self._suspend = True
        try:
            self.tree.selection_remove(*self.tree.selection())
        finally:
            self._suspend = False

    # -- 事件 ---------------------------------------------------------------

    def _handle_select(self, _event=None) -> None:
        if self._suspend or self._on_select is None:
            return
        selection = self.tree.selection()
        if selection:
            self._on_select(selection[0])

    def _handle_activate(self, event) -> None:
        if self._on_activate is None:
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self._on_activate(iid)

    def _handle_motion(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row == self._hover_row:
            return
        self._hover_row = row
        tip = self.tooltips.get(row, "")
        if tip:
            self.tooltip.schedule(tip, event.x_root, event.y_root)
        else:
            self._hide_tip()

    def _hide_tip(self) -> None:
        self._hover_row = None
        self.tooltip.hide()
