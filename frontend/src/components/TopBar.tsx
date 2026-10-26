import { useState } from 'react';
import { api } from '../api/client';
import type { Severity } from '../api/types';
import { STALE_MS, useDash, useNow } from '../state/dashboard';
import { ACTIVE, age, clock } from '../lib/format';
import { Button, Dot } from './ui';

export function connectionView(conn: string, lastMessageAt: number | null, now: number) {
  if (conn === 'open') {
    const fresh = lastMessageAt !== null && now - lastMessageAt <= STALE_MS;
    return fresh ? { tone: 'live' as const, text: 'LIVE' } : { tone: 'warn' as const, text: 'STALE' };
  }
  if (conn === 'reconnecting' || conn === 'connecting') return { tone: 'warn' as const, text: conn === 'connecting' ? 'CONNECTING' : 'RECONNECTING' };
  return { tone: 'down' as const, text: 'OFFLINE' };
}

function Simulate() {
  const { state } = useDash();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (state.scenarios.length === 0) return null;
  const run = async () => {
    if (!name) return;
    setBusy(true); setMsg(null);
    try {
      const r = await api.runScenario(name);
      setMsg(r.errors.length
        ? { ok: false, text: `${r.errors.length} step error(s): ${r.errors[0]}` }
        : { ok: true, text: `${r.steps_processed} steps, ${r.incidents.length} incident(s) raised` });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex items-center gap-1.5">
      <select aria-label="Scenario to simulate" value={name} onChange={(e) => setName(e.target.value)}
              className="h-7 max-w-[13rem] bg-ink-800 border border-ink-600 rounded-sm px-1 text-xs text-fg">
        <option value="">Simulate scenario…</option>
        {state.scenarios.map((s) => <option key={s.name} value={s.name} title={s.description}>{s.name}</option>)}
      </select>
      <Button onClick={run} busy={busy} disabled={!name}>Run</Button>
      {msg && <span role="status" className={`text-2xs max-w-[18rem] truncate ${msg.ok ? 'text-fg-muted' : 'text-sev-critical'}`} title={msg.text}>{msg.text}</span>}
    </div>
  );
}

export function TopBar({ onSeverity, minSeverity = null }: { onSeverity?: (s: Severity) => void; minSeverity?: Severity | null } = {}) {
  const { state } = useDash();
  const now = useNow(1000);
  const conn = connectionView(state.conn, state.lastMessageAt, now);
  const cams = state.live?.cameras ?? [];
  const liveCams = cams.filter((c) => c.source.state === 'live').length;
  const configured = state.site?.cameras.length ?? 0;
  const active = Object.values(state.incidents).filter((i) => ACTIVE.includes(i.status));
  const bySev = (s: string) => active.filter((i) => i.severity === s).length;
  const auto = state.auto;

  return (
    <header className="shrink-0 flex items-center gap-4 h-11 px-3 border-b border-ink-700 bg-ink-900 text-xs" role="banner">
      <div className="flex items-baseline gap-2 min-w-0">
        <span className="font-bold tracking-wide text-fg">OFFICE VISION TWIN</span>
        <span className="text-fg-muted truncate">{state.site?.name ?? 'Site'}{state.site?.live_config ? '' : ' · no live cameras configured'}</span>
      </div>
      <div className="flex items-center gap-1.5 font-semibold tracking-wider" data-testid="conn" title="push channel (GET /api/stream)">
        <Dot tone={conn.tone} />
        <span className={{ live: 'text-sig-live', warn: 'text-sig-warn', down: 'text-sig-down' }[conn.tone]}>{conn.text}</span>
        <span className="text-fg-faint font-normal tnum">{state.lastMessageAt ? `${age(state.lastMessageAt, now)}` : ''}</span>
      </div>
      <div className="hidden md:flex items-center gap-4 text-fg-muted tnum">
        <span title="camera streams delivering frames"><b className={liveCams < configured ? 'text-sig-warn' : 'text-fg'}>{liveCams}/{configured}</b> cameras live</span>
        {state.live?.totals && <span title="detections per second, all cameras">{state.live.totals.processed_fps.toFixed(0)} det/s</span>}
        <span title="people currently tracked by the twin (estimate; per-camera tracks)"><b className="text-fg">{state.twin?.total_active_tracks ?? '—'}</b> tracked</span>
        <span className="flex items-center gap-0.5" role="group" aria-label="Active incidents by severity (click to filter the queue)">
          {([['CRITICAL', 'crit', 'text-sev-critical'], ['HIGH', 'high', 'text-sev-high'], ['MEDIUM', 'med', 'text-sev-medium']] as const).map(([s, label, color]) => (
            <button key={s} onClick={() => onSeverity?.(s)} aria-pressed={minSeverity === s} data-testid={`sev-${s}`}
                    title={minSeverity === s ? 'Show all severities again' : `Show only ${label}${s === 'CRITICAL' ? '' : ' and above'} in the queue`}
                    className={`px-1.5 h-6 rounded-sm ${minSeverity === s ? 'bg-ink-600 text-fg' : 'hover:bg-ink-700'}`}>
              <b className={color}>{bySev(s)}</b> {label}
            </button>
          ))}
        </span>
        <span title={auto?.enabled ? `one per incident type and room per ${Math.round((auto.cooldown_seconds ?? 0) / 60)} min, max ${auto.max_per_hour}/h` : 'automatic AI review is off'}>
          AI auto-review {auto?.enabled ? (auto.paused_seconds_left ? <b className="text-sig-warn">paused (AWS sign-in)</b> : 'on') : 'off'}
        </span>
      </div>
      <div className="ml-auto flex items-center gap-3">
        <Simulate />
        <span className="tnum text-fg font-semibold" title="local time">{clock(now)}</span>
      </div>
    </header>
  );
}
