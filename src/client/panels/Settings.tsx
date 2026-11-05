/**
 * Settings 面板：诊断 + 写作风格（style.yaml）+ 改稿学习 / 范文库列表。
 * - style：读 style.get 展示，可编辑并 style.put 保存，style.validate 显示问题。
 * - 学习/范文：learn.list / exemplar.list 列表视图，exemplar 提供入库入口。
 */

import { useEffect, useState } from 'react';
import { parse } from 'yaml';
import type { WeWriteClient } from '../rpc';

interface SettingsPanelProps {
  readonly client: WeWriteClient;
}

interface DiagnosticAction {
  label: string;
  command: string;
  kind: string;
  recheck: boolean;
}

interface Diagnostic {
  code: string;
  message: string;
  blocking: boolean;
  actions?: DiagnosticAction[];
}

interface StyleState {
  loading: boolean;
  error: string | null;
  exists: boolean;
  text: string;
  version: string | null;
  issues: string[];
  saving: boolean;
  saved: boolean;
  validating: boolean;
}

interface LearnItem {
  file: string;
  date?: string;
  timestamp?: string;
  patternCount?: number;
}

interface ExemplarItem {
  id: string;
  title: string;
  source: string;
  category: string;
  ownership: string;
  authenticity: string;
}

/** 与 yaml 解析能力一致的占位示例（缩进用两空格，列表项缩进两格）。 */
export const STYLE_PLACEHOLDER = [
  'name: "客户名称"',
  'industry: "行业"',
  'topics:',
  '  - "方向1"',
  '  - "方向2"',
  'tone: "写作风格"',
  'theme: "professional-clean"',
  'target_audience: "目标受众"',
  'content_style: "干货"',
  'blacklist:',
  '  words: []',
  '  topics: []',
].join('\n');

