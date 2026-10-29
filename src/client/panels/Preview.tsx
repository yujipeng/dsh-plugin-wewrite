/**
 * Preview 面板：sandbox iframe 预览（无脚本/同源能力、禁导航与自动外连）。
 * 服务端返回的 HTML 已白名单清洗 + CSP；图片走 asset/get 本地数据。
 */

import { useEffect, useState } from 'react';
import type { WeWriteClient } from '../rpc';
import { PanelState } from './common';

interface PreviewPanelProps {
  readonly client: WeWriteClient;
  readonly runId: string;
  readonly version: string | null;
}

export function PreviewPanel({ client, runId, version }: PreviewPanelProps): JSX.Element {
  const [html, setHtml] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      const result = await client.articlePreview({
        requestId: crypto.randomUUID(),
        runId,
        kind: 'article',
        expectedVersion: version,
        themeId: 'professional-clean',
      });
      if (!active) return;
      if (!result.ok) {
        setError(result.error.message);
        setLoading(false);
        return;
      }
      setHtml((result.data as { html: string }).html);
      setLoading(false);
    })();
    return () => { active = false; };
  }, [client, runId, version]);

  return (
    <PanelState loading={loading} error={error} forbidden={false} empty={html === null} emptyText="暂无预览">
      <div className="ww-preview" data-testid="wewrite-preview">
        <iframe
          title="preview"
          sandbox=""
          srcDoc={html ?? ''}
          data-testid="ww-preview-iframe"
        />
      </div>
    </PanelState>
  );
}
