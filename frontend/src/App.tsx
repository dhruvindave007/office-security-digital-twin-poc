import React, { useEffect, useState } from 'react';
import {
  Shield,
  Activity,
  Camera,
  Layers,
  Sparkles,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Clock,
  Server,
} from 'lucide-react';

interface HealthStatus {
  status: string;
  service: string;
  version: string;
  environment: string;
}

export default function App(): React.ReactElement {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);

  const fetchHealth = async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/health');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: HealthStatus = await response.json();
      setHealth(data);
      setLastChecked(new Date());
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setError(msg);
      setHealth(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* Navigation Bar */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-indigo-600/20 border border-indigo-500/30 rounded-lg text-indigo-400">
              <Shield className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-white flex items-center gap-2">
                Office Vision Intelligence
                <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-medium">
                  M0 Foundation
                </span>
              </h1>
              <p className="text-xs text-slate-400">Digital Twin & CCTV Security Intelligence</p>
            </div>
          </div>

          <div className="flex items-center space-x-3">
            <button
              onClick={fetchHealth}
              disabled={loading}
              className="inline-flex items-center space-x-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-indigo-400' : ''}`} />
              <span>{loading ? 'Checking...' : 'Refresh Health'}</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* System Health Card */}
        <section className="bg-slate-900/80 border border-slate-800 rounded-xl p-6 shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-800 pb-4 mb-4">
            <div className="flex items-center space-x-2.5">
              <Activity className="w-5 h-5 text-indigo-400" />
              <h2 className="text-base font-semibold text-slate-200">System Gateway & Health Status</h2>
            </div>
            <div className="flex items-center space-x-2 text-xs text-slate-400">
              <Clock className="w-3.5 h-3.5" />
              <span>
                Last check: {lastChecked ? lastChecked.toLocaleTimeString() : 'Never'}
              </span>
            </div>
          </div>

          {error ? (
            <div className="flex items-center space-x-3 p-4 rounded-lg bg-red-950/40 border border-red-800 text-red-300">
              <AlertCircle className="w-5 h-5 text-red-400 shrink-0" />
              <div>
                <p className="font-medium text-sm">Backend Health Check Failed</p>
                <p className="text-xs text-red-400/90">{error}</p>
              </div>
            </div>
          ) : health ? (
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">Service Status</span>
                <div className="mt-2 flex items-center space-x-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span className="text-sm font-semibold capitalize text-emerald-400">{health.status}</span>
                </div>
              </div>

              <div className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">Backend Service</span>
                <div className="mt-2 flex items-center space-x-2">
                  <Server className="w-4 h-4 text-indigo-400" />
                  <span className="text-sm font-semibold text-slate-200">{health.service}</span>
                </div>
              </div>

              <div className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">Version</span>
                <div className="mt-2">
                  <span className="text-sm font-mono font-semibold text-slate-300">{health.version}</span>
                </div>
              </div>

              <div className="p-4 rounded-lg bg-slate-950/60 border border-slate-800/80">
                <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">Environment</span>
                <div className="mt-2">
                  <span className="text-xs font-semibold px-2.5 py-1 rounded bg-slate-800 border border-slate-700 text-indigo-300 font-mono">
                    {health.environment}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-400">Loading system status...</div>
          )}
        </section>

        {/* Modular Roadmap Placeholders (M0 Architecture Freeze Shell) */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* CCTV Vision Module */}
          <div className="p-5 rounded-xl bg-slate-900/50 border border-slate-800/80 flex flex-col justify-between">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="p-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg text-emerald-400">
                  <Camera className="w-5 h-5" />
                </div>
                <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                  M2 Module
                </span>
              </div>
              <h3 className="font-semibold text-sm text-slate-200">CCTV Vision Stream Engine</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Local low-latency vision pipeline for video clips and camera feeds. Architectural placeholder established.
              </p>
            </div>
            <div className="mt-4 pt-3 border-t border-slate-800/50 text-[11px] text-slate-500 flex items-center justify-between">
              <span>Pipeline: Dormant</span>
              <span className="text-slate-400 font-mono">backend/src/vision/</span>
            </div>
          </div>

          {/* Digital Twin State Module */}
          <div className="p-5 rounded-xl bg-slate-900/50 border border-slate-800/80 flex flex-col justify-between">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="p-2 bg-sky-500/10 border border-sky-500/20 rounded-lg text-sky-400">
                  <Layers className="w-5 h-5" />
                </div>
                <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                  M3 Module
                </span>
              </div>
              <h3 className="font-semibold text-sm text-slate-200">Digital Twin State Graph</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Deterministic office zone, badge, and person tracking engine. C# POC authoritative during M0 freeze.
              </p>
            </div>
            <div className="mt-4 pt-3 border-t border-slate-800/50 text-[11px] text-slate-500 flex items-center justify-between">
              <span>State: Authoritative Frozen</span>
              <span className="text-slate-400 font-mono">backend/src/twin/</span>
            </div>
          </div>

          {/* Bedrock Multimodal AI Module */}
          <div className="p-5 rounded-xl bg-slate-900/50 border border-slate-800/80 flex flex-col justify-between">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="p-2 bg-purple-500/10 border border-purple-500/20 rounded-lg text-purple-400">
                  <Sparkles className="w-5 h-5" />
                </div>
                <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                  M1 & M4 Module
                </span>
              </div>
              <h3 className="font-semibold text-sm text-slate-200">Bedrock Intelligence & Copilot</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Evidence-grounded asynchronous investigation engine via Amazon Bedrock Converse API with structured tooling.
              </p>
            </div>
            <div className="mt-4 pt-3 border-t border-slate-800/50 text-[11px] text-slate-500 flex items-center justify-between">
              <span>Status: Awaiting M1 Activation</span>
              <span className="text-slate-400 font-mono">backend/src/ai/</span>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800 py-4 bg-slate-950/80 text-center text-xs text-slate-500">
        Office Vision Intelligence Platform • Foundation M0 • All local & cloud boundaries preserved
      </footer>
    </div>
  );
}
