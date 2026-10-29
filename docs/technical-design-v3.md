# DSH × WeWrite 插件技术详细设计

版本：定点补齐稿 v3，2026-10-28；对应 JEE-1939。本文替代 v2，仅补 generation 栅栏与执行令牌用例，交项目编排直接验收，不开启第 3 轮全量设计评审。本文不是实现或测试通过报告。

本轮修订索引：① §6 分列读/写副作用；② §7、§8、§9 采纳宿主逐次 ask 为权威审批证据，增加执行令牌不跨调用复用与 intentId 不重复建草稿两条断言（v3 更正原 callId 证据点）；③ §6、§8 收窄并发承诺；④ §4、§6、§8 固定 fork 派生语义；⑤ §5、§8、§9 分层处理工具限制，并更正 AgentHandle、取消参数和 idle 审批时序。

## 1. 决策、证据与交付范围

采用原生 DSH bundle：TypeScript host/client/工具 + 随包 WeWrite skills + 独立 Python facade。新仓库 `dsh-plugin-wewrite` 承载全部增量，不修改 WeWrite 与参考仓库。GitHub 归属、可见性和建仓权限由项目编排推进，不阻塞设计。

本轮实际读取：

| 依据 | 核实结果 |
|---|---|
| 已确认附件 `dsh-wewrite-python-plugin-plan-v2.html` | 要求完整写作、审稿、工作台编辑预览、逐篇确认微信草稿；不含群发 |
| `yujipeng/wewrite`，`220db1dcf141d92c03ddb704628a774ecd2b79f7`，`VERSION=4.2.1` | `src/wewrite/runs.py`：创建只建状态；`create_run()` 写全局指针；`load_run()` 可升级写盘；完成后正文不可改；`finish_run()` 校验审稿并写共享历史 |
| 同仓 `commands/content_eval.py:30` | `build_report()` 汇总编辑判断，不是自动事实核查或替代审稿的模型 |
| 同仓 `toolkit/converter.py`、`publisher.py`、`wechat_api.py` | 转换为确定性本地能力；`create_draft()` 仅建草稿，必须有封面；获取令牌和上传图片同样属于外部调用 |
| 同仓 `skills/wewrite*/` | 现有命令有大量隐式 current run，允许 Bash/Write，发布权限写 state；不能原样作为受控 DSH 工作流执行 |
| `jerryjiao/dsh-wewrite`，`b62cd19a9b10be04cb63d31fcdb3772268973fab` | 可参考 `package.json`、`cordis.patch.yml`、`src/client/index.tsx:140`、`host/rpc.ts`、`host/agent-tools/push-approval.ts`；其独立 TypeScript 管线/存储不复制 |
| 本机 DSH `0.2.1-rc.4` 安装包声明及代码 | 有 `skills.registerProvider()`、`agents.create({setup})`、`Agent.followup()`/`cancel()`、`tools.execute()`/`guard()`/`restrict()`、`connection.rpc.handle()`；声明可用不等于端到端已验证 |
| 可写 `DSH_HOME` 下实际执行 profile `--dump-config` | 成功得到 Web 配置，含 skills、approval、credentials、Agent、Web 审批界面；未启动真实 Agent 或调用微信 |

开发锁定上述 WeWrite 修订与目标 DSH 版本，依赖带锁文件和来源哈希；不能只用不受控的 `wewrite>=4`。新仓库记录来源与 MIT 许可。Python skills 不在现有 Python wheel 的 package-data 清单中，必须由 bundle 单独包含。

首版不包含定时写作、群发、自动重推、图片帖、多账号批量发布、TypeScript 重写写作引擎或常驻 Python HTTP 服务。可选视觉流程只在用户启用时进入；封面可由工作台上传，不能因推送缺封面自动触发生图。

## 2. 模块与数据职责

按 codebase-design 的 Module / Interface / Seam / Adapter 思路，集中两个深模块，避免 client、RPC、工具各自处理状态和审批：

- `WorkbenchService` 是 host 业务 Module：统一身份/任务绑定、Agent 生命周期、内容版本、审批、推送幂等和错误脱敏。RPC 和工具是同一 Interface 的两个 Adapter。
- `WeWriteFacade` 是 Python Module：封装路径安全、只读投影、写锁、WeWrite 状态与历史兼容、内容校验、转换和受控微信调用。外部 Seam 是有限操作的 JSON 子进程协议；真实 publisher 与内存假 publisher 是内部可替换 Adapter。
- client 只处理视图、用户输入、审批状态展示与轮询，不接触文件系统、Python、微信密钥或 HTTP 接口。
- DSH 负责模型、Agent、skills、审批、会话；facade 不生成或审判文章，不伪装写作引擎。

建议新仓库清单：

```text
package.json / pnpm-lock.yaml / cordis.patch.yml
src/host/index.ts                 生命周期与能力探测
src/host/workbench-service.ts     业务 Interface
src/host/agent-workflow.ts        创建/恢复/取消与 run 绑定
src/host/skills-provider.ts       带来源哈希的随包 Provider
src/host/wewrite-bridge.ts        固定解释器、JSON 协议、子进程回收
src/host/rpc.ts                   RPC schema 与身份验证
src/host/tools/{run,artifact,review,preview,push}.ts
src/host/push-approval.ts         ask 武装、执行 guard、逐次授权
src/host/redaction.ts
src/shared/{contract,tool-contract}.ts
src/client/{index,App}.tsx
src/client/panels/{Runs,Topics,Editor,Preview,Review,Settings}.tsx
python/pyproject.toml / python/requirements.lock
python/src/dsh_wewrite_facade/{__main__,contract,projection,storage,review,preview,publish}.py
skills/{wewrite,wewrite-topic,wewrite-write,wewrite-review,wewrite-visual,wewrite-publish}/
skills/manifest.json              名称、源修订、源哈希、适配版本与引用资源
docs/{installation,usage,compatibility,poc-evidence}.md
tests/{contract,host,client,security,integration,e2e}/
LICENSE / THIRD_PARTY_NOTICES
```

facade 使用独立 Python 包名，禁止在新仓库放一个残缺 `wewrite` 包遮蔽上游。构建产物包括 `lib/index.js`、`lib/client.js`、`lib/shared.js`、类型声明、`cordis.patch.yml`、`skills/**`、Python wheel 与锁文件、许可。不能依赖源码 checkout 的相对目录。

## 3. bundle 与 profile 装载

`package.json`：`type=module`，根 export 指向 host，`./client` 指向 Web client，`dsh.bundle.patch="./cordis.patch.yml"`；`dsh.client.platform="web"`，声明实际用到的 connection/runtime/locale/UI 注入。运行依赖按本机 `0.2.1-rc.4` 编译，不照抄参考仓库 `rc.7` 依赖。React 作为宿主 peer，构建时外置宿主包，不打包第二份 Cordis。

`cordis.patch.yml` 内容形态如下；默认只启用写作，不启用真实推送：

```yaml
- insert:
    - id: wewrite-python
      name: dsh-plugin-wewrite
      config:
        agentToolsEnabled: true
        pushEnabled: false
        publishTransport: fake
        bridgeTimeoutMs: 30000
```

