"""业务错误码与信封（与 src/shared/constants.ts 对齐，禁漂移）。

错误信息一律中文、无栈、无秘密。业务错误码只出现在成功传输 value 内的
业务失败联合里，不写进宿主传输 error.code。
"""

from __future__ import annotations

from typing import Any

# 与 src/shared/constants.ts 的 ERROR_CODES 保持一致（18 项）。
# STATE_CORRUPTED：读写入口在状态不可读时返回的业务码（不进 host 传输 error.code）。
# 该码已在只读面作为诊断键（Issue.code/lastError.code）使用，写面沿用同一键以便前端统一识别。
ERROR_CODES = (
    "INVALID_INPUT",
    "NOT_FOUND",
    "FORBIDDEN",
    "VERSION_CONFLICT",
    "RUN_BUSY",
    "INVALID_TRANSITION",
    "CAPABILITY_UNAVAILABLE",
    "DEPENDENCY_MISMATCH",
    "ARTIFACT_MISSING",
    "REVIEW_REQUIRED",
    "APPROVAL_REQUIRED",
    "APPROVAL_DENIED",
    "APPROVAL_UNAVAILABLE",
    "TIMEOUT",
    "CANCELLED",
    "PUBLISH_UNKNOWN",
    "INTERNAL",
    "STATE_CORRUPTED",
)

ERROR_MESSAGES: dict[str, str] = {
    "INVALID_INPUT": "请求参数不合法",
    "NOT_FOUND": "目标不存在",
    "FORBIDDEN": "无权限执行该操作",
    "VERSION_CONFLICT": "内容版本冲突，请刷新后重试",
    "RUN_BUSY": "该写作任务正被占用",
    "INVALID_TRANSITION": "当前状态不允许此操作",
    "CAPABILITY_UNAVAILABLE": "所需能力不可用",
    "DEPENDENCY_MISMATCH": "依赖版本不匹配",
    "ARTIFACT_MISSING": "产物缺失",
    "REVIEW_REQUIRED": "需要先完成审稿",
    "APPROVAL_REQUIRED": "需要审批",
    "APPROVAL_DENIED": "审批被拒绝",
    "APPROVAL_UNAVAILABLE": "审批服务不可用",
    "TIMEOUT": "操作超时",
    "CANCELLED": "操作已取消",
    "PUBLISH_UNKNOWN": "推送结果未知",
    "INTERNAL": "内部错误",
    "STATE_CORRUPTED": "状态不可读，禁止编辑",
}

# 读面诊断键；STATE_CORRUPTED 同时也是业务错误码，其余不进 error.code。
DIAGNOSTIC_CODES = (
    "HOME_UNINITIALIZED",  # 数据根/锁设施不存在，需先 create_run 或显式写操作
    "STATE_CORRUPTED",     # state.yaml 无法解析（读面诊断键；写面同键进入 error.code）
    "STATE_LEGACY",        # 旧格式 state（缺字段，已内存补默认）
)


class FacadeError(Exception):
    """业务失败异常。携带 error 信封字段，message 一律中文、无栈、无秘密。"""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        retryable: bool = False,
        run_id: str | None = None,
    ) -> None:
        super().__init__(message or ERROR_MESSAGES.get(code, "内部错误"))
        if code not in ERROR_CODES:
            raise ValueError(f"未知业务错误码: {code}")
        self.code = code
        self.retryable = retryable
        self.run_id = run_id

    def to_error(self) -> dict[str, Any]:
        err: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }
        if self.run_id is not None:
            err["runId"] = self.run_id
        return err


def invalid_input(message: str | None = None) -> FacadeError:
    return FacadeError("INVALID_INPUT", message)


def not_found(message: str | None = None) -> FacadeError:
    return FacadeError("NOT_FOUND", message)


def forbidden(message: str | None = None) -> FacadeError:
    return FacadeError("FORBIDDEN", message)


def version_conflict(message: str | None = None) -> FacadeError:
    return FacadeError("VERSION_CONFLICT", message)


def invalid_transition(message: str | None = None) -> FacadeError:
    return FacadeError("INVALID_TRANSITION", message)


def internal(message: str | None = None) -> FacadeError:
    return FacadeError("INTERNAL", message)


def state_corrupted(message: str | None = None) -> FacadeError:
    return FacadeError("STATE_CORRUPTED", message)


def run_busy(message: str | None = None) -> FacadeError:
    return FacadeError("RUN_BUSY", message)


def cancelled(message: str | None = None, run_id: str | None = None) -> FacadeError:
    return FacadeError("CANCELLED", message, run_id=run_id)


def business_failure(code: str, message: str | None = None, **extra: Any) -> dict[str, Any]:
    """构造业务失败联合 {ok:false,error:{code,message,retryable,runId?}}。"""
    err = FacadeError(code, message, **extra)
    return {"ok": False, "error": err.to_error()}


def business_success(data: Any) -> dict[str, Any]:
    """构造业务成功联合 {ok:true,data}。"""
    return {"ok": True, "data": data}
