import { useEffect, useState } from 'react';
import { frameUrl } from '../api/client';
import type { LiveCamera, SiteCamera } from '../api/types';
import { useDash, useNow } from '../state/dashboard';
import { age } from '../lib/format';
import { Dot } from './ui';

const FRAME_INTERVAL_MS = 250;   // <= 4 pictures/s per tile; each is a short request (not a held stream)
const NO_PICTURE_MS = 5000;

/**
 * Pulls the camera's latest annotated picture (GET /api/live/cameras/{id}/frame.jpg) one after another.
 * Not MJPEG: browsers allow ~6 connections per host and the push channel already holds one, so six
 * endless MJPEG streams would starve the API. Fetched (not <img src>) so the X-Frame-Age-Ms header can
 * be read: the server keeps serving the last picture after a stream stops, and only the picture's own
 * capture time tells a live picture from a frozen one.
 */
export function useSnapshot(cameraId: string, enabled: boolean, width: number) {
  const [src, setSrc] = useState<string | null>(null);
  const [takenAt, setTakenAt] = useState<number | null>(null);     // capture time, browser clock
  const [failing, setFailing] = useState(false);

  useEffect(() => {
    if (!enabled) { setSrc(null); setTakenAt(null); return; }
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let current: string | null = null;
    const ctrl = new AbortController();
    const schedule = (delay: number) => { timer = setTimeout(tick, delay); };
    const tick = async () => {
      if (!alive) return;
      if (typeof document !== 'undefined' && document.hidden) { schedule(1000); return; }   // tab hidden: idle
      const started = Date.now();
      try {
        const res = await fetch(`${frameUrl(cameraId, width)}&t=${started}`, { signal: ctrl.signal, cache: 'no-store' });
        if (!res.ok) throw new Error(String(res.status));
        const ageMs = Number(res.headers.get('X-Frame-Age-Ms'));
        const blob = await res.blob();
        if (!alive) return;
        const url = URL.createObjectURL(blob);
        if (current) URL.revokeObjectURL(current);
        current = url;
        setSrc(url);
        setTakenAt(Number.isFinite(ageMs) ? Date.now() - ageMs : Date.now());
        setFailing(false);
        schedule(Math.max(0, FRAME_INTERVAL_MS - (Date.now() - started)));
      } catch {
        if (!alive) return;
        setFailing(true);
        schedule(2000);
      }
    };
    schedule(0);
    return () => {
      alive = false;
      ctrl.abort();
      if (timer) clearTimeout(timer);
      if (current) URL.revokeObjectURL(current);
    };
  }, [cameraId, enabled, width]);

  return { src, takenAt, failing };
}

function streamTone(cam: LiveCamera | undefined): { tone: 'live' | 'warn' | 'down' | 'idle'; text: string } {
  if (!cam) return { tone: 'idle', text: 'NOT RUNNING' };
  if (cam.state === 'error') return { tone: 'down', text: 'ERROR' };
  if (cam.state === 'loading_model') return { tone: 'warn', text: 'STARTING' };
  const s = cam.source.state;
  if (s === 'live') return { tone: 'live', text: 'LIVE' };
  if (s === 'reconnecting' || s === 'connecting') return { tone: 'warn', text: s.toUpperCase() };
  return { tone: 'down', text: s.toUpperCase() };
}

