import { useEffect, useMemo, useRef } from 'react';
import type { Incident, Severity } from '../api/types';
import { useDash, useNow } from '../state/dashboard';
import { ACTIVE, SEVERITY_RANK, age, clock, incidentCameras, parseTs, sortIncidents, typeLabel, zoneName } from '../lib/format';
import { SEV_BAR, SectionHead, StatusTag } from './ui';

export type QueueFilter = { scope: 'active' | 'all'; minSeverity: Severity; type: string };
export const DEFAULT_FILTER: QueueFilter = { scope: 'active', minSeverity: 'LOW', type: '' };
const NEW_MS = 30000;

export function filterIncidents(all: Incident[], f: QueueFilter): Incident[] {
  return sortIncidents(all.filter((i) =>
    (f.scope === 'all' || ACTIVE.includes(i.status))
    && SEVERITY_RANK[i.severity] >= SEVERITY_RANK[f.minSeverity]
    && (!f.type || i.incident_type === f.type)));
}

export function IncidentQueue({ rows, filter, setFilter, selectedId, onSelect }: {
  rows: Incident[]; filter: QueueFilter; setFilter: (f: QueueFilter) => void;
  selectedId: string | null; onSelect: (id: string) => void;
}) {
  const { state } = useDash();
  const now = useNow(1000);
  const mountedAt = useRef(Date.now());
  const all = Object.values(state.incidents);
  const active = all.filter((i) => ACTIVE.includes(i.status)).length;
  const types = useMemo(() => [...new Set(all.map((i) => i.incident_type))].sort(), [all.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const listRef = useRef<HTMLUListElement>(null);

  // keep the keyboard-selected row visible
  useEffect(() => {
    if (!selectedId || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-id="${selectedId}"]`);
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [selectedId]);

  return (
    <section className="flex flex-col min-h-0 h-full" aria-label="Incident queue">
      <SectionHead
        title="Incidents"
        right={<span className="tnum"><b className="text-fg">{active}</b> active · {state.incidentsTotal} total</span>}
      />
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-ink-700 bg-ink-900 text-2xs">
        <div role="group" aria-label="Scope" className="flex border border-ink-600 rounded-sm overflow-hidden">
          {(['active', 'all'] as const).map((s) => (
            <button key={s} onClick={() => setFilter({ ...filter, scope: s })} aria-pressed={filter.scope === s}
                    className={`px-2 h-6 font-semibold uppercase tracking-wider ${filter.scope === s ? 'bg-ink-600 text-fg' : 'text-fg-muted hover:text-fg'}`}>
              {s}
            </button>
          ))}
        </div>
        <select aria-label="Minimum severity" value={filter.minSeverity}
                onChange={(e) => setFilter({ ...filter, minSeverity: e.target.value as Severity })}
                className="h-6 bg-ink-800 border border-ink-600 rounded-sm px-1 text-fg">
          <option value="LOW">All severities</option>
          <option value="MEDIUM">Medium +</option>
          <option value="HIGH">High +</option>
          <option value="CRITICAL">Critical</option>
        </select>
        <select aria-label="Incident type" value={filter.type} onChange={(e) => setFilter({ ...filter, type: e.target.value })}
                className="h-6 min-w-0 flex-1 bg-ink-800 border border-ink-600 rounded-sm px-1 text-fg">
          <option value="">All types</option>
          {types.map((t) => <option key={t} value={t}>{typeLabel(t)}</option>)}
        </select>
      </div>
      {rows.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-1 text-fg-muted p-6 text-center">
          <span className="label">{filter.scope === 'active' ? 'No active incidents' : 'No incidents'}</span>
          <span className="text-2xs text-fg-faint">New incidents appear here as the rules raise them.</span>
        </div>
      ) : (
        <ul ref={listRef} role="listbox" aria-label="Incidents" className="flex-1 min-h-0 overflow-y-auto">
          {rows.map((i) => {
            const t = parseTs(i.start_time);
            const isNew = t > mountedAt.current - 1000 && now - t < NEW_MS;
            const cams = incidentCameras(i, state.site);
            const sel = i.incident_id === selectedId;
            return (
              <li key={i.incident_id} data-id={i.incident_id} role="option" aria-selected={sel}>
                <button onClick={() => onSelect(i.incident_id)} data-testid={`incident-${i.incident_id}`}
                        className={`w-full flex text-left border-b border-ink-800 ${sel ? 'bg-ink-700' : 'hover:bg-ink-850'} ${ACTIVE.includes(i.status) ? '' : 'opacity-60'}`}>
                  <span aria-hidden className={`w-1 shrink-0 ${SEV_BAR[i.severity]}`} />
                  <span className="flex-1 min-w-0 px-2.5 py-1.5">
                    <span className="flex items-baseline gap-2">
                      <span className="font-semibold text-fg truncate">{typeLabel(i.incident_type)}</span>
                      <span className="text-fg-muted truncate">{zoneName(state.zones, i.zone_id)}</span>
                      {isNew && <span className="text-2xs font-bold text-sev-high" title="raised in the last 30 s">NEW</span>}
                      <span className="ml-auto text-fg-muted tnum shrink-0" title={i.start_time}>{clock(i.start_time)}</span>
                    </span>
                    <span className="flex items-center gap-2 mt-0.5 text-2xs text-fg-muted">
                      <span className="sr-only">{i.severity} severity, </span>
                      <StatusTag status={i.status} />
                      {i.track_id && <span className="font-mono truncate" title="per-camera track label, not an identity">{i.track_id}</span>}
                      {cams.length > 0 && <span className="font-mono shrink-0">{cams.join(' ')}</span>}
                      <span className="ml-auto tnum shrink-0">{age(t, now)} ago</span>
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
