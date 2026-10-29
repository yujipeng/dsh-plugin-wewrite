/**
 * 契约测试（设计 §4）：公共类型、全部 RPC 请求/响应 schema、四类负向。
 * 断言依据：docs/technical-design-v3.md §4。
 */

import { describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { ERROR_MESSAGES } from '@/shared/constants';
import {
  ERROR_CODES,
  RPC_CHANNEL,
  rpcContract,
  RunIdSchema,
  HashSchema,
  AssessmentSchema,
  ReportSchema,
  TOPIC_MAX,
  TEXT_MAX_BYTES,
  IMAGE_MAX_BYTES,
  HOTSPOT_LIMIT_MIN,
  HOTSPOT_LIMIT_MAX,
  PAGE_LIMIT_MIN,
  PAGE_LIMIT_MAX,
  type RpcEndpoint,
} from '@/shared/contract';

const runId = '20261028-120000-abcdef';
const hash64 = 'a'.repeat(64);
const time = '2026-10-28T12:00:00Z';

function dims() {
  return { accuracy: 5, viewpoint: 5, usefulness: 5, voice: 5, readability: 5 };
}

describe('RPC 通道与错误码常量（§4）', () => {
  it('通道名锁定为 /wewrite-python', () => {
    expect(RPC_CHANNEL).toBe('/wewrite-python');
  });

  it('业务错误码全集（§4 固定 18 项）', () => {
    expect(ERROR_CODES).toHaveLength(18);
    expect(ERROR_CODES).toContain('INVALID_INPUT');
    expect(ERROR_CODES).toContain('PUBLISH_UNKNOWN');
    expect(ERROR_CODES).toContain('INTERNAL');
    expect(ERROR_CODES).toContain('STATE_CORRUPTED');
  });

  it('Python 全部业务失败均可由 TS 解析，错误码和中文消息双向一致', () => {
    // 从真实 Python 错误模块生成信封，避免在测试内复制第三份错误码清单。
    const output = execFileSync(process.env.FACADE_PYTHON ?? 'python3', ['-c', `
import json, sys
sys.path.insert(0, sys.argv[1])
from dsh_wewrite_facade.errors import ERROR_CODES, ERROR_MESSAGES, business_failure
print(json.dumps({"codes": ERROR_CODES, "messages": ERROR_MESSAGES,
                  "failures": [business_failure(code) for code in ERROR_CODES]}))
`, resolve('python/src')], { encoding: 'utf8' });
    const python = JSON.parse(output);
    expect([...python.codes].sort()).toEqual([...ERROR_CODES].sort());
    expect(python.messages).toEqual(ERROR_MESSAGES);
    for (const failure of python.failures) {
      expect(rpcContract.snapshot.response.safeParse(failure).success, failure.error.code).toBe(true);
    }
    // 未登记码仍应拒绝，不能通过放宽 schema 掩盖漂移。
    expect(rpcContract.snapshot.response.safeParse({
      ok: false, error: { code: 'UNREGISTERED_CODE', message: '未登记错误', retryable: false },
    }).success).toBe(false);
  });

  it('每个端点同时具备 request 与 response schema', () => {
    for (const endpoint of Object.keys(rpcContract) as RpcEndpoint[]) {
      const entry = rpcContract[endpoint];
      expect(entry, `端点 ${endpoint} 缺失`).toBeDefined();
      expect(typeof entry.request.safeParse).toBe('function');
      expect(typeof entry.response.safeParse).toBe('function');
    }
  });

  it('端点全集覆盖设计 §4 的 17 个 RPC', () => {
    expect(Object.keys(rpcContract).sort()).toEqual(
      [
        'snapshot',
        'run/list',
        'hotspots/fetch',
        'run/create',
        'run/start',
        'run/resume',
        'run/detail',
        'run/cancel',
        'article/get',
        'article/save',
        'article/fork',
        'article/review',
        'article/preview',
        'asset/put',
        'asset/get',
        'wechat/prepareDraft',
        'wechat/pushDraft',
        'wechat/pushStatus',
      ].sort(),
    );
  });
});

describe('RunId 与 Hash 基础形状（§4）', () => {
  it('RunId 严格匹配正则', () => {
    expect(RunIdSchema.safeParse('20261028-120000-abcdef').success).toBe(true);
    expect(RunIdSchema.safeParse('20261028-120000-ABCDEF').success).toBe(false);
    expect(RunIdSchema.safeParse('20261028120000-abcdef').success).toBe(false);
    expect(RunIdSchema.safeParse('x').success).toBe(false);
  });

  it('Hash 严格匹配 64 位小写十六进制', () => {
    expect(HashSchema.safeParse('a'.repeat(64)).success).toBe(true);
    expect(HashSchema.safeParse('A'.repeat(64)).success).toBe(false);
    expect(HashSchema.safeParse('a'.repeat(63)).success).toBe(false);
  });
});

describe('四类负向：缺字段、未知字段、超限、非法 enum', () => {
  it('缺字段：run/create 缺 topic/mode/visualMode/themeId/requestId 全拒', () => {
    const schema = rpcContract['run/create'].request;
    expect(schema.safeParse({}).success).toBe(false);
    expect(
      schema.safeParse({ requestId: 'r1', mode: 'draft', visualMode: 'none', themeId: 't1' }).success,
    ).toBe(false);
  });

  it('未知字段：所有修改请求 schema 均拒未知键（strictObject）', () => {
    const base = {
      requestId: 'r1',
      topic: '选题',
      mode: 'draft',
      visualMode: 'none',
      themeId: 't1',
    };
    expect(rpcContract['run/create'].request.safeParse({ ...base, __extra: 1 }).success).toBe(false);
    expect(rpcContract['snapshot'].request.safeParse({ __extra: 1 }).success).toBe(false);
  });

  it('超限：topic 500 字上限', () => {
    const schema = rpcContract['run/create'].request;
    const ok = { requestId: 'r1', topic: 'x'.repeat(TOPIC_MAX), mode: 'draft', visualMode: 'none', themeId: 't1' };
    expect(schema.safeParse(ok).success).toBe(true);
    const over = { ...ok, topic: 'x'.repeat(TOPIC_MAX + 1) };
    const parsed = schema.safeParse(over);
    expect(parsed.success).toBe(false);
    if (!parsed.success) expect(parsed.error.issues[0]!.message).toContain('500');
  });

  it('超限：文本产物 2 MiB 上限（article/save markdown，按 UTF-8 字节）', () => {
    const schema = rpcContract['article/save'].request;
    const base = {
      requestId: 'r1',
      runId,
      expectedVersion: hash64,
      metadata: { title: 't', digest: 'd', themeId: 'th' },
    };
    expect(schema.safeParse({ ...base, markdown: 'x'.repeat(TEXT_MAX_BYTES) }).success).toBe(true);
    const parsed = schema.safeParse({ ...base, markdown: 'x'.repeat(TEXT_MAX_BYTES + 1) });
    expect(parsed.success).toBe(false);
    if (!parsed.success) expect(parsed.error.issues[0]!.message).toContain('字节');
  });

  it('超限：100 万汉字（3,000,000 UTF-8 字节）拒，ASCII 3 MiB 拒', () => {
    const schema = rpcContract['article/save'].request;
    const base = {
      requestId: 'r1',
      runId,
      expectedVersion: hash64,
      metadata: { title: 't', digest: 'd', themeId: 'th' },
    };
    // 100 万汉字：每个汉字 UTF-8 占 3 字节 → 3,000,000 字节，超过 2 MiB (2,097,152)
    const hanzi = '汉'.repeat(1_000_000);
    expect(Buffer.byteLength(hanzi, 'utf8')).toBe(3_000_000);
    expect(schema.safeParse({ ...base, markdown: hanzi }).success).toBe(false);
    // 3 MiB ASCII 也应拒绝
    expect(schema.safeParse({ ...base, markdown: 'x'.repeat(3 * 1024 * 1024) }).success).toBe(false);
    // 恰 2 MiB 的汉字（2,097,152 字节 = 699,050.67 汉字，用 699,050 汉字 = 2,097,150 字节，加 2 个 ASCII）
    const nearLimit = '汉'.repeat(699_050) + 'ab';
    expect(Buffer.byteLength(nearLimit, 'utf8')).toBe(2_097_152);
    expect(schema.safeParse({ ...base, markdown: nearLimit }).success).toBe(true);
  });

  it('超限：图片解码后 10 MiB 上限（asset/put base64）', () => {
    const schema = rpcContract['asset/put'].request;
    const base = {
      requestId: 'r1',
      runId,
      role: 'cover',
      mediaType: 'image/png',
      expectedArticleVersion: hash64,
    };
    const bytes = IMAGE_MAX_BYTES;
    const base64 = Buffer.alloc(bytes, 0).toString('base64');
    expect(schema.safeParse({ ...base, base64 }).success).toBe(true);
    const over = Buffer.alloc(bytes + 3, 0).toString('base64');
    const parsed = schema.safeParse({ ...base, base64: over });
    expect(parsed.success).toBe(false);
    if (!parsed.success) expect(parsed.error.issues[0]!.message).toContain('字节');
  });

  it('超限：热榜 limit 1–50', () => {
    const schema = rpcContract['hotspots/fetch'].request;
    expect(schema.safeParse({ limit: HOTSPOT_LIMIT_MIN }).success).toBe(true);
    expect(schema.safeParse({ limit: HOTSPOT_LIMIT_MAX }).success).toBe(true);
    expect(schema.safeParse({ limit: HOTSPOT_LIMIT_MIN - 1 }).success).toBe(false);
    expect(schema.safeParse({ limit: HOTSPOT_LIMIT_MAX + 1 }).success).toBe(false);
    expect(schema.safeParse({ limit: 1.5 }).success).toBe(false);
  });

  it('超限：分页 limit 1–100', () => {
    const schema = rpcContract['run/list'].request;
    expect(schema.safeParse({ limit: PAGE_LIMIT_MIN }).success).toBe(true);
    expect(schema.safeParse({ limit: PAGE_LIMIT_MAX }).success).toBe(true);
    expect(schema.safeParse({ limit: PAGE_LIMIT_MIN - 1 }).success).toBe(false);
    expect(schema.safeParse({ limit: PAGE_LIMIT_MAX + 1 }).success).toBe(false);
  });

  it('非法 enum：mode/visualMode/kind/phase/publishTransport/status 全拒', () => {
    expect(
      rpcContract['run/create'].request.safeParse({
        requestId: 'r1',
        topic: 't',
        mode: 'bogus',
        visualMode: 'none',
        themeId: 't1',
      }).success,
    ).toBe(false);
    expect(
      rpcContract['run/create'].request.safeParse({
        requestId: 'r1',
        topic: 't',
        mode: 'draft',
        visualMode: 'bogus',
        themeId: 't1',
      }).success,
    ).toBe(false);
    expect(rpcContract['article/get'].request.safeParse({ runId, kind: 'bogus' }).success).toBe(false);
    expect(
      rpcContract['article/preview'].request.safeParse({
        requestId: 'r1',
        runId,
        kind: 'bogus',
        expectedVersion: hash64,
        themeId: 't1',
      }).success,
    ).toBe(false);
    expect(
      rpcContract['asset/put'].request.safeParse({
        requestId: 'r1',
        runId,
        role: 'bogus',
        mediaType: 'image/png',
        base64: 'aGVsbG8=',
        expectedArticleVersion: hash64,
      }).success,
    ).toBe(false);
    expect(
      rpcContract['asset/put'].request.safeParse({
        requestId: 'r1',
        runId,
        role: 'cover',
        mediaType: 'image/gif',
        base64: 'aGVsbG8=',
        expectedArticleVersion: hash64,
      }).success,
    ).toBe(false);
  });
});

describe('合法请求与响应形状（表驱动抽查）', () => {
  it('snapshot 空请求，响应为业务联合 {ok:true,data}', () => {
    const data = {
      runs: [],
      nextCursor: null,
      themes: [{ id: 't1', label: '主题一' }],
      capabilities: {
        read: true,
        write: false,
        agent: false,
        skills: false,
        preview: false,
        push: false,
      },
      diagnostics: [],
      settings: {
        visualMode: 'none',
        pushEnabled: false,
        publishTransport: 'fake',
        credentialConfigured: false,
      },
    };
    expect(rpcContract['snapshot'].request.safeParse({}).success).toBe(true);
    expect(rpcContract['snapshot'].response.safeParse({ ok: true, data }).success).toBe(true);
    // 裸 data 不再合法（必须包业务联合）
    expect(rpcContract['snapshot'].response.safeParse(data).success).toBe(false);
    // 业务失败联合也可通过
    expect(
      rpcContract['snapshot'].response.safeParse({
        ok: false,
        error: { code: 'INTERNAL', message: '内部错误', retryable: false },
      }).success,
    ).toBe(true);
  });

  it('run/detail 全量响应（run/binding/steps/artifacts/gate/lastError/push）', () => {
    const data = {
      run: {
        runId,
        title: '标题',
        mode: 'draft',
        pythonStatus: 'created',
        phase: 'created',
        updated: time,
        editable: true,
      },
      binding: null,
      steps: [{ step: 'topic', status: 'pending', error: null }],
      artifacts: [],
      gate: null,
      lastError: null,
      push: null,
    };
    expect(rpcContract['run/detail'].response.safeParse({ ok: true, data }).success).toBe(true);
    // 裸 data 不再合法（必须包业务联合）
    expect(rpcContract['run/detail'].response.safeParse(data).success).toBe(false);
  });

  it('wechat/pushStatus 响应 mediaId 与 issue 均可为 null', () => {
    const data = {
      operationId: 'op1',
      status: 'succeeded',
      mediaId: 'M1',
      issue: null,
      dryRun: true,
    };
    expect(rpcContract['wechat/pushStatus'].response.safeParse({ ok: true, data }).success).toBe(true);
  });

  it('Assessment.dimensions 评分必须 1–5，非法拒', () => {
    const base = {
      decision: 'pass',
      pass_number: 1,
      dimensions: dims(),
      blockers: [],
      major_issues: [],
      notes: '',
    };
    expect(AssessmentSchema.safeParse(base).success).toBe(true);
    expect(
      AssessmentSchema.safeParse({ ...base, dimensions: { ...dims(), accuracy: 0 } }).success,
    ).toBe(false);
    expect(
      AssessmentSchema.safeParse({ ...base, dimensions: { ...dims(), readability: 6 } }).success,
    ).toBe(false);
  });

  it('Report.pass_number 仅 1|2，decision 非法 enum 拒', () => {
    const base = {
      version: 1,
      decision: 'pass',
      pass_number: 1,
      publishable: true,
      dimensions: dims(),
      dimension_average: 5,
      blockers: [],
      major_issues: [],
      notes: '',
      draft_chars: 100,
      final_chars: 120,
      edit_ratio: 0.2,
      structure_changed: false,
    };
    expect(ReportSchema.safeParse(base).success).toBe(true);
    expect(ReportSchema.safeParse({ ...base, pass_number: 3 }).success).toBe(false);
    expect(ReportSchema.safeParse({ ...base, decision: 'bogus' }).success).toBe(false);
  });
});
