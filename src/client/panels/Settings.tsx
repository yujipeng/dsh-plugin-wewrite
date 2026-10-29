/**
 * Settings 面板：设置/诊断。
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

export function SettingsPanel({ client }: SettingsPanelProps): JSX.Element {
  const [diagnostics, setDiagnostics] = useState<Diagnostic[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <PanelState loading={loading} error={error} forbidden={false} empty={diagnostics !== null && diagnostics.length === 0} emptyText="无诊断项">
      <div className="ww-settings" data-testid="wewrite-settings">
        <ul className="ww-settings__diags">
          {(diagnostics ?? []).map((d, i) => (
            <li key={i} data-testid={`ww-diag-${i}`}>{d.blocking ? '⚠️' : 'ℹ️'} {d.code}：{d.message}</li>
          ))}
        </ul>
      </div>
    </PanelState>
  );
}