host 内注册 skills provider，无需另猜一个不存在的 provider 插件包。解释器、`WEWRITE_HOME`、受控工作区根由 profile 私有覆盖配置提供，补丁不含密钥或机器绝对路径；配置合并按整项替换处理，安装文档列出完整覆盖项。

安装流程：在可写专属目录设置 `DSH_HOME`；先用 `dsh --from-default-profile web --profile wewrite --dump-config` 初始化；再 `dsh plugin --profile wewrite add <预构建包或本地tarball>`；最后 `dsh --profile wewrite --dump-config` 核对只出现一个插件，再启动 Web。`--from-default-profile` 会重建 profile，不能作为每次启动命令。开发才用 `--patch ./cordis.patch.yml`，不能同时 bundle 加载和重复 patch 插入。

安装单独虚拟环境中的上游固定版本与 facade wheel；host 配置绝对解释器路径，以 `spawn(executable, ['-m','dsh_wewrite_facade'], {shell:false,...})` 调用。安装不隐式执行未经审阅的 Git prepare。启动检查版本、解释器、包来源、可写数据根、skills、Agent、审批服务，快照返回逐项诊断。关键能力缺失时按钮禁用并给出明确原因。

client 使用 `conversation.view`（接收 sessionId）、`sidebar.footer.action`、`shell.overlay`，参照真实范本；没有会话上下文的侧栏不能凭空选一个 Agent。以新建专属写作会话为默认行为，创建后选择该会话；会话切换能力未验证前不宣称一键闭环。

## 4. 通用契约与全部 RPC

以下是拟实现的业务 schema，非声称上游已有接口。TypeScript 用严格 schema（拒绝未知字段），Python 用等价验证；客户端请求和 host 响应均校验。公共通道建议 `/wewrite-python`，目标版本以 `connection.rpc.handle(channel, handler)` 注册；handler 收 `endpoint,payload,signal,peer`。只接受已认证 owner 浏览器/所属工作区，不能把 loopback 当成唯一身份凭证；远端 API principal 无授权时拒绝。

明确区分两层：宿主传输信封 `ConnectionRpcResult` 为 `{ok:true,value:<业务结果>}` 或 `{ok:false,error:<ConnectionRpcFailure>}`；`ConnectionRpcFailure` 按宿主 schema 包含 `code,message,details`。插件自行选择在成功传输的 value 内放业务联合：业务成功 `{ok:true,data:<下表>}`，业务失败 `{ok:false,error:{code,message,retryable,runId?}}`。这层嵌套是本插件设计，不是宿主强制语义；插件业务错误码不写进传输 error.code。传输层失败使用宿主正式错误构造器，固定包含要求的 details，禁止直接透传上游异常。

公共类型（下述字段均必填，`?` 表可选；省略 `?` 不代表可缺失）：

```typescript
RunId = string // ^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$，还须验证存在与归属
Id = string    // 非空随机业务标识，不用作路径
Hash = string  // 64位小写十六进制 SHA-256
Time = string  // RFC3339
Kind = 'brief'|'claims'|'draft'|'article'|'review_report'|'sources'|
       'illustrated_article'|'image_prompts'|'images_manifest'
Phase = 'created'|'starting'|'writing'|'reviewing'|'ready'|'failed'|
        'cancelling'|'cancelled'|'interrupted'
Step = 'topic'|'write'|'review'|'visual'|'publish'
StepStatus = 'pending'|'in_progress'|'completed'|'failed'|'skipped'
Issue = {code:string,message:string,blocking:boolean}
Version = Hash // 实际文件字节哈希；不存在为 null，不以 mtime 当版本
Artifact = {kind:Kind,exists:boolean,nonEmpty:boolean,version:Version|null,
            bytes:integer>=0,mediaType:string}
Gate = {publishable:boolean,issues:Issue[],articleVersion:Version,
        reportVersion:Version|null}
Assessment = {decision:'pass'|'revise'|'needs_input',pass_number:1|2,
 dimensions:{accuracy:number[1,5],viewpoint:number[1,5],usefulness:number[1,5],
 voice:number[1,5],readability:number[1,5]},blockers:string[],
 major_issues:string[],notes:string}
Report = {version:1,decision:Assessment.decision,pass_number:1|2,
 publishable:boolean,dimensions:Assessment.dimensions,dimension_average:number,
 blockers:string[],major_issues:string[],notes:string,draft_chars:integer,
 final_chars:integer,edit_ratio:number,structure_changed:boolean}
Binding = {runId:RunId,agentId:Id,sessionId:Id,workspaceId:Id,
           workflowId:Id,generation:integer>=1,phase:Phase}
RunSummary = {runId:RunId,title:string,mode:'draft'|'complete',
              pythonStatus:string,phase:Phase,updated:Time,editable:boolean}
```

请求上限：topic 500 字；文本产物各 2 MiB；单张图片解码后 10 MiB；热榜 limit 1–50；分页 limit 1–100。JSON/YAML 产物必须解析为对应结构，不能任意 blob 写入。未知步骤/主题/枚举拒绝；themeId 从已核实主题目录枚举。所有修改请求带 `requestId:Id`，同一身份与操作重放时复用结果，相同 requestId 不同参数报冲突。

