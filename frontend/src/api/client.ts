import type {
  AutoStatus, EvidenceManifest, Heatmap, Incident, IncidentStatus, InvestigationList, LiveStatus,
  Scenario, Site, TwinEvent, TwinState, Zone,
} from './types';

/** An HTTP failure with the server's own explanation (FastAPI puts it in `detail`). */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } });
  } catch {
    throw new ApiError(0, 'Backend unreachable');
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  site: () => request<Site>('/api/site'),
  zones: () => request<Zone[]>('/api/twin/zones'),
  state: () => request<TwinState>('/api/twin/state'),
  incidents: (limit = 500) => request<{ total: number; incidents: Incident[] }>(`/api/twin/incidents?limit=${limit}`),
  incident: (id: string) => request<Incident>(`/api/twin/incidents/${encodeURIComponent(id)}`),
  setStatus: (id: string, status: IncidentStatus) =>
    request<Incident>(`/api/twin/incidents/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  events: (ids: string[]) =>
    ids.length === 0
      ? Promise.resolve({ total: 0, events: [] as TwinEvent[] })
      : request<{ total: number; events: TwinEvent[] }>(`/api/twin/events?event_ids=${ids.slice(0, 200).map(encodeURIComponent).join(',')}`),
  evidence: (id: string) => request<EvidenceManifest>(`/api/twin/incidents/${encodeURIComponent(id)}/evidence`),
  investigations: (id: string) => request<InvestigationList>(`/api/twin/incidents/${encodeURIComponent(id)}/investigations`),
  investigate: (id: string, question: string) =>
    request<unknown>(`/api/twin/incidents/${encodeURIComponent(id)}/investigate`, { method: 'POST', body: JSON.stringify({ question }) }),
  autoStatus: () => request<AutoStatus>('/api/twin/investigations/auto'),
  live: () => request<LiveStatus>('/api/live/status'),
  scenarios: () => request<Scenario[]>('/api/simulator/scenarios'),
  runScenario: (name: string) =>
    request<{ incidents: string[]; errors: string[]; steps_processed: number }>(`/api/simulator/scenarios/${encodeURIComponent(name)}/run`, { method: 'POST' }),
  heatmap: (startIso: string, cell = 0.5) => request<Heatmap>(`/api/twin/heatmap?start_time=${encodeURIComponent(startIso)}&cell_size_m=${cell}`),
};

export const frameUrl = (cameraId: string, width = 640) => `/api/live/cameras/${encodeURIComponent(cameraId)}/frame.jpg?width=${width}`;
