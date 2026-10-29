/**
 * Runs 面板：任务列表 + 状态点/门禁标记/筛选。
 */

import { useEffect, useState } from 'react';
import type { WeWriteClient } from '../rpc';
import { PanelState } from './common';

interface RunItem {
  runId: string;
  title: string;
  phase: string;
  pythonStatus: string;
  editable: boolean;
  updated: string;
}

interface RunsPanelProps {
  readonly client: WeWriteClient;
  readonly onSelect?: (runId: string) => void;
}

type Filter = 'all' | 'editable' | 'ready';

export function RunsPanel({ client, onSelect }: RunsPanelProps): JSX.Element {
  const [runs, setRuns] = useState<RunItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('all');

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
      setRuns(((result.data as { runs: RunItem[] }).runs) ?? []);
      setLoading(false);
    })();
    return () => { active = false; };
  }, [client]);

  const filtered = (runs ?? []).filter((r) => {
    if (filter === 'editable') return r.editable;
    if (filter === 'ready') return r.phase === 'ready';
    return true;
  });

  return (
    <PanelState loading={loading} error={error} forbidden={false} empty={runs !== null && runs.length === 0} emptyText="还没有写作任务">
      <div className="ww-runs" data-testid="wewrite-runs">
        <div className="ww-runs__filter">
          {(['all', 'editable', 'ready'] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? 'is-active' : ''} onClick={() => setFilter(f)} data-testid={`ww-filter-${f}`}>
              {f === 'all' ? '全部' : f === 'editable' ? '可编辑' : '已就绪'}
            </button>
          ))}
        </div>
        <ul className="ww-runs__list">
          {filtered.map((run) => (
            <li key={run.runId} className="ww-runs__item" data-testid={`ww-run-${run.runId}`}>
              <span className={`ww-runs__dot ww-runs__dot--${run.phase}`} data-testid={`ww-phase-${run.runId}`} />
              <span className="ww-runs__title">{run.title || '未命名文章'}</span>
              <span className="ww-runs__status">{run.phase}</span>
              <button onClick={() => onSelect?.(run.runId)} data-testid={`ww-open-${run.runId}`}>打开</button>
            </li>
          ))}
        </ul>
      </div>
    </PanelState>
  );
}
