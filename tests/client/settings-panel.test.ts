/**
 * 设置面板纯逻辑测试（返修 6）：YAML 解析与 placeholder 能力一致。
 * - 必须能原样解析 yaml.safe_dump(allow_unicode=True, sort_keys=False) 的输出。
 * - placeholder 自身必须能被解析成合法对象。
 * - 四个账号级字段（topics/target_audience/blacklist/content_style）可读回。
 */

import { describe, expect, it } from 'vitest';
import { parseStyleText, STYLE_PLACEHOLDER } from '@/client/panels/Settings';

/** 服务端 yaml.safe_dump 的实际输出格式（列表项零缩进）。 */
const SERVER_DUMP = [
  'name: Demo科技',
  'industry: 科技/互联网',
  'target_audience: 25-40岁',
  'topics:',
  '- AI',
  '- 产品设计',
  'tone: 专业',
  'theme: professional-clean',
  'content_style: 干货',
  'blacklist:',
  '  words:',
  '  - 震惊',
  '  topics:',
  '  - 政治敏感',
].join('\n');

describe('设置面板 style YAML 解析（返修 6）', () => {
  it('能原样解析服务端 yaml.safe_dump 输出，四个字段可读回', () => {
    const obj = parseStyleText(SERVER_DUMP);
    expect(obj).not.toBeNull();
    expect(obj!.topics).toEqual(['AI', '产品设计']);
    expect(obj!.target_audience).toBe('25-40岁');
    expect(obj!.blacklist).toEqual({ words: ['震惊'], topics: ['政治敏感'] });
    expect(obj!.content_style).toBe('干货');
  });

  it('placeholder 自身能被解析成合法对象', () => {
    const obj = parseStyleText(STYLE_PLACEHOLDER);
    expect(obj).not.toBeNull();
    expect(Array.isArray(obj!.topics)).toBe(true);
    expect(obj!.topics).toEqual(['方向1', '方向2']);
    expect(obj!.blacklist).toEqual({ words: [], topics: [] });
  });

  it('非法 YAML / 非对象返回 null', () => {
    expect(parseStyleText('topics: [unclosed')).toBeNull();
    expect(parseStyleText('- 1\n- 2')).toBeNull();
  });
});
