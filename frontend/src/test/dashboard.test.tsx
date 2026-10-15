import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from '../App';
import type { Investigation } from '../api/types';
import { FakeEventSource } from './setup';
import { HttpError, emptyInvestigations, frame, incident, installApi, live, twin } from './fakeApi';

async function boot(overrides = {}, incidents = [incident()]) {
  const server = installApi(overrides, incidents);
  const view = render(<App />);
  await screen.findByTestId('conn');
  const es = FakeEventSource.latest();
  act(() => es.open());
  return { server, es, unmount: view.unmount };
}

describe('initial load', () => {
  it('shows loading, then the real site: cameras, rooms, counts and incidents from the API', async () => {
    installApi();
    render(<App />);
    expect(screen.getByTestId('boot-loading')).toBeTruthy();
    await screen.findByTestId('camera-cam_2');
    expect(screen.getByTestId('camera-cam_3')).toBeTruthy();
    expect(screen.getByTestId('zone-zone_vision_lab')).toBeTruthy();
    expect(screen.getByTestId('count-zone_corridor').textContent).toBe('1');
    expect(screen.getByTestId(`incident-${incident().incident_id}`).textContent).toContain('Tailgating');
    expect(FakeEventSource.latest().url).toBe('/api/stream');
  });

  it('API failure shows an error with retry, and retry recovers', async () => {
    let fail = true;
    installApi({ 'GET /api/site': () => { if (fail) throw new HttpError(503, 'Digital Twin engine is not initialized.'); return (installApi as unknown as never); } });
    render(<App />);
    const err = await screen.findByTestId('boot-error');
    expect(err.textContent).toContain('not initialized');
    fail = false;
    installApi();
    fireEvent.click(within(err).getByText('Retry'));
    await screen.findByTestId('camera-cam_2');
  });

  it('empty queue says so', async () => {
    await boot({}, []);
    expect(screen.getByText('No active incidents')).toBeTruthy();
  });
});

describe('live push channel', () => {
  it('room counts and camera status update from pushed state, without reloading', async () => {
    const { es } = await boot();
    act(() => es.emit('state', twin({ zone_corridor: 0, zone_vision_lab: 7 })));
    expect(screen.getByTestId('count-zone_vision_lab').textContent).toBe('7');
    act(() => es.emit('live', live('reconnecting')));
    expect(within(screen.getByTestId('camera-cam_3')).getByText('RECONNECTING')).toBeTruthy();
  });

  it('a pushed incident appears in the queue once, and an update replaces it (no duplicates)', async () => {
    const { es } = await boot();
    const fresh = incident({ incident_id: '01a10000-0000-7000-8000-0000000000aa', incident_type: 'UNAUTHORIZED_ENTRY' });
    act(() => es.emit('incident', fresh));
    act(() => es.emit('incident', fresh));
    expect(screen.getAllByTestId(`incident-${fresh.incident_id}`)).toHaveLength(1);
    expect(screen.getByTestId(`incident-${fresh.incident_id}`).textContent).toContain('NEW');
    act(() => es.emit('incident', { ...fresh, status: 'RESOLVED' }));
    expect(screen.queryByTestId(`incident-${fresh.incident_id}`)).toBeNull();     // left the "active" view
  });

  it('dropped connection: shows reconnecting, then resyncs incidents and state when it returns', async () => {
    const { es, server } = await boot();
    act(() => es.fail(0));
    expect(screen.getByTestId('conn').textContent).toContain('RECONNECTING');
    expect(screen.getByTestId('conn-banner').textContent).toContain('interrupted');
    const before = server.calls.filter((c) => c.path.startsWith('/api/twin/incidents')).length;
    await act(async () => { es.open(); });
    await waitFor(() => expect(server.calls.filter((c) => c.path.startsWith('/api/twin/incidents')).length).toBe(before + 1));
    expect(screen.queryByTestId('conn-banner')).toBeNull();
  });

  it('if the browser gives up (CLOSED), a new connection is created after a delay', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { es } = await boot();
      act(() => es.fail(2));
      expect(es.closed).toBe(true);
      await act(async () => { vi.advanceTimersByTime(3100); });
      expect(FakeEventSource.instances.length).toBe(2);
      expect(FakeEventSource.latest()).not.toBe(es);
    } finally {
      vi.useRealTimers();
    }
  });

  it('marks data stale when the channel is open but silent for more than 5 s', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { es } = await boot();
      act(() => es.emit('state', twin()));
      expect(screen.getByTestId('conn').textContent).toContain('LIVE');
      await act(async () => { vi.advanceTimersByTime(6500); });
      expect(screen.getByTestId('conn').textContent).toContain('STALE');
      expect(screen.getByTestId('conn-banner').textContent).toContain('out of date');
    } finally {
      vi.useRealTimers();
    }
  });

  it('unmounting closes the stream and removes every listener', async () => {
    const { es, unmount } = await boot();
    expect(es.listenerCount()).toBe(4);
    unmount();
    expect(es.closed).toBe(true);
    expect(es.listenerCount()).toBe(0);
    expect(FakeEventSource.instances.filter((x) => !x.closed)).toHaveLength(0);
  });
});

