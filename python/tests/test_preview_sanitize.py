"""预览安全负向断言（返修 5）：HTML 白名单清洗 + CSP。

注入 script/onerror/远程跟踪图/javascript: 链接后，输出必须：
- 零脚本节点、零 iframe/object/embed、零事件属性、零 javascript: URL。
- 远程图片 URL 被改写为 data 占位，无 http(s) 自动外连。
- 输出含 CSP 头（default-src 'none'）。
"""

from __future__ import annotations

import pytest

from dsh_wewrite_facade import sanitize


@pytest.mark.parametrize("payload", [
    '<script>alert(1)</script><p>正文</p>',
    '<p onclick="steal()">正文</p>',
    '<img src="https://tracker.example.com/x.gif" onerror="pwn()">',
    '<a href="javascript:alert(1)">链接</a>',
    '<iframe src="https://evil.example.com"></iframe><p>正文</p>',
    '<object data="https://evil.example.com/x"></object>',
    '<img src="data:text/html;base64,PHNjcmlwdD4=">',
    '<style>@import url(https://evil.example.com/x.css);</style><p>正文</p>',
    # 返修二轮：实体编码输入（convert_charrefs 会解码，必须重新转义）。
    '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>',
    '<a href="https://x.com/?a=1&quot; onmouseover=&quot;alert(1)">链</a>',
    '<p>&lt;img src=x onerror=alert(1)&gt;</p>',
])
def test_sanitize_strips_unsafe(payload):
    out = sanitize.sanitize_html(payload)
    flags = sanitize.has_unsafe_content(out)
    assert flags["script"] is False, f"仍含 script 节点：{out}"
    assert flags["iframe"] is False, f"仍含 iframe/object/embed：{out}"
    assert flags["event_attr"] is False, f"仍含事件属性：{out}"
    assert flags["javascript_url"] is False, f"仍含 javascript: URL：{out}"
    assert flags["remote_url"] is False, f"仍含远程图片 URL：{out}"
    assert 'Content-Security-Policy' in out
    assert "default-src 'none'" in out


def test_sanitize_keeps_safe_markup():
    out = sanitize.sanitize_html('<h1>标题</h1><p><strong>加粗</strong><a href="/local">内链</a></p>')
    assert '<h1>' in out
    assert '<strong>' in out
    assert 'href="/local"' in out
    flags = sanitize.has_unsafe_content(out)
    assert flags["script"] is False


def test_sanitize_remote_image_rewritten():
    out = sanitize.sanitize_html('<img src="https://cdn.example.com/cover.png" alt="封面">')
    assert 'https://cdn.example.com' not in out
    assert 'data:image/gif;base64,' in out
    assert sanitize.has_unsafe_content(out)["remote_url"] is False
