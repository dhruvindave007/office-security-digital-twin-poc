import { useEffect, useMemo, useRef, useState } from 'react';
import type { Incident, IncidentStatus, Severity } from '../api/types';
import { useDash, useNow } from '../state/dashboard';
import { useOps } from '../state/ops';
import { ACTIVE, SEVERITY_RANK, age, clock, groupKey, incidentCameras, isSimulated, parseTs, sortIncidents, typeLabel, zoneName } from '../lib/format';
import { Button, SEV_BAR, SectionHead, StatusTag } from './ui';

export type QueueFilter = { scope: 'active' | 'all'; minSeverity: Severity; type: string; zone: string; grouped: boolean };
export const DEFAULT_FILTER: QueueFilter = { scope: 'active', minSeverity: 'LOW', type: '', zone: '', grouped: true };
const NEW_MS = 30000;

export function filterIncidents(all: Incident[], f: QueueFilter): Incident[] {
  return sortIncidents(all.filter((i) =>
    (f.scope === 'all' || ACTIVE.includes(i.status))
    && SEVERITY_RANK[i.severity] >= SEVERITY_RANK[f.minSeverity]
    && (!f.type || i.incident_type === f.type)
    && (!f.zone || i.zone_id === f.zone)));
}

type Group = { key: string; members: Incident[] };   // members keep the queue order (worst, newest first)
type Item = { kind: 'incident'; inc: Incident; nested: boolean } | { kind: 'group'; g: Group; open: boolean };
const itemKey = (it: Item) => (it.kind === 'group' ? `g:${it.g.key}` : `i:${it.inc.incident_id}`);

/** Repeats (same rule, room, source and active/closed) become one group, placed where its first member sorts. */
export function groupRows(rows: Incident[], grouped: boolean): Group[] {
  if (!grouped) return rows.map((i) => ({ key: i.incident_id, members: [i] }));
  const map = new Map<string, Group>();
  for (const i of rows) {
    const k = groupKey(i);
    const g = map.get(k);
    if (g) g.members.push(i); else map.set(k, { key: k, members: [i] });
  }
  return [...map.values()];
}

const BULK: { to: IncidentStatus; label: string; tone?: 'primary' | 'ghost' }[] = [
  { to: 'INVESTIGATING', label: 'Start investigating' },
  { to: 'RESOLVED', label: 'Resolve', tone: 'primary' },
  { to: 'FALSE_POSITIVE', label: 'False positive' },
];

