"""run 绑定与 generation 栅栏持久层（设计 §5 接管栅栏 + §6 存储布局）。

绑定持久化在 ``runs/<runId>/.dsh-workbench/binding.json``，是 facade 侧唯一权威
fencing token；host 的 Binding 注册表是内存投影，落地后必须与这里的持久化一致。

写面每次实际落盘/推送占用前，在 run 锁内把 host 传来的不可变上下文
``{runId, workflowId, generation}`` 与持久化绑定复核：workflowId 不同或 generation
更低/不同一律拒绝（旧写租约零写）。host 只在工具入场检查不够，子进程排队后也必须复核。
"""

from __future__ import annotations

import json
import time
from typing import Any

from . import paths, storage
from .errors import FacadeError, forbidden, invalid_input, version_conflict

_BINDING_REL = ".dsh-workbench/binding.json"


def _valid_generation(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise invalid_input("generation 不合法")
    return value


def _valid_workflow_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_input("workflowId 不合法")
    return value


def read_binding(run_id: str) -> dict[str, Any] | None:
    """只读读取绑定；不存在返回 None。绝不创建目录/文件。"""
    paths.validate_run_id(run_id)
    try:
        fd, _st = paths.open_run_file_rel(run_id, _BINDING_REL)
    except FacadeError:
        return None
    raw = os_fd_read(fd)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def os_fd_read(fd: int) -> bytes:
    import os

    with os.fdopen(fd, "rb") as f:
        return f.read()


def _binding_matches(binding: dict[str, Any] | None, workflow_id: str, generation: int) -> bool:
    if not isinstance(binding, dict):
        return False
    return binding.get("workflowId") == workflow_id and binding.get("generation") == generation


def require_active_binding(run_id: str, workflow_id: Any, generation: Any) -> None:
    """写面 fencing 复核：host 上下文必须与持久化绑定一致，否则拒绝（旧租约零写）。

    - 无绑定 → FORBIDDEN（未绑定 run 不允许 Agent/受控写入）。
    - workflowId 不同 → FORBIDDEN（跨工作流/跨 run 参数被拒）。
    - generation 不同 → CANCELLED（旧写租约已被新接管撤销）。
    调用方必须已持 run 锁。
    """
    workflow_id = _valid_workflow_id(workflow_id)
    generation = _valid_generation(generation)
    binding = read_binding(run_id)
    if not isinstance(binding, dict):
        raise forbidden("run 未绑定写作会话")
    if binding.get("workflowId") != workflow_id:
        raise forbidden("工作流与当前 run 绑定不符")
    if binding.get("generation") != generation:
        raise FacadeError("CANCELLED", "写租约已失效，需重新接管", run_id=run_id)


def publish_binding(
    run_id: str,
    *,
    workflow_id: str,
    generation: int,
    agent_id: str,
    session_id: str,
    workspace_id: str,
    phase: str,
    expected_generation: int | None,
) -> dict[str, Any]:
    """在 run 锁内 CAS 发布绑定（设计 §5 接管栅栏第 1、3 步的落地）。

    - ``expected_generation`` 为 None 表示首次绑定（当前必须无绑定），否则必须等于
      持久化当前 generation；不匹配抛 VERSION_CONFLICT（并发接管仅一个成功）。
    - ``generation`` 是本次发布的**新** generation（首次 1，接管 g+1）。
    - 发布失败不写盘，不回退代次。
    """
    paths.validate_run_id(run_id)
    workflow_id = _valid_workflow_id(workflow_id)
    generation = _valid_generation(generation)
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise invalid_input("agentId 不合法")
    if not isinstance(session_id, str) or not session_id.strip():
        raise invalid_input("sessionId 不合法")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise invalid_input("workspaceId 不合法")

    with storage.run_lock(run_id, create=True):
        storage.ensure_writable_state(run_id)
        current = read_binding(run_id)
        if expected_generation is None:
            if isinstance(current, dict) and current.get("generation"):
                raise version_conflict("run 已被绑定，请用 run/resume 接管")
        else:
            expected_generation = _valid_generation(expected_generation)
            if not isinstance(current, dict) or current.get("generation") != expected_generation:
                raise version_conflict("接管代次冲突，请刷新后重试")

        binding = {
            "runId": run_id,
            "workflowId": workflow_id,
            "generation": generation,
            "agentId": agent_id,
            "sessionId": session_id,
            "workspaceId": workspace_id,
            "phase": phase,
            "updated": int(time.time()),
        }
        storage.write_run_file_rel(
            run_id,
            _BINDING_REL,
            json.dumps(binding, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return binding
