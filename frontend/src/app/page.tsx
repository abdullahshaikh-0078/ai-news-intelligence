"use client";

import { useEffect, useState } from "react";
import { 
  Activity, 
  Cpu, 
  Database, 
  Layers, 
  Radio, 
  Sparkles, 
  CheckCircle2, 
  AlertCircle, 
  ExternalLink,
  ShieldAlert,
  Server,
  Terminal,
  Clock
} from "lucide-react";

interface HealthData {
  status: string;
  app_name: string;
  version: string;
  environment: string;
  timestamp: string;
  checks: Record<string, string>;
}

interface ReadinessData {
  status: string;
  checks: Record<string, string>;
  error?: string | null;
}

export default function Home() {
  const backendUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const [health, setHealth] = useState<HealthData | null>(null);
  const [ready, setReady] = useState<ReadinessData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDiagnostics = async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthRes, readyRes] = await Promise.all([
        fetch(`${backendUrl}/health`),
        fetch(`${backendUrl}/ready`),
      ]);

      if (healthRes.ok) {
        const healthData = await healthRes.json();
        setHealth(healthData);
      }

      if (readyRes.ok) {
        const readyData = await readyRes.json();
        setReady(readyData);
      } else {
        const errData = await readyRes.json().catch(() => ({}));
        setReady(errData);
      }
    } catch (err: any) {
      setError(err.message || "Failed to reach backend");
      setHealth(null);
      setReady(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDiagnostics();
  }, []);

  return (
    <div className="min-h-screen flex flex-col justify-between">
      {/* Top Navigation */}
      <header className="sticky top-0 z-50 glass-panel border-b border-white/5 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-xl bg-gradient-to-tr from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <div>
              <span className="font-semibold tracking-tight text-white text-lg">AI News Intelligence</span>
              <span className="ml-2.5 text-xs px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 font-mono">
                v0.1.0 • Phase 1
              </span>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <a
              href={`${backendUrl}/docs`}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-gray-400 hover:text-white flex items-center gap-1.5 transition-colors"
            >
              <Terminal className="w-3.5 h-3.5" />
              API Docs
              <ExternalLink className="w-3 h-3 opacity-60" />
            </a>
            <div className="h-4 w-px bg-white/10" />
            <button
              onClick={fetchDiagnostics}
              disabled={loading}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-200 border border-white/10 transition-all active:scale-95 disabled:opacity-50"
            >
              <Radio className={`w-3.5 h-3.5 ${loading ? "animate-pulse text-yellow-400" : "text-emerald-400"}`} />
              Check Status
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 py-12 flex-1 w-full space-y-12">
        {/* Hero Section */}
        <section className="text-center max-w-3xl mx-auto space-y-4 pt-6">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/5 border border-white/10 text-xs text-gray-300 backdrop-blur-sm">
            <span className="flex h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            Phase 1: Core Backend & Data Access Verified
          </div>
          <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-white bg-gradient-to-r from-white via-gray-200 to-gray-400 bg-clip-text text-transparent">
            Automated AI Intelligence & Signal Discovery
          </h1>
          <p className="text-gray-400 text-sm sm:text-base leading-relaxed">
            Multi-source ingestion, semantic deduplication, explainable multi-factor ranking, and AI-synthesized intelligence across research papers, official labs, and developer discourse.
          </p>
        </section>

        {/* Live System Health Status Card */}
        <section className="glass-card rounded-2xl p-6 relative overflow-hidden">
          <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />

          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-6 border-b border-white/5">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
                <Activity className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-white">System Diagnostics (Liveness & Readiness)</h2>
                <p className="text-xs text-gray-400 font-mono">GET /health (Liveness) • GET /ready (Readiness & PostgreSQL)</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* Liveness Probe */}
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-gray-400">Liveness:</span>
                {loading ? (
                  <span className="text-xs text-yellow-400 font-mono">Checking...</span>
                ) : health?.status === "healthy" ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <CheckCircle2 className="w-3 h-3" />
                    Healthy
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20">
                    <AlertCircle className="w-3 h-3" />
                    Unreachable
                  </span>
                )}
              </div>

              {/* Readiness Probe */}
              <div className="flex items-center gap-1.5">
                <span className="text-xs text-gray-400">Readiness:</span>
                {loading ? (
                  <span className="text-xs text-yellow-400 font-mono">Pinging...</span>
                ) : ready?.checks?.database === "connected" ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <CheckCircle2 className="w-3 h-3" />
                    Ready
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
                    <AlertCircle className="w-3 h-3" />
                    Degraded
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 pt-6">
            <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5">
              <div className="flex items-center gap-2 text-gray-400 text-xs mb-1">
                <Server className="w-3.5 h-3.5" />
                <span>Service</span>
              </div>
              <p className="text-sm font-medium text-white font-mono truncate">
                {health?.app_name || "ai-news-intelligence"}
              </p>
            </div>

            <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5">
              <div className="flex items-center gap-2 text-gray-400 text-xs mb-1">
                <Layers className="w-3.5 h-3.5" />
                <span>Environment</span>
              </div>
              <p className="text-sm font-medium text-white font-mono">
                {health?.environment || "development"}
              </p>
            </div>

            <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5">
              <div className="flex items-center gap-2 text-gray-400 text-xs mb-1">
                <Clock className="w-3.5 h-3.5" />
                <span>Timestamp</span>
              </div>
              <p className="text-xs font-medium text-white font-mono truncate">
                {health?.timestamp ? new Date(health.timestamp).toLocaleTimeString() : "--:--:--"}
              </p>
            </div>

            <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5">
              <div className="flex items-center gap-2 text-gray-400 text-xs mb-1">
                <Database className="w-3.5 h-3.5" />
                <span>Database / PostgreSQL</span>
              </div>
              <p className={`text-xs font-medium font-mono ${ready?.checks?.database === 'connected' ? 'text-emerald-400' : 'text-amber-400'}`}>
                {ready?.checks?.database === 'connected' ? 'PostgreSQL 18 Connected' : 'Disconnected'}
              </p>
            </div>
          </div>
        </section>

        {/* Architectural Layers & Roadmap Cards */}
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">Pipeline Architecture</h2>
            <span className="text-xs text-emerald-400 font-mono">Phase 1 Complete</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="glass-card rounded-xl p-5 space-y-3">
              <div className="w-8 h-8 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-400 flex items-center justify-center">
                <Radio className="w-4 h-4" />
              </div>
              <h3 className="font-medium text-white text-sm">Multi-Source Ingestion</h3>
              <p className="text-xs text-gray-400 leading-relaxed">
                Decoupled adapters for RSS/Atom, ArXiv preprints, official AI research labs, Hacker News, and YouTube transcripts.
              </p>
            </div>

            <div className="glass-card rounded-xl p-5 space-y-3">
              <div className="w-8 h-8 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400 flex items-center justify-center">
                <Cpu className="w-4 h-4" />
              </div>
              <h3 className="font-medium text-white text-sm">Semantic Deduplication</h3>
              <p className="text-xs text-gray-400 leading-relaxed">
                Vector similarity clustering + temporal heuristics. Consolidates 20+ coverage pieces into a single unified Story without losing provenance.
              </p>
            </div>

            <div className="glass-card rounded-xl p-5 space-y-3">
              <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center">
                <Sparkles className="w-4 h-4" />
              </div>
              <h3 className="font-medium text-white text-sm">AI Synthesis & Ranking</h3>
              <p className="text-xs text-gray-400 leading-relaxed">
                Structured takeaways, why it matters, technical depth scoring, and explainable multi-factor importance ranking.
              </p>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-white/5 py-6 px-6 text-center text-xs text-gray-500">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-2">
          <span>AI News Intelligence Platform &copy; 2026. All rights reserved.</span>
          <span className="font-mono text-gray-600">Built with FastAPI • SQLAlchemy 2.0 • Next.js • Tailwind CSS</span>
        </div>
      </footer>
    </div>
  );
}
