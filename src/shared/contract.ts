/**
 * RPC 契约（设计 §4）：全部公共类型与 RPC 请求/响应 schema，严格校验（拒绝未知字段）。
 * client 与 host 双端共用，payload/response 全过校验。
 * 端点集合与形状由 tests/shared/contract.test.ts 钉定，禁漂移。
 */

import { z } from 'zod';
import { ERROR_CODES } from './constants';

export { ERROR_CODES, ERROR_MESSAGES, RPC_CHANNEL, CONTRACT_VERSION } from './constants';
export type { ErrorCode } from './constants';

// ── 基础形状（§4 公共类型）────────────────────────────────────────────────

/** RunId：^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$（存在与归属由服务层校验）。 */
export const RunIdSchema = z
  .string()
  .regex(/^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$/, '必须是合法 runId（形如 20261028-120000-abcdef）');
export type RunId = z.infer<typeof RunIdSchema>;

/** Id：非空随机业务标识，不用作路径。 */
export const IdSchema = z.string().min(1, '标识不能为空');
export type Id = z.infer<typeof IdSchema>;

/** Hash：64 位小写十六进制 SHA-256。 */
export const HashSchema = z.string().regex(/^[a-f0-9]{64}$/, '必须是 64 位小写十六进制 SHA-256');
export type Hash = z.infer<typeof HashSchema>;

const RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

/** Time：RFC3339。 */
export const TimeSchema = z.string().regex(RFC3339_RE, '必须是 RFC3339 时间');
export type Time = z.infer<typeof TimeSchema>;

export const KINDS = [
  'brief',
  'claims',
  'draft',
  'article',
  'review_report',
  'sources',
  'illustrated_article',
  'image_prompts',
  'images_manifest',
] as const;
export type Kind = (typeof KINDS)[number];
export const KindSchema = z.enum(KINDS);

export const PHASES = [
  'created',
  'starting',
  'writing',
  'reviewing',
  'ready',
  'failed',
  'cancelling',
  'cancelled',
  'interrupted',
] as const;
export type Phase = (typeof PHASES)[number];
export const PhaseSchema = z.enum(PHASES);

export const STEPS = ['topic', 'write', 'review', 'visual', 'publish'] as const;
export type Step = (typeof STEPS)[number];
export const StepSchema = z.enum(STEPS);

export const STEP_STATUSES = ['pending', 'in_progress', 'completed', 'failed', 'skipped'] as const;
export type StepStatus = (typeof STEP_STATUSES)[number];
export const StepStatusSchema = z.enum(STEP_STATUSES);

/** Issue：诊断/门禁条目。code 为业务错误码或诊断键（§4 错误码见 ERROR_CODES）。 */
export const IssueSchema = z.strictObject({
  code: z.string().min(1),
  message: z.string(),
  blocking: z.boolean(),
});
export type Issue = z.infer<typeof IssueSchema>;

/** Version：实际文件字节 SHA-256；不存在为 null。 */
export const VersionSchema = HashSchema.nullable();
export type Version = z.infer<typeof VersionSchema>;

export const ArtifactSchema = z.strictObject({
  kind: KindSchema,
  exists: z.boolean(),
  nonEmpty: z.boolean(),
  version: VersionSchema,
  bytes: z.number().int().min(0),
  mediaType: z.string(),
});
export type Artifact = z.infer<typeof ArtifactSchema>;

export const GateSchema = z.strictObject({
  publishable: z.boolean(),
  issues: z.array(IssueSchema),
  articleVersion: HashSchema,
  reportVersion: VersionSchema,
});
export type Gate = z.infer<typeof GateSchema>;

const DIMENSION = z.number().int().min(1, '维度评分须在 1–5').max(5, '维度评分须在 1–5');
const PASS_NUMBER = z.union([z.literal(1), z.literal(2)]);
const DECISION = z.enum(['pass', 'revise', 'needs_input']);

export const DimensionsSchema = z.strictObject({
  accuracy: DIMENSION,
  viewpoint: DIMENSION,
  usefulness: DIMENSION,
  voice: DIMENSION,
  readability: DIMENSION,
});
export type Dimensions = z.infer<typeof DimensionsSchema>;

