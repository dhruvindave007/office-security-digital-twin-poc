import { useCallback, useEffect, useState } from 'react';
import { ApiError, api } from '../api/client';
import type { ClaimCheck, Incident, Investigation, InvestigationList } from '../api/types';
import { useDash } from '../state/dashboard';
import { dateClock, shortId } from '../lib/format';
import { Button } from './ui';

type Badge = { label: string; tone: 'ok' | 'warn' | 'bad' | 'idle'; reasons: string[] };

/** The server's verification verdict, translated without ever upgrading it. */
export function verificationBadge(inv: Investigation): Badge {
  const v = inv.verification ?? {};
  if (inv.status !== 'COMPLETED') {
    return { label: inv.status === 'INVALID_OUTPUT' ? 'INVALID OUTPUT' : 'FAILED', tone: 'bad',
      reasons: [inv.error ?? 'The investigation did not complete.'] };
  }
  const reasons: string[] = [];
  const c = v.claims ?? {};
  if (v.json_valid === false) reasons.push('The answer was not in the required structure.');
  if (v.missing_keys?.length) reasons.push(`Missing sections: ${v.missing_keys.join(', ')}.`);
  if (v.unverified_ids?.length) reasons.push(`Cites ${v.unverified_ids.length} ID(s) that do not exist in the evidence.`);
  if (c.unsupported) reasons.push(`${c.unsupported} claim(s) not supported by the records they cite.`);
  if (c.partially_supported) reasons.push(`${c.partially_supported} claim(s) only partially supported.`);
  if (c.unchecked) reasons.push(`${c.unchecked} claim(s) could not be checked.`);
  if (v.banned_words?.length) reasons.push(`Uses accusatory wording: ${v.banned_words.join(', ')}.`);
  if (v.truncated) reasons.push('The answer was cut off.');
  if (v.passed === true) return { label: 'VERIFIED', tone: 'ok', reasons: ['Every cited ID exists and every checked claim is supported by its cited records.'] };
  if (v.passed === undefined) return { label: 'NOT CHECKED', tone: 'idle', reasons: reasons.length ? reasons : ['No verification result was stored.'] };
  const onlyPartial = !c.unsupported && !v.unverified_ids?.length && v.json_valid !== false && !!c.partially_supported;
  return { label: onlyPartial ? 'PARTIALLY SUPPORTED' : 'NOT VERIFIED', tone: onlyPartial ? 'warn' : 'bad',
    reasons: reasons.length ? reasons : ['Verification did not pass.'] };
}

const TONE = {
  ok: 'border-st-ok text-st-ok', warn: 'border-sev-medium text-sev-medium',
  bad: 'border-sev-critical text-sev-critical', idle: 'border-ink-500 text-fg-muted',
};
const VERDICT: Record<string, string> = {
  supported: 'text-st-ok', partially_supported: 'text-sev-medium', unsupported: 'text-sev-critical', unchecked: 'text-fg-faint',
};

function Ids({ ids, bad }: { ids: string[]; bad: Set<string> }) {
  if (!ids?.length) return <span className="text-sev-medium">no citation</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {ids.map((id) => (
        <span key={id} title={bad.has(id) ? `${id} — not in the evidence` : id}
              className={`font-mono px-1 border rounded-sm ${bad.has(id) ? 'border-sev-critical text-sev-critical line-through' : 'border-ink-600 text-fg-muted'}`}>
          {shortId(id)}
        </span>
      ))}
    </span>
  );
}

function Verdict({ claim }: { claim?: ClaimCheck }) {
  if (!claim) return null;
  return (
    <span className={`text-2xs font-semibold uppercase tracking-wider ${VERDICT[claim.verdict] ?? 'text-fg-faint'}`}
          title={claim.judge_reason || claim.deterministic_issues?.join('; ') || ''}>
      {claim.verdict.replace('_', ' ')}
    </span>
  );
}

