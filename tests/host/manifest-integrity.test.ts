/**
 * manifest 完整性（方案 A 验收 + 返修 4）：真实 sha256、无占位串、
 * 来源修订为 imraywang/wewrite@2860fbe、SKILL.md 与 references 逐文件哈希均与磁盘一致。
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
  references: Array<{ path: string; sha256: string; adapted?: boolean; note?: string }>;
}

const HEX64 = /^[a-f0-9]{64}$/;

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

describe('manifest 完整性（方案 A + 返修 4）', () => {
  const manifestPath = resolve(ROOT, 'skills', 'manifest.json');
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as Manifest;

  it('来源修订/哈希为 imraywang/wewrite@8a5cdab，无占位串', () => {
    expect(manifest.sourceRevision).toBe('imraywang/wewrite@8a5cdabd303c147f174c9597dd334a1c3d3b50bd');
    expect(manifest.sourceHash).toBe('8a5cdabd303c147f174c9597dd334a1c3d3b50bd');
    expect(manifest.sourceRevision).not.toContain('220db1dc');
  });

  it('每个 skill 的 sha256 都是 64 位小写十六进制真实值（无 main-skill/topic 等占位）', () => {
    expect(manifest.skills.length).toBeGreaterThanOrEqual(8);
    for (const skill of manifest.skills) {
      expect(skill.sha256).toMatch(HEX64);
      expect(skill.sha256).not.toMatch(/^(main-skill|topic|write|review|visual|publish|style|learn)$/);
    }
  });

  it('方案 B 批一：新增 wewrite-style 与 wewrite-learn 两个 skill 条目', () => {
    const names = manifest.skills.map((s) => s.name);
    expect(names).toContain('wewrite-style');
    expect(names).toContain('wewrite-learn');
  });

  it('每个 skill 的 sha256 与磁盘 SKILL.md 一致', () => {
    for (const skill of manifest.skills) {
      const file = resolve(ROOT, 'skills', skill.path);
      expect(existsSync(file)).toBe(true);
      expect(sha256(file)).toBe(skill.sha256);
    }
  });

  it('12 份 references 逐文件 sha256 已在 manifest 登记，且与磁盘文件一致', () => {
    expect(Array.isArray(manifest.references)).toBe(true);
    expect(manifest.references.length).toBe(12);
    for (const ref of manifest.references) {
      expect(ref.sha256).toMatch(HEX64);
      const file = resolve(ROOT, 'skills', ref.path);
      expect(existsSync(file), `${ref.path} 缺失`).toBe(true);
      expect(sha256(file), `${ref.path} 哈希不一致`).toBe(ref.sha256);
    }
  });

  it('pipeline-state.md 已标注「已按受控工具适配」，其余 11 份 adapted=false', () => {
    const pipeline = manifest.references.find((r) => r.path === 'wewrite/references/pipeline-state.md');
    expect(pipeline?.adapted).toBe(true);
    expect(pipeline?.note).toContain('受控工具适配');
    const others = manifest.references.filter((r) => r.path !== 'wewrite/references/pipeline-state.md');
    expect(others.length).toBe(11);
    for (const r of others) {
      expect(r.adapted).toBe(false);
    }
  });
});
