"""HTML 白名单清洗 + CSP（设计 §6/§8 预览安全 + JEE-1942 返修 5）。

预览 HTML 在返回 client 前必须：
- 白名单标签：只保留排版所需的安全标签，剔除 script/iframe/object/embed/style/link/meta 等。
- 白名单属性：剔除所有 on* 事件属性、style（内联样式可能含 expression/外联）、src/href 只允许
  http/https/相对/data:image（data 仅限安全媒体类型）。
- URL scheme 白名单：javascript:/vbscript:/data:text/html 一律剥离。
- 把远程图片改写为本地占位（预览面通过 asset/get 本地数据展示，不产生自动外连）。
- 注入 CSP：default-src 'none'; img-src data:; style-src 'unsafe-inline'（仅排版需要），
  禁 script 执行、禁子资源网络加载、禁导航外连。

纯 Python 标准库实现，不依赖第三方 HTML 清洗器。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

# 允许的标签白名单（排版安全集）。
_ALLOWED_TAGS = {
    "p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code", "em", "strong", "b", "i",
    "br", "hr", "a", "img", "table", "thead", "tbody", "tr", "th", "td",
    "section", "article", "header", "footer", "figure", "figcaption",
}

# 允许的属性（全局安全集）。id/class 仅用于样式锚点，不含事件。
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": {"id", "class"},
}

# 危险/网络/脚本标签：一律剔除。
_BLOCKED_TAGS = {"script", "style", "iframe", "frame", "frameset", "object", "embed", "link", "meta", "base", "form", "input", "button", "select", "textarea", "svg", "math"}

# URL scheme 白名单：允许 http/https/相对路径/锚点；data: 仅允许安全图片媒体类型。
_SAFE_SCHEME_RE = re.compile(r"^(?:https?:|//|/|\./|\.\./|#)", re.IGNORECASE)
_SAFE_DATA_RE = re.compile(r"^data:image/(?:png|jpeg|gif|webp);base64,", re.IGNORECASE)
_UNSAFE_DATA_RE = re.compile(r"^data:", re.IGNORECASE)

_CSP = (
    "default-src 'none'; "
    "img-src data:; "
    "style-src 'unsafe-inline'; "
    "script-src 'none'; "
    "connect-src 'none'; "
    "frame-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "navigate-to 'none'"
)


def _sanitize_url(value: str) -> str | None:
    """返回安全 URL，或 None（不安全则剥离该属性）。"""
    v = value.strip().replace("\x00", "")
    if _SAFE_SCHEME_RE.match(v):
        return v
    if _SAFE_DATA_RE.match(v):
        return v
    if _UNSAFE_DATA_RE.match(v):
        return None
    # 其他 scheme（javascript:/vbscript:/data:text/html 等）全部剥离。
    return None


def _allowed_attrs_for(tag: str) -> set[str]:
    base = _ALLOWED_ATTRS.get("*", set())
    return base | _ALLOWED_ATTRS.get(tag, set())


def _escape_text(value: str) -> str:
    """文本节点转义：&、<、>（convert_charrefs 已把实体解码，这里必须重新编码，防实体绕过）。"""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    """属性上下文转义：先 &，再 "、<、>。URL 与文本属性统一走这里，防引号闭合/标签逃逸。"""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class _Sanitizer(HTMLParser):
    """逐 token 重建安全 HTML。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        if tag_l in _BLOCKED_TAGS:
            self._blocked_depth += 1
            return
        if self._blocked_depth > 0:
            return
        if tag_l not in _ALLOWED_TAGS:
            return

        allowed = _allowed_attrs_for(tag_l)
        kept: list[str] = []
        for name, value in attrs:
            name_l = name.lower()
            if name_l.startswith("on"):
                continue  # 事件属性一律剥离
            if name_l not in allowed:
                continue
            if value is None:
                kept.append(name_l)
                continue
            if name_l in ("src", "href"):
                safe = _sanitize_url(value)
                if safe is None:
                    continue
                # URL 通过 scheme 白名单后仍按属性上下文转义（返修二轮：防引号闭合/标签逃逸）。
                kept.append(f'{name_l}="{_escape_attr(safe)}"')
            else:
                # alt/title/width/height 等属性值一律属性上下文转义。
                kept.append(f'{name_l}="{_escape_attr(value)}"')
        self._parts.append(f"<{tag_l}{(' ' + ' '.join(kept)) if kept else ''}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # 自闭合标签（如 <img/>、<br/>）按 starttag 处理，但需要补闭合。
        tag_l = tag.lower()
        if tag_l in _BLOCKED_TAGS or tag_l not in _ALLOWED_TAGS:
            return
        self.handle_starttag(tag_l, attrs)
        if self._blocked_depth == 0:
            # 移除刚才追加的开标签的 '>' 换成 '/>' 以保持自闭合语义（简单处理）。
            if self._parts and self._parts[-1].endswith(">"):
                self._parts[-1] = self._parts[-1][:-1] + "/>"

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in _BLOCKED_TAGS:
            if self._blocked_depth > 0:
                self._blocked_depth -= 1
            return
        if self._blocked_depth > 0:
            return
        if tag_l in _ALLOWED_TAGS:
            self._parts.append(f"</{tag_l}>")

    def handle_data(self, data: str) -> None:
        if self._blocked_depth > 0:
            return
        # 返修二轮：文本节点输出前转义（convert_charrefs 已把 &lt;script&gt; 解码为 <script>，
        # 这里必须重新编码，否则实体编码输入会重新变成真实标签）。
        self._parts.append(_escape_text(data))

    def handle_comment(self, data: str) -> None:
        # 注释一律剥离。
        return

    def handle_decl(self, decl: str) -> None:
        # <!DOCTYPE ...> 等声明剥离。
        return

    def handle_pi(self, data: str) -> None:
        return

    def output(self) -> str:
        return "".join(self._parts)


def sanitize_html(html: str) -> str:
    """清洗 HTML：白名单标签/属性/URL scheme，剥离脚本节点与远程 URL。"""
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    body = parser.output()
    # 远程跟踪图：源 HTML 中的远程 <img src="http..."> 会保留 scheme 允许的 http(s) 引用；
    # 但预览 iframe 的 CSP 只允许 img-src data:，因此任何远程图片都不会被加载。
    # 为双重保险，这里把 http(s) 图片源替换为本地占位（预览面经 asset/get 展示本地数据）。
    body = _rewrite_remote_images(body)
    return _wrap_with_csp(body)


def _rewrite_remote_images(html: str) -> str:
    """把 src=http(s) 的远程图片替换为 data 占位，杜绝自动外连（负向断言）。"""
    def _replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        # 仅处理 img 的远程 src；非 img 的 href 仍保留 http(s) 但 CSP 禁外连。
        if not re.search(r"<\s*img\b", tag, re.IGNORECASE):
            return tag
        return re.sub(
            r'(\bsrc\s*=\s*["\'])(https?://[^"\']*)(["\'])',
            r'\1data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7\3',
            tag,
            flags=re.IGNORECASE,
        )

    return re.sub(r"<\s*img\b[^>]*>", _replace, html, flags=re.IGNORECASE)


def _wrap_with_csp(body: str) -> str:
    """把清洗后的 body 包进完整 HTML，头部注入 CSP。"""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">'
        '</head><body>' + body + '</body></html>'
    )


class _NodeInspector(HTMLParser):
    """把清洗输出再解析一次，收集真实节点/属性集合（返修二轮：不用字符串包含判断）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[str] = []
        self.attrs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag.lower())
        for name, _value in attrs:
            self.attrs.append(name.lower())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag.lower())
        for name, _value in attrs:
            self.attrs.append(name.lower())


