"""逻辑关系图：自选文档作为节点，拖拽排版、连线表达关系，位置保存在数据库。

v0.4：
- 节点可按“最近一条落在时间段内的记录”自动染色，也可单独指定颜色；
- 支持 Ctrl+点击多选节点，批量拖动/染色/移除；
- 支持与文档无关的注释块（自由文本），可拖动、编辑、换底色。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .. import repo
from ..config import (
    COLOR_ACCENT,
    COLOR_MUTED,
    COLOR_WARN,
    FONT_FAMILY,
    FONT_SMALL,
    FONT_UI,
)
from .dialogs import ColorChoiceDialog, NoteDialog
from .widgets import Tooltip, tint_color

NODE_W = 160
NODE_H = 64
GRID_X = 204
GRID_Y = 116
NOTE_W = 232
NOTE_MIN_H = 70
NOTE_DEFAULT_COLOR = "#fef9c3"
NODE_DEFAULT_COLOR = "#93c5fd"


class GraphView(ttk.Frame):
    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, padding=(6, 4))
        self.app = app
        self.conn = app.conn
        self.nodes: dict[int, dict] = {}
        self.notes: dict[int, dict] = {}
        self.edges: list[dict] = []
        self.selected_docs: set[int] = set()
        self.selected_note: int | None = None
        self.selected_edge: int | None = None
        self.pending_doc: int | None = None
        self._rects: dict[int, int] = {}
        self._note_rects: dict[int, int] = {}
        self._note_heights: dict[int, int] = {}
        self._drag: dict | None = None
        self._hover_key: tuple[str, int] | None = None
        self.connect_var = tk.BooleanVar(value=False)
        # 显示缩放与字号：坐标仍按“世界坐标”保存，绘制时统一乘以 zoom。
        self.zoom = 1.0
        self.font_size = 9
        self._scope = app.current_folder_id
        self._undo: list[tuple[str, dict]] = []
        self._build()
        self.reload()

    # -- 构建 ---------------------------------------------------------------

    def _build(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text="添加文档…", command=self.add_documents_dialog).pack(
            side="left"
        )
        ttk.Button(bar, text="添加注释", command=self.add_note_dialog).pack(
            side="left", padx=(6, 0)
        )
        ttk.Checkbutton(
            bar,
            text="连线模式",
            variable=self.connect_var,
            style="Toolbutton",
            command=self._on_connect_toggle,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(bar, text="清空图板", command=self.clear_board).pack(
            side="left", padx=(6, 0)
        )
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(bar, text="缩放：", foreground=COLOR_MUTED, font=FONT_SMALL).pack(
            side="left"
        )
        ttk.Button(
            bar, text="−", width=3, command=lambda: self._zoom_step(-10)
        ).pack(side="left")
        self.zoom_label = ttk.Label(
            bar, text="100%", width=5, anchor="center", font=FONT_SMALL
        )
        self.zoom_label.pack(side="left")
        ttk.Button(
            bar, text="+", width=3, command=lambda: self._zoom_step(10)
        ).pack(side="left")
        ttk.Button(bar, text="1:1", width=4, command=self._reset_zoom).pack(
            side="left", padx=(3, 0)
        )
        self.hint = ttk.Label(bar, text="", foreground=COLOR_MUTED, font=FONT_SMALL)
        self.hint.pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            body,
            bg="#fbfbfc",
            highlightthickness=0,
            scrollregion=(0, 0, 1200, 760),
            takefocus=1,
        )
        vscroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        hscroll = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="we")

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", lambda _e: self._hide_tip())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Escape>", lambda _e: self._cancel_pending())
        self.canvas.bind("<Control-z>", lambda _e: self.undo())
        self.canvas.bind("<Control-Z>", lambda _e: self.undo())
        self.tooltip = Tooltip(self.canvas)

    # -- 数据加载与渲染 -----------------------------------------------------

    def reload(self) -> None:
        self.canvas.delete("all")
        self._rects.clear()
        self._note_rects.clear()
        self._note_heights.clear()
        self._drag = None
        self.tooltip.hide()
        self._hover_key = None

        scope = self.app.current_folder_id
        if scope != self._scope:
            # 切换文件夹作用域后，旧快照可能跨越作用域，撤销栈不再可靠。
            self._scope = scope
            self._undo.clear()
        self.font_size = max(6, min(18, repo.get_int(self.conn, "graph.font_size", 9)))
        self.zoom = max(
            0.5,
            min(2.5, repo.get_int(self.conn, "graph.zoom_percent", 100) / 100.0),
        )
        self._update_zoom_label()
        period_colors = repo.document_period_colors(self.conn, folder_id=scope)
        self.nodes = {}
        for row in repo.list_board_nodes(self.conn, folder_id=scope):
            doc_id = int(row["document_id"])
            period = period_colors.get(doc_id)
            self.nodes[doc_id] = {
                "x": float(row["x"]),
                "y": float(row["y"]),
                "name": str(row["name"]),
                "label": str(row["label"] or ""),
                "status": str(row["status"]),
                "ext": str(row["ext"] or ""),
                "color": row["color"],
                "period_color": period["color"] if period is not None else None,
                "period_name": str(period["name"]) if period is not None else "",
            }
        self.notes = {
            int(row["id"]): {
                "x": float(row["x"]),
                "y": float(row["y"]),
                "text": str(row["text"] or ""),
                "color": row["color"],
                "height": NOTE_MIN_H,
            }
            for row in repo.list_board_notes(self.conn, folder_id=scope)
        }
        self.edges = [
            {
                "edge_id": int(row["edge_id"]),
                "from_doc": int(row["from_doc"]),
                "to_doc": int(row["to_doc"]),
                "label": str(row["label"] or ""),
                "from_name": str(row["from_name"]),
                "to_name": str(row["to_name"]),
            }
            for row in repo.list_board_edges(self.conn, folder_id=scope)
        ]

        self.selected_docs &= set(self.nodes)
        if self.selected_note not in self.notes:
            self.selected_note = None
        if self.selected_edge not in {edge["edge_id"] for edge in self.edges}:
            self.selected_edge = None
        if self.pending_doc not in self.nodes:
            self.pending_doc = None

        for doc_id in self.nodes:
            self._draw_node(doc_id)
        self._draw_edges()
        self._draw_notes()
        self._update_scrollregion()
        self._update_hint()

        if not self.nodes and not self.notes:
            self.canvas.create_text(
                60,
                50,
                anchor="nw",
                justify="left",
                font=FONT_UI,
                fill=COLOR_MUTED,
                text=(
                    "图板是空的。\n\n"
                    "点上方「添加文档…」加入文档，或「添加注释」放一个说明块；\n"
                    "开启「连线模式」后依次点击两个节点即可连线。"
                ),
            )

    # -- 缩放与字体 ---------------------------------------------------------

    def _update_zoom_label(self) -> None:
        self.zoom_label.config(text=f"{round(self.zoom * 100)}%")

    def _display_font(self, delta: int = 0, bold: bool = False) -> tuple:
        """按“字号设置 × 缩放比例”生成 Canvas 实际字体。"""
        size = max(6, min(42, int(round((self.font_size + delta) * self.zoom))))
        return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)

    def _text_limit(self, base_limit: int) -> int:
        """字号变大时减少每行字符数，避免文字溢出节点框。"""
        return max(4, int(base_limit * 9 / max(6, self.font_size)))

    def _zoom_step(self, delta: int) -> None:
        """按百分比调整缩放并保存；向上滚放大（delta 为正）。"""
        current = repo.get_int(self.conn, "graph.zoom_percent", 100)
        target = max(50, min(250, current + int(delta)))
        if target == current:
            return
        repo.set_setting(self.conn, "graph.zoom_percent", str(target))
        self.zoom = target / 100.0
        self.reload()
        self.app.log.info("关系图缩放：%d%%", target)

    def _reset_zoom(self) -> None:
        if repo.get_int(self.conn, "graph.zoom_percent", 100) == 100:
            return
        repo.set_setting(self.conn, "graph.zoom_percent", "100")
        self.zoom = 1.0
        self.reload()

    # -- 撤销 ---------------------------------------------------------------

    def _push_undo(self, label: str) -> None:
        """在修改图板前保存整张图板快照；Ctrl+Z 时整体恢复。"""
        try:
            snapshot = repo.board_snapshot(self.conn)
        except Exception:
            self.app.log.exception("保存图板撤销快照失败")
            return
        self._undo.append((label, snapshot))
        if len(self._undo) > 50:
            self._undo.pop(0)

    def undo(self) -> str:
        if not self._undo:
            self._update_hint()
            self.hint.config(text="没有可撤销的关系图操作")
            return "break"
        label, snapshot = self._undo.pop()
        try:
            repo.restore_board_snapshot(self.conn, snapshot)
        except Exception:
            self.app.log.exception("撤销关系图操作失败")
            messagebox.showerror(
                "撤销失败", "图板未能恢复到操作前的状态，详细信息见日志。", parent=self
            )
            return "break"
        self.selected_docs.clear()
        self.selected_note = None
        self.selected_edge = None
        self.pending_doc = None
        self.app.doc_panel.show_recent()
        self.app.log.info("撤销关系图操作：%s（剩余 %d 步）", label, len(self._undo))
        self.reload()
        self.hint.config(text=f"已撤销：{label}（Ctrl+Z 可继续撤销）")
        return "break"

    def _node_accent(self, doc_id: int) -> str:
        node = self.nodes[doc_id]
        return node["color"] or node["period_color"] or NODE_DEFAULT_COLOR

    def _draw_node(self, doc_id: int) -> None:
        node = self.nodes[doc_id]
        z = self.zoom
        x, y = node["x"] * z, node["y"] * z
        width, height = NODE_W * z, NODE_H * z
        accent = self._node_accent(doc_id)
        missing = node["status"] == "missing"
        tags = (f"node:{doc_id}", "node")
        fill = "#fef3c7" if missing else tint_color(accent, 0.22)
        outline = COLOR_WARN if missing else accent
        rect = self.canvas.create_rectangle(
            x,
            y,
            x + width,
            y + height,
            fill=fill,
            outline=outline,
            width=max(1, int(round(2 * z))),
            tags=tags,
        )
        self._rects[doc_id] = rect
        stripe = max(2.0, 5 * z)
        self.canvas.create_rectangle(
            x + max(1.0, z),
            y + max(1.0, z),
            x + stripe,
            y + height - max(1.0, z),
            fill=COLOR_WARN if missing else accent,
            outline="",
            tags=tags,
        )
        self.canvas.create_text(
            x + 14 * z,
            y + 20 * z,
            anchor="w",
            text=self._ellipsis(node["name"], self._text_limit(13)),
            font=self._display_font(0, True),
            fill="#1f2937",
            tags=tags,
        )
        # 文件框里只显示“节点注释”（board_nodes.label）；不再显示时间段名与扩展名。
        subtitle = str(node.get("label") or "")
        if missing:
            subtitle = f"{subtitle} · 缺失" if subtitle else "文件缺失"
        if subtitle:
            self.canvas.create_text(
                x + 14 * z,
                y + 43 * z,
                anchor="w",
                text=self._ellipsis(subtitle, self._text_limit(18)),
                font=self._display_font(-1),
                fill=COLOR_WARN if missing else COLOR_MUTED,
                tags=tags,
            )
        self._style_node(doc_id)

    def _style_node(self, doc_id: int) -> None:
        rect = self._rects.get(doc_id)
        node = self.nodes.get(doc_id)
        if rect is None or node is None:
            return
        z = self.zoom
        missing = node["status"] == "missing"
        if missing:
            fill, outline = "#fef3c7", COLOR_WARN
        else:
            accent = self._node_accent(doc_id)
            fill, outline = tint_color(accent, 0.22), accent
        width = max(1, int(round(2 * z)))
        if doc_id in self.selected_docs:
            outline, width = COLOR_ACCENT, max(2, int(round(4 * z)))
        if doc_id == self.pending_doc:
            outline, width = "#059669", max(2, int(round(4 * z)))
        self.canvas.itemconfigure(rect, fill=fill, outline=outline, width=width)

    def _style_all_nodes(self) -> None:
        for doc_id in self.nodes:
            self._style_node(doc_id)

    def _draw_edges(self) -> None:
        self.canvas.delete("edge")
        z = self.zoom
        for edge in self.edges:
            source = self.nodes.get(edge["from_doc"])
            target = self.nodes.get(edge["to_doc"])
            if source is None or target is None:
                continue
            x1, y1 = self._anchor(source, target)
            x2, y2 = self._anchor(target, source)
            x1, y1, x2, y2 = x1 * z, y1 * z, x2 * z, y2 * z
            selected = edge["edge_id"] == self.selected_edge
            tags = (f"edge:{edge['edge_id']}", "edge")
            self.canvas.create_line(
                x1,
                y1,
                x2,
                y2,
                fill=COLOR_ACCENT if selected else "#94a3b8",
                width=max(1, int(round((3 if selected else 2) * z))),
                arrow="last",
                arrowshape=(10 * z, 12 * z, 4 * z),
                tags=tags,
            )
            if edge["label"]:
                text_item = self.canvas.create_text(
                    (x1 + x2) / 2,
                    (y1 + y2) / 2,
                    text=edge["label"],
                    font=self._display_font(-1),
                    fill=COLOR_MUTED,
                    tags=tags,
                )
                box = self.canvas.bbox(text_item)
                if box:
                    background = self.canvas.create_rectangle(
                        box[0] - 2,
                        box[1] - 1,
                        box[2] + 2,
                        box[3] + 1,
                        fill="#fbfbfc",
                        outline="",
                        tags=tags,
                    )
                    self.canvas.tag_lower(background, text_item)
        self.canvas.tag_lower("edge")

    def _draw_notes(self) -> None:
        self.canvas.delete("note")
        self._note_rects.clear()
        for note_id in self.notes:
            self._draw_note(note_id)

    def _draw_note(self, note_id: int) -> None:
        note = self.notes[note_id]
        z = self.zoom
        x, y = note["x"] * z, note["y"] * z
        tags = (f"note:{note_id}", "note")
        base = note["color"] or NOTE_DEFAULT_COLOR
        fill = tint_color(base, 0.55) if note["color"] else NOTE_DEFAULT_COLOR
        outline = COLOR_ACCENT if self.selected_note == note_id else "#d97706"
        width = max(1, int(round((3 if self.selected_note == note_id else 2) * z)))
        text_item = self.canvas.create_text(
            x + 12 * z,
            y + 10 * z,
            anchor="nw",
            width=(NOTE_W - 24) * z,
            text=note["text"] or "（空注释）",
            font=self._display_font(),
            fill="#374151",
            tags=tags,
        )
        box = self.canvas.bbox(text_item)
        content_height = ((box[3] - box[1]) / z) if box else 0
        height = max(NOTE_MIN_H, content_height + 22)
        rect = self.canvas.create_rectangle(
            x,
            y,
            x + NOTE_W * z,
            y + height * z,
            fill=fill,
            outline=outline,
            width=width,
            tags=tags,
        )
        self.canvas.tag_lower(rect, text_item)
        self._note_rects[note_id] = rect
        self._note_heights[note_id] = height
        note["height"] = height

    def _anchor(self, node: dict, other: dict) -> tuple[float, float]:
        """从节点中心指向对方中心，裁到矩形边框，避免连线穿过节点。"""
        cx, cy = node["x"] + NODE_W / 2, node["y"] + NODE_H / 2
        ox, oy = other["x"] + NODE_W / 2, other["y"] + NODE_H / 2
        dx, dy = ox - cx, oy - cy
        if dx == 0 and dy == 0:
            return cx, cy
        scale_x = (NODE_W / 2) / abs(dx) if dx else float("inf")
        scale_y = (NODE_H / 2) / abs(dy) if dy else float("inf")
        scale = min(scale_x, scale_y)
        return cx + dx * scale, cy + dy * scale

    def _update_scrollregion(self) -> None:
        max_x = max(
            [n["x"] + NODE_W for n in self.nodes.values()]
            + [n["x"] + NOTE_W for n in self.notes.values()]
            + [0]
        )
        max_y = max(
            [n["y"] + NODE_H for n in self.nodes.values()]
            + [n["y"] + n["height"] for n in self.notes.values()]
            + [0]
        )
        z = self.zoom
        self.canvas.configure(
            scrollregion=(
                0,
                0,
                max(900 * z, (max_x + 80) * z),
                max(640 * z, (max_y + 80) * z),
            )
        )

    def _update_hint(self) -> None:
        if self.connect_var.get():
            if self.pending_doc is None:
                text = "连线模式：先点击起点文档"
            else:
                name = self.nodes.get(self.pending_doc, {}).get("name", "")
                text = f"起点「{name}」→ 再点击终点文档（Esc 取消）"
        elif len(self.selected_docs) > 1:
            text = (
                f"已选 {len(self.selected_docs)} 个节点 · 可一起拖动/染色/注释/移除"
                " · Ctrl+Z 撤销"
            )
        else:
            text = "Ctrl+点击多选 · Ctrl+滚轮缩放 · Ctrl+Z 撤销 · 右键更多"
        self.hint.config(text=text)

    # -- 命中与选择 ---------------------------------------------------------

    def _hit(self, event) -> tuple[str, int] | None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        for item in reversed(self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)):
            for tag in self.canvas.gettags(item):
                if tag.startswith("note:"):
                    return ("note", int(tag[5:]))
                if tag.startswith("node:"):
                    return ("node", int(tag[5:]))
                if tag.startswith("edge:"):
                    return ("edge", int(tag[5:]))
        return None

    def _refresh_selection(self) -> None:
        self._style_all_nodes()
        self._draw_edges()
        self._draw_notes()
        self._update_hint()

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        ctrl = bool(event.state & 0x0004)
        hit = self._hit(event)
        if hit is None:
            self.selected_docs.clear()
            self.selected_note = None
            self.selected_edge = None
            self._refresh_selection()
            return

        kind, ident = hit
        if kind == "node":
            if self.connect_var.get():
                self._handle_connect(ident)
                return
            if ctrl:
                if ident in self.selected_docs:
                    self.selected_docs.discard(ident)
                else:
                    self.selected_docs.add(ident)
                self.selected_note = None
                self.selected_edge = None
                self._refresh_selection()
                if ident in self.selected_docs:
                    self._begin_node_drag(ident, event)
                return
            if ident not in self.selected_docs:
                self.selected_docs = {ident}
            self.selected_note = None
            self.selected_edge = None
            self._refresh_selection()
            self.app.doc_panel.show_document(ident)
            self._begin_node_drag(ident, event)
        elif kind == "note":
            self.selected_docs.clear()
            self.selected_edge = None
            self.selected_note = ident
            self._refresh_selection()
            note = self.notes[ident]
            cx = self.canvas.canvasx(event.x) / self.zoom
            cy = self.canvas.canvasy(event.y) / self.zoom
            self._drag = {
                "kind": "note",
                "note_id": ident,
                "dx": cx - note["x"],
                "dy": cy - note["y"],
                "moved": False,
            }
            self.canvas.configure(cursor="fleur")
        else:
            self.selected_docs.clear()
            self.selected_note = None
            self.selected_edge = ident
            self._refresh_selection()

    def _begin_node_drag(self, doc_id: int, event) -> None:
        node = self.nodes[doc_id]
        cx = self.canvas.canvasx(event.x) / self.zoom
        cy = self.canvas.canvasy(event.y) / self.zoom
        self._drag = {
            "kind": "nodes",
            "primary": doc_id,
            "dx": cx - node["x"],
            "dy": cy - node["y"],
            "origins": {
                item: (self.nodes[item]["x"], self.nodes[item]["y"])
                for item in self.selected_docs
            },
            "applied": (0.0, 0.0),
            "moved": False,
        }
        self.canvas.configure(cursor="fleur")

    def _on_motion(self, event) -> None:
        drag = self._drag
        if drag is None:
            return
        cx = self.canvas.canvasx(event.x) / self.zoom
        cy = self.canvas.canvasy(event.y) / self.zoom
        if drag["kind"] == "note":
            note = self.notes.get(drag["note_id"])
            if note is None:
                return
            new_x = max(0.0, cx - drag["dx"])
            new_y = max(0.0, cy - drag["dy"])
            dx, dy = new_x - note["x"], new_y - note["y"]
            if dx == 0 and dy == 0:
                return
            note["x"], note["y"] = new_x, new_y
            self.canvas.move(
                f"note:{drag['note_id']}", dx * self.zoom, dy * self.zoom
            )
            drag["moved"] = True
            self._update_scrollregion()
            return

        primary = drag["primary"]
        if primary not in self.nodes:
            return
        origin_x, origin_y = drag["origins"][primary]
        delta_x = cx - drag["dx"] - origin_x
        delta_y = cy - drag["dy"] - origin_y
        min_x = min(value[0] for value in drag["origins"].values())
        min_y = min(value[1] for value in drag["origins"].values())
        delta_x = max(delta_x, -min_x)
        delta_y = max(delta_y, -min_y)
        step_x = delta_x - drag["applied"][0]
        step_y = delta_y - drag["applied"][1]
        if abs(step_x) < 1e-9 and abs(step_y) < 1e-9:
            return
        for item in self.selected_docs:
            node = self.nodes[item]
            node["x"] += step_x
            node["y"] += step_y
            self.canvas.move(
                f"node:{item}", step_x * self.zoom, step_y * self.zoom
            )
        drag["applied"] = (delta_x, delta_y)
        drag["moved"] = True
        self._draw_edges()
        self._update_scrollregion()

    def _on_release(self, _event) -> None:
        drag = self._drag
        self._drag = None
        try:
            self.canvas.configure(cursor="")
        except tk.TclError:
            pass
        if drag is None or not drag.get("moved"):
            return
        try:
            if drag["kind"] == "note":
                note = self.notes.get(drag["note_id"])
                if note is not None:
                    self._push_undo("移动注释块")
                    repo.update_board_note(
                        self.conn,
                        drag["note_id"],
                        x=round(note["x"], 1),
                        y=round(note["y"], 1),
                    )
                    self.app.log.info(
                        "移动图板注释：note=%s -> (%.0f, %.0f)",
                        drag["note_id"],
                        note["x"],
                        note["y"],
                    )
            else:
                positions = {
                    item: (
                        round(self.nodes[item]["x"], 1),
                        round(self.nodes[item]["y"], 1),
                    )
                    for item in self.selected_docs
                    if item in self.nodes
                }
                self._push_undo("移动节点")
                repo.update_board_nodes_positions(self.conn, positions)
                self.app.log.info(
                    "移动图板节点：%d 个 -> %s",
                    len(positions),
                    positions,
                )
        except Exception:
            self.app.log.exception("保存图板位置失败")
            messagebox.showerror(
                "保存失败", "图板位置未能保存，详细信息见日志。", parent=self
            )

    def _on_double(self, event) -> None:
        hit = self._hit(event)
        if hit is None:
            return
        kind, ident = hit
        if kind == "node":
            self.app.open_document(ident)
        elif kind == "note":
            self._edit_note(ident)

    def _on_wheel(self, event) -> str:
        if event.state & 0x0004:  # Ctrl：缩放，向上滚放大
            self._zoom_step(10 if event.delta > 0 else -10)
            return "break"
        step = -1 if event.delta > 0 else 1
        if event.state & 0x0001:  # Shift：横向滚动
            self.canvas.xview_scroll(step * 3, "units")
        else:
            self.canvas.yview_scroll(step * 3, "units")
        return "break"

    def _on_hover(self, event) -> None:
        if self._drag is not None:
            return
        hit = self._hit(event)
        if hit == self._hover_key:
            return
        self._hover_key = hit
        if hit is None:
            self.tooltip.hide()
            return
        kind, ident = hit
        if kind == "node":
            node = self.nodes[ident]
            lines = [node["name"]]
            if node["label"]:
                lines.append(f"节点注释：{node['label']}")
            if node["period_name"]:
                lines.append(f"所属时间段：{node['period_name']}")
            if node["color"]:
                lines.append("颜色：单独指定")
            elif node["period_color"]:
                lines.append("颜色：跟随时间段")
            if node["status"] == "missing":
                lines.append("状态：文件缺失")
            lines.append("Ctrl+点击可多选；右键可批量注释/染色/移除")
            text = "\n".join(lines)
        elif kind == "note":
            text = "注释块\n双击编辑；右键可换底色或删除"
        else:
            edge = next(
                (item for item in self.edges if item["edge_id"] == ident), None
            )
            if edge is None:
                self.tooltip.hide()
                return
            label = f"（{edge['label']}）" if edge["label"] else ""
            text = f"连线：{edge['from_name']} → {edge['to_name']}\n{label}"
        self.tooltip.schedule(text, event.x_root, event.y_root, delay=500)

    def _hide_tip(self) -> None:
        self._hover_key = None
        self.tooltip.hide()

    # -- 连线 ---------------------------------------------------------------

    def _on_connect_toggle(self) -> None:
        self.pending_doc = None
        self._style_all_nodes()
        self._update_hint()

    def _handle_connect(self, doc_id: int) -> None:
        if self.pending_doc is None:
            self.pending_doc = doc_id
            self._style_all_nodes()
            self._update_hint()
            return
        if self.pending_doc == doc_id:
            self._cancel_pending()
            return
        source = self.pending_doc
        if any(
            edge["from_doc"] == source and edge["to_doc"] == doc_id
            for edge in self.edges
        ):
            messagebox.showinfo("无法连线", "这两篇文档之间已经有连线了。", parent=self)
            self._cancel_pending()
            return
        self._push_undo("新建连线")
        try:
            repo.add_board_edge(self.conn, source, doc_id)
        except ValueError as exc:
            messagebox.showinfo("无法连线", str(exc), parent=self)
            self._cancel_pending()
            return
        self.app.log.info("图板连线：doc=%s -> doc=%s", source, doc_id)
        self.pending_doc = None
        self.selected_docs = {doc_id}
        self.reload()

    def _cancel_pending(self) -> None:
        if self.pending_doc is None:
            return
        self.pending_doc = None
        self._style_all_nodes()
        self._update_hint()

    # -- 图板编辑 -----------------------------------------------------------

    def add_documents_dialog(self) -> None:
        existing = repo.board_document_ids(self.conn)
        documents = [
            doc
            for doc in repo.list_documents(
                self.conn, folder_id=self.app.current_folder_id
            )
            if doc["status"] != "ignored" and int(doc["id"]) not in existing
        ]
        if not documents:
            messagebox.showinfo(
                "没有可添加的文档",
                "当前文件夹的文档都已经在图板上，或该文件夹还没有纳入索引的文档。",
                parent=self,
            )
            return
        from .dialogs import BoardPickerDialog

        dialog = BoardPickerDialog(self, documents)
        if not dialog.result:
            return
        self._push_undo("添加文档")
        taken = {(round(n["x"]), round(n["y"])) for n in self.nodes.values()}
        added = 0
        for doc_id in dialog.result:
            x, y = self._next_position(taken)
            if repo.add_board_node(self.conn, doc_id, x, y):
                taken.add((round(x), round(y)))
                added += 1
        self.app.log.info("加入图板：%d 篇", added)
        self.reload()

    def add_note_dialog(self) -> None:
        dialog = NoteDialog(self, title="添加注释")
        if dialog.result is None:
            return
        self._push_undo("添加注释块")
        x, y = self._next_note_position()
        note_id = repo.add_board_note(
            self.conn, self.app.current_folder_id, x, y, dialog.result
        )
        self.app.log.info("新增图板注释：note=%s", note_id)
        self.selected_note = note_id
        self.selected_docs.clear()
        self.reload()

    def _add_note_at(self, x: float, y: float) -> None:
        dialog = NoteDialog(self, title="在此添加注释")
        if dialog.result is None:
            return
        self._push_undo("添加注释块")
        note_id = repo.add_board_note(
            self.conn,
            self.app.current_folder_id,
            max(0.0, x),
            max(0.0, y),
            dialog.result,
        )
        self.app.log.info("新增图板注释：note=%s", note_id)
        self.selected_note = note_id
        self.selected_docs.clear()
        self.reload()

    def remove_selected(self) -> None:
        if self.selected_docs:
            self._remove_nodes(sorted(self.selected_docs))
            return
        if self.selected_note is not None:
            self._remove_note(self.selected_note)
            return
        if self.selected_edge is not None:
            self._remove_edge(self.selected_edge)
            return
        messagebox.showinfo(
            "提示", "请先点击选择要移除的节点、注释或连线。", parent=self
        )

    def _remove_nodes(self, document_ids: list[int]) -> None:
        if not document_ids:
            return
        if len(document_ids) == 1:
            name = self.nodes.get(document_ids[0], {}).get("name", "")
            message = (
                f"把「{name}」从图板移除？\n\n"
                "只移除图板节点与相关连线，不会删除文档、索引或日期记录。"
            )
        else:
            message = (
                f"把选中的 {len(document_ids)} 个节点从图板移除？\n\n"
                "只移除图板节点与相关连线，不会删除文档、索引或日期记录。"
            )
        if not messagebox.askyesno("从图板移除", message, parent=self):
            return
        self._push_undo("移除节点")
        repo.remove_board_nodes(self.conn, document_ids)
        self.selected_docs.clear()
        self.app.log.info("图板移除节点：%s", document_ids)
        self.reload()

    def _remove_edge(self, edge_id: int) -> None:
        self._push_undo("删除连线")
        repo.remove_board_edge(self.conn, edge_id)
        self.selected_edge = None
        self.app.log.info("图板删除连线：edge=%s", edge_id)
        self.reload()

    def _remove_note(self, note_id: int) -> None:
        note = self.notes.get(note_id)
        if note is None:
            return
        preview = " ".join(note["text"].split())[:40]
        if not messagebox.askyesno(
            "删除注释",
            f"删除这条注释？\n\n{preview or '（空注释）'}",
            parent=self,
        ):
            return
        self._push_undo("删除注释块")
        repo.remove_board_note(self.conn, note_id)
        self.selected_note = None
        self.app.log.info("删除图板注释：note=%s", note_id)
        self.reload()

    def _edit_selected_labels(self) -> None:
        """为选中的节点设置同一个“节点注释”（区别于文档的日期记录备注）。

        多选后右键即可一次给多个文件框写相同注释；留空会清除这些节点的注释。
        """
        docs = sorted(self.selected_docs)
        if not docs:
            return
        labels = [self.nodes[doc_id]["label"] for doc_id in docs if doc_id in self.nodes]
        current = labels[0] if labels and all(item == labels[0] for item in labels) else ""
        if len(docs) == 1:
            name = self.nodes.get(docs[0], {}).get("name", "")
            prompt = (
                f"给「{name}」写一个显示在文件框里的节点注释"
                "（与文档的日期记录备注无关；留空则清除）："
            )
            title = "编辑节点注释"
        else:
            prompt = (
                f"为选中的 {len(docs)} 个文件框设置相同的节点注释"
                "（与文档的日期记录备注无关；留空则全部清除）："
            )
            title = "批量节点注释"
        value = simpledialog.askstring(
            title, prompt, initialvalue=current, parent=self
        )
        if value is None:
            return
        text = value.strip()
        self._push_undo("编辑节点注释")
        for doc_id in docs:
            repo.update_board_node(self.conn, doc_id, label=text)
        self.app.log.info("图板节点注释：%d 个 -> %s", len(docs), text or "（清空）")
        self.reload()

    def _edit_edge_label(self, edge_id: int) -> None:
        edge = next((item for item in self.edges if item["edge_id"] == edge_id), None)
        if edge is None:
            return
        value = simpledialog.askstring(
            "连线标签",
            f"{edge['from_name']} → {edge['to_name']}\n\n连线说明（可留空）：",
            initialvalue=edge["label"],
            parent=self,
        )
        if value is None:
            return
        self._push_undo("编辑连线标签")
        repo.update_board_edge(self.conn, edge_id, value.strip())
        self.app.log.info("图板连线标签：edge=%s", edge_id)
        self.reload()

    def _edit_note(self, note_id: int) -> None:
        note = self.notes.get(note_id)
        if note is None:
            return
        dialog = NoteDialog(self, title="编辑注释", text=note["text"])
        if dialog.result is None:
            return
        self._push_undo("编辑注释块")
        repo.update_board_note(self.conn, note_id, text=dialog.result)
        self.app.log.info("编辑图板注释：note=%s", note_id)
        self.reload()

    def _color_selected_nodes(self) -> None:
        docs = sorted(self.selected_docs)
        if not docs:
            return
        current = (
            self.nodes[docs[0]]["color"]
            or self.nodes[docs[0]]["period_color"]
            or NODE_DEFAULT_COLOR
        )
        dialog = ColorChoiceDialog(
            self,
            title="节点颜色",
            message=f"为选中的 {len(docs)} 个节点选择颜色（可恢复为跟随时间段）。",
            current=current,
            allow_auto=True,
            auto_label="跟随时间段颜色",
        )
        if dialog.result is None:
            return
        color = None if dialog.result == ColorChoiceDialog.AUTO else dialog.result
        self._push_undo("节点染色")
        for doc_id in docs:
            repo.update_board_node(self.conn, doc_id, color=color)
        self.app.log.info("图板节点染色：%d 个 -> %s", len(docs), color or "自动")
        self.reload()

    def _color_note(self, note_id: int) -> None:
        note = self.notes.get(note_id)
        if note is None:
            return
        dialog = ColorChoiceDialog(
            self,
            title="注释底色",
            message="为这个注释块选择底色（可恢复默认淡黄）。",
            current=note["color"] or NOTE_DEFAULT_COLOR,
            allow_auto=True,
            auto_label="恢复默认颜色",
        )
        if dialog.result is None:
            return
        color = None if dialog.result == ColorChoiceDialog.AUTO else dialog.result
        self._push_undo("注释块换底色")
        repo.update_board_note(self.conn, note_id, color=color)
        self.app.log.info("图板注释底色：note=%s -> %s", note_id, color or "默认")
        self.reload()

    def clear_board(self) -> None:
        scope = "全部文件夹"
        if self.app.current_folder_id is not None:
            scope = f"「{self.app.current_folder_label()}」"
        if not messagebox.askyesno(
            "清空图板",
            f"清空{scope}的图板节点、连线与注释？\n\n"
            "不会删除任何文档、索引或日期记录，只清空关系图布局。",
            parent=self,
        ):
            return
        self._push_undo("清空图板")
        repo.clear_board(self.conn, folder_id=self.app.current_folder_id)
        self.selected_docs.clear()
        self.selected_note = None
        self.selected_edge = None
        self.app.log.info("清空图板：%s", scope)
        self.reload()

    def select_document(self, doc_id: int) -> None:
        """供外部定位：选中并高亮一个图板节点。"""
        if doc_id in self.nodes:
            self.selected_docs = {doc_id}
            self.selected_note = None
            self.selected_edge = None
            self._refresh_selection()

    def _next_position(self, taken: set[tuple[int, int]]) -> tuple[float, float]:
        for index in range(2000):
            column = index % 6
            row = index // 6
            x = 40 + column * GRID_X
            y = 40 + row * GRID_Y
            if (x, y) not in taken:
                return float(x), float(y)
        return 40.0, 40.0

    def _next_note_position(self) -> tuple[float, float]:
        taken = {(round(n["x"]), round(n["y"])) for n in self.notes.values()}
        for index in range(200):
            x = 40 + (index % 4) * 270
            y = 40 + (index // 4) * 150
            if (x, y) not in taken:
                return float(x), float(y)
        return 40.0, 40.0

    # -- 右键菜单 -----------------------------------------------------------

    def _on_right_click(self, event) -> None:
        hit = self._hit(event)
        menu = tk.Menu(self, tearoff=0)
        if hit is None:
            cx = self.canvas.canvasx(event.x) / self.zoom
            cy = self.canvas.canvasy(event.y) / self.zoom
            menu.add_command(label="添加文档…", command=self.add_documents_dialog)
            menu.add_command(
                label="在此添加注释…", command=lambda: self._add_note_at(cx, cy)
            )
            menu.add_separator()
            menu.add_command(label="清空图板", command=self.clear_board)
        elif hit[0] == "node":
            doc_id = hit[1]
            # 右键已选中的节点时保留多选，批量操作针对整个选择集；
            # 右键未选中的节点才把选择替换为它自己。
            if doc_id not in self.selected_docs:
                self.selected_docs = {doc_id}
            if not self.selected_docs:
                self.selected_docs = {doc_id}
            self.selected_note = None
            self.selected_edge = None
            self._refresh_selection()
            self.app.doc_panel.show_document(doc_id)
            count = max(1, len(self.selected_docs))
            suffix = f"（{count} 个）" if count > 1 else ""
            open_label = "打开文档" if count == 1 else "打开文档（当前点击）"
            menu.add_command(
                label=open_label, command=lambda: self.app.open_document(doc_id)
            )
            menu.add_command(
                label="在资源管理器中定位",
                command=lambda: self.app.reveal_document(doc_id),
            )
            menu.add_command(
                label="在右栏查看",
                command=lambda: self.app.doc_panel.show_document(doc_id),
            )
            menu.add_separator()
            menu.add_command(
                label=f"添加相同注释…{suffix}" if count > 1 else "编辑节点注释…",
                command=self._edit_selected_labels,
            )
            menu.add_command(
                label=f"单独染色…{suffix}", command=self._color_selected_nodes
            )
            menu.add_command(
                label=f"跟随时间段颜色{suffix}",
                command=lambda: self._reset_selected_colors(),
            )
            menu.add_separator()
            menu.add_command(
                label=f"从图板移除{suffix}",
                command=lambda: self._remove_nodes(sorted(self.selected_docs)),
            )
        elif hit[0] == "note":
            note_id = hit[1]
            self.selected_docs.clear()
            self.selected_edge = None
            self.selected_note = note_id
            self._refresh_selection()
            menu.add_command(
                label="编辑注释…", command=lambda: self._edit_note(note_id)
            )
            menu.add_command(
                label="背景颜色…", command=lambda: self._color_note(note_id)
            )
            menu.add_separator()
            menu.add_command(
                label="删除注释", command=lambda: self._remove_note(note_id)
            )
        else:
            edge_id = hit[1]
            self.selected_docs.clear()
            self.selected_note = None
            self.selected_edge = edge_id
            self._refresh_selection()
            menu.add_command(
                label="编辑连线标签…",
                command=lambda: self._edit_edge_label(edge_id),
            )
            menu.add_command(
                label="删除连线", command=lambda: self._remove_edge(edge_id)
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _reset_selected_colors(self) -> None:
        docs = sorted(self.selected_docs)
        if not docs:
            return
        self._push_undo("恢复自动颜色")
        for doc_id in docs:
            repo.update_board_node(self.conn, doc_id, color=None)
        self.app.log.info("图板节点恢复自动颜色：%d 个", len(docs))
        self.reload()

    @staticmethod
    def _ellipsis(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"
