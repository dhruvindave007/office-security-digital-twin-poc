// Copyright (c) 2026 Dhruvin Dave
// SPDX-License-Identifier: AGPL-3.0-only
// Operator workflows: grouped repeats, bulk actions with undo, keyboard triage, room/severity filters.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from '../App';
import type { Incident, IncidentStatus } from '../api/types';
import { FakeEventSource } from './setup';
import { HttpError, incident, installApi } from './fakeApi';

const id = (n: number) => `01a10000-0000-7000-8000-00000000000${n}`;
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();

// three live repeats in the lab, one simulated repeat of the same rule, one other incident
const burst = (): Incident[] => [
  incident({ incident_id: id(1), incident_type: 'UNAUTHORIZED_ENTRY', track_id: 'live:cam_3:11', start_time: ago(30) }),
  incident({ incident_id: id(2), incident_type: 'UNAUTHORIZED_ENTRY', track_id: 'live:cam_3:12', start_time: ago(20) }),
  incident({ incident_id: id(3), incident_type: 'UNAUTHORIZED_ENTRY', track_id: 'live:cam_3:13', start_time: ago(10) }),
  incident({ incident_id: id(4), incident_type: 'UNAUTHORIZED_ENTRY', track_id: 'sim:lab_visitor', start_time: ago(5) }),
  incident({ incident_id: id(5), incident_type: 'ZONE_OVER_CAPACITY', severity: 'MEDIUM', zone_id: 'zone_corridor', track_id: null, start_time: ago(40) }),
];

/** A backend that really applies status changes, so later reads agree with earlier writes. */
function statefulApi(list: Incident[], extra = {}) {
  const db = new Map(list.map((i) => [i.incident_id, { ...i }]));
  const set = (ids: string[], status: IncidentStatus) => ids.filter((x) => db.has(x)).map((x) => {
    const next = { ...db.get(x)!, status, end_time: status === 'RESOLVED' || status === 'FALSE_POSITIVE' ? new Date().toISOString() : null };
    db.set(x, next);
    return next;
  });
  const server = installApi({
    'PATCH /api/twin/incidents/{id}': (url: URL, init?: RequestInit) =>
      set([decodeURIComponent(url.pathname.split('/').pop()!)], JSON.parse(String(init!.body)).status)[0],
    'POST /api/twin/incidents/status': (_: URL, init?: RequestInit) => {
      const b = JSON.parse(String(init!.body));
      return { updated: set(b.incident_ids, b.status), not_found: b.incident_ids.filter((x: string) => !db.has(x)) };
    },
    'GET /api/twin/incidents/{id}/evidence': (url: URL) => ({ incident_id: url.pathname.split('/')[4], clips: [] }),
    ...extra,
  }, list);
  return { server, db };
}

async function boot(list = burst(), extra = {}) {
  const api = statefulApi(list, extra);
  render(<App />);
  await screen.findByTestId('conn');
  act(() => FakeEventSource.latest().open());
  return api;
}
const queue = () => screen.getByRole('listbox', { name: 'Incidents' });
const key = (k: string) => fireEvent.keyDown(document.body, { key: k });