| RPC | 请求 schema（另含修改用 requestId） | 响应 data schema |
|---|---|---|
| `snapshot` | `{}` | `{runs:RunSummary[],nextCursor:Id|null,themes:{id:string,label:string}[],capabilities:{read:boolean,write:boolean,agent:boolean,skills:boolean,preview:boolean,push:boolean},diagnostics:Issue[],settings:{visualMode:string,pushEnabled:boolean,publishTransport:'fake'|'wechat',credentialConfigured:boolean}}` |
| `run/list` | `{cursor?:Id,limit?:integer}` | `{items:RunSummary[],nextCursor:Id|null}` |
| `hotspots/fetch` | `{limit:integer}` | `{timestamp:Time,items:{title:string,url:string,source:string,hotNormalized:number}[],sourcesFailed:string[]}` |
| `run/create` | `{topic:string,mode:'draft'|'complete',visualMode:'none'|'cover'|'full'|'prompts',themeId:string}` | `{runId:RunId,phase:'created'}`；绝不隐式启动 Agent或允许推送 |
| `run/start` | `{runId:RunId,workspaceId:Id}` | `{binding:Binding}`；后台 Agent 接管后返回，非正文完成 |
| `run/resume` | `{runId:RunId,expectedGeneration:integer}` | `{binding:Binding}`；只允许失败/中断任务，不能唤醒旧 generation 写入 |
| `run/detail` | `{runId:RunId}` | `{run:RunSummary,binding:Binding|null,steps:{step:Step,status:StepStatus,error:string|null}[],artifacts:Artifact[],gate:Gate|null,lastError:Issue|null,push:PushSummary|null}` |
| `run/cancel` | `{runId:RunId,generation:integer}` | `{runId:RunId,phase:'cancelling'|'cancelled',generation:integer}` |
| `article/get` | `{runId:RunId,kind:Kind}` | `{runId:RunId,kind:Kind,content:string,version:Version,editable:boolean}`；缺文件明确 NOT_FOUND |
| `article/save` | `{runId:RunId,markdown:string,expectedVersion:Version,metadata:{title:string,digest:string,themeId:string}}` | `{runId:RunId,version:Version,reviewRequired:true,previewReady:boolean}`；只改未封存、无写作占用的 article，旧报告立即失效 |
| `article/fork` | `{runId:RunId,expectedVersion:Version}` | `{runId:RunId,parentRunId:RunId,version:Version}`；响应 runId 必为新 run，parentRunId 必为请求中的源 runId，二者不相等；已封存文章编辑先派生、页面切换到新 run，源 run 原文不变，派生链可回溯 |
| `article/review` | `{runId:RunId,expectedVersion:Version,workspaceId:Id}` | `{binding:Binding}`；启动审稿 skill，不直接生成 pass |
| `article/preview` | `{runId:RunId,kind:'article'|'illustrated_article',expectedVersion:Version,themeId:string}` | `{runId:RunId,version:Version,html:string,issues:Issue[],remoteImagesUploaded:false}` |
| `asset/put` | `{runId:RunId,role:'cover'|'inline',mediaType:'image/png'|'image/jpeg',base64:string,expectedArticleVersion:Version}` | `{assetId:Id,contentHash:Hash,bytes:integer}`；文件名由 facade 生成，不接受原始路径 |
| `asset/get` | `{runId:RunId,assetId:Id}` | `{mediaType:string,base64:string,contentHash:Hash}`；只允许同 run 已登记图片，首版不用任意文件下载路由 |
| `wechat/prepareDraft` | `{runId:RunId,expectedVersion:Version,coverAssetId:Id,themeId:string}` | `{intentId:Id,expiresAt:Time,gate:Gate,summary:PushSummary}`；只本地预检，无令牌请求/上传 |
| `wechat/pushDraft` | `{runId:RunId,intentId:Id,expectedVersion:Version}` | `{operationId:Id,status:'pending_approval'}`；转入宿主工具审批，不直接触达 publisher |
| `wechat/pushStatus` | `{runId:RunId,operationId:Id}` | `{operationId:Id,status:'pending_approval'|'denied'|'sending'|'succeeded'|'failed'|'unknown',mediaId:string|null,issue:Issue|null,dryRun:boolean}` |

`PushSummary={runId,title,digest,articleVersion,themeId,coverHash,contentHash,accountLabel,dryRun}`，所有字段必填；accountLabel 仅显示账号名或掩码，不含 app secret。只提供设置诊断，不实现通用 `config/set`、`credentials/get` 或任意命令 RPC。密钥由宿主 credentials 配置界面管理；私有配置仅保 credential ref。

业务错误码固定为 18 项（JEE-1941 第 5 轮正式纳入状态损坏码）：`INVALID_INPUT`、`NOT_FOUND`、`FORBIDDEN`、`VERSION_CONFLICT`、`RUN_BUSY`、`INVALID_TRANSITION`、`CAPABILITY_UNAVAILABLE`、`DEPENDENCY_MISMATCH`、`ARTIFACT_MISSING`、`REVIEW_REQUIRED`、`APPROVAL_REQUIRED`、`APPROVAL_DENIED`、`APPROVAL_UNAVAILABLE`、`TIMEOUT`、`CANCELLED`、`PUBLISH_UNKNOWN`、`INTERNAL`、`STATE_CORRUPTED`。重试标志不等于允许自动重放外部动作。

状态损坏的读写操作统一返回业务码 `STATE_CORRUPTED`，默认中文消息为「状态不可读，禁止编辑」；列表及详情仍可成功返回不可读条目，并在诊断或 `lastError` 使用同名键。不得将损坏状态映射为普通 `INVALID_TRANSITION`，也不得写回合成状态。相较复用旧码，本次扩展要求 Python 与 TypeScript 同步升级错误码枚举；跨语言测试校验集合、中文消息和业务失败解析，防止单端新增漂移。

## 5. Agent 工具、skills 与生命周期

工具仅装在专属写作 Agent 的 scope，`setup` 在 `agents.create()` 发布前建立绑定、工具限制、skills 和 guard，避免先启动再补授权的窗口。`const handle = await ctx.agents.create(options)` 的返回值是 `AgentHandle`，必须通过 `const agent = handle.agent` 取得 Agent；从 `agent.id` 建立 Binding，持有 `handle.dispose()` 回收句柄，不能把 handle 当裸 Agent 调用 followup/cancel。每个 run 独立 session，不将两个任务排进同一个会话的全局 current 上下文。`agentToolsEnabled=false` 时不注册；Python/skills/模型能力不满足时拒绝开始并显示诊断。

工具严格请求/结果如下（写入均另带 requestId；工具参数必须显式含 runId）：

| 工具 | 参数 | 返回/注册条件 |
|---|---|---|
| `wewrite_run_start` | `{runId,workflowId,generation}` | `{binding}`；只绑定已有 run 并读初态，不能在已绑定 scope 新建第二篇 |
| `wewrite_run_detail` | `{runId}` | 等价 run/detail；仅当前绑定 run |
| `wewrite_artifact_get` | `{runId,kind}` | 等价 article/get |
| `wewrite_run_step` | `{runId,step,status,error?:string}` | `{runId,step,status}`；服务校验顺序，不能靠 step=completed 绕过审稿/封存 |
| `wewrite_artifact_write` | `{runId,kind,content,expectedVersion:Version|null}` | `{runId,kind,version}`；仅当前 generation，路径由 kind 决定；article 可保存候选终稿但立即使旧审稿失效，只有 review + finish 后才能 ready，初稿独立保存 |
| `wewrite_review` | `{runId,articleVersion,draftVersion,assessment:Assessment}` | `{runId,report:Report,reportVersion,gate}`；Python build_report，报告绑定两版正文和五维判断 |
| `wewrite_run_finish` | `{runId,expectedVersion,reportVersion}` | `{runId,pythonStatus:'completed'}`；验证真实文件、来源、报告后 finish_run；仅此操作进入 ready |
| `wewrite_preview` | 同 article/preview | 同 article/preview；仅本地，不授予推送权限 |
| `wewrite_push_draft` | `{runId,intentId,expectedVersion}` | `{operationId,status,mediaId?:string,dryRun}`；仅 pushEnabled、审批服务就绪、ask监听和执行guard已武装时注册 |

工具成功结构 `{ok:true,data}`，失败 `{ok:false,error}` 与业务契约相同。推送工具 `exclusive`，所有正文写入由 Python 锁串行；读取工具可并行。视觉模块启用时可通过受控资源登记复用上游能力，但具体生图 provider、预算与凭据传递属于 PoC；默认 `visualMode=none`，不借生图扩大首版阻断面。

skills 使用 `ctx.skills.registerProvider()`，provider 名 `wewrite-python-bundle`，候选 `source='bundled'`、`rank=BUNDLED_SKILL_RANK`、`resourceBase={kind:'directory',path:<随包目录>}`；list/get 从固定 manifest 加载，禁止目录扫描接受任意 skill 或引用逃出包根。依赖 references/scripts 一并入包，适配时保留来源与差异说明。以本机 `dsh-skill-office` 的真实 bundled Provider 为范本。

