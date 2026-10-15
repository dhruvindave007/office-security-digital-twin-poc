import React, { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef } from 'react';
import { api } from '../api/client';
import type { AutoStatus, Incident, LiveStatus, Scenario, Site, TwinState, Zone } from '../api/types';

export type Conn = 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface DashState {
  boot: 'loading' | 'ready' | 'error';
  bootError: string | null;
  site: Site | null;
  zones: Zone[];
  twin: TwinState | null;
  twinAt: number | null;          // when the last twin snapshot arrived (ms, browser clock)
  incidents: Record<string, Incident>;
  incidentsTotal: number;
  live: LiveStatus | null;
  liveAt: number | null;
  auto: AutoStatus | null;
  scenarios: Scenario[];
  conn: Conn;
  lastMessageAt: number | null;   // any message on the push channel
  resyncs: number;
}

type Action =
  | { type: 'BOOT_OK'; site: Site; zones: Zone[]; twin: TwinState; incidents: Incident[]; total: number; live: LiveStatus | null; auto: AutoStatus | null; scenarios: Scenario[]; at: number }
  | { type: 'BOOT_START' }
  | { type: 'BOOT_FAIL'; error: string }
  | { type: 'TWIN'; twin: TwinState; at: number }
  | { type: 'LIVE'; live: LiveStatus; at: number }
  | { type: 'INCIDENT'; incident: Incident; at: number }
  | { type: 'INCIDENTS'; incidents: Incident[]; total: number }
  | { type: 'AUTO'; auto: AutoStatus }
  | { type: 'CONN'; conn: Conn }
  | { type: 'RESYNCED' };

export const initialState: DashState = {
  boot: 'loading', bootError: null, site: null, zones: [], twin: null, twinAt: null,
  incidents: {}, incidentsTotal: 0, live: null, liveAt: null, auto: null, scenarios: [],
  conn: 'connecting', lastMessageAt: null, resyncs: 0,
};

const byId = (list: Incident[]) => Object.fromEntries(list.map((i) => [i.incident_id, i]));

export function reducer(s: DashState, a: Action): DashState {
  switch (a.type) {
    case 'BOOT_OK':
      return { ...s, boot: 'ready', bootError: null, site: a.site, zones: a.zones, twin: a.twin, twinAt: a.at,
        incidents: byId(a.incidents), incidentsTotal: a.total, live: a.live, liveAt: a.live ? a.at : null,
        auto: a.auto, scenarios: a.scenarios };
    case 'BOOT_START':
      return { ...s, boot: 'loading', bootError: null };
    case 'BOOT_FAIL':
      return { ...s, boot: 'error', bootError: a.error };
    case 'TWIN':
      return { ...s, twin: a.twin, twinAt: a.at, lastMessageAt: a.at };
    case 'LIVE':
      return { ...s, live: a.live, liveAt: a.at, lastMessageAt: a.at };
    case 'INCIDENT': {
      const known = a.incident.incident_id in s.incidents;
      return { ...s, incidents: { ...s.incidents, [a.incident.incident_id]: a.incident },
        incidentsTotal: s.incidentsTotal + (known ? 0 : 1), lastMessageAt: a.at };
    }
    case 'INCIDENTS':
      return { ...s, incidents: byId(a.incidents), incidentsTotal: a.total };
    case 'AUTO':
      return { ...s, auto: a.auto };
    case 'CONN':
      return { ...s, conn: a.conn };
    case 'RESYNCED':
      return { ...s, resyncs: s.resyncs + 1 };
    default:
      return s;
  }
}

interface Ctx {
  state: DashState;
  upsertIncident: (i: Incident) => void;
  resync: () => Promise<void>;
  retryBoot: () => void;
}

const DashContext = createContext<Ctx | null>(null);

export function useDash(): Ctx {
  const ctx = useContext(DashContext);
  if (!ctx) throw new Error('useDash outside DashboardProvider');
  return ctx;
}

const STREAM_URL = '/api/stream';
const RECREATE_MS = 3000;