export const AssessmentSchema = z.strictObject({
  decision: DECISION,
  pass_number: PASS_NUMBER,
  dimensions: DimensionsSchema,
  blockers: z.array(z.string()),
  major_issues: z.array(z.string()),
  notes: z.string(),
});
export type Assessment = z.infer<typeof AssessmentSchema>;

export const ReportSchema = z.strictObject({
  version: z.literal(1),
  decision: DECISION,
  pass_number: PASS_NUMBER,
  publishable: z.boolean(),
  dimensions: DimensionsSchema,
  dimension_average: z.number(),
  blockers: z.array(z.string()),
  major_issues: z.array(z.string()),
  notes: z.string(),
  draft_chars: z.number().int(),
  final_chars: z.number().int(),
  edit_ratio: z.number(),
  structure_changed: z.boolean(),
});
export type Report = z.infer<typeof ReportSchema>;

export const BindingSchema = z.strictObject({
  runId: RunIdSchema,
  agentId: IdSchema,
  sessionId: IdSchema,
  workspaceId: IdSchema,
  workflowId: IdSchema,
  generation: z.number().int().min(1),
  phase: PhaseSchema,
});
export type Binding = z.infer<typeof BindingSchema>;

export const RunSummarySchema = z.strictObject({
  runId: RunIdSchema,
  title: z.string(),
  mode: z.enum(['draft', 'complete']),
  pythonStatus: z.string(),
  phase: PhaseSchema,
  updated: TimeSchema,
  editable: z.boolean(),
});
export type RunSummary = z.infer<typeof RunSummarySchema>;

export const PushSummarySchema = z.strictObject({
  runId: RunIdSchema,
  title: z.string(),
  digest: z.string(),
  articleVersion: HashSchema,
  themeId: z.string(),
  coverHash: HashSchema,
  contentHash: HashSchema,
  accountLabel: z.string(),
  dryRun: z.boolean(),
});
export type PushSummary = z.infer<typeof PushSummarySchema>;

export const PUBLISH_TRANSPORTS = ['fake', 'wechat'] as const;
export type PublishTransport = (typeof PUBLISH_TRANSPORTS)[number];

export const PUSH_STATUSES = [
  'pending_approval',
  'denied',
  'sending',
  'succeeded',
  'failed',
  'unknown',
] as const;
export type PushStatus = (typeof PUSH_STATUSES)[number];

export const VISUAL_MODES = ['none', 'cover', 'full', 'prompts'] as const;
export type VisualMode = (typeof VISUAL_MODES)[number];

export const ASSET_ROLES = ['cover', 'inline'] as const;
export type AssetRole = (typeof ASSET_ROLES)[number];

// ── 上限（§4 请求上限）────────────────────────────────────────────────────

/** topic 500 字。 */
export const TOPIC_MAX = 500;
/** 文本产物 2 MiB。 */
export const TEXT_MAX_BYTES = 2 * 1024 * 1024;
/** 单张图片解码后 10 MiB。 */
export const IMAGE_MAX_BYTES = 10 * 1024 * 1024;
/** 热榜 limit 1–50。 */
export const HOTSPOT_LIMIT_MIN = 1;
export const HOTSPOT_LIMIT_MAX = 50;
/** 分页 limit 1–100。 */
export const PAGE_LIMIT_MIN = 1;
export const PAGE_LIMIT_MAX = 100;

/** 解码 base64 后的字节长度（不校验是否合法图片，仅校验体积上限）。 */
function base64DecodedBytes(value: string): number {
  const clean = value.replace(/[\r\n\t ]/g, '');
  const padding = clean.endsWith('==') ? 2 : clean.endsWith('=') ? 1 : 0;
  return Math.floor((clean.length * 3) / 4) - padding;
}

const TopicField = z.string().max(TOPIC_MAX, `主题不能超过 ${TOPIC_MAX} 字`);

/** 文本产物按 UTF-8 字节数校验（§4：文本产物各 2 MiB，不是字符数）。 */
const MarkdownField = z
  .string()
  .refine((value) => Buffer.byteLength(value, 'utf8') <= TEXT_MAX_BYTES, {
    message: `文本产物不能超过 ${TEXT_MAX_BYTES} 字节（UTF-8）`,
  });

// ── 请求 schema 组装 ──────────────────────────────────────────────────────

