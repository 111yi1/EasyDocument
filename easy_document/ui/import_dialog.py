"""扫描结果导入向导：勾选文件、确认为每条文档的入库日期。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import dates
from ..config import FONT_SMALL, FONT_UI
from ..scanner import FileInfo
from .widgets import human_size


class ImportDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        records: list[tuple[dict, FileInfo]],
        default_source: str = "mtime",
    ) -> None:
        super().__init__(master)
        self.result: list[dict] | None = None
        self.rows: list[dict] = []
        for folder, fi in records:
            day = fi.mtime_day if default_source != "added" else dates.today_str()
            self.rows.append(
                {
                    "folder": folder,
                    "fi": fi,
                    "selected": True,
                    "day": day,
                    "note": "",
                }
            )

        self.title("导入扫描到的文件")
        self.transient(master.winfo_toplevel())
        self.geometry("1040x560")
        self.minsize(820, 400)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="勾选需要纳入管理的文档（Word / PPT / Markdown）；双击「入库日期」/「备注」列可逐条修改。",
            font=FONT_UI,
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="日期默认取文件修改日期（可在设置中改为添加日期）。取消勾选的文件不会被索引，之后扫描仍会再次提示。",
            foreground="#6b7280",
            font=FONT_SMALL,
        ).pack(anchor="w", pady=(2, 8))

        columns = ("sel", "name", "folder", "rel", "size", "mtime", "day", "note")
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="extended")
        for col, label, width in (
            ("sel", "选", 40),
            ("name", "文件名", 180),
            ("folder", "文件夹", 120),
            ("rel", "相对路径", 230),
            ("size", "大小", 75),
            ("mtime", "修改时间", 130),
            ("day", "入库日期", 95),
            ("note", "备注", 140),
        ):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w", stretch=col in ("rel", "note"))
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")

        footer = ttk.Frame(self, padding=(12, 0, 12, 12))
        footer.pack(fill="x")
        ttk.Button(footer, text="全选", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(footer, text="全不选", command=lambda: self._set_all(False)).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(footer, text="全部用文件修改日期", command=self._all_mtime).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(footer, text="全部用今天", command=self._all_today).pack(
            side="left", padx=(4, 0)
        )
        self.count_label = ttk.Label(footer, text="", foreground="#6b7280")
        self.count_label.pack(side="left", padx=(14, 0))
        ttk.Button(footer, text="确定导入", command=self._ok).pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=(0, 6))

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._on_double)
        self.bind("<Escape>", lambda _e: self.destroy())
        self._fill()
        self._update_count()

        self.update_idletasks()
        root = master.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_width()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.wait_window(self)

    # -- 内部 ---------------------------------------------------------------

    def _fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(index), values=self._values(row))

    def _values(self, row: dict) -> tuple:
        fi: FileInfo = row["fi"]
        return (
            "☑" if row["selected"] else "☐",
            fi.name,
            row["folder"]["name"],
            fi.rel_path,
            human_size(fi.size),
            fi.mtime.replace("T", " ")[:16],
            row["day"],
            row["note"],
        )

    def _refresh_row(self, index: int) -> None:
        iid = str(index)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._values(self.rows[index]))

    def _update_count(self) -> None:
        selected = sum(1 for row in self.rows if row["selected"])
        self.count_label.config(text=f"共 {len(self.rows)} 个，已选 {selected} 个")

    def _on_click(self, event) -> None:
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        index = int(iid)
        self.rows[index]["selected"] = not self.rows[index]["selected"]
        self._refresh_row(index)
        self._update_count()

    def _on_double(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        column = self.tree.identify_column(event.x)
        index = int(iid)
        if column == "#7":
            self._edit_day(index)
        elif column == "#8":
            self._edit_note(index)

    def _edit_day(self, index: int) -> None:
        row = self.rows[index]
        value = simpledialog.askstring(
            "入库日期",
            f"{row['fi'].name}\n\n日期（YYYY-MM-DD）：",
            initialvalue=row["day"],
            parent=self,
        )
        if not value:
            return
        parsed = dates.try_parse_day(value)
        if parsed is None:
            messagebox.showerror("日期无效", "请按 YYYY-MM-DD 填写，例如 2026-10-29。", parent=self)
            return
        row["day"] = parsed
        self._refresh_row(index)

    def _edit_note(self, index: int) -> None:
        row = self.rows[index]
        value = simpledialog.askstring(
            "备注",
            f"{row['fi'].name}\n\n备注：",
            initialvalue=row["note"],
            parent=self,
        )
        if value is None:
            return
        row["note"] = value.strip()
        self._refresh_row(index)

    def _set_all(self, selected: bool) -> None:
        for index, row in enumerate(self.rows):
            row["selected"] = selected
            self._refresh_row(index)
        self._update_count()

    def _all_mtime(self) -> None:
        for index, row in enumerate(self.rows):
            row["day"] = row["fi"].mtime_day
            self._refresh_row(index)

    def _all_today(self) -> None:
        today = dates.today_str()
        for index, row in enumerate(self.rows):
            row["day"] = today
            self._refresh_row(index)

    def _ok(self) -> None:
        selected = [row for row in self.rows if row["selected"]]
        if not selected:
            messagebox.showinfo("提示", "请至少勾选一个文件。", parent=self)
            return
        self.result = selected
        self.destroy()
