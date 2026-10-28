#!/usr/bin/env node
/**
 * 构建脚本：产 lib/index.js（host）、lib/shared.js（契约）、lib/client.js（Web client）。
 * - host/shared 打 ESM bundle，packages external（zod/@deepseek-ai/* 安装侧解析）。
 * - client 打 CJS，包 window.__ModuleLoader__.load 外壳；react/react-dom/@deepseek-ai/* 由宿主
 *   ModuleLoader 提供，external 化，绝不打包第二份 React/Cordis。
 * 类型声明由 tsc -p tsconfig.build.json 产 lib/types/*.d.ts。
 */

import { build } from 'esbuild';
import { mkdirSync } from 'node:fs';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const lib = join(root, 'lib');
mkdirSync(lib, { recursive: true });

const common = {
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node22',
  packages: 'external',
  sourcemap: true,
  logLevel: 'info',
};

await build({ ...common, entryPoints: [join(root, 'src/host/index.ts')], outfile: join(lib, 'index.js') });
await build({ ...common, entryPoints: [join(root, 'src/shared/index.ts')], outfile: join(lib, 'shared.js') });

const clientResult = await build({
  bundle: true,
  format: 'cjs',
  platform: 'browser',
  target: 'es2020',
  entryPoints: [join(root, 'src/client/index.tsx')],
  jsx: 'automatic',
  external: [
    'react',
    'react/jsx-runtime',
    'react-dom',
    'react-dom/client',
    '@deepseek-ai/*',
  ],
  minify: true,
  logLevel: 'info',
  write: false,
});

for (const out of clientResult.outputFiles) {
  if (out.path === '<stdout>' || out.path.endsWith('.js')) {
    const body = out.text;
    const wrapped = `window.__ModuleLoader__.load({ id: "dsh-plugin-wewrite", factory: (require) => {\nvar module = { exports: {} }; var exports = module.exports;\n${body}\nreturn module.exports; } });\n`;
    writeFileSync(join(lib, 'client.js'), wrapped, 'utf8');
  }
}

console.log('lib/index.js、lib/shared.js、lib/client.js 已生成');
