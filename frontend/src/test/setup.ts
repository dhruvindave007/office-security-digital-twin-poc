// Test-only doubles: a controllable EventSource (the backend's push channel) and a routed fetch.
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

type Listener = (ev: MessageEvent) => void;

export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
  url: string;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  listeners: Record<string, Set<Listener>> = {};
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: Listener) { (this.listeners[type] ??= new Set()).add(fn); }
  removeEventListener(type: string, fn: Listener) { this.listeners[type]?.delete(fn); }
  close() { this.closed = true; this.readyState = 2; }

  // --- test controls -------------------------------------------------------------------------
  open() { this.readyState = 1; this.onopen?.(new Event('open')); }
  emit(type: string, data: unknown) {
    const ev = new MessageEvent(type, { data: JSON.stringify(data) });
    this.listeners[type]?.forEach((fn) => fn(ev));
  }
  fail(readyState: 0 | 2 = 0) { this.readyState = readyState; this.onerror?.(new Event('error')); }
  listenerCount() { return Object.values(this.listeners).reduce((n, s) => n + s.size, 0); }
  static latest() { return FakeEventSource.instances[FakeEventSource.instances.length - 1]; }
}

(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;

// jsdom gaps
let blobN = 0;
URL.createObjectURL = vi.fn(() => `blob:test/${++blobN}`) as unknown as typeof URL.createObjectURL;
URL.revokeObjectURL = vi.fn();
HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve()) as unknown as HTMLMediaElement['play'];
HTMLMediaElement.prototype.pause = vi.fn();

afterEach(() => {
  cleanup();
  FakeEventSource.instances = [];
  try { window.localStorage.clear(); } catch { /* storage unavailable */ }
});