本次适配的关键变更：主 skill 接收已创建 run，不再执行 `wewrite run start/resume`；每次读写使用上述带 runId 的工具；移除发布 CLI、任意 Bash/Write/Edit 和 `permission publish allow`；把 `wewrite-publish` 改为预览与请求草稿推送工具。不复制另一套完整写作提示到 host，正文流程仍在 skills。

同名 skills 被 workspace/user 优先级覆盖时，检查当前 Agent 实际解析出的 provider 与 manifest 哈希；不能只凭 list 里有名字就 skillsReady=true。有冲突时阻断受控流程，给出冲突路径，不能静默执行未知技能。

宿主声明已确认 `Agent.followup(UserMessage)`，实现需使用其正式 message id、content blocks、source 结构；消息包含 runId、workflowId、generation、模式、主题、`WEWRITE_HOME` 的显示标识与主 skill 名。`followup()` 只表示输入入队，须观察实际工具开始和 session 事件，不能以返回 void 判为已写作。

每次工具执行由真实 `exec.agent` 查绑定，验证参数 runId 与该 Agent 固定 runId 相同；generation 从服务端绑定取，不能相信模型自报。会话不能通过传另一个 runId 获得跨任务写权限。`allowed-tools` frontmatter 不等于宿主安全约束。专属工作流采用 native 工具模式，实际只允许经核实的 skill、搜索/网页只读工具与本插件受控工具，按下面两条路径分别落实：

1. **继承层路径**：preset 贡献且位于该 Agent 继承的全局层/祖先层的工具，用 `agent.ctx.tools.restrict({allow:<核实后的继承只读工具名清单>})` 保留最小面；不把 scope-local 名称、未知名称或 reserved 名称传给 restrict。部署时枚举真实贡献层与名称，不能仅凭“由 preset 提供”就推断必为继承层。继承 shell、通用写入、代码执行、委派均不允许；再用单调 guard 限制实际调用与 run 绑定。
2. **自身层与 reserved 路径**：`restrict()` 不过滤该 scope 自身注册的工具，reserved `run_code` 也不在可移除的全局层，不能靠 allowlist 把它隐藏。为专属工作流选择不在自身层装配 shell/通用写入/委派的受控 preset；已有自身层危险贡献须由其拥有者的 disposer 或 preset 配置移除，不能擅自回收不属于本插件的注册。无论是否能从工具列表移除，都在该 Agent scope 注册单调 `tools.guard()`：实际调用名称不在受控执行清单即 deny，显式拒绝 `run_code`、shell、通用文件写入、代码执行和委派。自身层受控插件工具也受相同 run/generation 检查。设置 native 模式防止工具被 PTC 折叠为必须使用 run_code；不能同时承诺 PTC 可用并禁掉它的唯一入口。

这样分别验证“继承工具已被 restrict 隐藏”和“自身层/reserved 的真实执行已被 guard 拒绝”；reserved 名称仍可能出现在宿主目录中，验收不要求 restrict 将其移除。若 native 调用仍能绕过 guard，或存在未经该调度链的危险通道，PoC 不通过，交项目编排裁决，不能用提示词约束替代。

**同一 run 接管栅栏（v3 定点补齐）**：`workflowId` 表示一次具体工作流实例，不等同 runId 或 sessionId。初次绑定 generation=1；同一 run 被新 Agent 接管（包括 completed 后新建/恢复 Agent 用于推送、失败恢复、重启接管）必须产生新 workflowId，并执行固定顺序：

1. 在 run 锁内对 expectedGeneration 做 CAS；使旧 Binding/写租约失效，将持久化 generation 原子递增，并同时撤销旧 generation 的全部未消费 intent 与待执行推送 operation。失效与递增提交为同一可恢复事务；失败不发布任何新 Binding。
2. 停止旧 Agent 与其子进程，等待已进入提交临界区的操作结束。创建新 Agent 时 setup 只能准备尚未发布的绑定；在发布提交点再次核验持久化 generation 和本次接管所有权，才发布新 Binding。创建失败保留已递增的代次且无活跃 Binding，不能回退 generation 或复活旧租约。
3. 新 Binding 采用新 workflowId、新 Agent/session 与递增后的 generation。禁止“保留同一 generation、仅靠新 session 隔离”；session 不是 fencing token。同一 Agent 同一工作流的普通后续 turn 不构成接管，可以保持 generation；恢复或替换执行所有权则必须递增。

host 在每次工具入场从真实 Agent 绑定提取不可变的 `{runId,workflowId,generation}`；传给 facade 的上下文来自 host，不能从当前 generation 重新赋值来给旧请求续权。facade 在 run 锁内、每次实际落盘或推送占用前比较该上下文与持久化当前 Binding，旧 generation 一律拒绝。只在 host 入场检查不够，子进程排队后也必须复核。旧请求拒绝不得写正文/报告/状态或启动微信请求。

接管不改变正文 runId，也不自动使未变正文及其同版本审稿报告失效；它改变执行所有权，必须使旧 intent 失效。新 Agent 接管 completed run 后先完成新 Binding 发布，再重新 prepare intent；不复用接管前的 intent。intent 与 operation 在发起时固定 generation 和 workflowId，批准后、journal 原子占用时、外部发送前均核对；generation 变化与内容版本变化一样导致旧 intent 拒绝。对已经进入发送临界区的请求，接管须先等待其结束再提交代次变更；已发出的外部请求无法撤回，结果仍按 succeeded/unknown 记录，不承诺追溯取消。栅栏验收针对提交代次切换后到达的旧写入与尚未发送的旧 intent。

生命周期：created → starting → writing → reviewing → ready；错误保留产物进入 failed；重启后仍 active 的任务投影 interrupted，不盲目重发模型输入。resume 建新 generation 并撤销旧写租约，旧 Agent 的晚到写入拒绝。cancel 先撤租约和推送 intent，再调用对应 `Agent.cancel()`，终止本 run 子进程，等其静止后显示 cancelled；取消不能撤销已送达微信的动作，发送中取消须显示 unknown/最终结果。`Agent.cancel()` 的首参必须为 `AgentCancelCause` 判别联合：`{kind:'user'}`、`{kind:'parent'}`、`{kind:'hook',reason:string}` 或 `{kind:'disposed'}`，不能传裸字符串。页面取消用 `agent.cancel({kind:'user'})`；插件策略取消用 `agent.cancel({kind:'hook',reason:<脱敏原因>})`；资源卸载使用 disposed 语义并最终回收 handle，具体调用编译与生命周期探针验证。

## 6. Python facade、版本与并发

协议每次子进程一条请求、一条响应：

```json
{"protocol":1,"requestId":"随机标识","operation":"article.get","payload":{"runId":"20261028-120000-abcdef","kind":"article"}}
```

响应 `{protocol:1,requestId,ok:true,data}` 或 `{protocol:1,requestId,ok:false,error}`。stdout 只允许 JSON，stderr 是脱敏诊断；解释器固定，operation 白名单，不接受 Python 模块名、任意 argv、文件路径或 shell。普通调用 30 秒，热榜 60 秒，推送 120 秒；超时 TERM 后短时 KILL 并回收确切子进程，发送后超时标 unknown。字节流超限立刻停止，单请求/单响应不常驻。