describe('camera wall', () => {
  it('a fresh frame is shown as live; a frame the server reports as old is shown as frozen', async () => {
    await boot({ 'GET /api/live/cameras/{id}/frame.jpg': (u: URL) => frame(u.pathname.includes('cam_3') ? 9000 : 150) });
    await waitFor(() => expect(screen.getByTestId('camera-cam_2').getAttribute('data-picture')).toBe('fresh'));
    await waitFor(() => expect(screen.getByTestId('camera-cam_3').getAttribute('data-picture')).toBe('none'));
    expect(within(screen.getByTestId('camera-cam_3')).getByText('Picture frozen')).toBeTruthy();
    expect(screen.getByTestId('camera-cam_2').querySelector('img')!.getAttribute('src')).toMatch(/^blob:test/);
  });
});

describe('incident lifecycle', () => {
  const inc = incident();
  it('status changes only after the backend confirms', async () => {
    let release: (v: unknown) => void = () => undefined;
    const { server } = await boot({
      'PATCH /api/twin/incidents/{id}': () => new Promise((r) => { release = r; }),
      'GET /api/twin/incidents/{id}/evidence': () => ({ incident_id: inc.incident_id, clips: [] }),
    });
    fireEvent.click(screen.getByTestId(`incident-${inc.incident_id}`));
    const detail = await screen.findByTestId('incident-detail');
    fireEvent.click(within(detail).getByTestId('action-RESOLVED'));
    expect(within(detail).getAllByText('Open').length).toBeGreaterThan(0);      // still open while pending
    expect(server.calls.find((c) => c.method === 'PATCH')?.body).toEqual({ status: 'RESOLVED' });
    await act(async () => { release({ ...inc, status: 'RESOLVED' }); });
    await waitFor(() => expect(within(detail).getByText('Resolved')).toBeTruthy());
    expect(within(detail).getByTestId('action-OPEN').textContent).toBe('Reopen');
  });

  it('a failed change explains why, keeps the old status, and can be retried', async () => {
    let attempts = 0;
    await boot({
      'PATCH /api/twin/incidents/{id}': () => { attempts += 1; if (attempts === 1) throw new HttpError(500, 'database is locked'); return { ...inc, status: 'INVESTIGATING' }; },
      'GET /api/twin/incidents/{id}/evidence': () => ({ incident_id: inc.incident_id, clips: [] }),
    });
    fireEvent.click(screen.getByTestId(`incident-${inc.incident_id}`));
    const detail = await screen.findByTestId('incident-detail');
    fireEvent.click(within(detail).getByTestId('action-INVESTIGATING'));
    const err = await within(detail).findByTestId('action-error');
    expect(err.textContent).toContain('database is locked');
    expect(err.textContent).toContain('still open');
    fireEvent.click(within(err).getByText('Retry'));
    await waitFor(() => expect(within(detail).getByText('Investigating')).toBeTruthy());
    expect(attempts).toBe(2);
  });
});

describe('evidence', () => {
  const inc = incident({ start_time: new Date(Date.now() - 600000).toISOString() });
  it('plays the real clip URL and seeks to the incident moment', async () => {
    await boot({
      'GET /api/twin/incidents/{id}/evidence': () => ({ incident_id: inc.incident_id, clips: [{
        camera_id: 'cam_3', video: 'clips/cam_3_x.mp4', key_frame: 'clips/cam_3_x_key5.jpg',
        video_url: '/api/evidence/clips/cam_3_x.mp4', key_frame_url: '/api/evidence/clips/cam_3_x_key5.jpg',
        clip_start: inc.start_time, clip_end: inc.start_time, frames: 100, incident_offset_s: 12.4, track: '4' }] }),
    }, [inc]);
    fireEvent.click(screen.getByTestId(`incident-${inc.incident_id}`));
    const clip = await screen.findByTestId('evidence-clip');
    const video = clip.querySelector('video')!;
    expect(video.getAttribute('src')).toBe('/api/evidence/clips/cam_3_x.mp4');
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBeCloseTo(10.4);
    fireEvent.error(video);
    expect(within(clip).getByText(/could not be played/)).toBeTruthy();
  });

  it('no clip: explains when clips exist; error: offers retry', async () => {
    let fail = true;
    await boot({ 'GET /api/twin/incidents/{id}/evidence': () => { if (fail) throw new HttpError(500, 'disk error'); return { incident_id: inc.incident_id, clips: [] }; } }, [inc]);
    fireEvent.click(screen.getByTestId(`incident-${inc.incident_id}`));
    const detail = await screen.findByTestId('incident-detail');
    await within(detail).findByText(/Evidence could not be loaded: disk error/);
    fail = false;
    fireEvent.click(within(detail).getByText('Retry'));
    expect((await within(detail).findByTestId('evidence-empty')).textContent).toContain('No video clip');
  });

  it('a recent serious incident shows "recording" while the clip is still being written', async () => {
    const recent = incident();
    await boot({ 'GET /api/twin/incidents/{id}/evidence': () => ({ incident_id: recent.incident_id, clips: [] }) }, [recent]);
    fireEvent.click(screen.getByTestId(`incident-${recent.incident_id}`));
    expect((await screen.findByTestId('evidence-empty')).textContent).toContain('Recording');
  });
});

