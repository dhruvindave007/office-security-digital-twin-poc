import { useEffect, useMemo, useState } from 'react';
import { CameraWall } from './components/CameraWall';
import { FloorPlan } from './components/FloorPlan';
import { IncidentDetail } from './components/IncidentDetail';
import { DEFAULT_FILTER, IncidentQueue, filterIncidents, type QueueFilter } from './components/IncidentQueue';
import { TopBar } from './components/TopBar';
import { Button } from './components/ui';
import { incidentCameras } from './lib/format';
import { DashboardProvider, STALE_MS, useDash, useNow } from './state/dashboard';

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
  const [filter, setFilter] = useState<QueueFilter>(DEFAULT_FILTER);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [focusCamera, setFocusCamera] = useState<string | null>(null);

  const rows = useMemo(() => filterIncidents(Object.values(state.incidents), filter), [state.incidents, filter]);
  const selected = selectedId ? state.incidents[selectedId] ?? null : null;
  const highlight = selected ? incidentCameras(selected, state.site) : [];

  // keyboard: j/k or arrows move through the queue, Enter opens, Esc closes the detail / enlarged camera
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
      if (e.key === 'Escape') {
        if (typing) (t as HTMLElement).blur();
        if (focusCamera) setFocusCamera(null); else if (detailOpen) setDetailOpen(false);
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === 'j' || e.key === 'k' || (!detailOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp'))) {
        if (!rows.length) return;
        e.preventDefault();
        const i = rows.findIndex((r) => r.incident_id === selectedId);
        const d = e.key === 'j' || e.key === 'ArrowDown' ? 1 : -1;
        const next = rows[Math.min(rows.length - 1, Math.max(0, i === -1 ? 0 : i + d))];
        setSelectedId(next.incident_id);
      } else if (e.key === 'Enter' && selectedId && !detailOpen && (t === document.body || t?.closest('[role="listbox"]'))) {
        setDetailOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [rows, selectedId, detailOpen, focusCamera]);

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
      <TopBar />
      <ConnectionBanner />
      <main className="flex-1 min-h-0 flex flex-col lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(380px,32vw)]">
        <div className="order-2 lg:order-1 min-h-[60vh] lg:min-h-0 flex flex-col">
          <div className="flex-[3] min-h-0 flex flex-col bg-ink-950" aria-label="Camera wall">
            <CameraWall highlight={highlight} focusCamera={focusCamera} setFocusCamera={setFocusCamera} />
          </div>
          <div className="flex-[2] min-h-[200px]">
            <FloorPlan selected={selected} />
          </div>
        </div>
        <aside className="order-1 lg:order-2 h-[55vh] lg:h-auto min-h-0 border-l border-ink-700 bg-ink-850 flex flex-col">
          {detailOpen && selected ? (
            <IncidentDetail incident={selected} onClose={() => setDetailOpen(false)} />
          ) : (
            <IncidentQueue rows={rows} filter={filter} setFilter={setFilter} selectedId={selectedId}
                           onSelect={(id) => { setSelectedId(id); setDetailOpen(true); }} />
          )}
        </aside>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <DashboardProvider>
      <Dashboard />
    </DashboardProvider>
  );
}
