"""资产登记与读取（设计 §6 路径安全）：assetId 查登记项并复核真实路径/哈希。

拒绝 SVG/HTML、多态文件、解码炸弹。assetId 由 facade 生成，不接受原始路径。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import secrets
import stat
from typing import Any

from . import paths, storage
from .errors import FacadeError, forbidden, invalid_input, not_found

# 允许的图片媒体类型（契约只允许 png/jpeg）。
ALLOWED_EXT = {"image/png": "png", "image/jpeg": "jpg"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sniff_image(data: bytes, media_type: str) -> bool:
    """魔数校验 + Pillow 解码验证，拒绝 SVG/HTML/多态文件/解码炸弹。"""
    if media_type == "image/png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
    elif media_type == "image/jpeg":
        if not (data.startswith(b"\xff\xd8\xff")):
            return False
    else:
        return False
    # 拒绝多态/可疑内容：图片解码后必须以声明类型重新识别。
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            fmt = (im.format or "").upper()
        if media_type == "image/png" and fmt not in ("PNG",):
            return False
        if media_type == "image/jpeg" and fmt not in ("JPEG", "JPG"):
            return False
    except Exception:
        return False
    return True


def _read_registry(run_id: str) -> dict[str, Any]:
    """读资产登记 manifest.json；不存在返回空。"""
    try:
        fd, _st = paths.open_run_file_rel(run_id, f"{paths.ASSET_REGISTRY_DIR}/{paths.ASSET_REGISTRY_FILE}")
    except FacadeError:
        return {"assets": []}
    raw = os.fdopen(fd, "rb").read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"assets": []}
    return data if isinstance(data, dict) else {"assets": []}


def put_asset(*, run_id: str, role: str, media_type: str, base64_text: str,
              expected_article_version: str | None) -> dict[str, Any]:
    """登记一张图片资产。文件名由 facade 生成，不接受原始路径。"""
    paths.validate_run_id(run_id)
    if media_type not in ALLOWED_EXT:
        raise invalid_input("mediaType 不合法")
    try:
        data = base64.b64decode(base64_text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise invalid_input("base64 不合法") from exc

    if not _sniff_image(data, media_type):
        raise invalid_input("图片内容不合法")

    content_hash = _sha256(data)
    ext = ALLOWED_EXT[media_type]
    filename = f"{content_hash}.{ext}"

    with storage.run_lock(run_id, create=False):
        storage.ensure_writable_state(run_id)
        # 登记项写入（run 锁内原子）：relpath 为 assets/<hash>.<ext>，图片字节一并写入。
        relpath = f"{paths.ASSET_REGISTRY_DIR}/{filename}"
        storage.write_run_file_rel(run_id, relpath, data)

        registry = _read_registry(run_id)
        assets = registry.get("assets", [])
        if not isinstance(assets, list):
            assets = []
        assets = [a for a in assets if isinstance(a, dict)]
        asset_id = content_hash[:32]
        assets = [a for a in assets if a.get("assetId") != asset_id]
        assets.append({
            "assetId": asset_id,
            "role": role,
            "mediaType": media_type,
            "contentHash": content_hash,
            "relpath": f"{paths.ASSET_REGISTRY_DIR}/{filename}",
        })
        registry["assets"] = assets
        storage.write_run_file_rel(run_id, f"{paths.ASSET_REGISTRY_DIR}/{paths.ASSET_REGISTRY_FILE}",
                                   json.dumps(registry, ensure_ascii=False, indent=2).encode("utf-8"))

    return {"assetId": asset_id, "contentHash": content_hash, "bytes": len(data)}


def get_asset(run_id: str, asset_id: str) -> dict[str, Any]:
    """按 assetId 查登记项，复核真实路径与哈希后返回 base64。"""
    paths.validate_run_id(run_id)
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise invalid_input("assetId 不合法")
    registry = _read_registry(run_id)
    entry = None
    for a in registry.get("assets", []):
        if isinstance(a, dict) and a.get("assetId") == asset_id:
            entry = a
            break
    if entry is None:
        raise not_found("资产不存在")

    relpath = entry.get("relpath")
    if not isinstance(relpath, str):
        raise forbidden("资产登记损坏")
    # 复核真实路径：relpath 必须位于 assets/ 目录内且文件名与哈希一致。
    segs = paths.safe_relative_segments(relpath)
    if segs[0] != paths.ASSET_REGISTRY_DIR:
        raise forbidden("资产路径越界")
    expected_name = f"{entry.get('contentHash')}.{ALLOWED_EXT.get(entry.get('mediaType'), 'png')}"
    if segs[-1] != expected_name:
        raise forbidden("资产路径与哈希不符")

    fd, st = paths.open_run_file_rel(run_id, relpath)
    data = os.fdopen(fd, "rb").read()
    if _sha256(data) != entry.get("contentHash"):
        raise forbidden("资产哈希不符")
    return {
        "mediaType": entry.get("mediaType"),
        "base64": base64.b64encode(data).decode("ascii"),
        "contentHash": entry.get("contentHash"),
    }