function InvestigationView({ inv }: { inv: Investigation }) {
  const b = verificationBadge(inv);
  const r = inv.result;
  const bad = new Set(inv.verification?.unverified_ids ?? []);
  const claims = Object.fromEntries((inv.verification?.claims?.claims ?? []).map((c) => [c.claim_id, c]));
  const obs = (r?.findings ?? []).map((f, i) => ({ f, i })).filter(({ f }) => f.kind === 'observation');
  const inf = (r?.findings ?? []).map((f, i) => ({ f, i })).filter(({ f }) => f.kind !== 'observation');
  return (
    <article className="border border-ink-700" data-testid="investigation">
      <header className={`flex flex-wrap items-center gap-x-3 gap-y-1 px-2 py-1.5 border-l-4 bg-ink-900 ${TONE[b.tone]}`}>
        <span className="font-bold tracking-wider text-xs" data-testid="verification-badge">{b.label}</span>
        <span className="text-2xs text-fg-muted tnum">{dateClock(inv.created_at)} · {inv.model_id.replace(/^global\.anthropic\./, '')} · ${inv.cost_usd.toFixed(4)}</span>
        {inv.question && <span className="text-2xs text-fg-faint truncate max-w-full" title={inv.question}>Q: {inv.question}</span>}
      </header>
      {b.tone !== 'ok' && (
        <ul className="px-3 py-1.5 text-2xs text-fg border-b border-ink-700 list-disc list-inside" data-testid="verification-reasons">
          {b.reasons.map((x) => <li key={x}>{x}</li>)}
        </ul>
      )}
      {r && (
        <div className="p-3 space-y-3 text-[12.5px] leading-snug">
          {r.answer && <p className="text-fg"><span className="label mr-2">Answer</span>{r.answer} <Verdict claim={claims.answer} /></p>}
          {r.summary && <p className="text-fg-muted">{r.summary} <Verdict claim={claims.summary} /></p>}
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <h4 className="label mb-1">Observed in the evidence</h4>
              {obs.length === 0 ? <p className="text-fg-faint text-2xs">None stated.</p> : (
                <ul className="space-y-1.5">{obs.map(({ f, i }) => (
                  <li key={i}>{f.statement} <Verdict claim={claims[`findings[${i}]`]} /><div className="mt-0.5"><Ids ids={f.event_ids} bad={bad} /></div></li>
                ))}</ul>
              )}
            </div>
            <div>
              <h4 className="label mb-1">Inferred (interpretation)</h4>
              {inf.length === 0 ? <p className="text-fg-faint text-2xs">None stated.</p> : (
                <ul className="space-y-1.5">{inf.map(({ f, i }) => (
                  <li key={i} className="text-fg-muted">{f.statement} <Verdict claim={claims[`findings[${i}]`]} /><div className="mt-0.5"><Ids ids={f.event_ids} bad={bad} /></div></li>
                ))}</ul>
              )}
            </div>
          </div>
          <div className="border-t border-ink-700 pt-2 space-y-1.5">
            <h4 className="label">Uncertainty</h4>
            {r.person_identity && <p><span className="text-fg-muted">Identity: </span>{r.person_identity}</p>}
            {r.confidence && <p><span className="text-fg-muted">Model confidence: </span>{r.confidence}</p>}
            {!!r.missing_evidence?.length && <p><span className="text-fg-muted">Missing evidence: </span>{r.missing_evidence.join(' · ')}</p>}
            {!!r.benign_explanations?.length && <p><span className="text-fg-muted">Benign explanations: </span>{r.benign_explanations.join(' · ')}</p>}
          </div>
          {!!r.timeline?.length && (
            <details>
              <summary className="label cursor-pointer">Timeline ({r.timeline.length})</summary>
              <ol className="mt-1 space-y-1">{r.timeline.map((t, i) => (
                <li key={i} className="grid grid-cols-[auto_1fr] gap-x-2"><span className="font-mono text-fg-muted text-2xs">{t.time}</span>
                  <span>{t.description} <Verdict claim={claims[`timeline[${i}]`]} /> <Ids ids={t.event_ids} bad={bad} /></span></li>
              ))}</ol>
            </details>
          )}
          {!!r.recommended_actions?.length && (
            <div><h4 className="label mb-1">Suggested checks</h4><ul className="list-disc list-inside text-fg-muted">{r.recommended_actions.map((a) => <li key={a}>{a}</li>)}</ul></div>
          )}
        </div>
      )}
    </article>
  );
}

function runError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return `Daily AI budget reached: ${e.message}`;
    if (e.status === 503) return `AI unavailable: ${e.message}`;
    if (e.status === 502) return `Bedrock call failed: ${e.message}`;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export function AiPanel({ incident }: { incident: Incident }) {
  const { state } = useDash();
  const [list, setList] = useState<InvestigationList | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [question, setQuestion] = useState('');

  const reload = useCallback(() => {
    setLoadErr(null);
    return api.investigations(incident.incident_id).then(setList).catch((e) => setLoadErr(runError(e)));
  }, [incident.incident_id]);
  useEffect(() => { setList(null); void reload(); }, [reload]);

  const run = async () => {
    setRunning(true); setRunErr(null);
    try {
      await api.investigate(incident.incident_id, question.trim());
      setQuestion('');
    } catch (e) {
      setRunErr(runError(e));
    } finally {
      setRunning(false);
      void reload();
    }
  };

  const auto = state.auto?.recent?.find((r) => r.incident_id === incident.incident_id);
  const budgetLeft = list ? list.daily_budget_usd - list.spent_today_usd : null;
  const items = [...(list?.investigations ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));

  return (
    <div className="p-2 space-y-2">
      <div className="border border-ink-700 bg-ink-900 p-2 space-y-1.5">
        <label htmlFor="ai-q" className="label">Ask the AI investigator</label>
        <textarea id="ai-q" rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000}
                  placeholder="Optional question, e.g. “Did the second person use the same badge?”"
                  className="w-full bg-ink-850 border border-ink-600 rounded-sm px-2 py-1 text-fg placeholder:text-fg-faint" />
        <div className="flex flex-wrap items-center gap-2">
          <Button tone="primary" onClick={run} busy={running} disabled={budgetLeft !== null && budgetLeft <= 0}>
            {running ? 'Investigating… (15–30 s)' : 'Investigate'}
          </Button>
          {list && <span className="text-2xs text-fg-muted tnum">AI budget today ${list.spent_today_usd.toFixed(3)} / ${list.daily_budget_usd.toFixed(2)}</span>}
          <span className="text-2xs text-fg-faint">Answers use only the stored evidence and are machine-checked; they are not proof.</span>
        </div>
        {runErr && <p role="alert" className="text-sev-critical text-2xs" data-testid="ai-run-error">{runErr}</p>}
      </div>
      {auto && (
        <p className="text-2xs text-fg-muted px-1" data-testid="auto-note">
          Automatic review: {auto.outcome.replace(/_/g, ' ')}{auto.detail ? ` (${auto.detail})` : ''}
        </p>
      )}
      {loadErr && (
        <div className="flex items-center gap-2 p-2"><span className="text-sev-critical">{loadErr}</span><Button onClick={() => reload()}>Retry</Button></div>
      )}
      {!list && !loadErr && <p className="p-2 text-fg-muted">Loading investigations…</p>}
      {list && items.length === 0 && <p className="p-2 text-fg-muted" data-testid="ai-empty">No AI investigation for this incident yet.</p>}
      {items.map((inv) => <InvestigationView key={inv.investigation_id} inv={inv} />)}
    </div>
  );
}
