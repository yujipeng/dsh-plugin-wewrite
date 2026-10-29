/**
 * Topics 面板：选题中心（热榜 + 「写这个」）。
 */

import { useEffect, useState } from 'react';
import type { WeWriteClient } from '../rpc';
import { PanelState } from './common';

interface Hotspot {
  title: string;
  url: string;
  source: string;
  hotNormalized: number;
}

interface TopicsPanelProps {
  readonly client: WeWriteClient;
  readonly onWrite?: (topic: string, themeId: string) => void;
  /** 四轮-3：无工作区时「写这个」禁用并给原因，不发起 runCreate。 */
  readonly canWrite?: boolean;
  readonly writeDisabledReason?: string | null;
}

export function TopicsPanel({ client, onWrite, canWrite = true, writeDisabledReason = null }: TopicsPanelProps): JSX.Element {
  const [items, setItems] = useState<Hotspot[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      const result = await client.hotspotsFetch({ limit: 20 });
      if (!active) return;
      if (!result.ok) {
        setError(result.error.message);
        setLoading(false);
        return;
      }
      setItems(((result.data as { items: Hotspot[] }).items) ?? []);
      setLoading(false);
    })();
    return () => { active = false; };
  }, [client]);

  return (
    <PanelState loading={loading} error={error} forbidden={false} empty={items !== null && items.length === 0} emptyText="暂无热榜数据">
      <div className="ww-topics" data-testid="wewrite-topics">
        <ul className="ww-topics__list">
          {(items ?? []).map((item, i) => (
            <li key={`${item.source}-${i}`} className="ww-topics__item">
              <span className="ww-topics__title">{item.title}</span>
              <span className="ww-topics__source">{item.source} · {item.hotNormalized}</span>
              <button
                onClick={() => canWrite && onWrite?.(item.title, 'professional-clean')}
                disabled={!canWrite}
                title={writeDisabledReason ?? undefined}
                data-testid={`ww-write-${i}`}
              >写这个</button>
            </li>
          ))}
        </ul>
      </div>
    </PanelState>
  );
}
