"""推送本地预检（设计 §7 第 1 点 + 第二闸内容限制）。

纯函数集合，无副作用、无网络。prepare 与 send 二次复验共用同一实现，
避免「两套限制」漂移。限制口径：
- 正文 200–20000 字（去空白字符计数，fail-closed）；
- 摘要 ≤ 120 字节（UTF-8）；
- 图片 ≤ 10 张（Markdown 图片语法 ``![...](...)``）；
- 表格 ≤ 4 列（Markdown 表格表头 ``| a | b | ... |``）；
- 正文非空；
- 标题非空（WeChat 草稿标题必填）。
"""

from __future__ import annotations

import re

BODY_MIN_CHARS = 200
BODY_MAX_CHARS = 20000
DIGEST_MAX_BYTES = 120
MAX_IMAGES = 10
MAX_TABLE_COLUMNS = 4

_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?(\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*)\|?\s*$")


def body_char_count(markdown: str) -> int:
    """正文字数（去空白字符，与「字」口径一致）。"""
    if not isinstance(markdown, str):
        return 0
    return len(re.sub(r"\s+", "", markdown))


def digest_byte_count(digest: str) -> int:
    if not isinstance(digest, str):
        return 0
    return len(digest.encode("utf-8"))


def count_images(markdown: str) -> int:
    if not isinstance(markdown, str):
        return 0
    return len(_IMG_RE.findall(markdown))


def max_table_columns(markdown: str) -> int:
    """返回 Markdown 表格的最大列数；无表格返回 0。"""
    if not isinstance(markdown, str):
        return 0
    max_cols = 0
    lines = markdown.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if _TABLE_SEP_RE.match(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        # 只有出现分隔行才算表格；否则把竖线当普通文本，不计列。
        has_sep = any(_TABLE_SEP_RE.match(s.strip()) for s in lines[max(0, idx - 1): idx + 2])
        if not has_sep:
            continue
        max_cols = max(max_cols, len([c for c in cells if c]))
    return max_cols


def content_issues(*, markdown: str, title: str, digest: str) -> list[dict]:
    """返回内容限制的 Issue 清单（空列表 = 通过）。Issue 形状对齐契约 IssueSchema。"""
    issues: list[dict] = []
    if not isinstance(title, str) or not title.strip():
        issues.append({"code": "TITLE_MISSING", "message": "草稿标题不能为空", "blocking": True})
    chars = body_char_count(markdown)
    if chars == 0:
        issues.append({"code": "BODY_EMPTY", "message": "正文不能为空", "blocking": True})
    elif chars < BODY_MIN_CHARS:
        issues.append({"code": "BODY_TOO_SHORT", "message": f"正文不足 {BODY_MIN_CHARS} 字", "blocking": True})
    elif chars > BODY_MAX_CHARS:
        issues.append({"code": "BODY_TOO_LONG", "message": f"正文超过 {BODY_MAX_CHARS} 字", "blocking": True})
    if digest_byte_count(digest) > DIGEST_MAX_BYTES:
        issues.append({"code": "DIGEST_TOO_LONG", "message": f"摘要不能超过 {DIGEST_MAX_BYTES} 字节", "blocking": True})
    n_images = count_images(markdown)
    if n_images > MAX_IMAGES:
        issues.append({"code": "TOO_MANY_IMAGES", "message": f"图片不能超过 {MAX_IMAGES} 张", "blocking": True})
    n_cols = max_table_columns(markdown)
    if n_cols > MAX_TABLE_COLUMNS:
        issues.append({"code": "TABLE_TOO_WIDE", "message": f"表格不能超过 {MAX_TABLE_COLUMNS} 列", "blocking": True})
    return issues