export function IncidentQueue({ rows, filter, setFilter, selectedId, onSelect, onOpen }: {
  rows: Incident[]; filter: QueueFilter; setFilter: (f: QueueFilter) => void;
  selectedId: string | null; onSelect: (id: string) => void; onOpen: (id: string) => void;
}) {
  const { state } = useDash();
  const { changeStatus } = useOps();
  const now = useNow(1000);
  const mountedAt = useRef(Date.now());
  const all = Object.values(state.incidents);
  const active = all.filter((i) => ACTIVE.includes(i.status)).length;
  const types = useMemo(() => [...new Set(all.map((i) => i.incident_type))].sort(), [all.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const listRef = useRef<HTMLUListElement>(null);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState<string | null>(selectedId ? `i:${selectedId}` : null);
  const [busy, setBusy] = useState<IncidentStatus | null>(null);
  const [bulkErr, setBulkErr] = useState<string | null>(null);

  const groups = useMemo(() => groupRows(rows, filter.grouped), [rows, filter.grouped]);
  const items = useMemo<Item[]>(() => groups.flatMap((g): Item[] => {
    if (g.members.length === 1) return [{ kind: 'incident', inc: g.members[0], nested: false }];
    const open = expanded.has(g.key);
    return [{ kind: 'group', g, open }, ...(open ? g.members.map((inc): Item => ({ kind: 'incident', inc, nested: true })) : [])];
  }), [groups, expanded]);

  // selection survives only for incidents still in the list
  const visibleIds = useMemo(() => new Set(rows.map((r) => r.incident_id)), [rows]);
  const selection = useMemo(() => rows.filter((r) => checked.has(r.incident_id)), [rows, checked]);
  useEffect(() => {
    if ([...checked].some((id) => !visibleIds.has(id))) setChecked(new Set([...checked].filter((id) => visibleIds.has(id))));
  }, [visibleIds]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (ids: string[]) => setChecked((prev) => {
    const next = new Set(prev);
    const allOn = ids.every((id) => next.has(id));
    ids.forEach((id) => (allOn ? next.delete(id) : next.add(id)));
    return next;
  });
  const toggleGroup = (key: string) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const moveCursor = (it: Item) => {
    setCursor(itemKey(it));
    onSelect(it.kind === 'group' ? it.g.members[0].incident_id : it.inc.incident_id);
  };

  const bulk = async (targets: Incident[], to: IncidentStatus) => {
    const todo = targets.filter((i) => i.status !== to);
    if (!todo.length) return;
    setBusy(to); setBulkErr(null);
    try {
      await changeStatus(todo, to, { undo: true });
      setChecked((prev) => new Set([...prev].filter((id) => !todo.some((i) => i.incident_id === id))));
    } catch (e) {
      setBulkErr(`Could not change ${todo.length} incident(s): ${e instanceof Error ? e.message : String(e)}. Nothing was changed.`);
    } finally {
      setBusy(null);
    }
  };

  // keyboard (queue view): j/k or arrows move, Enter opens (or expands a group), x/Space selects, Esc clears the selection
  const kb = useRef({ items, cursor, checked });
  kb.current = { items, cursor, checked };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      const { items: its, cursor: cur, checked: chk } = kb.current;
      const i = its.findIndex((it) => itemKey(it) === cur);
      const here = i >= 0 ? its[i] : null;
      if (e.key === 'j' || e.key === 'k' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!its.length) return;
        e.preventDefault();
        const d = e.key === 'j' || e.key === 'ArrowDown' ? 1 : -1;
        moveCursor(its[Math.min(its.length - 1, Math.max(0, i === -1 ? 0 : i + d))]);
      } else if (e.key === 'Enter' && here && (t === document.body || t?.closest('[data-queue]'))) {
        e.preventDefault();
        if (here.kind === 'group') toggleGroup(here.g.key); else onOpen(here.inc.incident_id);
      } else if ((e.key === 'x' || (e.key === ' ' && t === document.body)) && here) {
        e.preventDefault();
        toggle(here.kind === 'group' ? here.g.members.map((m) => m.incident_id) : [here.inc.incident_id]);
      } else if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && here?.kind === 'group') {
        if ((e.key === 'ArrowRight') !== here.open) toggleGroup(here.g.key);
      } else if (e.key === 'Escape' && chk.size) {
        setChecked(new Set());
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  // keep the cursor row visible
  useEffect(() => {
    if (!cursor || !listRef.current) return;
    listRef.current.querySelector<HTMLElement>(`[data-key="${cursor.replace(/"/g, '')}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [cursor]);
  // follow a selection made elsewhere (floor plan, detail navigation)
  useEffect(() => {
    if (selectedId && cursor !== `i:${selectedId}` && items.some((it) => it.kind === 'incident' && it.inc.incident_id === selectedId)) setCursor(`i:${selectedId}`);
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  const zoneLabel = filter.zone ? zoneName(state.zones, filter.zone) : null;

  return (
    <section className="flex flex-col min-h-0 h-full" aria-label="Incident queue" data-queue>
      <SectionHead
        title="Incidents"
        right={<span className="tnum"><b className="text-fg">{active}</b> active · {state.incidentsTotal} total</span>}
      />
      <div className="flex flex-wrap items-center gap-1 px-2 py-1.5 border-b border-ink-700 bg-ink-900 text-2xs">
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
        <button onClick={() => setFilter({ ...filter, grouped: !filter.grouped })} aria-pressed={filter.grouped}
                title="Show repeats (same rule, room and source) as one row"
                className={`px-2 h-6 border rounded-sm font-semibold ${filter.grouped ? 'border-ink-500 bg-ink-600 text-fg' : 'border-ink-600 text-fg-muted hover:text-fg'}`}>
          Group repeats
        </button>
        {zoneLabel && (
          <span className="flex items-center gap-1 h-6 pl-2 pr-0.5 border border-sev-high/60 text-fg rounded-sm" data-testid="zone-filter">
            Room: {zoneLabel}
            <button className="w-5 h-5 text-fg-muted hover:text-fg" aria-label="Clear room filter" onClick={() => setFilter({ ...filter, zone: '' })}>×</button>
          </span>
        )}
      </div>

      {selection.length > 0 && (
        <div className="shrink-0 flex flex-wrap items-center gap-1.5 px-2 py-1.5 border-b border-st-inv/50 bg-st-inv/10 text-2xs" data-testid="bulk-bar">
          <span className="font-semibold text-fg tnum">{selection.length} selected</span>
          {BULK.map((b) => (
            <Button key={b.to} tone={b.tone ?? 'default'} busy={busy === b.to} disabled={busy !== null}
                    onClick={() => bulk(selection, b.to)} data-testid={`bulk-${b.to}`}>{b.label}</Button>
          ))}
          <Button tone="ghost" onClick={() => setChecked(new Set())}>Clear <kbd className="text-fg-faint">Esc</kbd></Button>
        </div>
      )}
      {bulkErr && <p role="alert" className="px-2 py-1 text-2xs text-sev-critical border-b border-ink-700" data-testid="bulk-error">{bulkErr}</p>}

      {rows.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-1 text-fg-muted p-6 text-center">
          <span className="label">{filter.scope === 'active' ? 'No active incidents' : 'No incidents'}{zoneLabel ? ` in ${zoneLabel}` : ''}</span>
          <span className="text-2xs text-fg-faint">New incidents appear here as the rules raise them.</span>
        </div>
      ) : (
        <ul ref={listRef} role="listbox" aria-label="Incidents" aria-multiselectable className="flex-1 min-h-0 overflow-y-auto">
          {items.map((it) => {
            const key = itemKey(it);
            const isCursor = key === cursor;
            if (it.kind === 'group') {
              const { g } = it;
              const head = g.members[0];
              const ids = g.members.map((m) => m.incident_id);
              const nChecked = ids.filter((id) => checked.has(id)).length;
              const nInv = g.members.filter((m) => m.status === 'INVESTIGATING').length;
              const worst = g.members.reduce((w, m) => (SEVERITY_RANK[m.severity] > SEVERITY_RANK[w] ? m.severity : w), head.severity);
              const newest = Math.max(...g.members.map((m) => parseTs(m.start_time)));
              const oldest = Math.min(...g.members.map((m) => parseTs(m.start_time)));
              const isActive = ACTIVE.includes(head.status);
              const cams = [...new Set(g.members.flatMap((m) => incidentCameras(m, state.site)))];
              const fresh = g.members.filter((m) => { const t = parseTs(m.start_time); return t > mountedAt.current - 1000 && now - t < NEW_MS; }).length;
              return (
                <li key={key} data-key={key} role="option" aria-selected={isCursor} aria-expanded={it.open}
                    className={`group/row flex border-b border-ink-800 ${isCursor ? 'bg-ink-700' : 'hover:bg-ink-850'} ${isActive ? '' : 'opacity-60'}`}>
                  <span aria-hidden className={`w-1 shrink-0 ${SEV_BAR[worst]}`} />
                  <Check checked={nChecked === ids.length} mixed={nChecked > 0 && nChecked < ids.length}
                         label={`Select all ${ids.length} in this group`} onChange={() => toggle(ids)} />
                  <button className="flex-1 min-w-0 text-left pl-1 pr-2.5 py-1.5" data-testid={`group-${head.incident_id}`}
                          onClick={() => { moveCursor(it); toggleGroup(g.key); }}>
                    <span className="flex items-baseline gap-2">
                      <span aria-hidden className="text-fg-muted w-3">{it.open ? '▾' : '▸'}</span>
                      <span className="font-semibold text-fg truncate">{typeLabel(head.incident_type)}</span>
                      <span className="text-fg-muted truncate">{zoneName(state.zones, head.zone_id)}</span>
                      <span className="px-1 rounded-sm bg-ink-600 text-fg font-bold tnum text-2xs" title={`${ids.length} repeats`}>×{ids.length}</span>
                      {isSimulated(head) && <span className="text-2xs font-semibold text-sev-medium">SIM</span>}
                      {fresh > 0 && <span className="text-2xs font-bold text-sev-high" title="raised in the last 30 s">+{fresh} NEW</span>}
                      <span className="ml-auto text-fg-muted tnum shrink-0" title="newest">{clock(newest)}</span>
                    </span>
                    <span className="flex items-center gap-2 mt-0.5 pl-5 text-2xs text-fg-muted">
                      <span className="sr-only">{worst} severity, </span>
                      <span className="tnum">{isActive ? `${ids.length - nInv} open${nInv ? ` · ${nInv} investigating` : ''}` : `${ids.length} closed`}</span>
                      {cams.length > 0 && <span className="font-mono shrink-0">{cams.join(' ')}</span>}
                      <span className="ml-auto tnum shrink-0">over {age(oldest, newest)} · last {age(newest, now)} ago</span>
                    </span>
                  </button>
                  {isActive && (
                    <span className={`flex items-center gap-1 pr-2 ${isCursor ? 'flex' : 'hidden group-hover/row:flex'}`}>
                      <Button tone="ghost" className="!h-6 !px-1.5" busy={busy === 'RESOLVED'} disabled={busy !== null}
                              onClick={() => bulk(g.members, 'RESOLVED')} title={`Resolve all ${ids.length}`}
                              data-testid={`group-resolve-${head.incident_id}`}>Resolve all</Button>
                      <Button tone="ghost" className="!h-6 !px-1.5" busy={busy === 'FALSE_POSITIVE'} disabled={busy !== null}
                              onClick={() => bulk(g.members, 'FALSE_POSITIVE')} title={`Mark all ${ids.length} as false positive`}>False +</Button>
                    </span>
                  )}
                </li>
              );
            }
            const i = it.inc;
            const t = parseTs(i.start_time);
            const isNew = t > mountedAt.current - 1000 && now - t < NEW_MS;
            const cams = incidentCameras(i, state.site);
            return (
              <li key={key} data-key={key} data-id={i.incident_id} role="option" aria-selected={isCursor}
                  className={`flex border-b border-ink-800 ${isCursor ? 'bg-ink-700' : it.nested ? 'bg-ink-900 hover:bg-ink-850' : 'hover:bg-ink-850'} ${ACTIVE.includes(i.status) ? '' : 'opacity-60'}`}>
                <span aria-hidden className={`w-1 shrink-0 ${SEV_BAR[i.severity]}`} />
                {it.nested && <span aria-hidden className="w-3 shrink-0 border-r border-ink-700" />}
                <Check checked={checked.has(i.incident_id)} label={`Select ${typeLabel(i.incident_type)} ${clock(i.start_time)}`}
                       onChange={() => toggle([i.incident_id])} />
                <button onClick={() => { moveCursor(it); onOpen(i.incident_id); }} data-testid={`incident-${i.incident_id}`}
                        className="flex-1 min-w-0 text-left pl-1 pr-2.5 py-1.5">
                  <span className="flex items-baseline gap-2">
                    <span className="font-semibold text-fg truncate">{typeLabel(i.incident_type)}</span>
                    <span className="text-fg-muted truncate">{zoneName(state.zones, i.zone_id)}</span>
                    {isSimulated(i) && <span className="text-2xs font-semibold text-sev-medium">SIM</span>}
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
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <p className="shrink-0 px-2 h-6 flex items-center gap-3 border-t border-ink-700 bg-ink-900 text-2xs text-fg-faint" aria-hidden>
        <span><kbd>j</kbd>/<kbd>k</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>x</kbd> select</span>
        <span><kbd>→</kbd>/<kbd>←</kbd> expand</span><span><kbd>u</kbd> undo</span><span><kbd>h</kbd> heatmap</span>
      </p>
    </section>
  );
}

function Check({ checked, mixed = false, label, onChange }: { checked: boolean; mixed?: boolean; label: string; onChange: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = mixed; }, [mixed]);
  return (
    <label className="flex items-start pt-2 pl-2 pr-1 cursor-pointer" onClick={(e) => e.stopPropagation()}>
      <input ref={ref} type="checkbox" checked={checked} onChange={onChange} aria-label={label}
             className="w-3.5 h-3.5 accent-st-inv cursor-pointer" />
    </label>
  );
}
