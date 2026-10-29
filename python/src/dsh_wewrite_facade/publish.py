"""draft.send 真实/假 transport（设计 §7/§8）。

真实 publisher 与内存假 publisher 为可替换 Adapter。发布白名单（设计 §7 第 5 点）：
- 仅允许：令牌（``cgi-bin/token``）、正文图片上传（``cgi-bin/media/uploadimg``）、
  封面素材上传（``cgi-bin/material/add_material``）、``/cgi-bin/draft/add``、
  有 mediaId 时用户主动核查用 ``cgi-bin/draft/get``。
- **没有** ``freepublish``、``mass/send`` 等群发端点。

凭据只在通过双闸后由 host 经**私有 stdin**（draft.send payload 的 credentials 字段）传入，
不写会话/日志/argv、不交给 skills/模型/client。真实 transport 每个外部调用前过白名单守卫
并计入 ``wechatApiCalls``；假 transport/dry-run 走与真实链路**完全相同**的 prepare/ask/执行
复验/journal，结果带明显假 mediaId 且真实调用计数恒为零。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .counters import get_counters
from .errors import FacadeError, forbidden, invalid_input

# 发布白名单（设计 §7 第 5 点）：端点子串集合，命中即放行，否则拒绝。
ALLOWED_WECHAT_ENDPOINTS = (
    "cgi-bin/token",
    "cgi-bin/media/uploadimg",
    "cgi-bin/material/add_material",
    "cgi-bin/draft/add",
    "cgi-bin/draft/get",
)

# 群发/发布类禁端点（显式枚举，防上游新增旁路被误放行）。
FORBIDDEN_WECHAT_ENDPOINTS = (
    "cgi-bin/message/mass/send",
    "cgi-bin/message/mass/sendall",
    "cgi-bin/freepublish/submit",
    "cgi-bin/message/subscribe/bizsend",
    "cgi-bin/message/template/send",
    "cgi-bin/message/custom/send",
    "cgi-bin/media/uploadnews",
)


def assert_whitelisted(url_or_endpoint: str) -> None:
    """发布白名单守卫：只放行白名单端点；群发端点或未知端点一律拒绝。

    独立 seam，供测试直接调用验证白名单边界（不含任何网络）。
    """
    if not isinstance(url_or_endpoint, str) or not url_or_endpoint:
        raise forbidden("微信端点不合法")
    if any(ep in url_or_endpoint for ep in FORBIDDEN_WECHAT_ENDPOINTS):
        raise forbidden("微信群发端点被禁止")
    if not any(ep in url_or_endpoint for ep in ALLOWED_WECHAT_ENDPOINTS):
        raise forbidden("微信端点不在发布白名单内")


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
    """真实 publisher：仅走发布白名单，凭据经私有 stdin 传入。

    每个真实微信调用前：
    1. 过 ``assert_whitelisted`` 白名单守卫；
    2. ``counters.inc_wechat()`` 计入真实调用数。
    """

    def __init__(self, credentials: dict[str, Any]) -> None:
        if not isinstance(credentials, dict):
            raise forbidden("微信凭据缺失")
        appid = credentials.get("appid") or credentials.get("app_id") or credentials.get("appId")
        secret = credentials.get("secret") or credentials.get("app_secret") or credentials.get("appSecret")
        if not isinstance(appid, str) or not appid.strip():
            raise forbidden("微信凭据缺失")
        if not isinstance(secret, str) or not secret.strip():
            raise forbidden("微信凭据缺失")
        self.appid = appid
        self.secret = secret
        self.calls = 0

    def create_draft(self, *, title: str, html: str, digest: str, thumb_media_id: str | None, author: str | None) -> dict:
        from wewrite.toolkit.publisher import create_draft as _upstream_create_draft
        from wewrite.toolkit.wechat_api import get_access_token, upload_image

        if not thumb_media_id:
            raise forbidden("微信草稿必须有封面")

        # 1) 令牌（白名单 + 计数）。
        assert_whitelisted("cgi-bin/token")
        get_counters().inc_wechat()
        self.calls += 1
        try:
            token = get_access_token(self.appid, self.secret)
        except Exception:
            raise FacadeError("PUBLISH_UNKNOWN", "微信令牌获取失败，结果无法确定", retryable=False)

        # 2) 正文图片上传（白名单 + 计数）：HTML 内嵌的本地资产引用换成上传后的 URL。
        #    首版只处理已登记本地资源；无本地图片引用时跳过（不产生外连）。
        html = self._upload_inline_images(token, html)

        # 3) 建草稿（白名单 + 计数）。
        assert_whitelisted("cgi-bin/draft/add")
        get_counters().inc_wechat()
        self.calls += 1
        try:
            result = _upstream_create_draft(
                access_token=token,
                title=title,
                html=html,
                digest=digest,
                thumb_media_id=thumb_media_id,
                author=author,
            )
        except Exception:
            raise FacadeError("PUBLISH_UNKNOWN", "微信建草稿结果无法确定", retryable=False)

        return {
            "mediaId": result.media_id,
            "dryRun": False,
            "title": title,
        }

    def _upload_inline_images(self, token: str, html: str) -> str:
        """把 HTML 内 ``asset://<assetId>`` 引用替换为上传后的微信图片 URL。

        无资产引用时原样返回（不产生网络调用）；每个真实上传过白名单 + 计数。
        远程 URL 不做抓取/上传（远程素材须先经安全素材获取归入 run，S4 未实现）。
        """
        if "asset://" not in html:
            return html
        raise FacadeError("CAPABILITY_UNAVAILABLE", "正文内联图片上传未在首版启用", retryable=False)


@dataclass
class PublishTransport:
    adapter: PublisherAdapter
    is_fake: bool


def build_transport(kind: str, credentials: dict[str, Any] | None = None) -> PublishTransport:
    """构造 transport。``fake``/``wechat`` 之外一律拒绝；真实 transport 必须有凭据。"""
    if kind == "fake":
        return PublishTransport(adapter=FakePublisher(), is_fake=True)
    if kind == "wechat":
        return PublishTransport(adapter=RealPublisher(credentials or {}), is_fake=False)
    raise invalid_input("publishTransport 不合法")


def send_draft_fake(transport: PublishTransport, *, title: str, html: str, digest: str,
                    thumb_media_id: str | None, author: str | None) -> dict:
    """走假 transport 发送草稿。断言真实微信调用计数为零（硬约束）。"""
    result = transport.adapter.create_draft(
        title=title, html=html, digest=digest,
        thumb_media_id=thumb_media_id, author=author,
    )
    assert get_counters().wechat() == 0, "假 transport 不得触发真实微信调用"
    return result


def send_draft_real(transport: PublishTransport, *, title: str, html: str, digest: str,
                    thumb_media_id: str | None, author: str | None) -> dict:
    """走真实 transport 发送草稿（每个外部调用已过白名单 + 计数）。"""
    return transport.adapter.create_draft(
        title=title, html=html, digest=digest,
        thumb_media_id=thumb_media_id, author=author,
    )