/** 解析 style.yaml 文本为对象；非法/非对象返回 null（面板保存与校验共用）。 */
export function parseStyleText(text: string): Record<string, unknown> | null {
  try {
    const obj = parse(text);
    if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) return null;
    return obj as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** 从诊断里取第一条「安装」类可执行动作（供设置面板展示缺什么 → 一条可复制命令）。 */
export function firstInstallAction(diagnostics: Diagnostic[] | null): DiagnosticAction | null {
  for (const d of diagnostics ?? []) {
    const action = (d.actions ?? []).find((a) => a.kind === 'install');
    if (action) return action;
  }
  return null;
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
    version: null,
    issues: [],
    saving: false,
    saved: false,
    validating: false,
  });
  const [learn, setLearn] = useState<{ loading: boolean; error: string | null; items: LearnItem[] }>({ loading: true, error: null, items: [] });
  const [exemplars, setExemplars] = useState<{ loading: boolean; error: string | null; items: ExemplarItem[] }>({ loading: true, error: null, items: [] });
  const [exemplarDraft, setExemplarDraft] = useState<{ title: string; content: string; source: string }>({ title: '', content: '', source: '' });
  const [exemplarMsg, setExemplarMsg] = useState<string | null>(null);
  const [rechecking, setRechecking] = useState(false);
  const [recheckMsg, setRecheckMsg] = useState<string | null>(null);

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

  /** 重新检测：走宿主能力重新探针，而不是重渲染旧快照。 */
  async function recheckDiagnostics() {
    setRechecking(true);
    setRecheckMsg(null);
    const result = await client.diagnosticsRecheck();
    if (!result.ok) {
      setRechecking(false);
      setRecheckMsg(result.error.message);
      return;
    }
    setDiagnostics(((result.data as { diagnostics: Diagnostic[] }).diagnostics) ?? []);
    setRechecking(false);
    setRecheckMsg('已重新检测');
  }

  async function reloadStyle() {
    setStyle((s) => ({ ...s, loading: true, error: null, saved: false }));
    const result = await client.styleGet();
    if (!result.ok && result.error.code !== 'NOT_FOUND') {
      setStyle((s) => ({ ...s, loading: false, error: result.error.message }));
      return;
    }
    if (result.ok) {
      const data = result.data as { raw: string; version: string };
      setStyle((s) => ({ ...s, loading: false, exists: true, text: data.raw, version: data.version, issues: [] }));
    } else {
      setStyle((s) => ({ ...s, loading: false, exists: false, text: STYLE_PLACEHOLDER, version: null, issues: [] }));
    }
  }

  async function reloadLists() {
    setLearn((s) => ({ ...s, loading: true, error: null }));
    setExemplars((s) => ({ ...s, loading: true, error: null }));
    const [lr, er] = await Promise.all([client.learnList(), client.exemplarList()]);
    if (lr.ok) setLearn({ loading: false, error: null, items: (lr.data as { items: LearnItem[] }).items ?? [] });
    else setLearn({ loading: false, error: lr.error.message, items: [] });
    if (er.ok) setExemplars({ loading: false, error: null, items: (er.data as { items: ExemplarItem[] }).items ?? [] });
    else setExemplars({ loading: false, error: er.error.message, items: [] });
  }

  useEffect(() => {
    void reloadStyle();
    void reloadLists();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  async function validateStyle() {
    setStyle((s) => ({ ...s, validating: true, issues: [], saved: false }));
    try {
      const obj = parse(style.text);
      if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
        setStyle((s) => ({ ...s, validating: false, issues: ['style.yaml 必须是 YAML 对象'] }));
        return;
      }
      const result = await client.styleValidate({ style: obj as Record<string, unknown> });
      if (!result.ok) {
        setStyle((s) => ({ ...s, validating: false, issues: [result.error.message] }));
        return;
      }
      setStyle((s) => ({ ...s, validating: false, issues: ((result.data as { issues: string[] }).issues) ?? [] }));
    } catch (err) {
      setStyle((s) => ({ ...s, validating: false, issues: [err instanceof Error ? `YAML 解析失败：${err.message}` : String(err)] }));
    }
  }

  async function saveStyle() {
    setStyle((s) => ({ ...s, saving: true, saved: false, issues: [] }));
    try {
      const obj = parse(style.text);
      if (obj === null || typeof obj !== 'object' || Array.isArray(obj)) {
        setStyle((s) => ({ ...s, saving: false, issues: ['style.yaml 必须是 YAML 对象'] }));
        return;
      }
      const result = await client.stylePut({ style: obj as Record<string, unknown> });
      if (!result.ok) {
        setStyle((s) => ({ ...s, saving: false, issues: [result.error.message] }));
        return;
      }
      const data = result.data as { version: string };
      setStyle((s) => ({ ...s, saving: false, saved: true, version: data.version, exists: true }));
    } catch (err) {
      setStyle((s) => ({ ...s, saving: false, issues: [err instanceof Error ? `YAML 解析失败：${err.message}` : String(err)] }));
    }
  }

  async function putExemplar() {
    setExemplarMsg(null);
    if (!exemplarDraft.title.trim() || !exemplarDraft.content.trim()) {
      setExemplarMsg('标题与正文不能为空');
      return;
    }
    const result = await client.exemplarPut({
      title: exemplarDraft.title.trim(),
      content: exemplarDraft.content,
      source: exemplarDraft.source.trim() || undefined,
    });
    if (!result.ok) {
      setExemplarMsg(result.error.message);
      return;
    }
    setExemplarDraft({ title: '', content: '', source: '' });
    setExemplarMsg('范文已入库');
    void reloadLists();
  }

  if (loading) {
    return <div className="ww-state ww-state--loading" data-testid="ww-loading">加载中…</div>;
  }
  if (error) {
    return <div className="ww-state ww-state--error" data-testid="ww-error">{error}</div>;
  }

  return (
    <div className="ww-settings" data-testid="wewrite-settings">
      {/* 写作风格区块：独立于诊断空态，数据根就绪（diagnostics 为空）时也必须可见。 */}
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
              placeholder={STYLE_PLACEHOLDER}
            />
            <div className="ww-settings__style-actions">
              <button type="button" data-testid="ww-style-save" disabled={style.saving} onClick={() => void saveStyle()}>
                {style.saving ? '保存中…' : '保存风格配置'}
              </button>
              <button type="button" data-testid="ww-style-validate" disabled={style.validating} onClick={() => void validateStyle()}>
                {style.validating ? '校验中…' : '校验'}
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

      {/* 改稿学习列表视图 */}
      <section className="ww-settings__learn" data-testid="ww-learn-section">
        <h2>改稿学习</h2>
        {learn.loading ? (
          <div>加载中…</div>
        ) : learn.error ? (
          <div>{learn.error}</div>
        ) : learn.items.length === 0 ? (
          <div data-testid="ww-learn-empty">暂无改稿学习记录（在编辑器保存时会记录差异）</div>
        ) : (
          <ul className="ww-settings__list" data-testid="ww-learn-list">
            {learn.items.map((item, i) => (
              <li key={i}>{item.file}{item.date ? `（${item.date}）` : ''}{item.patternCount !== undefined ? ` · ${item.patternCount} 个 pattern` : ''}</li>
            ))}
          </ul>
        )}
      </section>

      {/* 范文库列表视图 + 入库入口 */}
      <section className="ww-settings__exemplars" data-testid="ww-exemplars-section">
        <h2>范文库</h2>
        {exemplars.loading ? (
          <div>加载中…</div>
        ) : exemplars.error ? (
          <div>{exemplars.error}</div>
        ) : exemplars.items.length === 0 ? (
          <div data-testid="ww-exemplars-empty">范文库为空</div>
        ) : (
          <ul className="ww-settings__list" data-testid="ww-exemplars-list">
            {exemplars.items.map((item) => (
              <li key={item.id}>{item.title} · {item.source} · {item.category} · {item.ownership}</li>
            ))}
          </ul>
        )}
        <div className="ww-settings__exemplar-form" data-testid="ww-exemplar-form">
          <input
            data-testid="ww-exemplar-title"
            placeholder="范文标题"
            value={exemplarDraft.title}
            onChange={(e) => setExemplarDraft((d) => ({ ...d, title: e.target.value }))}
          />
          <input
            data-testid="ww-exemplar-source"
            placeholder="来源（可选）"
            value={exemplarDraft.source}
            onChange={(e) => setExemplarDraft((d) => ({ ...d, source: e.target.value }))}
          />
          <textarea
            data-testid="ww-exemplar-content"
            placeholder="范文正文（markdown）"
            rows={4}
            value={exemplarDraft.content}
            onChange={(e) => setExemplarDraft((d) => ({ ...d, content: e.target.value }))}
          />
          <button type="button" data-testid="ww-exemplar-put" onClick={() => void putExemplar()}>范文入库</button>
          {exemplarMsg ? <span data-testid="ww-exemplar-msg">{exemplarMsg}</span> : null}
        </div>
      </section>

      {/* 诊断列表（JEE-1996：缺依赖时给可执行命令 + 重新检测） */}
      <section className="ww-settings__diags-section" data-testid="ww-diags-section">
        <div className="ww-settings__diags-actions">
          <button type="button" data-testid="ww-recheck" disabled={rechecking} onClick={() => void recheckDiagnostics()}>
            {rechecking ? '检测中…' : '重新检测'}
          </button>
          {recheckMsg ? <span data-testid="ww-recheck-msg">{recheckMsg}</span> : null}
        </div>
        <ul className="ww-settings__diags">
          {(diagnostics ?? []).map((d, i) => (
            <li key={i} data-testid={`ww-diag-${i}`}>
              {d.blocking ? '⚠️' : 'ℹ️'} {d.code}：{d.message}
              {(d.actions ?? []).map((a, j) => (
                <div key={j} data-testid={`ww-diag-${i}-action-${j}`}>
                  <div data-testid={`ww-diag-${i}-action-label-${j}`}>{a.label}</div>
                  <code data-testid={`ww-diag-${i}-action-cmd-${j}`}>{a.command}</code>
                </div>
              ))}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
