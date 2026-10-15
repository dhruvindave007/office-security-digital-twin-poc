import React from 'react';
import type { IncidentStatus, Severity } from '../api/types';
import { STATUS_LABEL } from '../lib/format';

const SEV_CLASS: Record<Severity, string> = {
  CRITICAL: 'bg-sev-critical text-ink-950',
  HIGH: 'bg-sev-high text-ink-950',
  MEDIUM: 'bg-sev-medium text-ink-950',
  LOW: 'bg-ink-600 text-fg',
};
export const SEV_BAR: Record<Severity, string> = {
  CRITICAL: 'bg-sev-critical', HIGH: 'bg-sev-high', MEDIUM: 'bg-sev-medium', LOW: 'bg-sev-low',
};

export function SeverityTag({ severity }: { severity: Severity }) {
  return (
    <span className={`inline-block px-1.5 py-px text-2xs font-bold tracking-wider rounded-sm ${SEV_CLASS[severity]}`}>
      {severity}
    </span>
  );
}

const STATUS_CLASS: Record<IncidentStatus, string> = {
  OPEN: 'text-st-open border-ink-500',
  INVESTIGATING: 'text-st-inv border-st-inv/60',
  RESOLVED: 'text-st-ok border-st-ok/50',
  FALSE_POSITIVE: 'text-st-fp border-ink-600',
};

export function StatusTag({ status }: { status: IncidentStatus }) {
  return (
    <span className={`inline-block px-1.5 py-px text-2xs font-semibold uppercase tracking-wider border rounded-sm ${STATUS_CLASS[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Dot({ tone, pulse = false }: { tone: 'live' | 'warn' | 'down' | 'idle'; pulse?: boolean }) {
  const c = { live: 'bg-sig-live', warn: 'bg-sig-warn', down: 'bg-sig-down', idle: 'bg-ink-500' }[tone];
  return <span aria-hidden className={`inline-block w-2 h-2 rounded-full ${c} ${pulse ? 'animate-pulse' : ''}`} />;
}

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: 'default' | 'primary' | 'danger' | 'ghost'; busy?: boolean };
export function Button({ tone = 'default', busy = false, className = '', children, disabled, ...rest }: BtnProps) {
  const t = {
    default: 'bg-ink-700 hover:bg-ink-600 text-fg border-ink-500',
    primary: 'bg-st-inv/90 hover:bg-st-inv text-ink-950 border-st-inv',
    danger: 'bg-transparent hover:bg-sev-critical/10 text-sev-critical border-sev-critical/50',
    ghost: 'bg-transparent hover:bg-ink-700 text-fg-muted hover:text-fg border-transparent',
  }[tone];
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex items-center gap-1.5 h-7 px-2.5 text-xs font-semibold border rounded-sm disabled:opacity-50 disabled:cursor-not-allowed ${t} ${className}`}
    >
      {busy && <span aria-hidden className="w-3 h-3 border-2 border-current border-r-transparent rounded-full animate-spin" />}
      {children}
    </button>
  );
}

export function SectionHead({ title, right }: { title: string; right?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between h-8 px-3 border-b border-ink-700 bg-ink-900 shrink-0">
      <h2 className="label">{title}</h2>
      {right && <div className="flex items-center gap-2 text-2xs text-fg-muted">{right}</div>}
    </div>
  );
}

export function Mono({ children, title }: { children: React.ReactNode; title?: string }) {
  return <span title={title} className="font-mono text-[11.5px] text-fg">{children}</span>;
}
