/**
 * Settings 面板：设置/诊断 + 写作风格（style.yaml）区块。
 * 读 style.get 展示当前配置，可编辑并 style.put 保存，style.validate 显示问题。
 */

import { useEffect, useState } from 'react';
import type { WeWriteClient } from '../rpc';
import { PanelState } from './common';

interface SettingsPanelProps {
  readonly client: WeWriteClient;
}

interface Diagnostic {
  code: string;
  message: string;
  blocking: boolean;
}

interface StyleState {
  loading: boolean;
  error: string | null;
  exists: boolean;
  text: string;
  rawVersion: string | null;
  issues: string[];
  saving: boolean;
  saved: boolean;
}

export function SettingsPanel({ client }: SettingsPanelProps): JSX.Element {
  const [diagnostics, setDiagnostics] = useState<Diagnostic[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [style, setStyle] = useState<StyleState>({
    loading: true,
    error: null,
    exists: false,
    text: '',
    rawVersion: null,
    issues: [],
    saving: false,
    saved: false,
  });

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      const result = await client.snapshot();
      if (!active) return;
      if (!result.ok) {
        setError(result.error.message);
        setLoading(false);
        return;
      }
      setDiagnostics(((result.data as { diagnostics: Diagnostic[] }).diagnostics) ?? []);
      setLoading(false);
    })();
    return () => { active = false; };
  }, [client]);

  useEffect(() => {
    let active = true;
    (async () => {
      setStyle((s) => ({ ...s, loading: true, error: null, saved: false }));
      const result = await client.styleGet();
      if (!active) return;
      if (!result.ok && result.error.code !== 'NOT_FOUND') {
        setStyle((s) => ({ ...s, loading: false, error: result.error.message }));
        return;
      }
      if (result.ok) {
        const data = result.data as { exists: boolean; raw: string; version: string };
        setStyle((s) => ({
          ...s,
          loading: false,
          exists: true,
          text: data.raw,
          rawVersion: data.version,
          issues: [],
        }));
      } else {
        setStyle((s) => ({ ...s, loading: false, exists: false, text: '', rawVersion: null, issues: [] }));
      }
    })();
    return () => { active = false; };
  }, [client]);

  async function validateStyle(text: string): Promise<string[]> {
    try {
      const yaml = parseYaml(text);
      if (yaml instanceof Error) return [`style.yaml 不是合法 YAML：${yaml.message}`];
      const result = await client.styleValidate({ style: yaml });
      if (!result.ok) return [result.error.message];
      return ((result.data as { issues: string[] }).issues) ?? [];
    } catch (err) {
      return [err instanceof Error ? err.message : String(err)];
    }
  }

  async function saveStyle() {
    setStyle((s) => ({ ...s, saving: true, saved: false, issues: [] }));
    try {
      const yaml = parseYaml(style.text);
      if (yaml instanceof Error) {
        setStyle((s) => ({ ...s, saving: false, issues: [`style.yaml 不是合法 YAML：${yaml.message}`] }));
        return;
      }
      const result = await client.stylePut({ style: yaml });
      if (!result.ok) {
        setStyle((s) => ({ ...s, saving: false, issues: [result.error.message] }));
        return;
      }
      const data = result.data as { version: string };
      setStyle((s) => ({ ...s, saving: false, saved: true, rawVersion: data.version, exists: true }));
    } catch (err) {
      setStyle((s) => ({ ...s, saving: false, issues: [err instanceof Error ? err.message : String(err)] }));
    }
  }

  return (
    <PanelState loading={loading} error={error} forbidden={false} empty={diagnostics !== null && diagnostics.length === 0} emptyText="无诊断项">
      <div className="ww-settings" data-testid="wewrite-settings">
        <section className="ww-settings__style" data-testid="ww-style-section">
          <h2>写作风格</h2>
          {style.loading ? (
            <div data-testid="ww-style-loading">加载中…</div>
          ) : style.error ? (
            <div data-testid="ww-style-error">{style.error}</div>
          ) : (
            <>
              {!style.exists ? (
                <div data-testid="ww-style-missing">尚未配置 style.yaml，可粘贴 YAML 并保存。</div>
              ) : null}
              <textarea
                data-testid="ww-style-editor"
                value={style.text}
                rows={14}
                onChange={(e) => setStyle((s) => ({ ...s, text: e.target.value, saved: false }))}
                placeholder={'name: "客户名称"\nindustry: "行业"\ntopics:\n  - "方向1"\ntone: "写作风格"\ntheme: "professional-clean"\ntarget_audience: "目标受众"\ncontent_style: "干货"\nblacklist:\n  words: []\n  topics: []'}
              />
              <div className="ww-settings__style-actions">
                <button
                  type="button"
                  data-testid="ww-style-save"
                  disabled={style.saving}
                  onClick={() => void saveStyle()}
                >
                  {style.saving ? '保存中…' : '保存风格配置'}
                </button>
                {style.saved ? <span data-testid="ww-style-saved">已保存</span> : null}
              </div>
              {style.issues.length > 0 ? (
                <ul className="ww-settings__issues" data-testid="ww-style-issues">
                  {style.issues.map((issue, i) => (
                    <li key={i}>{issue}</li>
                  ))}
                </ul>
              ) : null}
            </>
          )}
        </section>

        <ul className="ww-settings__diags">
          {(diagnostics ?? []).map((d, i) => (
            <li key={i} data-testid={`ww-diag-${i}`}>{d.blocking ? '⚠️' : 'ℹ️'} {d.code}：{d.message}</li>
          ))}
        </ul>
      </div>
    </PanelState>
  );
}

