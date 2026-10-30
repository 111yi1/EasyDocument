"""读取 Office 2007+（docx / docm / pptx / pptm）的核心属性。

只用标准库 `zipfile` + `xml.etree.ElementTree` 读 `docProps/core.xml`
与 `docProps/app.xml`；任何异常都静默返回 None，不影响主流程。
老格式 `.doc` / `.ppt` 为 OLE 二进制，无法用标准库解析，直接返回 None。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

OOXML_EXTENSIONS = frozenset({".docx", ".docm", ".pptx", ".pptm"})
CORE_PART = "docProps/core.xml"
APP_PART = "docProps/app.xml"


@dataclass(frozen=True)
class OoxmlMeta:
    """文档属性；取不到的字段为空字符串 / 0。"""

    title: str = ""
    author: str = ""
    last_modified_by: str = ""
    created: str = ""
    modified: str = ""
    word_count: int = 0
    page_count: int = 0
    slide_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.title,
                self.author,
                self.last_modified_by,
                self.created,
                self.modified,
                self.word_count,
                self.page_count,
                self.slide_count,
            )
        )

    def summary(self) -> str:
        """给详情面板用的一行摘要；没有可用属性时返回空字符串。"""
        parts: list[str] = []
        if self.title:
            parts.append(f"标题：{self.title}")
        if self.author:
            parts.append(f"作者：{self.author}")
        if self.word_count:
            parts.append(f"{self.word_count:,} 字")
        if self.slide_count:
            parts.append(f"{self.slide_count} 张幻灯片")
        if self.page_count:
            parts.append(f"{self.page_count} 页")
        if self.modified:
            parts.append(f"属性修改：{self.modified}")
        return " · ".join(parts)


def supports(path: str | Path) -> bool:
    """该扩展名是否为可读属性的 OOXML 文档。"""
    return Path(path).suffix.lower() in OOXML_EXTENSIONS


def read_meta(path: str | Path) -> OoxmlMeta | None:
    """读取属性；文件不存在、非 OOXML、损坏或无属性时返回 None。"""
    target = Path(path)
    if not supports(target):
        return None
    try:
        with zipfile.ZipFile(target) as archive:
            core = _part_fields(archive, CORE_PART)
            app = _part_fields(archive, APP_PART)
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return None
    if not core and not app:
        return None
    return OoxmlMeta(
        title=_clean(core.get("title")),
        author=_clean(core.get("creator")),
        last_modified_by=_clean(core.get("lastModifiedBy")),
        created=_display_date(core.get("created")),
        modified=_display_date(core.get("modified")),
        word_count=_as_int(app.get("Words")),
        page_count=_as_int(app.get("Pages")),
        slide_count=_as_int(app.get("Slides")),
    )


def _part_fields(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """把 XML 部件读成 {本地标签名: 文本}；部件不存在时返回 {}。"""
    try:
        raw = archive.read(part)
    except KeyError:
        return {}
    root = ElementTree.fromstring(raw)
    fields: dict[str, str] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):  # 注释节点等
            continue
        name = element.tag.rsplit("}", 1)[-1]
        text = (element.text or "").strip()
        if text and name not in fields:
            fields[name] = text
    return fields


def _clean(text: str | None) -> str:
    if not text:
        return ""
    flat = " ".join(str(text).split())
    return flat[:120]


def _as_int(text: str | None) -> int:
    try:
        return max(0, int((text or "").strip()))
    except (TypeError, ValueError):
        return 0


def _display_date(text: str | None) -> str:
    """'2026-10-29T02:00:00Z' / '2026-10-29' → '2026-10-29'。"""
    value = (text or "").strip()
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return value[:20]