export function DashboardProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [bootKey, setBootKey] = React.useState(0);

  // ---- initial load: everything the screen shows comes from the live API -------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [site, zones, twin, inc] = await Promise.all([api.site(), api.zones(), api.state(), api.incidents()]);
        // Optional parts: the screen still works if live cameras, auto-investigation or the simulator are absent.
        const [live, auto, scenarios] = await Promise.all([
          api.live().catch(() => null), api.autoStatus().catch(() => null), api.scenarios().catch(() => [] as Scenario[]),
        ]);
        if (!cancelled) dispatch({ type: 'BOOT_OK', site, zones, twin, incidents: inc.incidents, total: inc.total, live, auto, scenarios, at: Date.now() });
      } catch (e) {
        if (!cancelled) dispatch({ type: 'BOOT_FAIL', error: e instanceof Error ? e.message : String(e) });
      }
    })();
    return () => { cancelled = true; };
  }, [bootKey]);

  const resync = useCallback(async () => {
    const [inc, twin, live, auto] = await Promise.all([
      api.incidents(), api.state(), api.live().catch(() => null), api.autoStatus().catch(() => null),
    ]);
    const now = Date.now();
    dispatch({ type: 'INCIDENTS', incidents: inc.incidents, total: inc.total });
    dispatch({ type: 'TWIN', twin, at: now });
    if (live) dispatch({ type: 'LIVE', live, at: now });
    if (auto) dispatch({ type: 'AUTO', auto });
    dispatch({ type: 'RESYNCED' });
  }, []);

  // ---- push channel (Server-Sent Events, GET /api/stream) -----------------------------------------
  const resyncRef = useRef(resync);
  resyncRef.current = resync;
  useEffect(() => {
    if (state.boot !== 'ready') return;
    let es: EventSource | null = null;
    let hadError = false;
    let recreateTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const parse = <T,>(ev: MessageEvent): T | null => {
      try { return JSON.parse(ev.data) as T; } catch { return null; }
    };
    const onState = (ev: MessageEvent) => { const d = parse<TwinState>(ev); if (d) dispatch({ type: 'TWIN', twin: d, at: Date.now() }); };
    const onLive = (ev: MessageEvent) => { const d = parse<LiveStatus>(ev); if (d) dispatch({ type: 'LIVE', live: d, at: Date.now() }); };
    const onIncident = (ev: MessageEvent) => { const d = parse<Incident>(ev); if (d) dispatch({ type: 'INCIDENT', incident: d, at: Date.now() }); };
    const onResync = () => { resyncRef.current().catch(() => undefined); };

    const open = () => {
      if (disposed) return;
      es = new EventSource(STREAM_URL);
      dispatch({ type: 'CONN', conn: hadError ? 'reconnecting' : 'connecting' });
      es.addEventListener('state', onState as EventListener);
      es.addEventListener('live', onLive as EventListener);
      es.addEventListener('incident', onIncident as EventListener);
      es.addEventListener('resync', onResync as EventListener);
      es.onopen = () => {
        dispatch({ type: 'CONN', conn: 'open' });
        // Anything raised while we were disconnected was never pushed to us: refetch once.
        if (hadError) { hadError = false; resyncRef.current().catch(() => undefined); }
      };
      es.onerror = () => {
        hadError = true;
        if (!es) return;
        if (es.readyState === 2 /* CLOSED: the browser gave up */) {
          close();
          dispatch({ type: 'CONN', conn: 'reconnecting' });
          recreateTimer = setTimeout(open, RECREATE_MS);
        } else {
          dispatch({ type: 'CONN', conn: 'reconnecting' });   // the browser retries by itself
        }
      };
    };
    const close = () => {
      if (!es) return;
      es.removeEventListener('state', onState as EventListener);
      es.removeEventListener('live', onLive as EventListener);
      es.removeEventListener('incident', onIncident as EventListener);
      es.removeEventListener('resync', onResync as EventListener);
      es.onopen = null; es.onerror = null;
      es.close();
      es = null;
    };
    open();
    return () => {
      disposed = true;
      if (recreateTimer) clearTimeout(recreateTimer);
      close();
      dispatch({ type: 'CONN', conn: 'closed' });
    };
  }, [state.boot]);

  // auto-investigation status is not pushed; refresh it slowly (cheap, read-only)
  useEffect(() => {
    if (state.boot !== 'ready') return;
    const t = setInterval(() => { api.autoStatus().then((a) => dispatch({ type: 'AUTO', auto: a })).catch(() => undefined); }, 15000);
    return () => clearInterval(t);
  }, [state.boot]);

  const upsertIncident = useCallback((i: Incident) => dispatch({ type: 'INCIDENT', incident: i, at: Date.now() }), []);
  const retryBoot = useCallback(() => { dispatch({ type: 'BOOT_START' }); setBootKey((k) => k + 1); }, []);
  const value = useMemo(() => ({ state, upsertIncident, resync, retryBoot }), [state, upsertIncident, resync, retryBoot]);
  return <DashContext.Provider value={value}>{children}</DashContext.Provider>;
}

/** Re-render every `ms` (for ages and stale indicators). */
export function useNow(ms = 1000): number {
  const [now, setNow] = React.useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export const STALE_MS = 5000;
