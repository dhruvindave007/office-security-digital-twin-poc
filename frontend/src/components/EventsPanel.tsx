// Copyright (c) 2026 Dhruvin Dave
// SPDX-License-Identifier: AGPL-3.0-only
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Incident, TwinEvent } from '../api/types';
import { clock, shortId } from '../lib/format';
import { Button } from './ui';

type Load = { kind: 'loading' } | { kind: 'ok'; events: TwinEvent[] } | { kind: 'error'; message: string };

/** The ledger events the incident cites as evidence (GET /api/twin/events?event_ids=...). */
export function EventsPanel({ incident, highlightIds = [] }: { incident: Incident; highlightIds?: string[] }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [tick, setTick] = useState(0);
  const ids = incident.supporting_event_ids;
  const key = ids.join(',');

  useEffect(() => {
    let stop = false;
    setLoad({ kind: 'loading' });
    api.events(ids).then((r) => { if (!stop) setLoad({ kind: 'ok', events: r.events }); })
      .catch((e) => { if (!stop) setLoad({ kind: 'error', message: e.message }); });
    return () => { stop = true; };
  }, [key, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  if (load.kind === 'loading') return <p className="p-3 text-fg-muted">Loading events…</p>;
  if (load.kind === 'error') {
    return (
      <div className="p-3 flex items-center gap-3">
        <span className="text-sev-critical">Events could not be loaded: {load.message}</span>
        <Button onClick={() => setTick((t) => t + 1)}>Retry</Button>
      </div>
    );
  }
  const missing = ids.length - load.events.length;
  return (
    <div className="p-2">
      <p className="px-1 pb-2 text-2xs text-fg-faint">
        {ids.length} supporting event{ids.length === 1 ? '' : 's'} cited by the rule
        {ids.length >= 50 ? ' (long-running incident: first 20 and latest 30 kept)' : ''}
        {missing > 0 ? ` · ${missing} not found in the ledger` : ''}. Track and person values are system labels, not identities.
      </p>
      {load.events.length === 0 ? (
        <p className="p-2 text-fg-muted">No supporting events.</p>
      ) : (
        <table className="w-full text-2xs tnum">
          <thead className="text-fg-faint text-left">
            <tr className="border-b border-ink-700">
              <th className="py-1 px-1 font-semibold">Time</th><th className="px-1 font-semibold">Event</th>
              <th className="px-1 font-semibold">Track</th><th className="px-1 font-semibold">Cam</th>
              <th className="px-1 font-semibold">Room</th><th className="px-1 font-semibold">ID</th>
            </tr>
          </thead>
          <tbody>
            {load.events.map((e) => (
              <tr key={e.event_id} className={`border-b border-ink-800 align-top ${highlightIds.includes(e.event_id) ? 'bg-ink-700' : ''}`}>
                <td className="py-1 px-1 text-fg whitespace-nowrap" title={e.timestamp}>{clock(e.timestamp)}</td>
                <td className="px-1 text-fg">{e.event_type}{e.payload?.badge_id ? <span className="text-fg-muted"> · {String(e.payload.badge_id)}</span> : null}</td>
                <td className="px-1 font-mono break-all">{e.track_id}{e.person_id ? <span className="text-fg-muted"> ({e.person_id})</span> : null}</td>
                <td className="px-1 font-mono">{e.camera_id ?? '—'}</td>
                <td className="px-1">{e.zone_id ?? '—'}</td>
                <td className="px-1 font-mono text-fg-faint" title={e.event_id}>{shortId(e.event_id)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