describe('AI review', () => {
  const inc = incident({ start_time: new Date(Date.now() - 600000).toISOString() });
  const base = (over: Partial<Investigation>): Investigation => ({
    investigation_id: 'inv1', incident_id: inc.incident_id, created_at: new Date().toISOString(), status: 'COMPLETED',
    model_id: 'global.anthropic.claude-haiku-5-5', question: null, cost_usd: 0.009, latency_ms: 15000, error: null,
    result: {
      answer: 'Not enough evidence to identify the second track.',
      findings: [
        { statement: 'Track live:cam_3:4 entered 1.4 s after the badge holder.', kind: 'observation', event_ids: ['01a10000-0000-7000-8000-0000000000e1'] },
        { statement: 'This may be a second person or a tracking split.', kind: 'inference', event_ids: ['01a13ffc-4f00-0000-0000-000000000000'] },
      ],
      person_identity: 'Not identified.', missing_evidence: ['door contact sensor'], benign_explanations: ['a visitor escorted by the lead'],
    },
    verification: { passed: true, json_valid: true, ids_passed: true, unverified_ids: [], claims: { claims: [
      { claim_id: 'findings[0]', kind: 'observation', text: '', event_ids: [], verdict: 'supported' },
      { claim_id: 'findings[1]', kind: 'inference', text: '', event_ids: [], verdict: 'supported' },
    ], unsupported: 0, partially_supported: 0, unchecked: 0, passed: true } },
    ...over,
  });
  const open = async (investigations: Investigation[], extra = {}) => {
    await boot({
      'GET /api/twin/incidents/{id}/evidence': () => ({ incident_id: inc.incident_id, clips: [] }),
      'GET /api/twin/incidents/{id}/investigations': () => ({ ...emptyInvestigations(inc.incident_id), investigations }),
      ...extra,
    }, [inc]);
    fireEvent.click(screen.getByTestId(`incident-${inc.incident_id}`));
    const detail = await screen.findByTestId('incident-detail');
    fireEvent.click(within(detail).getByRole('tab', { name: /AI review/ }));
    return detail;
  };

  it('verified result: observations and inferences are separated', async () => {
    const d = await open([base({})]);
    expect((await within(d).findByTestId('verification-badge')).textContent).toBe('VERIFIED');
    expect(within(d).getByText('Observed in the evidence')).toBeTruthy();
    expect(within(d).getByText('Inferred (interpretation)')).toBeTruthy();
    expect(within(d).getByText(/Not identified/)).toBeTruthy();
  });

  it('failed verification is shown as NOT VERIFIED with reasons and the invented ID marked', async () => {
    const d = await open([base({ verification: { passed: false, json_valid: true, ids_passed: false,
      unverified_ids: ['01a13ffc-4f00-0000-0000-000000000000'],
      claims: { claims: [{ claim_id: 'findings[0]', kind: 'observation', text: '', event_ids: [], verdict: 'unsupported', judge_reason: 'cites no records' }],
        unsupported: 1, partially_supported: 0, unchecked: 0, passed: false } } })]);
    expect((await within(d).findByTestId('verification-badge')).textContent).toBe('NOT VERIFIED');
    const reasons = within(d).getByTestId('verification-reasons').textContent!;
    expect(reasons).toContain('do not exist in the evidence');
    expect(reasons).toContain('1 claim(s) not supported');
    expect(within(d).getByTitle(/not in the evidence/).className).toContain('line-through');
    expect(within(d).queryByText('VERIFIED')).toBeNull();
  });

  it('a failed run is labelled FAILED, and budget refusal from the server is explained', async () => {
    const d = await open([base({ status: 'FAILED', result: null, error: 'BedrockCredentialsError: token expired' })], {
      'POST /api/twin/incidents/{id}/investigate': () => { throw new HttpError(429, 'Daily investigation budget of $1.00 reached'); },
    });
    expect((await within(d).findByTestId('verification-badge')).textContent).toBe('FAILED');
    fireEvent.click(within(d).getByText('Investigate'));
    expect((await within(d).findByTestId('ai-run-error')).textContent).toContain('Daily AI budget reached');
  });

  it('no investigation yet: says so', async () => {
    const d = await open([]);
    expect((await within(d).findByTestId('ai-empty')).textContent).toContain('No AI investigation');
  });
});