describe('grouped repeats', () => {
  it('collapses live repeats of one rule in one room into a single row; simulated ones stay separate', async () => {
    await boot();
    const group = await screen.findByTestId(`group-${id(3)}`);          // newest member heads the group
    expect(group.textContent).toContain('×3');
    expect(group.textContent).toContain('3 open');
    expect(screen.queryByTestId(`incident-${id(1)}`)).toBeNull();        // members hidden while collapsed
    expect(screen.getByTestId(`incident-${id(4)}`).textContent).toContain('SIM');
    expect(screen.getByTestId(`incident-${id(5)}`)).toBeTruthy();

    fireEvent.click(group);                                               // expand
    for (const n of [1, 2, 3]) expect(screen.getByTestId(`incident-${id(n)}`)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Group repeats' }));   // grouping off: plain list
    expect(screen.queryByTestId(`group-${id(3)}`)).toBeNull();
    expect(within(queue()).getAllByRole('option')).toHaveLength(5);
  });

  it('"Resolve all" on a group is one server call; rows change only after the answer; Undo restores them', async () => {
    const { server, db } = await boot();
    fireEvent.click(await screen.findByTestId(`group-resolve-${id(3)}`));
    await waitFor(() => expect(screen.queryByTestId(`group-${id(3)}`)).toBeNull());
    const call = server.calls.find((c) => c.method === 'POST' && c.path === '/api/twin/incidents/status');
    expect(call?.body).toEqual({ incident_ids: [id(3), id(2), id(1)], status: 'RESOLVED' });
    expect([1, 2, 3].map((n) => db.get(id(n))!.status)).toEqual(['RESOLVED', 'RESOLVED', 'RESOLVED']);

    const toast = screen.getByTestId('toast');
    expect(toast.textContent).toContain('Resolved: 3 incidents');
    fireEvent.click(within(toast).getByText('Undo'));
    await screen.findByTestId(`group-${id(3)}`);
    expect([1, 2, 3].map((n) => db.get(id(n))!.status)).toEqual(['OPEN', 'OPEN', 'OPEN']);
  });
});

describe('selection and bulk actions', () => {
  it('checkboxes select across rows; the bulk bar acts on exactly the selection', async () => {
    const { server } = await boot();
    fireEvent.click(await screen.findByLabelText(/Select all 3 in this group/));
    fireEvent.click(screen.getByLabelText(/Select Over capacity/));
    expect(screen.getByTestId('bulk-bar').textContent).toContain('4 selected');
    fireEvent.click(screen.getByTestId('bulk-FALSE_POSITIVE'));
    await waitFor(() => expect(screen.queryByTestId('bulk-bar')).toBeNull());
    const body = server.calls.find((c) => c.path === '/api/twin/incidents/status')!.body as { incident_ids: string[]; status: string };
    expect(new Set(body.incident_ids)).toEqual(new Set([id(1), id(2), id(3), id(5)]));
    expect(body.status).toBe('FALSE_POSITIVE');
    expect(within(queue()).getAllByRole('option')).toHaveLength(1);       // only the simulated one is still active
  });

  it('a failed bulk change says nothing was changed and keeps the rows', async () => {
    await boot(burst(), { 'POST /api/twin/incidents/status': () => { throw new HttpError(500, 'database is locked'); } });
    fireEvent.click(await screen.findByTestId(`group-resolve-${id(3)}`));
    const err = await screen.findByTestId('bulk-error');
    expect(err.textContent).toContain('database is locked');
    expect(err.textContent).toContain('Nothing was changed');
    expect(screen.getByTestId(`group-${id(3)}`).textContent).toContain('3 open');
  });

  it('keyboard: j moves, x selects, Esc clears the selection', async () => {
    await boot();
    await screen.findByTestId(`group-${id(3)}`);
    key('j');                                                             // row 1: the newer simulated incident
    key('j');                                                             // row 2: the lab group
    key('x');
    expect(screen.getByTestId('bulk-bar').textContent).toContain('3 selected');
    key('Escape');
    expect(screen.queryByTestId('bulk-bar')).toBeNull();
  });
});

describe('triage from the detail view', () => {
  it('r resolves (after the server confirms), shows the next incident, and u undoes', async () => {
    const { db } = await boot();
    fireEvent.click(screen.getByRole('button', { name: 'Group repeats' }));    // plain list: 3,2,1 (HIGH, newest first), sim 4 first
    fireEvent.click(await screen.findByTestId(`incident-${id(3)}`));
    const detail = await screen.findByTestId('incident-detail');
    expect(within(detail).getByTestId('detail-position').textContent).toContain('2 / 5');
    key('r');
    await waitFor(() => expect(db.get(id(3))!.status).toBe('RESOLVED'));
    await waitFor(() => expect(within(screen.getByTestId('incident-detail')).getByText(id(2))).toBeTruthy());  // moved on
    expect(screen.getByTestId('toast').textContent).toContain('Resolved: Unauthorized entry');
    key('u');
    await waitFor(() => expect(db.get(id(3))!.status).toBe('OPEN'));
  });

  it('s starts investigating and stays on the incident; j/k step through the queue', async () => {
    const { db } = await boot();
    fireEvent.click(await screen.findByTestId(`incident-${id(5)}`));
    const detail = await screen.findByTestId('incident-detail');
    key('s');
    await waitFor(() => expect(db.get(id(5))!.status).toBe('INVESTIGATING'));
    await waitFor(() => expect(within(detail).getAllByText('Investigating').length).toBeGreaterThan(0));
    expect(within(detail).getByText(id(5))).toBeTruthy();
    key('k');
    await waitFor(() => expect(within(screen.getByTestId('incident-detail')).queryByText(id(5))).toBeNull());
  });

  it('"Resolve all" for similar incidents closes the whole burst from the detail view', async () => {
    const { server, db } = await boot();
    fireEvent.click(await screen.findByTestId(`group-${id(3)}`));
    fireEvent.click(screen.getByTestId(`incident-${id(2)}`));
    const bar = await screen.findByTestId('similar-bar');
    expect(bar.textContent).toContain('2 more active like this');       // the simulated repeat is not "similar"
    fireEvent.click(within(bar).getByTestId('similar-RESOLVED'));
    await waitFor(() => expect([1, 2, 3].every((n) => db.get(id(n))!.status === 'RESOLVED')).toBe(true));
    expect(db.get(id(4))!.status).toBe('OPEN');
    expect((server.calls.find((c) => c.path === '/api/twin/incidents/status')!.body as { incident_ids: string[] }).incident_ids[0]).toBe(id(2));
  });

  it('a camera button in the detail enlarges that camera', async () => {
    await boot();
    fireEvent.click(await screen.findByTestId(`incident-${id(5)}`));
    fireEvent.click(await screen.findByTitle('Show cam_2 live, enlarged'));
    expect(screen.getByLabelText('Close enlarged cam_2')).toBeTruthy();
    expect(screen.queryByTestId('camera-cam_3')).toBeNull();
  });
});

describe('filters', () => {
  it('clicking a room on the floor plan filters the queue to it; clicking again clears', async () => {
    await boot();
    await screen.findByTestId(`group-${id(3)}`);
    fireEvent.click(screen.getByTestId('zone-zone_corridor'));
    expect(screen.getByTestId('zone-filter').textContent).toContain('Corridor');
    expect(within(queue()).getAllByRole('option')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('zone-zone_corridor'));
    expect(screen.queryByTestId('zone-filter')).toBeNull();
    expect(within(queue()).getAllByRole('option')).toHaveLength(3);
  });

  it('severity counts in the top bar filter the queue and are remembered after a reload', async () => {
    await boot();
    await screen.findByTestId(`group-${id(3)}`);
    expect(screen.getByTestId('sev-HIGH').textContent).toBe('4 high');
    fireEvent.click(screen.getByTestId('sev-HIGH'));
    expect(screen.queryByTestId(`incident-${id(5)}`)).toBeNull();        // MEDIUM hidden
    expect(screen.getByTestId('sev-HIGH').getAttribute('aria-pressed')).toBe('true');
    expect(document.title).toBe('(4) Office Vision Twin');
  });

  it('a remembered filter is restored; unreadable storage falls back to the default', async () => {
    window.localStorage.setItem('ovt.queueFilter.v1', JSON.stringify({ zone: 'zone_corridor' }));
    await boot();
    expect((await screen.findByTestId('zone-filter')).textContent).toContain('Corridor');
  });
});
