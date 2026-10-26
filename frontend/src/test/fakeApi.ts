// Routed fetch for tests only. Responses follow the backend's real response shapes.
import { vi } from 'vitest';
import type { Incident, InvestigationList, LiveStatus, Site, TwinState, Zone } from '../api/types';

export const NOW_ISO = () => new Date().toISOString();

export const site: Site = {
  name: 'WiseNET · floor R+3',
  live_config: 'live.yaml',
  notes: [],
  cameras: [
    { camera_id: 'cam_2', name: 'Corridor east', zone_id: 'zone_corridor', positioning: 'metric', in_live_config: true, feeds_twin: true },
    { camera_id: 'cam_3', name: 'Vision lab', zone_id: 'zone_vision_lab', positioning: 'room', in_live_config: true, feeds_twin: true },
  ],
  doors: [{ door_id: 'door_d4', name: 'Door d4', zone_id: 'zone_vision_lab', camera_id: 'cam_3', badge_required: true, jambs_m: [[25.2, 5.8], [26.7, 5.8]] }],
};

const rect = (x0: number, y0: number, x1: number, y1: number) =>
  [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map(([x, y], i) => ({ vertex_order: i, x, y }));
export const zones: Zone[] = [
  { zone_id: 'zone_corridor', name: 'Corridor R+3', zone_type: 'corridor', security_level: 1, max_occupancy: 20, current_occupancy: 0, min_x: 4, min_y: 4, max_x: 30, max_y: 5.6, vertices: rect(4, 4, 30, 5.6) },
  { zone_id: 'zone_vision_lab', name: 'Vision lab', zone_type: 'restricted', security_level: 4, max_occupancy: 6, current_occupancy: 0, min_x: 25, min_y: 5.8, max_x: 31, max_y: 13, vertices: rect(25, 5.8, 31, 13) },
];

export const twin = (occ: Record<string, number> = { zone_corridor: 1, zone_vision_lab: 0 }): TwinState => ({
  timestamp: NOW_ISO(), total_active_tracks: Object.values(occ).reduce((a, b) => a + b, 0), zone_occupancy: occ,
  active_tracks: [],
});

export const incident = (over: Partial<Incident> = {}): Incident => ({
  incident_id: '01a10000-0000-7000-8000-000000000001', incident_type: 'TAILGATING', severity: 'HIGH', status: 'OPEN',
  zone_id: 'zone_vision_lab', track_id: 'live:cam_3:4', person_id: null, start_time: NOW_ISO(), end_time: null,
  supporting_event_ids: ['01a10000-0000-7000-8000-0000000000e1'], summary: 'Track live:cam_3:4 entered zone_vision_lab 1.4 s after track live:cam_3:1.',
  ...over,
});

export const live = (state = 'live'): LiveStatus => ({
  running: true, config_path: 'live.yaml', started_at: NOW_ISO(), process_fps: 5,
  cameras: ['cam_2', 'cam_3'].map((id) => ({
    camera_id: id, state: 'running', feed_twin: true, detect: true, device: 'intel:gpu', target_fps: 5, processed_fps: 5,
    frames_processed: 100, missed_slots: 0, people_in_view: id === 'cam_2' ? 1 : 0, incidents_raised: 0,
    latency_ms: { end_to_end_ms: { p50: 80, p95: 110 } }, last_error: null,
    source: { state, url: `rtsp://***@cam/${id}`, frames_read: 500, source_fps: 25, reconnects: 0, size: [640, 480], last_error: null },
  })),
  totals: { cameras: 2, live: state === 'live' ? 2 : 0, people_in_view: 1, processed_fps: 10, missed_slots: 0 },
});

export const emptyInvestigations = (id: string): InvestigationList => ({ incident_id: id, investigations: [], spent_today_usd: 0.012, daily_budget_usd: 1 });

type Handler = (url: URL, init?: RequestInit) => unknown | Promise<unknown>;
export interface FakeServer { routes: Record<string, Handler>; calls: { method: string; path: string; body?: unknown }[] }

export class HttpError { constructor(public status: number, public detail: string) {} }

/** Install a fetch that routes "METHOD /path" (query ignored) to handlers. Unrouted calls fail the test. */
export function installApi(overrides: Record<string, Handler> = {}, incidents: Incident[] = [incident()]): FakeServer {
  const server: FakeServer = {
    calls: [],
    routes: {
      'GET /api/auth/status': () => ({ auth_required: false, authenticated: true }),
      'GET /api/site': () => site,
      'GET /api/twin/zones': () => zones,
      'GET /api/twin/state': () => twin(),
      'GET /api/twin/incidents': () => ({ total: incidents.length, incidents }),
      'GET /api/live/status': () => live(),
      'GET /api/twin/investigations/auto': () => ({ enabled: true, cooldown_seconds: 600, max_per_hour: 12, paused_seconds_left: 0, counts: {}, recent: [] }),
      'GET /api/live/cameras/{id}/frame.jpg': () => frame(200),
      'GET /api/simulator/scenarios': () => [{ name: 'lab_unauthorized_visitor', description: 'SIMULATED', step_count: 3 }],
      ...overrides,
    },
  };
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, 'http://dash.test');
    const method = (init?.method ?? 'GET').toUpperCase();
    server.calls.push({ method, path: url.pathname + url.search, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    let key = `${method} ${url.pathname}`;
    if (!(key in server.routes)) key = Object.keys(server.routes).find((k) => new RegExp(`^${k.replace(/\{[^/]+\}/g, '[^/]+')}$`).test(`${method} ${url.pathname}`)) ?? key;
    const h = server.routes[key];
    if (!h) return new Response(JSON.stringify({ detail: `unrouted ${key}` }), { status: 404 });
    try {
      const body = await h(url, init);
      if (body instanceof Response) return body;
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    } catch (e) {
      if (e instanceof HttpError) return new Response(JSON.stringify({ detail: e.detail }), { status: e.status });
      throw e;
    }
  }));
  return server;
}

/** A JPEG response as the backend sends it, with the picture's age. */
export const frame = (ageMs: number) =>
  new Response(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: 'image/jpeg' }), {
    status: 200, headers: { 'Content-Type': 'image/jpeg', 'X-Frame-Age-Ms': String(ageMs) },
  });
