# dsh_wewrite_facade

插件与 WeWrite 之间的唯一受控 Python 通道（`dsh-plugin-wewrite` 的 Python facade）。
独立包名，禁止在新仓库放残缺 `wewrite` 包遮蔽上游。

## 协议

JSON 子进程协议（设计 §6），单请求单响应：

```json
{"protocol":1,"requestId":"…","operation":"article.save","payload":{…}}
```

响应 `{protocol:1,requestId,ok:true,data}` 或 `{protocol:1,requestId,ok:false,error}`。
stdout 只允许 JSON，stderr 是脱敏诊断。operation 白名单见 `contract.OPERATIONS`。

## 安装与运行

```bash
python -m venv .venv
.venv/bin/pip install -e python
echo '{"protocol":1,"requestId":"r1","operation":"diagnose","payload":{}}' | \
  WEWRITE_HOME=/tmp/wewrite .venv/bin/python -m dsh_wewrite_facade
```

## 模块

- `protocol.py` — JSON 子进程协议
- `contract.py` — operation 白名单与 payload 校验
- `paths.py` — 路径安全（openat + O_NOFOLLOW）
- `projection.py` — 只读投影（绝不写盘）
- `storage.py` — 写锁、原子写、版本 CAS、journal
- `review.py` / `hotspots.py` — 复用上游 build_report / 热榜 / 排版
- `assets.py` — 资产登记与图片安全
- `publish.py` — 假 transport（本 stage 真实微信调用恒为零）
- `service.py` — operation 分发
