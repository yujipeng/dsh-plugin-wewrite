"""draft.send 假 transport（设计 §6/§8）：真实 publisher 与内存假 publisher 为可替换 Adapter。

本 stage 只接假 transport：不取令牌、不上传、不调 draft/add，断言真实微信调用计数
为零（wechatApiCalls === 0）。真实 publisher 留待 S4，这里不实现也不留旁路。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .counters import get_counters
from .errors import FacadeError, invalid_input


class PublisherAdapter(Protocol):
    def create_draft(self, *, title: str, html: str, digest: str, thumb_media_id: str | None, author: str | None) -> dict:
        ...


class FakePublisher:
    """内存假 publisher：不产生任何网络/微信调用，返回明显假 mediaId。"""

    def __init__(self) -> None:
        self.calls = 0

    def create_draft(self, *, title: str, html: str, digest: str, thumb_media_id: str | None, author: str | None) -> dict:
        self.calls += 1
        return {
            "mediaId": f"fake-media-{self.calls}",
            "dryRun": True,
            "title": title,
        }


class RealPublisher:
    """真实 publisher：S4 才实现。本 stage 严禁接入，构造即拒绝。"""

    def __init__(self) -> None:
        raise NotImplementedError("真实微信 publisher 属 S4，本 stage 未实现")

    def create_draft(self, **kwargs) -> dict:
        raise NotImplementedError("真实微信 publisher 属 S4，本 stage 未实现")


@dataclass
class PublishTransport:
    adapter: PublisherAdapter
    is_fake: bool


def build_transport(kind: str) -> PublishTransport:
    if kind == "fake":
        return PublishTransport(adapter=FakePublisher(), is_fake=True)
    if kind == "wechat":
        # 本 stage 不实现真实 transport；明确拒绝而非静默降级。
        raise FacadeError("CAPABILITY_UNAVAILABLE", "真实微信推送未启用")
    raise invalid_input("publishTransport 不合法")


def send_draft_fake(transport: PublishTransport, *, title: str, html: str, digest: str,
                    thumb_media_id: str | None, author: str | None) -> dict:
    """走假 transport 发送草稿。断言真实微信调用计数为零。"""
    result = transport.adapter.create_draft(
        title=title, html=html, digest=digest,
        thumb_media_id=thumb_media_id, author=author,
    )
    # 本 stage 硬约束：真实微信调用计数必须为 0。
    assert get_counters().wechat() == 0, "假 transport 不得触发真实微信调用"
    return result
