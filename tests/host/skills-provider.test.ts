/**
 * skills-provider 测试（设计 §5 范围第 5 条）：固定 manifest 加载、路径逃出拒绝。
 */

import { describe, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildProvider, loadManifest, PROVIDER_NAME, safeSkillPath } from '@/host/skills-provider';

function makeFixture() {
  const root = mkdtempSync(join(tmpdir(), 'wewrite-skills-'));
  mkdirSync(join(root, 'wewrite'));
  writeFileSync(
    join(root, 'wewrite', 'SKILL.md'),
    '---\ndescription: 测试技能\n---\n\n# 正文\n',
    'utf8',
  );
  const manifest = {
    version: 1,
    provider: PROVIDER_NAME,
    sourceRevision: 'rev',
    sourceHash: 'hash',
    skills: [{ name: 'wewrite', description: '测试技能', path: 'wewrite/SKILL.md', sha256: 'h' }],
  };
  writeFileSync(join(root, 'manifest.json'), JSON.stringify(manifest), 'utf8');
  return root;
}

describe('skills-provider（随包 Provider）', () => {
  it('manifest 加载 + provider 候选（source=bundled、rank=BUNDLED、resourceBase=directory）', async () => {
    const root = makeFixture();
    const provider = buildProvider({ manifestPath: join(root, 'manifest.json') });
    expect(provider.name).toBe(PROVIDER_NAME);
    const list = (await provider.list({})) as unknown as Array<Record<string, unknown>>;
    expect(list).toHaveLength(1);
    expect(list[0].name).toBe('wewrite');
    expect(list[0].source).toBe('bundled');
    expect(list[0].resourceBase).toEqual({ kind: 'directory', path: join(root, 'wewrite') });
    const def = await provider.get(list[0] as never, {});
    expect(def?.content).toContain('# 正文');
  });

  it('路径逃出包根被拒（../ 与绝对路径）', () => {
    const root = makeFixture();
    expect(() => safeSkillPath(root, '../escape')).toThrow('越界');
    expect(() => safeSkillPath(root, '/abs/path')).toThrow('越界');
  });

  it('manifest provider 名不符被拒', () => {
    const root = makeFixture();
    const manifestPath = join(root, 'manifest.json');
    const parsed = loadManifest(manifestPath);
    expect(parsed.provider).toBe(PROVIDER_NAME);
    expect(() => loadManifest(join(root, 'nope.json'))).toThrow();
  });
});
