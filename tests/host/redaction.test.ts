/**
 * 日志脱敏器测试（设计 §5：无栈、无 token、无完整带凭据 URL）。
 */

import { describe, expect, it } from 'vitest';
import {
  maskSecret,
  redactError,
  redactKeys,
  redactText,
  redactUrlCredentials,
  truncateMessage,
} from '@/host/redaction';

describe('maskSecret（保留 <=4 字符可见）', () => {
  it('长 secret 保留前 4 字符 + 掩码尾', () => {
    expect(maskSecret('abcdefghijklmnopqrstuvwxyz')).toBe('abcd****');
  });

  it('短 secret（<=8 字符）全掩', () => {
    expect(maskSecret('12345678')).toBe('****');
    expect(maskSecret('x')).toBe('****');
  });
});

describe('redactText（已知 secret 值扫描替换）', () => {
  it('token 响应日志：access_token 值被替换，errcode 保留', () => {
    const token = 'ACCESS_TOKEN_64_CHARS_LONG';
    const text = `{"access_token":"${token}","errcode":0,"expires_in":7200}`;
    const redacted = redactText(text, [token]);
    expect(redacted).not.toContain(token);
    expect(redacted).toContain('ACCE****');
    expect(redacted).toContain('"errcode":0');
  });

  it('空 secret 列表：原文原样返回', () => {
    expect(redactText('plain log line', [])).toBe('plain log line');
  });
});

describe('redactKeys（键名匹配脱敏，深遍历）', () => {
  it('authorization/api_key/access_token/secret/password 键的值替换为 [redacted]', () => {
    const input = {
      authorization: 'Bearer sk-live-123456',
      nested: { api_key: 'rk-987654321', secret: 's3cr3t' },
      keepMe: 'plain value',
      count: 42,
    };
    const output = redactKeys(input) as Record<string, unknown>;
    expect(output.authorization).toBe('[redacted]');
    expect((output.nested as Record<string, unknown>).api_key).toBe('[redacted]');
    expect((output.nested as Record<string, unknown>).secret).toBe('[redacted]');
    expect(output.keepMe).toBe('plain value');
    expect(output.count).toBe(42);
  });

  it('纯函数：返回新对象，原对象不被修改', () => {
    const input = { secret: 'ORIGINAL_VALUE' };
    redactKeys(input);
    expect(input.secret).toBe('ORIGINAL_VALUE');
  });
});

describe('redactUrlCredentials（无完整带凭据 URL）', () => {
  it('userinfo 打码为 //****@', () => {
    expect(redactUrlCredentials('https://user:pass@api.example.com/x')).toBe(
      'https://****@api.example.com/x',
    );
  });

  it('普通 URL 原样保留', () => {
    expect(redactUrlCredentials('https://api.example.com/x')).toBe('https://api.example.com/x');
  });
});

describe('redactError（剥栈、去凭据 URL、去 secret、截断）', () => {
  it('只取 message，绝不透传 stack', () => {
    const err = new Error('boom');
    err.stack = 'at secret.js:1\nboom';
    expect(redactError(err)).toBe('boom');
  });

  it('message 内含凭据 URL 与 token 一并打码', () => {
    const token = 'TOKEN_XYZ_987654321';
    const message = `failed https://u:p@api.example.com?t=${token}`;
    const out = redactError(new Error(message), [token]);
    expect(out).not.toContain(token);
    expect(out).not.toContain('u:p@');
    expect(out).toContain('****');
  });

  it('超长错误截断到 500 + 省略号', () => {
    const out = redactError(new Error('x'.repeat(600)));
    expect(out.length).toBeLessThanOrEqual(501);
    expect(out.endsWith('…')).toBe(true);
  });
});

describe('truncateMessage', () => {
  it('超过 500 截断，不足原样', () => {
    expect(truncateMessage('y'.repeat(500))).toBe('y'.repeat(500));
    expect(truncateMessage('short')).toBe('short');
    expect(truncateMessage('x'.repeat(600)).endsWith('…')).toBe(true);
  });
});
