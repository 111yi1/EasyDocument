"""月历视图：自绘 7 列网格，展示每天关联的文档与所属时间段。"""

from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date, timedelta
from tkinter import ttk

from .. import dates, repo
from ..config import (
    COLOR_ACCENT,
    COLOR_BORDER,
    COLOR_CELL_HAS,
    COLOR_MULTI_BG,
    COLOR_MUTED,
    COLOR_SELECTED_BG,
    COLOR_TODAY,
    FONT_SMALL,
    FONT_TITLE,
    FONT_UI_BOLD,
)
from .widgets import Tooltip

WEEKDAY_NAMES = ("一", "二", "三", "四", "五", "六", "日")


class CalendarView(ttk.Frame):
    def __init__(self, master: tk.Misc, app, on_select) -> None:
        super().__init__(master, padding=(6, 4))
        self.app = app
        self.conn = app.conn
        self.on_select = on_select
        today = date.today()
        self.year = today.year
        self.month = today.month
        self.selected: str | None = None
        self.multi: set[str] = set()
        self._anchor: str | None = None
        self._cells: dict[str, dict] = {}
        self._titles: dict[str, list[str]] = {}
        self._periods: dict[str, dict] = {}
        self._weekday_frame: ttk.Frame | None = None
        self.firstweekday = repo.get_int(self.conn, "calendar.firstweekday", 0)
        self._build_header()
        self._build_weekdays()
        self.grid_frame = tk.Frame(self, bg="#fbfbfc", highlightthickness=0, takefocus=1)
        self.grid_frame.pack(fill="both", expand=True)
        self.grid_frame.bind("<Left>", lambda _e: self._move(-1))
        self.grid_frame.bind("<Right>", lambda _e: self._move(1))
        self.grid_frame.bind("<Up>", lambda _e: self._move(-7))
        self.grid_frame.bind("<Down>", lambda _e: self._move(7))
        self.grid_frame.bind("<Prior>", lambda _e: self.go_month(-1))
        self.grid_frame.bind("<Next>", lambda _e: self.go_month(1))
        self.reload()

    # -- 构建 ---------------------------------------------------------------

    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 4))
        ttk.Button(header, text="‹", width=3, command=lambda: self.go_month(-1)).pack(
            side="left"
        )
        ttk.Button(header, text="›", width=3, command=lambda: self.go_month(1)).pack(
            side="left", padx=(2, 0)
        )
        self.month_label = ttk.Label(header, text="", font=FONT_TITLE)
        self.month_label.pack(side="left", padx=10)
        ttk.Button(header, text="今天", command=self.go_today).pack(side="right")
        hint = ttk.Label(header, text="Shift 多选", foreground=COLOR_MUTED, font=FONT_SMALL)
        hint.pack(side="right", padx=(0, 8))
        tip = Tooltip(hint)
        hint.bind(
            "<Enter>",
            lambda e: tip.schedule(
                "按住 Shift 连选多日，再右键可把选中的日期设为时间段。",
                e.x_root,
                e.y_root,
                delay=400,
            ),
        )
        hint.bind("<Leave>", lambda _e: tip.hide())

    def _build_weekdays(self) -> None:
        if self._weekday_frame is not None:
            for child in self._weekday_frame.winfo_children():
                child.destroy()
            frame = self._weekday_frame
        else:
            frame = ttk.Frame(self)
            frame.pack(fill="x", pady=(0, 2))
            self._weekday_frame = frame
        names = [WEEKDAY_NAMES[(self.firstweekday + i) % 7] for i in range(7)]
        for index, name in enumerate(names):
            frame.columnconfigure(index, weight=1)
            ttk.Label(frame, text=name, anchor="center", foreground=COLOR_MUTED).grid(
                row=0, column=index, sticky="we"
            )

    def refresh_settings(self) -> None:
        """设置改变（每周起始日）后重建星期表头并重新载入。"""
        self.firstweekday = repo.get_int(self.conn, "calendar.firstweekday", 0)
        self._build_weekdays()
        self.reload()

    # -- 渲染 ---------------------------------------------------------------

    def reload(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._cells.clear()

        weeks = calendar.monthcalendar(self.year, self.month)
        for column in range(7):
            self.grid_frame.columnconfigure(column, weight=1, uniform="cal")
        for row in range(len(weeks)):
            self.grid_frame.rowconfigure(row, weight=1)

        first = date(self.year, self.month, 1)
        last = date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])
        self._titles = repo.day_titles(
            self.conn,
            first.isoformat(),
            last.isoformat(),
            folder_id=self.app.current_folder_id,
        )
        self._periods = {
            day: {"name": row["name"], "color": row["color"] or COLOR_MUTED}
            for day, row in repo.period_day_map(self.conn).items()
        }
        self.month_label.config(text=f"{self.year} 年 {self.month} 月")

        for row_index, week in enumerate(weeks):
            for column_index, day_number in enumerate(week):
                if not day_number:
                    tk.Frame(self.grid_frame, bg="#fbfbfc").grid(
                        row=row_index, column=column_index, sticky="nsew", padx=1, pady=1
                    )
                    continue
                day = date(self.year, self.month, day_number).isoformat()
                self._cells[day] = self._make_cell(row_index, column_index, day)
        self._repaint()

    def _make_cell(self, row: int, column: int, day: str) -> dict:
        period = self._periods.get(day)
        outer = tk.Frame(
            self.grid_frame,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            bd=0,
            cursor="hand2",
        )
        outer.grid(row=row, column=column, sticky="nsew", padx=1, pady=1)

        bar = tk.Frame(outer, width=4, bg=period["color"] if period else "#ffffff")
        bar.pack(side="left", fill="y")

        inner = tk.Frame(outer, bg="#ffffff")
        inner.pack(side="left", fill="both", expand=True)

        day_label = tk.Label(
            inner,
            text=str(int(day[8:10])),
            anchor="w",
            bg="#ffffff",
            fg=COLOR_MUTED,
            font=FONT_SMALL,
            padx=2,
        )
        day_label.pack(fill="x", pady=(1, 0))

        names = self._titles.get(day, [])
        labels: list[tk.Widget] = [day_label]
        for name in names[:2]:
            label = tk.Label(
                inner,
                text=self._ellipsis(name, 6),
                anchor="w",
                bg="#ffffff",
                fg="#374151",
                font=FONT_SMALL,
                padx=2,
            )
            label.pack(fill="x")
            labels.append(label)
        if len(names) > 2:
            more = tk.Label(
                inner,
                text=f"+{len(names) - 2} 篇",
                anchor="w",
                bg="#ffffff",
                fg="#9aa0a6",
                font=FONT_SMALL,
                padx=2,
            )
            more.pack(fill="x")
            labels.append(more)

        widgets = [outer, bar, inner, *labels]
        for widget in widgets:
            widget.bind("<Button-1>", lambda e, d=day: self._click(d, e))
            widget.bind("<Button-3>", lambda e, d=day: self._right_click(d, e))

        tip = Tooltip(outer)
        tip_text = self._cell_tooltip(day, names, period)
        if tip_text:
            for widget in widgets:
                widget.bind(
                    "<Enter>",
                    lambda e, t=tip_text: tip.schedule(t, e.x_root, e.y_root, delay=600),
                )
                widget.bind("<Leave>", lambda _e: tip.hide())

        return {
            "frame": outer,
            "bar": bar,
            "inner": inner,
            "labels": labels,
            "has_entries": bool(names),
            "period": period,
        }

    def _cell_tooltip(self, day: str, names: list[str], period) -> str:
        if not names and period is None:
            return ""
        head = f"{dates.format_day_cn(day)} · {len(names)} 篇" if names else dates.format_day_cn(day)
        lines = [head]
        if period is not None:
            lines.append(f"时间段：{period['name']}")
        lines.extend(names[:8])
        if len(names) > 8:
            lines.append(f"…共 {len(names)} 篇")
        return "\n".join(lines)

    def _repaint(self) -> None:
        today = dates.today_str()
        for day, info in self._cells.items():
            if day == self.selected:
                bg = COLOR_SELECTED_BG
            elif day in self.multi:
                bg = COLOR_MULTI_BG
            elif info["has_entries"]:
                bg = COLOR_CELL_HAS
            else:
                bg = "#ffffff"
            if day == today:
                border = COLOR_TODAY
            elif day in self.multi:
                border = COLOR_ACCENT
            else:
                border = COLOR_BORDER
            info["frame"].configure(bg=bg, highlightbackground=border)
            info["inner"].configure(bg=bg)
            for widget in info["labels"]:
                widget.configure(bg=bg)
            period = info["period"]
            info["bar"].configure(bg=period["color"] if period else bg)
            if day == today:
                info["labels"][0].configure(fg=COLOR_ACCENT, font=FONT_UI_BOLD)
            else:
                info["labels"][0].configure(fg=COLOR_MUTED, font=FONT_SMALL)

    # -- 交互 ---------------------------------------------------------------

    def _click(self, day: str, event=None) -> None:
        shift = bool(event is not None and (event.state & 0x0001) and self._anchor)
        if shift and self._anchor:
            self.multi = set(self._day_range(self._anchor, day))
        else:
            self.multi = set()
            self._anchor = day
        self.selected = day
        self.grid_frame.focus_set()
        self._repaint()
        self.on_select(day)

    def _right_click(self, day: str, event) -> None:
        if day not in self.multi:
            self.multi = set()
            self._anchor = day
            self.selected = day
        self._repaint()
        days = self.selection_days()
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label=f"查看 {dates.format_day_cn(day)}", command=lambda: self.on_select(day)
        )
        menu.add_separator()
        menu.add_command(
            label=f"设为时间段…（{len(days)} 天）",
            command=lambda d=list(days): self.app.create_period_from_days(d),
        )
        periods = repo.list_periods(self.conn)
        if periods:
            submenu = tk.Menu(menu, tearoff=0)
            for period in periods:
                submenu.add_command(
                    label=str(period["name"]),
                    command=lambda pid=int(period["id"]), d=list(days): (
                        self.app.add_days_to_period(pid, d)
                    ),
                )
            menu.add_cascade(label="加入时间段", menu=submenu)
            menu.add_command(
                label="从时间段移出",
                command=lambda d=list(days): self.app.remove_days_from_period(d),
            )
        menu.add_separator()
        menu.add_command(label="清除多选", command=self.clear_multi)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def selection_days(self) -> list[str]:
        """当前用于批量操作的日期：多选优先，否则为单选日。"""
        if self.multi:
            return sorted(self.multi)
        return [self.selected] if self.selected else []

    def _day_range(self, start: str, end: str) -> list[str]:
        first = date.fromisoformat(min(start, end))
        last = date.fromisoformat(max(start, end))
        result = []
        while first <= last:
            result.append(first.isoformat())
            first += timedelta(days=1)
        return result

    def _ellipsis(self, text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def go_month(self, delta: int) -> None:
        month_index = self.month - 1 + delta
        self.year += month_index // 12
        self.month = month_index % 12 + 1
        self.reload()

    def go_today(self) -> None:
        today = date.today()
        self.year, self.month = today.year, today.month
        self.selected = today.isoformat()
        self.multi = set()
        self._anchor = self.selected
        self.reload()
        self.on_select(self.selected)

    def select_day(self, day: str, notify: bool = False) -> None:
        target = date.fromisoformat(day)
        self.multi = set()
        self._anchor = day
        if target.year != self.year or target.month != self.month:
            self.year, self.month = target.year, target.month
            self.selected = day
            self.reload()
        else:
            self.selected = day
            self._repaint()
        if notify:
            self.on_select(day)

    def clear_multi(self) -> None:
        self.multi = set()
        self._repaint()

    def clear_selection(self) -> None:
        self.selected = None
        self.multi = set()
        self._anchor = None
        self._repaint()

    def _move(self, days: int) -> None:
        base = date.fromisoformat(self.selected) if self.selected else date.today()
        self.select_day((base + timedelta(days=days)).isoformat(), notify=True)
