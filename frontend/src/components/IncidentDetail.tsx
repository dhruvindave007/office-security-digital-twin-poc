import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Incident, IncidentStatus } from '../api/types';
import { useDash, useNow } from '../state/dashboard';
import { age, dateClock, incidentCameras, parseTs, typeLabel, zoneName } from '../lib/format';
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
type Tab = 'evidence' | 'events' | 'ai';

export function IncidentDetail({ incident, onClose }: { incident: Incident; onClose: () => void }) {
  const { state, upsertIncident } = useDash();
  const now = useNow(1000);
  const [tab, setTab] = useState<Tab>('evidence');
  const [pending, setPending] = useState<IncidentStatus | null>(null);
  const [failed, setFailed] = useState<{ to: IncidentStatus; message: string } | null>(null);
  const cams = incidentCameras(incident, state.site);

  useEffect(() => { setPending(null); setFailed(null); }, [incident.incident_id]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '1') setTab('evidence');
      if (e.key === '2') setTab('events');
      if (e.key === '3') setTab('ai');
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, []);

  // The backend is authoritative: the status shown changes only when the PATCH response arrives.
  const change = async (to: IncidentStatus) => {
    setPending(to); setFailed(null);
    try {
      upsertIncident(await api.setStatus(incident.incident_id, to));
    } catch (e) {
      setFailed({ to, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="flex flex-col min-h-0 h-full" aria-label="Incident detail" data-testid="incident-detail">
      <header className="shrink-0 border-b border-ink-700 bg-ink-900">
        <div className="flex items-center gap-2 h-8 px-2 border-b border-ink-800">
          <Button tone="ghost" onClick={onClose} aria-label="Back to incident queue">← Queue <kbd className="text-fg-faint ml-1">Esc</kbd></Button>
          <span className="ml-auto font-mono text-2xs text-fg-faint select-all" title="incident ID">{incident.incident_id}</span>
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
            {incident.track_id && <><dt className="text-fg-faint">Track</dt><dd><span className="font-mono text-fg">{incident.track_id}</span> <span className="text-fg-faint">per-camera label, not an identity{incident.track_id.startsWith('sim:') ? ' · simulated' : ''}</span></dd></>}
            {incident.person_id && <><dt className="text-fg-faint">Person label</dt><dd><span className="font-mono text-fg">{incident.person_id}</span> <span className="text-fg-faint">system label (e.g. from a badge), not verified</span></dd></>}
            <dt className="text-fg-faint">Cameras</dt><dd className="font-mono text-fg">{cams.length ? cams.join(' ') : '—'}</dd>
          </dl>
          <div className="flex flex-wrap items-center gap-1.5 pt-1" role="group" aria-label="Lifecycle actions">
            {ACTIONS[incident.status].map((a) => (
              <Button key={a.to} tone={a.tone ?? 'default'} onClick={() => change(a.to)} busy={pending === a.to} disabled={pending !== null}
                      data-testid={`action-${a.to}`}>
                {a.label}
              </Button>
            ))}
          </div>
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
