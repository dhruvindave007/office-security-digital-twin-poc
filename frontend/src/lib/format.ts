// Copyright (c) 2026 Dhruvin Dave
// SPDX-License-Identifier: AGPL-3.0-only
import type { Incident, IncidentStatus, Severity, Site, Zone } from '../api/types';

export const SEVERITY_RANK: Record<Severity, number> = { CRITICAL: 3, HIGH: 2, MEDIUM: 1, LOW: 0 };
export const ACTIVE: IncidentStatus[] = ['OPEN', 'INVESTIGATING'];

const TYPE_LABEL: Record<string, string> = {
  UNAUTHORIZED_ENTRY: 'Unauthorized entry',
  TAILGATING: 'Tailgating',
  ACCESS_WITHOUT_BADGE: 'Entry without badge',
  BADGE_WITHOUT_ENTRY: 'Badge without entry',
  ZONE_OVER_CAPACITY: 'Over capacity',
  DWELL_ANOMALY: 'Dwell anomaly',
  AFTER_HOURS_BREACH: 'After-hours presence',
  IMPOSSIBLE_TRANSIT: 'Impossible transit',
  TRACK_DISCONTINUITY: 'Track discontinuity',
};
export const typeLabel = (t: string) => TYPE_LABEL[t] ?? t.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

export const STATUS_LABEL: Record<IncidentStatus, string> = {
  OPEN: 'Open', INVESTIGATING: 'Investigating', RESOLVED: 'Resolved', FALSE_POSITIVE: 'False positive',
};

export const parseTs = (iso: string) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();

const pad = (n: number) => String(n).padStart(2, '0');
/** Local wall-clock time, 24 h, with seconds. */
export function clock(iso: string | number): string {
  const d = new Date(typeof iso === 'number' ? iso : parseTs(iso));
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
export function dateClock(iso: string): string {
  const d = new Date(parseTs(iso));
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${clock(iso)}`;
}
/** "4 s", "3 min", "2 h" */
export function age(fromMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - fromMs) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

export const shortId = (id: string) => id.slice(0, 8);

export function zoneName(zones: Zone[], zoneId: string | null): string {
  if (!zoneId) return '—';
  const z = zones.find((x) => x.zone_id === zoneId);
  return z ? z.name.replace(/\s*\(.*\)$/, '') : zoneId;
}

/** Camera ID encoded in a live track ID ("live:cam_3:12" -> "cam_3"). */
export function cameraOfTrack(trackId: string | null): string | null {
  if (!trackId) return null;
  const m = /^live:([^:]+):/.exec(trackId);
  return m ? m[1] : null;
}

/** Cameras relevant to an incident: the track's own camera, else the cameras covering its room. */
export function incidentCameras(inc: Incident, site: Site | null): string[] {
  const own = cameraOfTrack(inc.track_id);
  if (own) return [own];
  if (!site || !inc.zone_id) return [];
  return site.cameras.filter((c) => c.zone_id === inc.zone_id).map((c) => c.camera_id);
}

export function sortIncidents(list: Incident[]): Incident[] {
  const activeFirst = (i: Incident) => (ACTIVE.includes(i.status) ? 0 : 1);
  return [...list].sort((a, b) =>
    activeFirst(a) - activeFirst(b)
    || SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]
    || parseTs(b.start_time) - parseTs(a.start_time));
}

export const isSimulated = (i: Incident) => !!i.track_id?.startsWith('sim:');

/**
 * Incidents that are repeats of one another for an operator: same rule, same room, same source
 * (live cameras vs simulator) and both still active or both closed. The WiseNET lab raises dozens
 * of "Unauthorized entry" incidents in minutes; grouping lets one decision cover the burst.
 */
export const groupKey = (i: Incident) =>
  `${i.incident_type}|${i.zone_id ?? '-'}|${isSimulated(i) ? 'sim' : 'live'}|${ACTIVE.includes(i.status) ? 'active' : 'closed'}`;
