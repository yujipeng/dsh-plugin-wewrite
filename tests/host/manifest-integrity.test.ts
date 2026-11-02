/**
 * manifest 完整性（方案 A 验收）：真实 sha256、无占位串、来源修订为 imraywang/wewrite@2860fbe、
 * 逐文件哈希与磁盘文件一致，且 references 目录全部随包（files 覆盖 skills/**）。
 */

import { createHash } from 'node:crypto';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = resolve(__dirname, '..', '..');

interface Manifest {
  version: number;
  provider: string;
  sourceRevision: string;
  sourceHash: string;
  skills: Array<{ name: string; description: string; path: string; sha256: string }>;
}

const HEX64 = /^[a-f0-9]{64}$/;

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

describe('manifest 完整性（方案 A）', () => {
  const manifestPath = resolve(ROOT, 'skills', 'manifest.json');
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as Manifest;

  it('来源修订/哈希为 imraywang/wewrite@2860fbe，无占位串', () => {
    expect(manifest.sourceRevision).toBe('imraywang/wewrite@2860fbe958311fcc04009d8132908989e43e6eaa');
    expect(manifest.sourceHash).toBe('2860fbe958311fcc04009d8132908989e43e6eaa');
    expect(manifest.sourceRevision).not.toContain('220db1dc');
  });

  it('每个 skill 的 sha256 都是 64 位小写十六进制真实值（无 main-skill/topic 等占位）', () => {
    expect(manifest.skills.length).toBeGreaterThanOrEqual(6);
    for (const skill of manifest.skills) {
      expect(skill.sha256).toMatch(HEX64);
      expect(skill.sha256).not.toMatch(/^(main-skill|topic|write|review|visual|publish)$/);
    }
  });

  it('每个 skill 的 sha256 与磁盘 SKILL.md 一致', () => {
    for (const skill of manifest.skills) {
      const file = resolve(ROOT, 'skills', skill.path);
      expect(existsSync(file)).toBe(true);
      expect(sha256(file)).toBe(skill.sha256);
    }
  });

  it('9 个 references 文件全部就位（含随包 skills/** 目录）', () => {
    const references = [
      'skills/wewrite/references/pipeline-state.md',
      'skills/wewrite-topic/references/topic-selection.md',
      'skills/wewrite-write/references/article-brief.md',
      'skills/wewrite-write/references/content-enhance.md',
      'skills/wewrite-write/references/editorial-quality.md',
      'skills/wewrite-write/references/frameworks-quick.md',
      'skills/wewrite-review/references/seo-rules.md',
      'skills/wewrite-visual/references/visual-guide.md',
      'skills/wewrite-publish/references/wechat-constraints.md',
    ];
    for (const ref of references) {
      expect(existsSync(resolve(ROOT, ref)), `${ref} 缺失`).toBe(true);
    }
  });
});