内部 operation 仅限 `diagnose`、`snapshot`、`hotspots.fetch`、`run.create/detail/step/finish/fork`、`artifact.get/write`、`article.save`、`review.build`、`preview.render`、`asset.put/get`、`draft.prepare/send/status/recover`；UI Agent 操作留在 host。没有 update 任意 patch、任意 state 字段修改或 current run 操作。

读/写面副作用清单（不以 Python 函数名像“读取”还是“创建”代替实际副作用判断）：

| 操作面 | 实际行为与副作用 | 调用约束 |
|---|---|---|
| `snapshot/list/detail/artifact.get` 只读投影 | 只读既有目录与文件，在内存补字段；无 mkdir、replace、状态/历史/指针写入 | 不调用 load_run、resume_run、ensure_home 或升级写盘路径；缺锁设施返回需初始化诊断 |
| `load_run()` | 旧 state 可经 `_upgrade_state()` 后 `_write_state()` 写盘 | 禁止读面调用；写面显式兼容步骤才可调用 |
| `create_run()` | 内部无条件调用 `ensure_home()`，创建 home 及 `output/runs/exemplars/corpus/lessons/themes/personas` 七个标准目录（已存在时幂等）；创建独立 run 目录；写 `state.yaml`、初始 `sources.yaml`、全局 `current_run` | 仅显式创建/派生写操作，持全局写锁；不能算入“读取无写盘”承诺 |
| step/update/finish 与编辑、派生 | 写 run 状态/正文/报告/插件 journal；finish 等完成后状态更新可 upsert 共享历史 | 写锁、显式 run_id、版本校验；升级与共享历史均纳入写面证据 |
| preview/review 与热榜 | 纯转换/报告计算仅内存；若保存 preview/report 则是显式写操作；热榜有网络读取但不写任务 state | RPC 必须区分内存结果与持久化写入，不能将落盘预览伪装为无副作用 GET |

复用边界：

- `paths.home()` 作为唯一数据根；每个子进程在自身环境设置相同固定 WEWRITE_HOME，不改 host 的全局环境。
- 读面用 `yaml.safe_load` + 显式 schema + 内存投影；不调用 `load_run()`、`resume_run()`、`ensure_home()`、`_upgrade_state()` 或任何会写盘的读路径。旧格式缺字段时内存补默认并返回诊断；损坏状态显示不可读而不是消失。snapshot/list 不触发迁移。
- 写面在锁内复用 `create_run()`、`mark_step()`、`update_run()`、`finish_run()` 的公开方法，并始终传显式 run_id。需要升级旧格式时作为写入前的显式兼容操作，记录迁移结果，不在读请求中发生。
- `create_run()` 无关闭 current pointer 的参数，故保留其兼容副作用，但在全局写锁下创建；之后插件从不读该指针，也不依赖 `resolve_run_id(None)`。不得“创建后恢复旧指针”伪装无副作用，那会覆盖外部 CLI 的选择。
- 热榜 facade 调 `fetch_weibo/fetch_toutiao/fetch_baidu/deduplicate`，集中复刻已读 main 的排序聚合并做兼容测试；不修改上游，也不直接调用解析 sys.argv 的 main。来源失败部分返回，不伪造新鲜数据。
- `build_report()` 复用上游，另外保存 article/draft 哈希与 assessment 来源；用户编辑后旧 pass 无效，不能用旧 assessment 给新正文自动重新判 pass。`WeChatConverter.convert()`/`preview_html()` 复用排版，不做任何网络上传。
- `publisher.create_draft()`、令牌与图片上传函数只在 `draft.send` 中经过 host 授权后调用；内部注入假 transport 用于测试，禁止 CLI publish 的旁路调用。

### 6.1 推送 intent 状态机与 send 门闩（JEE-1942 第五轮返修后落地）

**状态机**：`prepared → claimed → dispatching → sent|unknown`；`prepared/claimed` 可转 `revoked`；`sent/unknown/revoked` 为终态不回 `prepared`，`unknown` 是幂等墓碑不释放。持久化在 `runs/<runId>/.dsh-workbench/intents.json`（`schemaVersion:2`），每项含 `state/operationId/claimNonce/claimedAt/dispatchStartedAt/result/issue`。`binding.json` 的 `revokedIntentIds` 仍是代次与撤销唯一权威。

**send 全链**（`draft_dispatch.send`）：
1. run 短锁 CAS `prepared→claimed`，写 `operationId/claimNonce`。
2. 出锁本地构造 transport（无网络）。
3. 取独立 `send.lock`（`LOCK_EX|LOCK_NB`，竞争即 `RUN_BUSY`）→ run 短锁二次复验（context/binding/intent/正文版本/门禁全一致）→ CAS `claimed→dispatching` + fsync。
4. **持 send 门闩**发送。
5. 持门闩重取 run 锁提交 `sent/unknown`。

**门闩参与者**（判据：是否改写审批摘要字段 title/digest/articleVersion/reportVersion/coverHash/themeId/accountLabel）：
- 取门闩：`article.save`、`artifact.write(kind=article|draft|review_report)`、`review.build`、`asset.put(role=cover)`、`draft.send`、`draft.recover`、`binding.publish`、`binding.revoke-intents`。
- 不取门闩（只 run 短锁）：`artifact.write(其余 kind)`、`asset.put(role=inline)`、`draft.prepare`（纯本地预检）。
- 锁序一律 `send 门闩 → run 锁`；持门闩/run 路径禁止取 home，杜绝 `send → home → run`。

**恢复 `draft.recover`**（私有 facade operation，不进浏览器 RPC/模型工具）：入参 `{requestId,runId,intentId?}` 严格拒未知字段；`claimed→revoked`、`dispatching→unknown`、终态不动；幂等依据记录状态，重复恢复零变更不重写文件。锁序 `send 门闩 → run 锁`，全程不取 home、不调用 transport。

**`draft.send` 不可变 context**：`{runId,workflowId,generation}` 仅存在于内部 `draft.send` 协议，host 从可信 Agent 绑定注入；公开 `wechat/pushDraft` 不加 context，浏览器/模型自报 context 一律拒绝。

存储布局保留上游 `WEWRITE_HOME/runs/<runId>/{state.yaml,brief.yaml,claims.yaml,draft.md,article.md,review-report.json,sources.yaml,...}`；插件增量放 `.dsh-workbench/`，含绑定、generation、版本/审稿关联、元数据、推送日志、幂等记录和 locks。共享历史仍由上游写，不引入第二套文章真源。

并发规则：Linux 上 facade 全部写入采用 `fcntl.flock`：固定顺序先 home 全局锁（仅创建、历史和共享索引），再 run 锁；普通 run 文件修改只取 run 锁，不允许反序获取。相关读在同一锁下取一致快照，但不创建读侧状态；锁文件由安装/首次明确写操作预建；GET 仅以不创建方式打开已有锁取共享锁，锁基础设施不存在时返回需初始化诊断，不为 GET 创建目录或锁文件。内容写采用同目录临时文件 + replace；正文版本、审稿失效记录必须用可恢复 journal 保持一致，读面遇未完成事务报冲突，不展示半提交 pass。锁内验证 expectedVersion 并计算新 SHA-256；100 个并发保存最多一个成功。

