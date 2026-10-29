/**
 * Review 面板：审稿报告面板。
 */

import { useEffect, useState } from 'react';
import type { WeWriteClient } from '../rpc';
import { PanelState } from './common';

interface ReviewPanelProps {
  readonly client: WeWriteClient;
  readonly runId: string;
}

export function ReviewPanel({ client, runId }: ReviewPanelProps): JSX.Element {
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [gate, setGate] = useState<{ publishable: boolean; issues: Array<{ code: string; message: string }> } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      const result = await client.runDetail({ runId });
      if (!active) return;
      if (!result.ok) {
        setError(result.error.message);
        setLoading(false);
        return;
      }
      const data = result.data as { gate: { publishable: boolean; issues: Array<{ code: string; message: string }> } | null };
      setGate(data.gate);
      setLoading(false);
    })();
    return () => { active = false; };
  }, [client, runId]);

  return (
    <PanelState loading={loading} error={error} forbidden={false} empty={gate === null} emptyText="尚无审稿报告">
      <div className="ww-review" data-testid="wewrite-review">
        <span className={`ww-review__verdict ${gate?.publishable ? 'is-pass' : 'is-blocked'}`} data-testid="ww-review-verdict">
          {gate?.publishable ? '审稿通过' : '需要修改'}
        </span>
        <ul className="ww-review__issues">
          {(gate?.issues ?? []).map((issue, i) => (
            <li key={i} data-testid={`ww-review-issue-${i}`}>{issue.code}：{issue.message}</li>
          ))}
        </ul>
      </div>
    </PanelState>
  );
}
