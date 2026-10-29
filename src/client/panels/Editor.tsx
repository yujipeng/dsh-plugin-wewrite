/**
 * Editor 面板：编辑器三视图（编辑 + 实时微信预览）。
 */

import { useEffect, useState } from 'react';
import type { WeWriteClient } from '../rpc';
import { PanelState } from './common';

interface EditorPanelProps {
  readonly client: WeWriteClient;
  readonly runId: string;
}

interface ArticleData {
  content: string;
  version: string | null;
  editable: boolean;
}

export function EditorPanel({ client, runId }: EditorPanelProps): JSX.Element {
  const [article, setArticle] = useState<ArticleData | null>(null);
  const [html, setHtml] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'edit' | 'preview'>('edit');
  const [draft, setDraft] = useState('');

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      const result = await client.articleGet({ runId, kind: 'article' });
      if (!active) return;
      if (!result.ok) {
        setError(result.error.message);
        setLoading(false);
        return;
      }
      const data = result.data as ArticleData;
      setArticle(data);
      setDraft(data.content);
      setLoading(false);
    })();
    return () => { active = false; };
  }, [client, runId]);

  async function refreshPreview(): Promise<void> {
    const result = await client.articlePreview({
      requestId: crypto.randomUUID(),
      runId,
      kind: 'article',
      expectedVersion: article?.version ?? null,
      themeId: 'professional-clean',
    });
    if (result.ok) setHtml((result.data as { html: string }).html);
  }

  async function save(): Promise<void> {
    const result = await client.articleSave({
      requestId: crypto.randomUUID(),
      runId,
      markdown: draft,
      expectedVersion: article?.version ?? null,
      metadata: { title: '未命名文章', digest: '', themeId: 'professional-clean' },
    });
    if (result.ok) {
      setArticle({ content: draft, version: (result.data as { version: string }).version, editable: true });
    } else {
      setError(result.error.message);
    }
  }

  return (
    <PanelState loading={loading} error={error} forbidden={!article?.editable && article !== null} empty={article === null} emptyText="暂无正文">
      <div className="ww-editor" data-testid="wewrite-editor">
        <div className="ww-editor__tabs">
          <button className={tab === 'edit' ? 'is-active' : ''} onClick={() => setTab('edit')}>编辑</button>
          <button className={tab === 'preview' ? 'is-active' : ''} onClick={() => { setTab('preview'); void refreshPreview(); }}>实时微信预览</button>
        </div>
        {tab === 'edit' ? (
          <div className="ww-editor__edit">
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} data-testid="ww-editor-textarea" />
            <button onClick={() => void save()} data-testid="ww-editor-save">保存</button>
          </div>
        ) : (
          <div className="ww-editor__preview">
            {/* sandbox iframe：无脚本/同源能力、禁导航与自动外连，图片走 asset/get 本地数据。
                预览 HTML 经服务端白名单清洗 + CSP；iframe 无 allow-scripts/allow-same-origin。 */}
            <iframe
              title="wechat-preview"
              sandbox=""
              srcDoc={html ?? ''}
              data-testid="ww-editor-preview"
            />
          </div>
        )}
      </div>
    </PanelState>
  );
}