首版并发承诺严格限定为**插件自身两个 run 之间不串写**。明确排除“插件与外部原版 CLI 在同一 `WEWRITE_HOME` 并发写”，即使它们操作不同 run 也排除：插件 create 会写共享 current_run 并改变原版 CLI 的隐式任务选择，外部 CLI 也不遵守插件锁。不得把“插件不读 current_run”说成对外部 CLI 无影响。

使用要求是原版 CLI 写入与插件写工作流串行；暂停插件写操作后可由 CLI 核验或管理同一数据。真正无副作用的文件读取可以并行，但原版 CLI 若内部调用 load_run 升级旧格式，也属于写入，不能算并行只读核验。host 对意外外部变更做哈希检测，使旧审稿、预览和 intent 失效；这是故障检测，不是外部 CLI 并发一致性保证。支持该范围需另行统一上游锁/指针协议，本首版不承诺。

编辑规则：写作运行中编辑器只读；Agent quiescent 后允许改未封存 article。完成 run 的正文由 `article/fork` 复制到新 run（记录 parentRunId、来源快照），编辑器切换后保存并重新审稿，再封存。源任务不改写；“同一 runId 贯通”写死为每个工作流内 UI、Binding、Agent 工具、Python 状态与产物均使用同一 runId；编辑 completed 必须派生新工作流。`article/fork` 响应 runId 是新 run，parentRunId 是源 run，UI 显示派生关系且可回溯源 run，源 run 原文字节不变；不能把派生任务悄悄伪装原任务。fork 不继承旧推送许可、报告 pass 或 mediaId。

路径安全：runId 先正则再检查 state.run_id；kind 映射固定文件名，不信任 state.artifacts 指向任意位置；校验规范化后的真实路径在该 run 根内，拒绝符号链接、`..`、绝对路径、编码穿越、NUL、Windows 分隔符和跨 run 引用。图片用 assetId 查登记项，真实路径/哈希复核；拒绝图片 SVG/HTML、多态文件与解码炸弹。创建/打开阶段防 symlink 竞态，不能只做字符串 startsWith。

## 7. 微信草稿双闸、授权与 dry-run

严格链路：本地 prepare → 页面展示逐篇摘要与门禁 → 宿主工具调度 ask → 用户 allowed-once → execute 再校验 → Python 取令牌/上传/建草稿。页面确认不能替代 DSH ask，state.permissions.publish=true 也不能替代本次授权。

第一闸：为 `wewrite_push_draft` 武装 `tools/pre-execute`，精确匹配工具返回 `{kind:'ask',reason:<标题、账号、版本、封面、门禁、仅草稿>}`，其他工具调用 next。必须检查审批服务实际存在并有 Web 交互，监听返回可回收句柄；缺失、抛错、被回收时不注册推送工具。推送工具不允许“始终允许”替代逐次 allowed-once。

审批依据仍为宿主逐次 ask 返回的 `allowed-once`，只授权当前 exec 的正常 dispatch。源码 `prepareExecution` 顺序为 waterfall → `await serviceAsk(exec, gate)` → guard → dispatch，不把业务塞入审批回调。`approval/asked.callId` 仅是审计关联字段，与 decided 经审批 id 配对，不是审批权能或防复用令牌。

执行身份的硬证据改取 `exec.token`：目标宿主 `createExecution()` 每次调用 `createExecutionToken()`，后者返回新 `Symbol('dsh.tool.execution')`；`ToolExecution.token` 为只读的进程内 opaque token，`ToolGuard(execution)` 可读取。它标识具体 execution，不单独表示“已批准”；两次 token 不同加上第二次独立 ask/决定与实际调用计数，才构成“不跨 exec 复用”的可判定证据。插件不得缓存/复用 allowed-once，也不得以旧 token 解锁新执行。

“用旧 token 构造第二次调用必须失败”准确指**不能使第二次 execution 采用第一次的 token 或借其免审批**：公开 `ToolExecutionInput` 无 token 字段，TypeScript 对带 token 的直接对象字面量调用应报类型错误；不能声称任意强制类型转换都编译失败。运行期经 any/非类型输入强塞旧 token 时，宿主不读取该输入字段，而是重新铸造 token，故实际 token 必须不等于旧 token，第二次仍需 ask。失败的是旧 token 注入/授权企图，不保证整个 execute 请求抛异常。Symbol 引用在同进程可以被持有或复制，不能把它描述成“不可传递”；安全依据是公开输入不接受 execution token、宿主重新铸造且插件不提供旧决定消费入口。token 不跨 RPC/Python/日志序列化，测试只在进程内比较身份，审计输出可记布尔值与测试标签。

第二闸：工具 execute 内重新验证 armed、pushEnabled、可信调用绑定、当前 generation、未过期且未消费 intent、正文版本与封面/主题/账号哈希、同版本 publishable 报告、非空正文、实际封面和内容限制。这一闸是当前业务状态复验，不是第二次证明宿主审批。单调 guard 只负责身份、允许的工具与业务约束，不能宣称 guard 能重建 allowed-once 或强制一个已被其他 hook 改为 allow 的 gate；受控插件装配必须使 push 调用最终 gate 为 ask，探针包含其他策略叠加后仍逐次出现 ask 的检查，冲突配置下不启用推送。

页面推送必须进入**真实 open turn**：`ApprovalService.request()` 在 idle 时直接拒绝，不能拿 quiescent Agent 从 RPC 直接执行 ask。`wechat/pushDraft` 创建 operation，将固定 runId/intentId/expectedVersion 作为一次专属推送请求通过 `agent.followup(UserMessage)` 入队；Agent 在该次真实 turn 中调用受控 `wewrite_push_draft`，由正常宿主工具调度走 ask → guard → execute。若正文工作流仍占用 Agent 则返回 RUN_BUSY；已封存任务需要新建或恢复受同一 run 绑定约束的专属 Agent，但不派生正文 run。未发起工具或 turn 结束则 operation 失败，微信调用为零。不得自行 append 假 turn/start 绕过宿主生命周期。

`tools.execute({callId,name,arguments,agent,signal})` 是宿主有的完整调度入口，但不能据此在 idle RPC 中直接 ask；如集成使用显式 execute，也只能由真实 turn 内的受控调度路径调用，不能放进 approval.request 回调。client 查询 operation 进展，断开不得重复派发；host 生命周期持有 operation，重启使未消费 intent 失效。Agent 自主请求推送同样遵守 prepare、真实 turn、逐次 ask 与执行复验。

PoC 初始 `pushEnabled=false` 是安全默认值，**不是首版终态**。先在隔离 profile、假 transport 与拒绝真实外连条件下，验证服务/监听就绪并显式开启探针推送，再真实观察 Web 弹窗及 allowed-once → 工具 execute → 假 transport 全链；通过并记录证据后才允许目标实例开启真实微信推送。首版交付必须提供逐篇确认后真实建草稿能力，不能以永久不注册工具代替该验收项。审批服务缺失/监听卸载时仍须临时不注册或拒绝，修复恢复后继续验证，不因此削减首版范围。