const EmptyRequest = z.strictObject({});

/** 修改请求统一携带 requestId:Id（§4：同一身份与操作重放时复用结果）。 */
const withRequestId = <T extends z.ZodRawShape>(shape: T) =>
  z.strictObject({ requestId: IdSchema, ...shape } as T);

const LimitField = z.number().int().min(PAGE_LIMIT_MIN).max(PAGE_LIMIT_MAX);

/** 业务失败联合（§4：code 固定 18 码，retryable 必填，runId 可选）。 */
const BusinessErrorSchema = z.strictObject({
  code: z.enum(ERROR_CODES as unknown as [string, ...string[]]),
  message: z.string(),
  retryable: z.boolean(),
  runId: RunIdSchema.optional(),
});

/** 业务联合（§4：成功传输 value 内是 {ok:true,data} 或 {ok:false,error}）。 */
function businessResult<T extends z.ZodTypeAny>(dataSchema: T) {
  return z.union([
    z.strictObject({ ok: z.literal(true), data: dataSchema }),
    z.strictObject({ ok: z.literal(false), error: BusinessErrorSchema }),
  ]);
}

const SnapshotDataSchema = z.strictObject({
  runs: z.array(RunSummarySchema),
  nextCursor: IdSchema.nullable(),
  themes: z.array(z.strictObject({ id: z.string(), label: z.string() })),
  capabilities: z.strictObject({
    read: z.boolean(),
    write: z.boolean(),
    agent: z.boolean(),
    skills: z.boolean(),
    preview: z.boolean(),
    push: z.boolean(),
  }),
  diagnostics: z.array(IssueSchema),
  settings: z.strictObject({
    visualMode: z.string(),
    pushEnabled: z.boolean(),
    publishTransport: z.enum(PUBLISH_TRANSPORTS),
    credentialConfigured: z.boolean(),
  }),
});

const StepStateSchema = z.strictObject({
  step: StepSchema,
  status: StepStatusSchema,
  error: z.string().nullable(),
});

const OperationStatusSchema = z.enum(PUSH_STATUSES);

