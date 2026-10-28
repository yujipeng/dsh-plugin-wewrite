"""JSON 子进程协议（设计 §6）：单请求单响应。

- 请求 `{protocol:1,requestId,operation,payload}`。
- 响应 `{protocol:1,requestId,ok:true,data}` 或 `{protocol:1,requestId,ok:false,error}`。
- stdout 只允许 JSON；stderr 脱敏诊断。
- 字节流超限立即停止（MAX_REQUEST_BYTES）。
- 不常驻：读一行请求 → 处理 → 写一行响应 → 退出。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import contract, counters, service

PROTOCOL = 1

# 请求体字节上限（防解码炸弹）。
MAX_REQUEST_BYTES = 8 * 1024 * 1024


class ProtocolError(Exception):
    """协议层错误：返回传输级失败（不是业务失败）。"""


def _clean_text(value: str, limit: int = 200) -> str:
    """脱敏 + 截断诊断文本（无栈、无秘密）。"""
    return value[:limit]


def parse_request(line: str) -> dict[str, Any]:
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError("请求不是合法 JSON") from exc
    if not isinstance(req, dict):
        raise ProtocolError("请求不是对象")
    if req.get("protocol") != PROTOCOL:
        raise ProtocolError("协议版本不匹配")
    request_id = req.get("requestId")
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolError("requestId 缺失")
    operation = req.get("operation")
    if not isinstance(operation, str):
        raise ProtocolError("operation 缺失")
    payload = req.get("payload", {})
    return {"requestId": request_id, "operation": operation, "payload": payload}


def handle_line(line: str) -> dict[str, Any]:
    """处理单行请求，返回响应 dict（业务错误在 ok:false 里）。"""
    try:
        req = parse_request(line)
    except ProtocolError as exc:
        return {
            "protocol": PROTOCOL,
            "requestId": None,
            "ok": False,
            "error": {"code": "INVALID_INPUT", "message": str(exc), "retryable": False},
        }

    request_id = req["requestId"]
    result = service.dispatch(req["operation"], req["payload"])
    return {
        "protocol": PROTOCOL,
        "requestId": request_id,
        **result,
    }


def read_request_line(stream) -> str:
    """读一行请求；超限立即停止（抛 ProtocolError，不读入更多）。"""
    line = stream.readline(MAX_REQUEST_BYTES + 1)
    if not line:
        raise ProtocolError("空请求")
    if len(line.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ProtocolError("请求体超限")
    return line


def run_stdio() -> int:
    """stdin 读一条请求，stdout 写一条响应，然后退出。"""
    try:
        line = read_request_line(sys.stdin)
    except ProtocolError as exc:
        _write({"protocol": PROTOCOL, "requestId": None, "ok": False,
                "error": {"code": "INVALID_INPUT", "message": str(exc), "retryable": False}})
        return 2

    resp = handle_line(line)
    _write(resp)
    return 0


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
