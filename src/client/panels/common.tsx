/**
 * 面板公共组件：四态（空/加载/错误/无权限）不白屏、不伪造数据。
 */

import type { ReactNode } from 'react';

export interface PanelStateProps {
  readonly empty: boolean;
  readonly loading: boolean;
  readonly error: string | null;
  readonly forbidden: boolean;
  readonly emptyText?: string;
  readonly children: ReactNode;
}

/** 统一四态外壳：加载/错误/无权限/空 任一命中即显示对应态，否则渲染 children。 */
export function PanelState(props: PanelStateProps): JSX.Element {
  if (props.loading) {
    return <div className="ww-state ww-state--loading" data-testid="ww-loading">加载中…</div>;
  }
  if (props.forbidden) {
    return <div className="ww-state ww-state--forbidden" data-testid="ww-forbidden">无权限访问该工作台</div>;
  }
  if (props.error) {
    return <div className="ww-state ww-state--error" data-testid="ww-error">{props.error}</div>;
  }
  if (props.empty) {
    return <div className="ww-state ww-state--empty" data-testid="ww-empty">{props.emptyText ?? '暂无数据'}</div>;
  }
  return <>{props.children}</>;
}