export function CameraTile({ cam, live, highlighted, onExpand, expanded = false }: {
  cam: SiteCamera; live: LiveCamera | undefined; highlighted: boolean; onExpand: () => void; expanded?: boolean;
}) {
  const now = useNow(1000);
  const running = !!live && live.state !== 'stopped';
  const snap = useSnapshot(cam.camera_id, running, expanded ? 1280 : 640);
  const st = streamTone(live);
  const pictureAge = snap.takenAt ? now - snap.takenAt : null;
  const noPicture = running && (pictureAge === null || pictureAge > NO_PICTURE_MS);
  const lat = live?.latency_ms?.end_to_end_ms?.p50;
  const role = cam.feeds_twin ? 'feeds twin' : 'view only';
  const positioning = cam.positioning === 'metric' ? 'calibrated floor position' : cam.positioning === 'room' ? 'room-level' : 'not calibrated';

  return (
    <figure
      data-testid={`camera-${cam.camera_id}`}
      data-picture={!running ? 'off' : noPicture ? 'none' : 'fresh'}
      className={`relative bg-black border ${highlighted ? 'border-sev-high' : 'border-ink-700'} min-h-0 overflow-hidden group`}
    >
      {snap.src && (
        <img
          src={snap.src}
          alt={`${cam.camera_id} live picture`}
          className={`w-full h-full object-contain ${noPicture ? 'opacity-30' : ''}`}
          draggable={false}
        />
      )}
      {(!running || noPicture) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-center px-3">
          <span className="label">{!running ? 'Camera not running' : pictureAge !== null ? 'Picture frozen' : snap.failing ? 'No picture' : 'Waiting for picture'}</span>
          {running && pictureAge !== null && <span className="text-2xs text-fg-muted tnum">last frame captured {age(snap.takenAt!, now)} ago</span>}
          {live?.source.last_error && <span className="text-2xs text-sev-medium max-w-full truncate" title={live.source.last_error}>{live.source.last_error}</span>}
        </div>
      )}
      {/* header strip */}
      <figcaption className="absolute top-0 inset-x-0 flex items-center gap-2 px-2 h-6 bg-black/70 text-2xs">
        <span className="font-mono font-semibold text-fg">{cam.camera_id}</span>
        <span className="truncate text-fg-muted" title={cam.name}>{cam.name}</span>
        <span className="ml-auto flex items-center gap-1 font-semibold tracking-wider" title={live?.source.url}>
          <Dot tone={st.tone} /> <span className={st.tone === 'live' ? 'text-sig-live' : st.tone === 'idle' ? 'text-fg-faint' : st.tone === 'warn' ? 'text-sig-warn' : 'text-sig-down'}>{st.text}</span>
        </span>
      </figcaption>
      {/* footer strip */}
      {live && (
        <div className="absolute bottom-0 inset-x-0 flex items-center gap-3 px-2 h-5 bg-black/70 text-2xs text-fg-muted tnum">
          <span title="people detected in this camera's picture"><b className="text-fg">{live.people_in_view}</b> in view</span>
          <span>{live.processed_fps.toFixed(1)} fps</span>
          {lat != null && <span title="camera frame read -> twin updated (median)">{Math.round(lat)} ms</span>}
          <span className="ml-auto" title={positioning}>{role}</span>
        </div>
      )}
      <button
        onClick={onExpand}
        className="absolute inset-0 w-full h-full opacity-0 focus-visible:opacity-100"
        aria-label={expanded ? `Close enlarged ${cam.camera_id}` : `Enlarge ${cam.camera_id}`}
      />
    </figure>
  );
}

export function CameraWall({ highlight, focusCamera, setFocusCamera }: {
  highlight: string[]; focusCamera: string | null; setFocusCamera: (c: string | null) => void;
}) {
  const { state } = useDash();
  const cams = state.site?.cameras ?? [];
  const liveBy = Object.fromEntries((state.live?.cameras ?? []).map((c) => [c.camera_id, c]));
  if (cams.length === 0) {
    return <div className="flex-1 flex items-center justify-center text-fg-muted">No cameras configured for this site.</div>;
  }
  if (focusCamera) {
    const cam = cams.find((c) => c.camera_id === focusCamera);
    if (cam) {
      return (
        <div className="flex-1 min-h-0 grid p-1">
          <CameraTile cam={cam} live={liveBy[cam.camera_id]} highlighted={highlight.includes(cam.camera_id)} expanded onExpand={() => setFocusCamera(null)} />
        </div>
      );
    }
  }
  return (
    <div className="flex-1 min-h-0 grid grid-cols-2 xl:grid-cols-3 auto-rows-fr gap-1 p-1">
      {cams.map((c) => (
        <CameraTile key={c.camera_id} cam={c} live={liveBy[c.camera_id]} highlighted={highlight.includes(c.camera_id)} onExpand={() => setFocusCamera(c.camera_id)} />
      ))}
    </div>
  );
}