/** 极简 YAML 子集解析（style.yaml 全为标量/列表/映射，不涉及锚点/多行块，足够展示与校验）。 */
function parseYaml(text: string): Record<string, unknown> | Error {
  const result: Record<string, unknown> = {};
  const lines = text.split('\n');
  const stack: Array<{ key: string; target: unknown; indent: number }> = [];

  for (const raw of lines) {
    if (raw.trim() === '' || raw.trimStart().startsWith('#')) continue;
    const indent = raw.length - raw.trimStart().length;
    const line = raw.trim();

    const listMatch = /^- (.*)$/.exec(line);
    if (listMatch !== null) {
      const value = parseScalar(listMatch[1].trim());
      const parent = stack.length > 0 ? stack[stack.length - 1]!.target : result;
      if (!Array.isArray(parent)) {
        return new Error('列表缩进不合法');
      }
      parent.push(value);
      continue;
    }

    const mapMatch = /^([A-Za-z_][A-Za-z0-9_-]*):(.*)$/.exec(line);
    if (mapMatch === null) return new Error(`无法解析行：${line}`);
    const key = mapMatch[1]!;
    const rawValue = mapMatch[2]!.trim();

    while (stack.length > 0 && indent <= stack[stack.length - 1]!.indent) {
      stack.pop();
    }
    const parent = stack.length > 0 ? stack[stack.length - 1]!.target : result;

    if (rawValue === '') {
      const child: Record<string, unknown> = {};
      (parent as Record<string, unknown>)[key] = child;
      stack.push({ key, target: child, indent });
      continue;
    }
    if (rawValue.startsWith('[') && rawValue.endsWith(']')) {
      (parent as Record<string, unknown>)[key] = parseInlineList(rawValue);
      continue;
    }
    (parent as Record<string, unknown>)[key] = parseScalar(rawValue);
  }
  return result;
}

function parseInlineList(text: string): unknown[] {
  const inner = text.slice(1, -1).trim();
  if (inner === '') return [];
  return inner.split(',').map((item) => parseScalar(item.trim().replace(/^"(.*)"$/, '$1')));
}

function parseScalar(text: string): unknown {
  if (text === '' || text === '~' || text === 'null') return null;
  if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) {
    return text.slice(1, -1);
  }
  if (/^-?\d+$/.test(text)) return Number(text);
  if (/^(true|false)$/i.test(text)) return text.toLowerCase() === 'true';
  return text;
}