def has_unsafe_content(html: str) -> dict[str, bool]:
    """负向断言：把清洗结果**再解析**，看真实节点/属性集合。

    返回：
    - script: 是否出现 script/iframe/frame/object/embed 节点。
    - event_attr: 是否出现 on* 事件属性。
    - javascript_url: href/src 属性是否以 javascript: 开头（解析后）。
    - remote_url: img 的 src 是否 http(s)。
    """
    inspector = _NodeInspector()
    inspector.feed(html)
    inspector.close()

    blocked_tags = {"script", "iframe", "frame", "object", "embed"}
    has_script_node = any(t in blocked_tags for t in inspector.tags)
    has_event_attr = any(a.startswith("on") for a in inspector.attrs)
    has_javascript_url = any(a in ("href", "src") and False for a in inspector.attrs)  # 由下面具体检查
    has_remote_img = False

    # 二次解析 URL 属性值（保留原始值以便判断 scheme）。
    class _UrlInspector(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.urls: list[str] = []
            self.event_attrs: list[str] = []

        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name.lower().startswith("on"):
                    self.event_attrs.append(name.lower())
                if name.lower() in ("href", "src") and value:
                    self.urls.append((name.lower(), value))

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

    ui = _UrlInspector()
    ui.feed(html)
    ui.close()
    has_javascript_url = any(n in ("href", "src") and v.strip().lower().startswith("javascript:") for n, v in ui.urls)
    has_remote_img = any(n == "src" and v.strip().lower().startswith(("http://", "https://")) for n, v in ui.urls)

    return {
        "script": has_script_node,
        "iframe": has_script_node,
        "event_attr": has_event_attr or bool(ui.event_attrs),
        "javascript_url": has_javascript_url,
        "remote_url": has_remote_img,
    }
