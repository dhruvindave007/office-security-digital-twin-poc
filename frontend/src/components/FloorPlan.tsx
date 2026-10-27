// Copyright (c) 2026 Dhruvin Dave
// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { Heatmap, Incident, Zone } from '../api/types';
import { useDash, useNow, STALE_MS } from '../state/dashboard';
import { ACTIVE, SEVERITY_RANK, age, cameraOfTrack } from '../lib/format';
import { SectionHead } from './ui';

const HEAT_WINDOW_MIN = 10;
const PAD = 0.6;     // metres around the building

/** Plan coordinates are metres with y pointing north; the SVG is drawn north-up (y flipped). */
function useFrame(zones: Zone[]) {
  return useMemo(() => {
    const xs = zones.flatMap((z) => z.vertices.map((v) => v.x));
    const ys = zones.flatMap((z) => z.vertices.map((v) => v.y));
    if (!xs.length) return null;
    const minX = Math.min(...xs) - PAD, maxX = Math.max(...xs) + PAD;
    const minY = Math.min(...ys) - PAD, maxY = Math.max(...ys) + PAD;
    return { minX, maxX, minY, maxY, w: maxX - minX, h: maxY - minY, X: (x: number) => x - minX, Y: (y: number) => maxY - y };
  }, [zones]);
}

function centroid(z: Zone): [number, number] {
  // area-weighted polygon centroid (falls back to the bbox centre)
  const v = z.vertices;
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < v.length; i++) {
    const p = v[i], q = v[(i + 1) % v.length];
    const f = p.x * q.y - q.x * p.y;
    a += f; cx += (p.x + q.x) * f; cy += (p.y + q.y) * f;
  }
  if (Math.abs(a) < 1e-9) return [(z.min_x + z.max_x) / 2, (z.min_y + z.max_y) / 2];
  return [cx / (3 * a), cy / (3 * a)];
}

