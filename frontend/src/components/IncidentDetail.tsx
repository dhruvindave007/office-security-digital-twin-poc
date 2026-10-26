import { useEffect, useMemo, useRef, useState } from 'react';
import type { Incident, IncidentStatus } from '../api/types';
import { useDash, useNow } from '../state/dashboard';
import { useOps } from '../state/ops';
import { ACTIVE, age, clock, dateClock, groupKey, incidentCameras, parseTs, typeLabel, zoneName } from '../lib/format';
import { AiPanel } from './AiPanel';
import { EventsPanel } from './EventsPanel';
import { EvidencePanel } from './EvidencePanel';
import { Button, SeverityTag, StatusTag } from './ui';

const ACTIONS: Record<IncidentStatus, { to: IncidentStatus; label: string; tone?: 'primary' | 'danger' | 'default' | 'ghost' }[]> = {
  OPEN: [{ to: 'INVESTIGATING', label: 'Start investigating', tone: 'primary' }, { to: 'RESOLVED', label: 'Resolve' }, { to: 'FALSE_POSITIVE', label: 'False positive', tone: 'ghost' }],
  INVESTIGATING: [{ to: 'RESOLVED', label: 'Resolve', tone: 'primary' }, { to: 'FALSE_POSITIVE', label: 'False positive', tone: 'ghost' }, { to: 'OPEN', label: 'Back to open', tone: 'ghost' }],
  RESOLVED: [{ to: 'OPEN', label: 'Reopen' }],
  FALSE_POSITIVE: [{ to: 'OPEN', label: 'Reopen' }],
};
const KEY: Record<IncidentStatus, string> = { INVESTIGATING: 's', RESOLVED: 'r', FALSE_POSITIVE: 'f', OPEN: 'o' };
const CLOSING: IncidentStatus[] = ['RESOLVED', 'FALSE_POSITIVE'];
type Tab = 'evidence' | 'events' | 'ai';

