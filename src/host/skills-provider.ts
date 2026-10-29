/**
 * skills-provider.ts（设计 §5 范围第 5 条）：带来源哈希的随包 Provider。
 *
 * - provider 名 `wewrite-python-bundle`，`source='bundled'`、`rank=BUNDLED_SKILL_RANK`、
 *   `resourceBase={kind:'directory'}`。
 * - 从固定 `skills/manifest.json` 加载（含名称、源修订、源哈希），**禁止目录扫描**接受任意
 *   skill 或引用逃出包根。
 */

import { readFileSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BUNDLED_SKILL_RANK, type SkillCandidate, type SkillDefinition, type SkillProvider } from '@deepseek-ai/dsh-skill';

export const PROVIDER_NAME = 'wewrite-python-bundle';

interface ManifestSkill {
  readonly name: string;
  readonly description: string;
  readonly path: string;
  readonly sha256: string;
}

interface Manifest {
  readonly version: number;
  readonly provider: string;
  readonly sourceRevision: string;
  readonly sourceHash: string;
  readonly skills: ManifestSkill[];
}

const SKILL_FILENAME = 'SKILL.md';

function parseSkill(raw: string, path: string): { description: string; content: string } {
  const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/u.exec(raw);
  if (frontmatter?.[1] === undefined) throw new Error(`wewrite skills: ${path} 缺少 YAML frontmatter`);
  const descriptionMatch = /^\s*description\s*:\s*(.+)$/m.exec(frontmatter[1]);
  if (!descriptionMatch) throw new Error(`wewrite skills: ${path} 缺少 description`);
  return { description: descriptionMatch[1].trim(), content: raw.slice(frontmatter[0].length).trim() };
}

/** 校验相对路径不逃出包根（禁绝对路径/../ 编码穿越）。 */
export function safeSkillPath(root: string, rel: string): string {
  if (!rel || rel.includes('\0') || rel.includes('\\')) throw new Error(`wewrite skills: 非法技能路径 ${rel}`);
  const resolved = resolve(root, rel);
  const rootWithSep = root.endsWith(sep) ? root : root + sep;
  if (resolved !== root && !resolved.startsWith(rootWithSep)) {
    throw new Error(`wewrite skills: 技能路径越界 ${rel}`);
  }
  return resolved;
}

/** 从固定 manifest.json 加载候选（禁止目录扫描）。 */
export function loadManifest(manifestPath: string): Manifest {
  const raw = readFileSync(manifestPath, 'utf8');
  const parsed = JSON.parse(raw) as Manifest;
  if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.skills)) {
    throw new Error('wewrite skills: manifest 不合法');
  }
  if (parsed.provider !== PROVIDER_NAME) {
    throw new Error(`wewrite skills: provider 名必须为 ${PROVIDER_NAME}`);
  }
  return parsed;
}

export function buildProvider(options: { manifestPath: string; resourceBase?: string }): SkillProvider {
  const manifest = loadManifest(options.manifestPath);
  const manifestDir = dirname(options.manifestPath);
  const resourceBase = options.resourceBase ?? manifestDir;

  const candidates: SkillCandidate[] = manifest.skills.map((skill) => {
    const directory = safeSkillPath(manifestDir, dirname(skill.path));
    const skillFile = safeSkillPath(manifestDir, skill.path);
    return {
      name: skill.name,
      description: skill.description,
      invocation: { modelInvocable: true, userInvocable: true },
      provider: PROVIDER_NAME,
      source: 'bundled',
      rank: BUNDLED_SKILL_RANK,
      resourceBase: { kind: 'directory', path: directory.split(sep).join('/') },
      locator: skillFile,
      metadata: {
        sourceRevision: manifest.sourceRevision,
        sourceHash: manifest.sourceHash,
        sha256: skill.sha256,
      },
    };
  });

  return {
    name: PROVIDER_NAME,
    list: () => Promise.resolve(candidates),
    async get(candidate, options): Promise<SkillDefinition | undefined> {
      const { rank: _rank, locator, ...summary } = candidate as SkillCandidate & { locator: string };
      const file = safeSkillPath(manifestDir, locator);
      const raw = readFileSync(file, 'utf8');
      const parsed = parseSkill(raw, file);
      return { ...summary, content: parsed.content };
    },
  };
}

/** 默认包内 manifest 路径（相对本文件：skills/manifest.json 位于包根）。 */
export function defaultManifestPath(): string {
  return fileURLToPath(new URL('../../skills/manifest.json', import.meta.url));
}

export function isAbsolutePath(p: string): boolean {
  return isAbsolute(p);
}