export function FloorPlan({ selected, zoneFilter = '', onZone }: { selected: Incident | null; zoneFilter?: string; onZone?: (zoneId: string) => void }) {
  const { state } = useDash();
  const now = useNow(1000);
  const [heatOn, setHeatOn] = useState(false);
  const [heat, setHeat] = useState<Heatmap | null>(null);
  const [heatErr, setHeatErr] = useState<string | null>(null);
  const zones = useMemo(() => state.zones.filter((z) => z.vertices.length >= 3), [state.zones]);
  const F = useFrame(zones);

  // heatmap: fetched on demand, refreshed every 15 s while shown
  useEffect(() => {
    if (!heatOn) return;
    let stop = false;
    const load = () => api.heatmap(new Date(Date.now() - HEAT_WINDOW_MIN * 60000).toISOString(), 0.5)
      .then((h) => { if (!stop) { setHeat(h); setHeatErr(null); } })
      .catch((e) => { if (!stop) setHeatErr(e.message); });
    load();
    const t = setInterval(load, 15000);
    return () => { stop = true; clearInterval(t); };
  }, [heatOn]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
      if (e.key === 'h' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) setHeatOn((v) => !v);
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, []);

  const occ = state.twin?.zone_occupancy ?? {};
  const metricCams = new Set((state.site?.cameras ?? []).filter((c) => c.positioning === 'metric').map((c) => c.camera_id));
  const camsByZone: Record<string, string[]> = {};
  for (const c of state.site?.cameras ?? []) if (c.zone_id) (camsByZone[c.zone_id] ??= []).push(c.camera_id);
  const activeByZone: Record<string, Incident[]> = {};
  for (const i of Object.values(state.incidents)) if (i.zone_id && ACTIVE.includes(i.status)) (activeByZone[i.zone_id] ??= []).push(i);
  const twinAge = state.twinAt ? now - state.twinAt : null;
  const stale = twinAge === null || twinAge > STALE_MS;
  const heatMax = heat ? Math.max(1, ...heat.cells.flat()) : 1;

  return (
    <section className="flex flex-col min-h-0 h-full bg-ink-900 border-t border-ink-700" aria-label="Floor plan">
      <SectionHead
        title="Floor plan"
        right={<>
          <span className={`tnum ${stale ? 'text-sig-warn' : ''}`} title="time since the last twin update">
            {stale ? 'STALE · ' : ''}twin {twinAge === null ? '—' : `${age(state.twinAt!, now)} ago`}
          </span>
          <label className="flex items-center gap-1 cursor-pointer select-none">
            <input type="checkbox" checked={heatOn} onChange={(e) => setHeatOn(e.target.checked)} className="accent-sev-high" />
            heatmap ({HEAT_WINDOW_MIN} min) <kbd className="text-fg-faint">h</kbd>
          </label>
        </>}
      />
      {!F ? (
        <div className="flex-1 flex items-center justify-center text-fg-muted">No floor plan for this site.</div>
      ) : (
        <div className="flex-1 min-h-0 relative">
          <svg viewBox={`0 0 ${F.w} ${F.h}`} preserveAspectRatio="xMidYMid meet" className="w-full h-full" role="img"
               aria-label="Floor plan with rooms, doors, people and room counts">
            {heatOn && heat && heat.cells.map((row, r) => row.map((n, c) => n > 0 && (
              <rect key={`${r}-${c}`} x={F.X(c * heat.cell_size_m)} y={F.Y((r + 1) * heat.cell_size_m)}
                    width={heat.cell_size_m} height={heat.cell_size_m}
                    fill="#f2762e" fillOpacity={0.12 + 0.68 * Math.log1p(n) / Math.log1p(heatMax)} />
            )))}
            {zones.map((z) => {
              const n = occ[z.zone_id] ?? 0;
              const over = z.max_occupancy != null && n > z.max_occupancy;
              const sel = selected?.zone_id === z.zone_id;
              const filtered = zoneFilter === z.zone_id;
              const pts = z.vertices.map((v) => `${F.X(v.x)},${F.Y(v.y)}`).join(' ');
              const name = z.name.replace(/\s*\(.*\)$/, '');
              return (
                <polygon key={z.zone_id} points={pts} data-testid={`zone-${z.zone_id}`}
                         fill={over ? '#ff4d4f' : filtered ? '#4c9fe8' : '#d6dbe1'} fillOpacity={over ? 0.16 : filtered ? 0.12 : heatOn ? 0.0 : 0.035}
                         stroke={sel ? '#f2762e' : filtered ? '#4c9fe8' : z.security_level >= 3 ? '#8d96a2' : '#3b4552'}
                         strokeWidth={sel || filtered ? 0.16 : 0.07} strokeDasharray={z.security_level >= 3 && !sel && !filtered ? '0.35 0.2' : undefined}
                         className={onZone ? 'cursor-pointer hover:fill-opacity-[0.1]' : undefined}
                         {...(onZone ? {
                           role: 'button', tabIndex: 0, 'aria-pressed': filtered,
                           'aria-label': filtered ? `${name}: show incidents in every room` : `${name}: show only this room's incidents`,
                           onClick: () => onZone(z.zone_id),
                           onKeyDown: (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onZone(z.zone_id); } },
                         } : {})}>
                  {onZone && <title>{filtered ? 'Click: show every room' : 'Click: show only this room in the incident queue'}</title>}
                </polygon>
              );
            })}
            {(state.site?.doors ?? []).filter((d) => d.jambs_m && d.jambs_m.length === 2).map((d) => {
              const [[x1, y1], [x2, y2]] = d.jambs_m!;
              return (
                <g key={d.door_id} pointerEvents="none">
                  <line x1={F.X(x1)} y1={F.Y(y1)} x2={F.X(x2)} y2={F.Y(y2)}
                        stroke={d.badge_required ? '#d9a514' : '#5f6873'} strokeWidth={d.badge_required ? 0.3 : 0.18} strokeLinecap="butt" />
                  {d.badge_required && (
                    <text x={F.X((x1 + x2) / 2)} y={F.Y((y1 + y2) / 2) - 0.35} fontSize={0.5} fill="#d9a514" textAnchor="middle">
                      {d.door_id.replace('door_', '')} badge
                    </text>
                  )}
                </g>
              );
            })}
            {(state.twin?.active_tracks ?? []).filter((t) => t.position && metricCams.has(t.current_camera_id ?? cameraOfTrack(t.track_id) ?? '')).map((t) => (
              <circle key={t.track_id} cx={F.X(t.position![0])} cy={F.Y(t.position![1])} r={0.28} fill="#4c9fe8" stroke="#0a0c0f" strokeWidth={0.06}>
                <title>{`${t.track_id} (per-camera track label, not an identity) · ${t.current_zone_id ?? 'no room'}`}</title>
              </circle>
            ))}
            {zones.map((z) => {
              const [cx, cy] = centroid(z);
              const n = occ[z.zone_id] ?? 0;
              const act = activeByZone[z.zone_id] ?? [];
              const worst = act.reduce<string | null>((w, i) => (!w || SEVERITY_RANK[i.severity] > SEVERITY_RANK[w as keyof typeof SEVERITY_RANK] ? i.severity : w), null);
              const big = z.max_x - z.min_x > 4 && z.max_y - z.min_y > 2.5;
              const fs = big ? 0.62 : 0.42;
              return (
                <g key={`l-${z.zone_id}`} data-testid={`zone-label-${z.zone_id}`} pointerEvents="none">
                  <text x={F.X(cx)} y={F.Y(cy) - fs * 0.9} fontSize={fs * 0.82} fill="#8d96a2" textAnchor="middle">
                    {z.name.replace(/\s*\(.*\)$/, '').replace(/ R\+3$/, '')}
                  </text>
                  <text x={F.X(cx)} y={F.Y(cy) + fs * 0.35} fontSize={fs * 1.35} fontWeight={700} textAnchor="middle"
                        fill={z.max_occupancy != null && n > z.max_occupancy ? '#ff4d4f' : '#d6dbe1'}>
                    <tspan data-testid={`count-${z.zone_id}`}>{n}</tspan>
                    {z.max_occupancy != null && <tspan fontSize={fs * 0.75} fill="#5f6873" fontWeight={400}>{` / ${z.max_occupancy}`}</tspan>}
                  </text>
                  {big && camsByZone[z.zone_id] && (
                    <text x={F.X(cx)} y={F.Y(cy) + fs * 1.3} fontSize={fs * 0.62} fill="#5f6873" textAnchor="middle" fontFamily="Consolas, monospace">
                      {camsByZone[z.zone_id].join(' ')}
                    </text>
                  )}
                  {worst && (
                    <rect x={F.X(cx) + fs * 1.6} y={F.Y(cy) - fs * 0.6} width={fs * 0.55} height={fs * 0.55}
                          fill={{ CRITICAL: '#ff4d4f', HIGH: '#f2762e', MEDIUM: '#d9a514', LOW: '#7d8b99' }[worst]}>
                      <title>{`${act.length} active incident(s)`}</title>
                    </rect>
                  )}
                </g>
              );
            })}
          </svg>
          <div className="absolute bottom-1 left-2 right-2 flex flex-wrap gap-x-4 gap-y-0.5 text-2xs text-fg-faint pointer-events-none">
            <span><span className="inline-block w-2 h-2 rounded-full bg-st-inv mr-1 align-middle" />person · calibrated corridor cameras only</span>
            <span>room numbers = twin headcount estimate (undercounts dense groups)</span>
            <span>tracks are per camera; no cross-camera identity</span>
            {onZone && <span>click a room to filter incidents</span>}
            {heatOn && <span>heatmap: stored position samples; room-level cameras place everyone at the room centre{heatErr ? ` · error: ${heatErr}` : ''}</span>}
          </div>
        </div>
      )}
    </section>
  );
}