intent 默认有效 5 分钟，绑定发起时的 generation、workflowId、runId、articleVersion、reportVersion、完整内容哈希、封面哈希、themeId、账号 credential ref 与版本、dryRun 标志。审批期间允许其他合法操作造成内容变化，execute 仍需复验并拒绝旧 intent；这属于业务版本竞争。服务端控制 id、TTL 和消费，不接受客户端 approved=true 或传入授权 token。当前工具 execute 通过复验并在 journal 原子占用 intent 后，才启动私有 facade 子进程；stdin 仅承载本次发送必需数据和凭据，不缓存或跨 exec 传递 allowed-once/capability。facade 不暴露普通业务 RPC 可直接触达的 send 路由。

两条替代断言是强制验收项：**两次推送的 exec.token 不同，第二次独立出现 approval/asked，旧 token 注入不能赋予第二次调用旧执行身份或免审批**；**同一 intentId 不得产生第二次真实 draft/add**（独立于审批，在推送 journal 层以 intentId 唯一键原子强制）。callId 仅用于审计关联，不作为跨执行授权证据。同一 intent 即使两次分别获批也最多发送一次，unknown 不释放幂等键。

发布前所有微信调用数为零，包括令牌刷新、网络诊断、图片上传。微信凭据只在通过双闸后从 credentials 取出并通过私有 stdin 输入，绝不交给 skills/模型/client。同机同用户不是恶意操作系统用户的隔离措施；目标是阻止受控工作流和普通 RPC/工具绕过，不宣称抵御同用户直接读进程/磁盘。

预检要求：编辑决策 pass、报告 publishable=true 且正文版本一致；标题、120 字节摘要、200–20000 字正文、最多 10 张图片、表格最多 4 列；实际封面与图片可读、主题可用、没有路径越界。封面变化也重新确认。预览 iframe 用 sandbox 无脚本/同源能力，禁止导航和网络自动加载；图片通过 asset/get 本地数据展示，HTML 白名单清洗并配 CSP。转换器用于微信的处理不等于浏览器 XSS 防护。

真实动作白名单仅：令牌、正文图片上传、封面素材上传、`/cgi-bin/draft/add`，以及有 mediaId 时用户主动核查用 draft/get。没有 freepublish、mass/send 等群发端点。远程图片先经安全素材获取归入 run（限制协议、地址、重定向、大小，禁止私网与云元数据）；send 阶段只处理已登记本地资源。

推送日志在外部调用前持久化 sending，唯一键为 intentId；同 operation 并发或重放不能再次 add。成功记录 mediaId 和最终 HTML 哈希。微信请求超时、进程中断或成功后落盘失败标 unknown，不自动重试 add，也不能凭标题查无结果就认定失败；有 mediaId 则人工触发核查，没有则到公众平台核对，再由用户新建 intent 重新确认。不能承诺微信侧严格 exactly-once。

dry-run 用假 publisher/假上传/假令牌，运行与真实链路相同的 prepare、ask、执行复验和 journal，结果带 `dryRun=true` 及明显假 mediaId，不写为真实已推送。网络 transport 设为拒绝外连，断言真实请求总数零；假的调用次数可观测。先通过取消/拒绝/超时/审批卸载/版本变化等负向用例，再进行一次经授权的真实账号建草稿，不能以 dry-run 代替真微信验收。

## 8. 可判定测试与验收

以下为开发与质量把关需执行的清单，本轮没有运行这些实现测试。

| 类别 | 输入/动作 | 必须观察到的结果 |
|---|---|---|
| 包与安装 | `pnpm pack` 后在干净可写 profile 安装；无源码路径 | host/client/skills/Python 内容齐全，补丁仅一次，页面入口可打开 |
| 契约 | 每个 RPC/工具合法、缺字段、未知字段、超限、非法 enum | 双端 schema 一致；宿主能接收响应；错误中文且无栈/秘密 |
| 一键写作 | 选题 → create/start，真实 Agent 执行适配 skills | session 事件、所有工具、state、产物及 UI 中 runId 相同；非空 brief/claims/draft/article/report；审稿通过才 ready |
| 插件自身双 run 并发 | 仅插件创建 A/B 不同选题与哨兵文本，交错 write/review/finish/cancel A，不运行外部 CLI 写操作 | B 不被取消；A/B 文件、报告、sources、Agent 无串写；插件创建 B 更新 current_run 不影响插件 A；跨 run 参数被拒。外部原版 CLI 同 WEWRITE_HOME 并发写明确排除出本项与首版承诺 |
| 竞争与恢复 | 同版 100 次保存、取消后的迟到写入、断电 journal、重启 resume | 最多一个 CAS 成功；旧 generation 零写；半提交不显示有效 pass；不重复启动 Agent |
| 只读投影 | v1/v3/v4 与损坏 state，目录只读，连续 snapshot/detail/get | 所有既有文件字节/mtime/目录列表不变，无迁移写盘；损坏项显式诊断 |
| 真实文件 | 只有 state 预设路径、零字节正文/报告/封面 | UI 显示缺失，不能预览为成功或推送 |
| 路径安全 | `../`、编码穿越、反斜线、state 路径伪造、跨 run、symlink 置换 | 无越界读写；种子文件哈希不变；拒绝消息不泄露绝对敏感路径 |
| 编辑审稿与派生链 | 保存已编辑正文；对源 completed run 调 article/fork 并完成新工作流 | 前者旧报告/intent 全失效、需复审；后者响应 runId 为新 run、parentRunId 等于源 runId 且可回溯，源 run 原文哈希不变；新工作流各环节仅用新 runId，源工作流各环节仍用源 runId |
| 审批负向 | 未武装、无审批服务、拒绝、取消、超时、armed 回收、伪造 approved | 未武装工具列表无 push；其余动作被拒；微信全接口调用计数恒为零 |
| 审批期间业务竞争 | ask 等待期间改正文/封面/主题/账号，随后批准 | execute 复验拒绝过期版本 intent，微信调用零；不涉及另取 execution-token 证据 |
| 执行令牌不跨调用复用（替换 callId 用例） | 同一 run、同一 generation 发起两次推送（各自有效的新 intent），进程内 guard 记录两次 exec.token；第一批准、第二拒绝；另测第二批准分支。编译探针向 ToolExecutionInput 字面量传 token；运行探针以 any 强塞第一次 token | 两次实际 token 不相等；第二次必重新出现 approval/asked，拒绝分支微信调用不增加；批准分支只按本次决定执行。字面量 token 字段编译失败；运行注入不能指定实际 token，仍生成新 Symbol 并重新 ask，不要求整个请求抛异常。callId 仅用于关联审计，不承担授权证明 |
| 同 run 新 Agent 接管栅栏 | 在 generation=g 的旧写入/旧 intent 尚未提交或发送时暂停，接管为新 Agent，再恢复旧请求；同时模拟新 Agent 创建失败与两个并发接管 | 旧 Binding 失效、持久化 generation=g+1 后才发布新 Binding，workflowId 改变；CAS 仅一个成功；创建失败不回退代次。旧请求在 facade 提交复验时被拒，正文/报告/状态字节不变，旧 intent 的微信全接口调用数为零。未变正文与同版报告可保留，新代次重新 prepare 后可正常逐篇审批推送 |
| intentId 独立幂等 | 同 intent 分别以不同 callId 获批，含并发、重启、发送超时后重复操作 | journal 唯一键仅允许一次 draft/add；假 transport 探针计数最多一，真实账号验收同 intent 的真实 draft/add 也最多一；unknown 不释放该键 |
| open turn 与 Web 审批 | 从 idle 页面发推送，验证真实 turn/start → 工具调用 → Web ask → allowed-once → 假 transport → turn/end；反例在 idle 直接 ask | 正例审计 asked.callId 与本次工具一致、asked/decided 同 id 且在 turn 内；反例拒绝且微信调用零。业务执行在正常 dispatch，不在审批回调 |
| UI 旁路 | 直接调用 push RPC、未认证/非 owner peer、另一个 workspace | 无 publisher 直达路径；未授权零外部调用；不会操作另一个会话 |
| dry-run | 拒绝真实网络的 transport 下走完整批准链 | 假调用一次、真实调用零，结果清楚标记演练 |
| 密钥不回显 | 注入哨兵 secret/token 并诱发上游完整 URL/JSON 异常 | stdout/stderr/RPC/tool/session/日志/截图/配置快照均无哨兵；含令牌 URL 不原样记录 |
| 浏览器安全 | script/onerror、恶意链接、远程跟踪图、伪造图片 | iframe 无脚本执行、导航或自动外连，编辑器文本无注入 |
| 真实微信 | 用户单次批准后建一篇测试草稿并去公众平台核对 | 有真实 mediaId、正文/封面可核对；无群发；超时 unknown 不自动重试 |
| scope 继承层限制 | 在继承全局/祖先层提供 shell/通用写入/委派后创建工作流 | restrict 后这些继承项不可见，真实调用被拒；只读与插件工具可正常执行 |
| scope 自身层与 reserved 限制 | 在自身层注入同名危险工具并直接调用 reserved run_code，尝试委派与代码执行 | 不声称 restrict 能移除；受控 preset 不装危险项，防御探针的自身层危险调用及 run_code 被 guard 拒绝，实际副作用零，native 插件工具仍可执行 |
| Agent 句柄与取消参数 | 编译并创建 AgentHandle，按 handle.agent 取 id/followup，再 cancel({kind:'user'}) 与 handle.dispose() | 类型检查通过，Binding 使用 Agent id，取消和回收只影响目标 run；无裸字符串 cause |
| 生命周期 | 卸载/HMR/关闭 profile、取消 Agent | 工具/Provider/监听/RPC disposer 与 AgentHandle 回收，子进程无遗留，撤销后的业务状态拒绝发送；不缓存批准供新 exec 使用 |

