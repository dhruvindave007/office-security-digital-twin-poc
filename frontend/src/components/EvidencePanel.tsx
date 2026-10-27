// Copyright (c) 2026 Dhruvin Dave
// SPDX-License-Identifier: AGPL-3.0-only
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { EvidenceClip, Incident } from '../api/types';
import { clock, parseTs, SEVERITY_RANK } from '../lib/format';
import { Button } from './ui';

const RECORDING_GRACE_MS = 45000;   // pre-roll is instant; the clip is written ~8 s after the incident (post-roll)
const POLL_MS = 4000;

type Load = { kind: 'loading' } | { kind: 'ok'; clips: EvidenceClip[] } | { kind: 'error'; message: string };

export function EvidencePanel({ incident }: { incident: Incident }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [tick, setTick] = useState(0);
  const recent = Date.now() - parseTs(incident.start_time) < RECORDING_GRACE_MS;
  const serious = SEVERITY_RANK[incident.severity] >= SEVERITY_RANK.HIGH;

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const fetchIt = () => api.evidence(incident.incident_id)
      .then((m) => {
        if (stop) return;
        setLoad({ kind: 'ok', clips: m.clips });
        // a serious live incident's clip may still be recording: look again shortly
        if (m.clips.length === 0 && serious && Date.now() - parseTs(incident.start_time) < RECORDING_GRACE_MS) timer = setTimeout(fetchIt, POLL_MS);
      })
      .catch((e) => { if (!stop) setLoad({ kind: 'error', message: e.message }); });
    setLoad({ kind: 'loading' });
    fetchIt();
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, [incident.incident_id, incident.start_time, serious, tick]);

  if (load.kind === 'loading') return <p className="p-3 text-fg-muted">Loading evidence…</p>;
  if (load.kind === 'error') {
    return (
      <div className="p-3 flex items-center gap-3">
        <span className="text-sev-critical">Evidence could not be loaded: {load.message}</span>
        <Button onClick={() => setTick((t) => t + 1)}>Retry</Button>
      </div>
    );
  }
  if (load.clips.length === 0) {
    return (
      <div className="p-3 space-y-1" data-testid="evidence-empty">
        {serious && recent ? (
          <p className="text-fg"><span className="inline-block w-2 h-2 rounded-full bg-sig-warn mr-2 animate-pulse" />Recording — the clip is saved about 8 s after the incident. Checking again…</p>
        ) : (
          <p className="text-fg-muted">No video clip for this incident.</p>
        )}
        <p className="text-2xs text-fg-faint">
          Clips are recorded only for HIGH and CRITICAL incidents raised while live cameras cover the room
          (12 s before, 8 s after).
        </p>
      </div>
    );
  }
  return (
    <div className="p-2 space-y-3">
      {incident.track_id?.startsWith('sim:') && (
        <p className="px-2 py-1.5 border border-sev-medium/50 text-sev-medium text-2xs" data-testid="simulated-note">
          Simulated incident: the clip is real camera footage from that moment, but the simulated person is not in it.
        </p>
      )}
      {load.clips.map((c) => <Clip key={c.video} clip={c} incident={incident} />)}
    </div>
  );
}

function Clip({ clip, incident }: { clip: EvidenceClip; incident: Incident }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);
  const toIncident = useCallback(() => {
    const v = ref.current;
    if (v) { v.currentTime = Math.max(0, clip.incident_offset_s - 2); void v.play?.().catch(() => undefined); }
  }, [clip.incident_offset_s]);
  const fmt = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`;
  return (
    <figure className="border border-ink-700 bg-black" data-testid="evidence-clip">
      {failed ? (
        <div className="aspect-video flex flex-col items-center justify-center gap-2 text-center p-4">
          <span className="text-sev-medium">This clip could not be played in the browser.</span>
          <a className="underline text-st-inv" href={clip.video_url} download>Download the file</a>
          <img src={clip.key_frame_url} alt="Key frame at the incident moment" className="max-h-40" />
        </div>
      ) : (
        <video ref={ref} src={clip.video_url} controls preload="metadata" className="w-full aspect-video bg-black"
               poster={clip.key_frame_url}
               onLoadedMetadata={(e) => { (e.currentTarget as HTMLVideoElement).currentTime = Math.max(0, clip.incident_offset_s - 2); }}
               onError={() => setFailed(true)} aria-label={`Evidence clip from ${clip.camera_id}`} />
      )}
      <figcaption className="flex flex-wrap items-center gap-x-3 gap-y-1 px-2 py-1.5 bg-ink-900 text-2xs text-fg-muted tnum">
        <span className="font-mono text-fg">{clip.camera_id}</span>
        <span>{clock(clip.clip_start)}–{clock(clip.clip_end)}</span>
        <span>incident at <b className="text-fg">{fmt(clip.incident_offset_s)}</b> ({clock(incident.start_time)})</span>
        {clip.track && <span>track <span className="font-mono">#{clip.track}</span> boxed in red</span>}
        <span className="ml-auto flex gap-1">
          {!failed && <Button tone="ghost" onClick={toIncident}>Jump to incident</Button>}
          <a className="inline-flex items-center h-7 px-2 text-xs text-fg-muted hover:text-fg" href={clip.key_frame_url} target="_blank" rel="noreferrer">Key frame</a>
        </span>
      </figcaption>
    </figure>
  );
}
