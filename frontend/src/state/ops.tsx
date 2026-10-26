import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { Incident, IncidentStatus } from '../api/types';
import { STATUS_LABEL, typeLabel } from '../lib/format';
import { useDash } from './dashboard';

const TOAST_MS = 8000;

type Toast = { id: number; text: string; tone: 'ok' | 'bad'; action?: { label: string; run: () => void } };

interface Ops {
  /**
   * Set a status on one or many incidents. The backend is authoritative: nothing changes on screen
   * until it answers, and a failure throws (the caller shows why). With `undo`, a toast offers to put
   * every incident back to the status it had before (also on the `u` key).
   */
  changeStatus: (incidents: Incident[], to: IncidentStatus, opts?: { undo?: boolean; onUndone?: () => void }) =>
    Promise<{ updated: Incident[]; missing: string[] }>;
  notify: (text: string, tone?: Toast['tone'], action?: Toast['action']) => void;
}

const OpsContext = createContext<Ops | null>(null);

export function useOps(): Ops {
  const ctx = useContext(OpsContext);
  if (!ctx) throw new Error('useOps outside OpsProvider');
  return ctx;
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function OpsProvider({ children }: { children: React.ReactNode }) {
  const { upsertIncident } = useDash();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const notify = useCallback<Ops['notify']>((text, tone = 'ok', action) => {
    const id = nextId.current++;
    setToasts((t) => [...t.slice(-2), { id, text, tone, action }]);
    setTimeout(() => dismiss(id), TOAST_MS);
  }, [dismiss]);

  const apply = useCallback(async (ids: string[], to: IncidentStatus) => {
    if (ids.length === 1) return { updated: [await api.setStatus(ids[0], to)], missing: [] as string[] };
    const r = await api.setStatusBulk(ids, to);
    return { updated: r.updated, missing: r.not_found };
  }, []);

  const changeStatus = useCallback<Ops['changeStatus']>(async (incidents, to, opts) => {
    const before = new Map(incidents.map((i) => [i.incident_id, i.status]));
    const result = await apply([...before.keys()], to);
    result.updated.forEach(upsertIncident);
    if (opts?.undo && result.updated.length) {
      const what = result.updated.length === 1
        ? `${typeLabel(result.updated[0].incident_type)}`
        : `${result.updated.length} incidents`;
      const missing = result.missing.length ? ` (${result.missing.length} no longer exist)` : '';
      notify(`${STATUS_LABEL[to]}: ${what}${missing}`, 'ok', {
        label: 'Undo',
        run: async () => {
          const byStatus = new Map<IncidentStatus, string[]>();
          for (const inc of result.updated) {
            const prev = before.get(inc.incident_id);
            if (prev && prev !== to) byStatus.set(prev, [...(byStatus.get(prev) ?? []), inc.incident_id]);
          }
          try {
            for (const [status, ids] of byStatus) (await apply(ids, status)).updated.forEach(upsertIncident);
            notify(`Undone: back to ${[...byStatus.keys()].map((s) => STATUS_LABEL[s].toLowerCase()).join(' / ')}`);
            opts.onUndone?.();
          } catch (e) {
            notify(`Undo failed: ${errText(e)}`, 'bad');
          }
        },
      });
    }
    return result;
  }, [apply, notify, upsertIncident]);

  // `u` runs the newest toast's action (Undo) while it is on screen
  const toastsRef = useRef(toasts);
  toastsRef.current = toasts;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT');
      if (e.key !== 'u' || typing || e.ctrlKey || e.metaKey || e.altKey) return;
      const last = [...toastsRef.current].reverse().find((x) => x.action);
      if (last?.action) { dismiss(last.id); last.action.run(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dismiss]);

  const value = useMemo(() => ({ changeStatus, notify }), [changeStatus, notify]);
  return (
    <OpsContext.Provider value={value}>
      {children}
      <div className="fixed bottom-3 left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-1.5 pointer-events-none" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} role="status" data-testid="toast"
               className={`pointer-events-auto flex items-center gap-3 pl-3 pr-1 h-9 min-w-[18rem] max-w-[36rem] border shadow-lg shadow-black/50 bg-ink-800 text-xs ${t.tone === 'bad' ? 'border-sev-critical/70 text-sev-critical' : 'border-ink-500 text-fg'}`}>
            <span className="truncate" title={t.text}>{t.text}</span>
            <span className="ml-auto flex items-center">
              {t.action && (
                <button className="h-7 px-2 font-semibold text-st-inv hover:bg-ink-700" onClick={() => { dismiss(t.id); t.action!.run(); }}>
                  {t.action.label} <kbd className="text-fg-faint font-normal">u</kbd>
                </button>
              )}
              <button className="h-7 w-7 text-fg-faint hover:text-fg" aria-label="Dismiss" onClick={() => dismiss(t.id)}>×</button>
            </span>
          </div>
        ))}
      </div>
    </OpsContext.Provider>
  );
}