export const rpcContract = {
  snapshot: {
    request: EmptyRequest,
    response: businessResult(SnapshotDataSchema),
  },
  'run/list': {
    request: z.strictObject({
      cursor: IdSchema.optional(),
      limit: LimitField.optional(),
    }),
    response: z.strictObject({
      items: z.array(RunSummarySchema),
      nextCursor: IdSchema.nullable(),
    }),
  },
  'hotspots/fetch': {
    request: z.strictObject({
      limit: z
        .number()
        .int()
        .min(HOTSPOT_LIMIT_MIN, `热榜 limit 须在 ${HOTSPOT_LIMIT_MIN}–${HOTSPOT_LIMIT_MAX}`)
        .max(HOTSPOT_LIMIT_MAX, `热榜 limit 须在 ${HOTSPOT_LIMIT_MIN}–${HOTSPOT_LIMIT_MAX}`),
    }),
    response: z.strictObject({
      timestamp: TimeSchema,
      items: z.array(
        z.strictObject({
          title: z.string(),
          url: z.string(),
          source: z.string(),
          hotNormalized: z.number(),
        }),
      ),
      sourcesFailed: z.array(z.string()),
    }),
  },
  'run/create': {
    request: withRequestId({
      topic: TopicField,
      mode: z.enum(['draft', 'complete']),
      visualMode: z.enum(VISUAL_MODES),
      themeId: IdSchema,
    }),
    response: z.strictObject({
      runId: RunIdSchema,
      phase: z.literal('created'),
    }),
  },
  'run/start': {
    request: withRequestId({
      runId: RunIdSchema,
      workspaceId: IdSchema,
    }),
    response: z.strictObject({ binding: BindingSchema }),
  },
  'run/resume': {
    request: withRequestId({
      runId: RunIdSchema,
      expectedGeneration: z.number().int().min(1),
    }),
    response: z.strictObject({ binding: BindingSchema }),
  },
  'run/detail': {
    request: z.strictObject({ runId: RunIdSchema }),
    response: z.strictObject({
      run: RunSummarySchema,
      binding: BindingSchema.nullable(),
      steps: z.array(StepStateSchema),
      artifacts: z.array(ArtifactSchema),
      gate: GateSchema.nullable(),
      lastError: IssueSchema.nullable(),
      push: PushSummarySchema.nullable(),
    }),
  },
  'run/cancel': {
    request: withRequestId({
      runId: RunIdSchema,
      generation: z.number().int().min(1),
    }),
    response: z.strictObject({
      runId: RunIdSchema,
      phase: z.enum(['cancelling', 'cancelled']),
      generation: z.number().int().min(1),
    }),
  },
  'article/get': {
    request: z.strictObject({ runId: RunIdSchema, kind: KindSchema }),
    response: z.strictObject({
      runId: RunIdSchema,
      kind: KindSchema,
      content: z.string(),
      version: VersionSchema,
      editable: z.boolean(),
    }),
  },
  'article/save': {
    request: withRequestId({
      runId: RunIdSchema,
      markdown: MarkdownField,
      expectedVersion: VersionSchema,
      metadata: z.strictObject({
        title: z.string(),
        digest: z.string(),
        themeId: z.string(),
      }),
    }),
    response: z.strictObject({
      runId: RunIdSchema,
      version: HashSchema,
      reviewRequired: z.literal(true),
      previewReady: z.boolean(),
    }),
  },
  'article/fork': {
    request: withRequestId({
      runId: RunIdSchema,
      expectedVersion: VersionSchema,
    }),
    response: z.strictObject({
      runId: RunIdSchema,
      parentRunId: RunIdSchema,
      version: HashSchema,
    }),
  },
  'article/review': {
    request: withRequestId({
      runId: RunIdSchema,
      expectedVersion: VersionSchema,
      workspaceId: IdSchema,
    }),
    response: z.strictObject({ binding: BindingSchema }),
  },
  'article/preview': {
    request: withRequestId({
      runId: RunIdSchema,
      kind: z.enum(['article', 'illustrated_article']),
      expectedVersion: VersionSchema,
      themeId: IdSchema,
    }),
    response: z.strictObject({
      runId: RunIdSchema,
      version: HashSchema,
      html: z.string(),
      issues: z.array(IssueSchema),
      remoteImagesUploaded: z.literal(false),
    }),
  },
  'asset/put': {
    request: withRequestId({
      runId: RunIdSchema,
      role: z.enum(ASSET_ROLES),
      mediaType: z.enum(['image/png', 'image/jpeg']),
      base64: z
        .string()
        .min(1)
        .refine((v) => base64DecodedBytes(v) <= IMAGE_MAX_BYTES, {
          message: `图片解码后不能超过 ${IMAGE_MAX_BYTES} 字节`,
        }),
      expectedArticleVersion: VersionSchema,
    }),
    response: z.strictObject({
      assetId: IdSchema,
      contentHash: HashSchema,
      bytes: z.number().int().min(0),
    }),
  },
  'asset/get': {
    request: z.strictObject({ runId: RunIdSchema, assetId: IdSchema }),
    response: z.strictObject({
      mediaType: z.string(),
      base64: z.string(),
      contentHash: HashSchema,
    }),
  },
  'wechat/prepareDraft': {
    request: withRequestId({
      runId: RunIdSchema,
      expectedVersion: VersionSchema,
      coverAssetId: IdSchema,
      themeId: IdSchema,
    }),
    response: z.strictObject({
      intentId: IdSchema,
      expiresAt: TimeSchema,
      gate: GateSchema,
      summary: PushSummarySchema,
    }),
  },
  'wechat/pushDraft': {
    request: withRequestId({
      runId: RunIdSchema,
      intentId: IdSchema,
      expectedVersion: VersionSchema,
    }),
    response: z.strictObject({
      operationId: IdSchema,
      status: z.literal('pending_approval'),
    }),
  },
  'wechat/pushStatus': {
    request: z.strictObject({ runId: RunIdSchema, operationId: IdSchema }),
    response: z.strictObject({
      operationId: IdSchema,
      status: OperationStatusSchema,
      mediaId: z.string().nullable(),
      issue: IssueSchema.nullable(),
      dryRun: z.boolean(),
    }),
  },
} as const;

export type RpcContract = typeof rpcContract;
export type RpcEndpoint = keyof RpcContract;

export const RPC_ENDPOINTS = Object.keys(rpcContract) as RpcEndpoint[];
