// Copyright (c) 2026 Dhruvin Dave
// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Severity } from './api/types';
import { CameraWall } from './components/CameraWall';
import { FloorPlan } from './components/FloorPlan';
import { IncidentDetail } from './components/IncidentDetail';
import { DEFAULT_FILTER, IncidentQueue, filterIncidents, type QueueFilter } from './components/IncidentQueue';
import { AuthGate } from './components/SignIn';
import { TopBar } from './components/TopBar';
import { Button } from './components/ui';
import { ACTIVE, SEVERITY_RANK, incidentCameras } from './lib/format';
import { DashboardProvider, STALE_MS, useDash, useNow } from './state/dashboard';
import { OpsProvider } from './state/ops';

const FILTER_KEY = 'ovt.queueFilter.v1';
const TITLE = 'Office Vision Twin';

/** The queue filter is a per-viewer convenience: remembered in this browser if storage works, never required. */
function loadFilter(): QueueFilter {
  try {
    const raw = window.localStorage.getItem(FILTER_KEY);
    return raw ? { ...DEFAULT_FILTER, ...JSON.parse(raw) } : DEFAULT_FILTER;
  } catch {
    return DEFAULT_FILTER;
  }
}
function saveFilter(f: QueueFilter) {
  try { window.localStorage.setItem(FILTER_KEY, JSON.stringify(f)); } catch { /* storage blocked: keep in memory */ }
}

function ConnectionBanner() {
  const { state } = useDash();
  const now = useNow(1000);
  if (state.conn === 'open' && state.lastMessageAt !== null && now - state.lastMessageAt <= STALE_MS) return null;
  if (state.conn === 'connecting' && state.lastMessageAt === null) return null;   // first connect: no alarm yet
  const text = state.conn === 'open'
    ? 'Connected, but no update for over 5 s: the picture on screen may be out of date.'
    : 'Live updates interrupted. Reconnecting… Incidents raised meanwhile are fetched when the connection returns.';
  return (
    <div role="status" data-testid="conn-banner" className="shrink-0 px-3 py-1 text-xs bg-sig-warn/15 text-sig-warn border-b border-sig-warn/40">
      {text}
    </div>
  );
}

function Dashboard() {
  const { state, retryBoot } = useDash();
  const [filter, setFilterState] = useState<QueueFilter>(loadFilter);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [focusCamera, setFocusCamera] = useState<string | null>(null);

  const setFilter = useCallback((f: QueueFilter) => { setFilterState(f); saveFilter(f); }, []);
  const rows = useMemo(() => filterIncidents(Object.values(state.incidents), filter), [state.incidents, filter]);
  const selected = selectedId ? state.incidents[selectedId] ?? null : null;
  const highlight = selected ? incidentCameras(selected, state.site) : [];

  // browser tab shows how many serious incidents are waiting, so a backgrounded tab still tells you
  const urgent = useMemo(() => Object.values(state.incidents)
    .filter((i) => ACTIVE.includes(i.status) && SEVERITY_RANK[i.severity] >= SEVERITY_RANK.HIGH).length, [state.incidents]);
  useEffect(() => { document.title = urgent ? `(${urgent}) ${TITLE}` : TITLE; }, [urgent]);

  const step = useCallback((d: 1 | -1) => {
    if (!rows.length) return;
    const i = rows.findIndex((r) => r.incident_id === selectedId);
    setSelectedId(rows[Math.min(rows.length - 1, Math.max(0, i === -1 ? 0 : i + d))].incident_id);
  }, [rows, selectedId]);

  // After closing incidents, show the next active one in the queue (below first, then above). Planned
  // before the change is sent, from the list the operator is looking at; applied once the server agreed.
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const planAdvance = useCallback((done: string[]) => {
    const list = rowsRef.current;
    const gone = new Set(done);
    const i = list.findIndex((r) => r.incident_id === done[0]);
    const open = (r: (typeof list)[number]) => !gone.has(r.incident_id) && ACTIVE.includes(r.status);
    const next = i === -1 ? list.find(open) : list.slice(i + 1).find(open) ?? list.slice(0, i).reverse().find(open);
    // nothing left: stay on the closed incident, showing its new status
    return () => { if (next) setSelectedId(next.incident_id); };
  }, []);

  // keyboard (app level): Esc closes the enlarged camera / the detail; j/k step through incidents while the detail is open
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
      if (e.key === 'Escape') {
        if (typing) (t as HTMLElement).blur();
        if (focusCamera) setFocusCamera(null); else if (detailOpen) setDetailOpen(false);
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey || !detailOpen) return;
      if (e.key === 'j' || e.key === 'k') { e.preventDefault(); step(e.key === 'j' ? 1 : -1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [detailOpen, focusCamera, step]);

  const open = useCallback((id: string) => { setSelectedId(id); setDetailOpen(true); }, []);
  const filterZone = useCallback((zoneId: string) => {
    setFilter({ ...filter, zone: filter.zone === zoneId ? '' : zoneId });
    setDetailOpen(false);
  }, [filter, setFilter]);
  const filterSeverity = useCallback((s: Severity) => {
    const same = filter.minSeverity === s && filter.scope === 'active';
    setFilter({ ...filter, scope: 'active', minSeverity: same ? 'LOW' : s });
    setDetailOpen(false);
  }, [filter, setFilter]);

  if (state.boot === 'loading') {
    return <div className="h-full flex items-center justify-center text-fg-muted" data-testid="boot-loading">Loading site, cameras and incidents…</div>;
  }
  if (state.boot === 'error') {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3" data-testid="boot-error">
        <p className="label">Backend not reachable</p>
        <p className="text-fg-muted max-w-md text-center">{state.bootError}</p>
        <p className="text-2xs text-fg-faint">Start it with: backend\.venv\Scripts\python.exe scripts\demo.py</p>
        <Button onClick={retryBoot}>Retry</Button>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <TopBar onSeverity={filterSeverity} minSeverity={filter.scope === 'active' ? filter.minSeverity : null} />
      <ConnectionBanner />
      <main className="flex-1 min-h-0 flex flex-col lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(400px,34vw)]">
        <div className="order-2 lg:order-1 min-h-[60vh] lg:min-h-0 flex flex-col">
          <div className="flex-[3] min-h-0 flex flex-col bg-ink-950" aria-label="Camera wall">
            <CameraWall highlight={highlight} focusCamera={focusCamera} setFocusCamera={setFocusCamera} />
          </div>
          <div className="flex-[2] min-h-[200px]">
            <FloorPlan selected={selected} zoneFilter={filter.zone} onZone={filterZone} />
          </div>
        </div>
        <aside className="order-1 lg:order-2 h-[55vh] lg:h-auto min-h-0 border-l border-ink-700 bg-ink-850 flex flex-col">
          {detailOpen && selected ? (
            <IncidentDetail
              incident={selected}
              onClose={() => setDetailOpen(false)}
              position={{ index: rows.findIndex((r) => r.incident_id === selected.incident_id), total: rows.length }}
              onStep={step}
              planAdvance={planAdvance}
              onShowCamera={setFocusCamera}
            />
          ) : (
            <IncidentQueue rows={rows} filter={filter} setFilter={setFilter} selectedId={selectedId}
                           onSelect={setSelectedId} onOpen={open} />
          )}
        </aside>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthGate>
      <DashboardProvider>
        <OpsProvider>
          <Dashboard />
        </OpsProvider>
      </DashboardProvider>
    </AuthGate>
  );
}