通过条件：包安装、真实 Agent 启动与 skills 发现、插件自身双 run 隔离（不含外部原版 CLI 同 WEWRITE_HOME 并发写）、每个工作流内 runId 贯通与 completed 编辑派生链、编辑/审稿/预览、审批负向零调用、执行令牌不跨调用复用、同 run 接管栅栏、intentId 不重复 draft/add 必须全部满足；真实微信验收单列证据，缺账号条件只能记待验证，不能宣布首版全验收。验收材料需含版本/修订、操作步骤、预期与实际、runId/sessionId 对照、已脱敏日志、外部调用计数和关键页面截图。

建议评审顺序：开发工程师核对 schema/目标 SDK/兼容封装 → 质量把关核对负向断言与证据 → 项目编排确认争议和实现入口；有异议在本 issue 修订，不由架构师新建子任务。

## 9. 待目标 DSH 实例验证（PoC 阻断项）

1. **真实 Web 装载**：声明层和 dump-config 已核实，bundle/client slot/RPC 实际加载、宿主错误信封、认证 peer 的 owner/workspace 校验仍需干净 profile 实测。参考仓库旧 authority 参数不能直接搬用。
2. **Agent 创建与界面选中**：`agents.create({setup})` 返回 AgentHandle，实际操作对象取 handle.agent；followup 与 AgentCancelCause 判别联合已核对声明。实际 preset、workspace cwd、UserMessage source、模型凭据、会话选择、真实 open turn 推送请求及生命周期事件仍须编译并运行验证。普通 RPC 不自带可信 Agent 身份。
3. **scope 限制**：分别运行两组探针：继承的全局/祖先危险工具经 restrict 不可见且不可执行；自身层危险工具经受控 preset 配置避免装配，并以注入探针确认 guard 拒绝，reserved run_code 也必须直接调用失败、副作用零，不能用“restrict 清单里写了 run_code”作为证据。检查 native 模式下受控插件工具仍能正常执行；同时验证 skill 冲突解析、resourceBase 引用。任一路径失败或仍可 Bash/代码执行/委派绕过 facade，均不通过，交项目编排裁决。
4. **逐次 ask 全链（探针第一优先）**：源码已确认宿主 ask 的 allowed-once 为当前 exec 的权威审批结果，且每次 execution 的 exec.token 为宿主新铸造 Symbol，guard 可读；token 是执行身份，不单独充当审批结果。真实运行仍待验证：idle 页面请求须经 followup 进入真实 turn，在 Web 看到逐次弹窗，allowed-once 后仅该次 dispatch 执行假 transport；审计按 asked.callId 与 asked/decided.id 配对观测。必须附两条独立断言证据：同 run 两次 exec.token 不同，第二次独立 asked；旧 token 注入不能指定新执行身份或免审批（编译拒绝 token 输入字段，运行重新铸造 token），第二次拒绝时零新增微信调用；同 intent 即使多个 callId 分别批准，也只能有一次 draft/add，unknown 保持占用。另测策略叠加、取消/断连/HMR 与缺失审批通道。pushEnabled=false 仅为 PoC 初始安全默认值，隔离假 transport 探针需显式开启；通过后启用真实逐篇草稿推送完成首版范围。工具执行不得塞入审批回调，不采用永久禁用推送作为首版终态。
5. **Python 来源一致性**：源码 4.2.1 已读，目标安装的 PyPI wheel 尚未执行；检查固定包与源码一致、公开方法形状、私有主题覆盖与 facade 锁协议、上游安装依赖。不能把源码阅读等同运行兼容性通过。
6. **视觉与微信环境**：目标 credentials 读取授权、图片能力/预算、微信 IP 白名单/账号权限/封面上传与真实草稿接口都尚未验证；默认演练不能替代实际验证。

PoC 最小闭环必须使用真实 DSH Agent 与适配后的 skill 产出本地文章及报告；由工作台显示相同 run、预览并走假微信审批；同时完成插件自身双 run（排除外部 CLI 并发写）、审批拒绝零调用、执行令牌不跨调用复用、同 run 接管栅栏及 intentId 幂等探针。仅 `run start`、静态 UI 或手写假产物不能算 PoC 通过。阻断项失败时在本 issue 回报准确能力缺口和可复用产物，由项目编排组织裁决，不擅自转为纯只读版或重写管线。