export function IncidentDetail({ incident, onClose, position, onStep, planAdvance, onShowCamera }: {
  incident: Incident;
  onClose: () => void;
  position?: { index: number; total: number };      // place in the current queue (index -1: no longer in it)
  onStep?: (delta: 1 | -1) => void;
  planAdvance?: (ids: string[]) => () => void;      // called before closing ids; returns 'show the next one'
  onShowCamera?: (cameraId: string) => void;
}) {
  const { state } = useDash();
  const { changeStatus } = useOps();
  const now = useNow(1000);
  const [tab, setTab] = useState<Tab>('evidence');
  const [pending, setPending] = useState<IncidentStatus | 'similar' | null>(null);
  const [failed, setFailed] = useState<{ to: IncidentStatus; message: string } | null>(null);
  const cams = incidentCameras(incident, state.site);

  // other active repeats of this incident (same rule, room and source), newest first
  const similar = useMemo(() => {
    if (!ACTIVE.includes(incident.status)) return [];
    const k = groupKey(incident);
    return Object.values(state.incidents)
      .filter((i) => i.incident_id !== incident.incident_id && groupKey(i) === k)
      .sort((a, b) => parseTs(b.start_time) - parseTs(a.start_time));
  }, [state.incidents, incident]);

  useEffect(() => { setPending(null); setFailed(null); }, [incident.incident_id]);

  // The backend is authoritative: the status shown changes only when the server's answer arrives.
  const change = async (to: IncidentStatus) => {
    setPending(to); setFailed(null);
    // "next" is decided from the queue as the operator sees it now, before this incident leaves it
    const showNext = CLOSING.includes(to) ? planAdvance?.([incident.incident_id]) : undefined;
    try {
      await changeStatus([incident], to, { undo: CLOSING.includes(to) });
      showNext?.();
    } catch (e) {
      setFailed({ to, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setPending(null);
    }
  };
  const closeSimilar = async (to: IncidentStatus) => {
    const batch = [incident, ...similar];
    setPending('similar'); setFailed(null);
    const showNext = planAdvance?.(batch.map((i) => i.incident_id));
    try {
      await changeStatus(batch, to, { undo: true });
      showNext?.();
    } catch (e) {
      setFailed({ to, message: `${batch.length} incidents: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setPending(null);
    }
  };

  // keys: 1/2/3 tabs; s/r/f/o run the status actions shown on the buttons
  const keyState = useRef({ change, status: incident.status, pending });
  keyState.current = { change, status: incident.status, pending };
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '1') setTab('evidence');
      if (e.key === '2') setTab('events');
      if (e.key === '3') setTab('ai');
      const { change: go, status, pending: busy } = keyState.current;
      const action = ACTIONS[status].find((a) => KEY[a.to] === e.key);
      if (action && busy === null) { e.preventDefault(); void go(action.to); }
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, []);

  return (
    <section className="flex flex-col min-h-0 h-full" aria-label="Incident detail" data-testid="incident-detail">
      <header className="shrink-0 border-b border-ink-700 bg-ink-900">
        <div className="flex items-center gap-2 h-8 px-2 border-b border-ink-800">
          <Button tone="ghost" onClick={onClose} aria-label="Back to incident queue">← Queue <kbd className="text-fg-faint ml-1">Esc</kbd></Button>
          {position && onStep && (
            <span className="flex items-center gap-0.5 text-2xs text-fg-muted tnum" data-testid="detail-position">
              <Button tone="ghost" className="!px-1.5" onClick={() => onStep(-1)} aria-label="Previous incident" title="Previous (k)">‹</Button>
              <span>{position.index >= 0 ? `${position.index + 1} / ${position.total}` : `– / ${position.total}`}</span>
              <Button tone="ghost" className="!px-1.5" onClick={() => onStep(1)} aria-label="Next incident" title="Next (j)">›</Button>
            </span>
          )}
          <span className="ml-auto font-mono text-2xs text-fg-faint select-all truncate" title="incident ID">{incident.incident_id}</span>
        </div>
        <div className="px-3 pt-2 pb-2 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <SeverityTag severity={incident.severity} />
            <h2 className="text-[15px] font-semibold text-fg">{typeLabel(incident.incident_type)}</h2>
            <span className="text-fg-muted">{zoneName(state.zones, incident.zone_id)}</span>
            <span className="ml-auto"><StatusTag status={incident.status} /></span>
          </div>
          <p className="text-fg-muted text-[12.5px]">{incident.summary}</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-2xs tnum">
            <dt className="text-fg-faint">Raised</dt><dd className="text-fg">{dateClock(incident.start_time)} <span className="text-fg-muted">({age(parseTs(incident.start_time), now)} ago)</span>{incident.end_time ? <span className="text-fg-muted"> · ended {dateClock(incident.end_time)}</span> : null}</dd>
            {incident.track_id && <><dt className="text-fg-faint">Track</dt><dd><span className="font-mono text-fg">{incident.track_id}</span> <span className="text-fg-faint">{incident.track_id.startsWith('badge:') ? 'badge-event label, no person tracked' : 'per-camera label, not an identity'}{incident.track_id.startsWith('sim:') ? ' · simulated' : ''}</span></dd></>}
            {incident.person_id && <><dt className="text-fg-faint">Person label</dt><dd><span className="font-mono text-fg">{incident.person_id}</span> <span className="text-fg-faint">system label (e.g. from a badge), not verified</span></dd></>}
            <dt className="text-fg-faint">Cameras</dt>
            <dd className="font-mono text-fg flex flex-wrap gap-1">
              {cams.length ? cams.map((c) => (
                onShowCamera
                  ? <button key={c} className="px-1 border border-ink-600 rounded-sm hover:border-sev-high" title={`Show ${c} live, enlarged`} onClick={() => onShowCamera(c)}>{c} ⤢</button>
                  : <span key={c}>{c}</span>
              )) : '—'}
            </dd>
          </dl>
          <div className="flex flex-wrap items-center gap-1.5 pt-1" role="group" aria-label="Lifecycle actions">
            {ACTIONS[incident.status].map((a) => (
              <Button key={a.to} tone={a.tone ?? 'default'} onClick={() => change(a.to)} busy={pending === a.to} disabled={pending !== null}
                      data-testid={`action-${a.to}`} title={`Shortcut: ${KEY[a.to]}`}>
                {a.label}<kbd aria-hidden className="font-normal opacity-60">{KEY[a.to]}</kbd>
              </Button>
            ))}
          </div>
          {similar.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 px-2 py-1 border border-ink-600 bg-ink-850 text-2xs" data-testid="similar-bar">
              <span className="text-fg-muted">
                <b className="text-fg tnum">{similar.length}</b> more active like this (same rule, room, source) · newest {clock(similar[0].start_time)}
              </span>
              <span className="ml-auto flex gap-1">
                <Button className="!h-6" busy={pending === 'similar'} disabled={pending !== null} onClick={() => closeSimilar('RESOLVED')}
                        data-testid="similar-RESOLVED">Resolve all {similar.length + 1}</Button>
                <Button tone="ghost" className="!h-6" disabled={pending !== null} onClick={() => closeSimilar('FALSE_POSITIVE')}
                        data-testid="similar-FALSE_POSITIVE">False positive all {similar.length + 1}</Button>
              </span>
            </div>
          )}
          {failed && (
            <div role="alert" className="flex items-center gap-2 text-2xs text-sev-critical" data-testid="action-error">
              <span>Could not change status: {failed.message}. The incident is still {incident.status.toLowerCase().replace('_', ' ')}.</span>
              <Button onClick={() => change(failed.to)}>Retry</Button>
            </div>
          )}
        </div>
        <nav className="flex px-2" role="tablist" aria-label="Incident sections">
          {([['evidence', 'Evidence', '1'], ['events', `Events (${incident.supporting_event_ids.length})`, '2'], ['ai', 'AI review', '3']] as const).map(([k, label, key]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} title={`Shortcut: ${key}`}
                    className={`h-8 px-3 text-xs font-semibold border-b-2 ${tab === k ? 'border-sev-high text-fg' : 'border-transparent text-fg-muted hover:text-fg'}`}>
              {label}
            </button>
          ))}
        </nav>
      </header>
      <div className="flex-1 min-h-0 overflow-y-auto" role="tabpanel">
        {tab === 'evidence' && <EvidencePanel incident={incident} />}
        {tab === 'events' && <EventsPanel incident={incident} />}
        {tab === 'ai' && <AiPanel incident={incident} />}
      </div>
    </section>
  );
}
