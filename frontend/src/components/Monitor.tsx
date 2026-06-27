import { useEffect, useRef, useState } from "react";
import axios from "axios";
import {
  Activity,
  Database,
  FileCheck,
  HardDrive,
  Cpu,
  AlertTriangle,
  Network,
  Layers,
} from "lucide-react";

const API = "";

// ── Tipos del payload /api/monitor ────────────────────────────────────────

interface SourceBucket {
  done: number;
  pending: number;
  error: number;
  total: number;
}

interface MonitorData {
  catalog: {
    total_docs: number;
    done: number;
    pending: number;
    error: number;
    by_source: Record<string, SourceBucket>;
    by_tipo: Record<string, SourceBucket>;
  };
  progress: {
    pct_scrapeado: number;
    rate_per_min: number;
    rate_window_min: number;
    recent_done: number;
    remaining: number;
    eta_seconds: number | null;
  };
  qdrant: {
    legal_corpus: number | null;
    legal_corpus__docreps: number | null;
  };
  neo4j: {
    nodes_by_label: Record<string, number>;
    relationships_total: number;
    ok: boolean;
    error?: string;
  };
  mapeo_660k: {
    fuentes: FuenteMapeo[];
    objetivo_total: number;
    actual_total: number;
  };
  calidad: {
    parsed_revisados: number;
    vacios: number;
    pct_vacios: number;
  };
  vm: {
    mem_total_mb?: number;
    mem_used_mb?: number;
    mem_available_mb?: number;
    mem_pct?: number;
    disk_total?: string;
    disk_used?: string;
    disk_avail?: string;
    disk_pct?: number;
    load_1?: number;
    load_5?: number;
    load_15?: number;
  };
  generated_at: string;
}

interface FuenteMapeo {
  fuente: string;
  nombre: string;
  actual: number;
  objetivo: number;
  faltante: number;
  pct_objetivo: number;
}

// ── Utilidades ────────────────────────────────────────────────────────────

const fmt = (n: number | null | undefined): string =>
  n == null ? "—" : n.toLocaleString("es-CO");

const pct = (part: number, whole: number): number =>
  whole > 0 ? Math.min((part / whole) * 100, 100) : 0;

function formatEta(seconds: number | null): string {
  if (seconds == null || seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h >= 24) {
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h`;
  }
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ── Componente ────────────────────────────────────────────────────────────

const POLL_MS = 10_000;

export default function Monitor() {
  const [data, setData] = useState<MonitorData | null>(null);
  const [error, setError] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = () => {
    axios
      .get<MonitorData>(`${API}/api/monitor`, { timeout: POLL_MS - 500 })
      .then((res) => {
        setData(res.data);
        setError("");
        setLastFetch(new Date());
      })
      .catch(() => setError("No se pudo conectar con /api/monitor"));
  };

  // Fetch inmediato al montar.
  useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-refresh toggle.
  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (autoRefresh) {
      timerRef.current = setInterval(fetchData, POLL_MS);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRefresh]);

  if (error && !data) {
    return (
      <div className="dashboard">
        <div className="error-card">
          <h2>⚠️ API no disponible</h2>
          <p>{error}</p>
          <code>GET /api/monitor</code>
        </div>
      </div>
    );
  }

  if (!data) {
    return <div className="dashboard"><p>Cargando monitor…</p></div>;
  }

  const { catalog, progress, qdrant, neo4j, mapeo_660k, calidad, vm } = data;
  const neo4jNodes = Object.values(neo4j.nodes_by_label).reduce((a, b) => a + b, 0);
  const ramColor = (vm.mem_pct ?? 0) > 85 ? "#b3261e" : (vm.mem_pct ?? 0) > 70 ? "#b7791f" : "#1d6b53";
  const diskColor = (vm.disk_pct ?? 0) > 85 ? "#b3261e" : (vm.disk_pct ?? 0) > 70 ? "#b7791f" : "#1d6b53";

  return (
    <div className="dashboard">
      {/* Header + controles */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem" }}>
        <div>
          <h2 className="page-title">Monitor de Ingesta</h2>
          <p className="page-subtitle">
            Progreso en tiempo real del corpus legal colombiano ·{" "}
            {lastFetch ? `Actualizado: ${lastFetch.toLocaleTimeString("es-CO")}` : "—"}
          </p>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer", fontWeight: 600, color: "var(--text-secondary)" }}>
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            style={{ width: 18, height: 18, cursor: "pointer" }}
          />
          Auto-refresh (10s)
        </label>
      </div>

      {/* Cards grandes */}
      <div className="stats-grid">
        <StatCard icon={<Layers />} label="Total Docs" value={fmt(catalog.total_docs)} color="#3b82f6" />
        <StatCard icon={<FileCheck />} label="Scrapeados" value={fmt(catalog.done)} color="#1d6b53" />
        <StatCard icon={<Activity />} label="Pendientes" value={fmt(catalog.pending)} color="#b7791f" />
        <StatCard icon={<Database />} label="Qdrant Chunks" value={fmt(qdrant.legal_corpus)} color="#6b4fa0" />
        <StatCard icon={<Network />} label="Neo4j Nodos" value={fmt(neo4j.ok ? neo4jNodes : null)} color="#f59e0b" />
        <StatCard
          icon={<Cpu />}
          label="% Completado"
          value={`${progress.pct_scrapeado.toFixed(1)}%`}
          color="#3b82f6"
        />
      </div>

      {/* Progreso + ETA */}
      <div className="chart-card" style={{ marginTop: "1.5rem" }}>
        <h3>Progreso del Pipeline</h3>
        <div className="bar-chart">
          <div className="bar-row">
            <span className="bar-label">Completado</span>
            <div className="bar-track">
              <div className="bar-fill status-bar" data-status="done" style={{ width: `${progress.pct_scrapeado}%` }} />
            </div>
            <span className="bar-value">{progress.pct_scrapeado.toFixed(1)}%</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: "2rem", marginTop: "0.75rem", flexWrap: "wrap", fontSize: "0.9rem", color: "var(--text-secondary)" }}>
          <span><strong>Tasa:</strong> {fmt(progress.rate_per_min)} docs/min (últ. {progress.rate_window_min} min)</span>
          <span><strong>Restantes:</strong> {fmt(progress.remaining)}</span>
          <span><strong>ETA:</strong> {formatEta(progress.eta_seconds)}</span>
          <span><strong>Errores:</strong> {fmt(catalog.error)}</span>
        </div>
      </div>

      {/* Barras por fuente */}
      <div className="charts-row">
        <div className="chart-card">
          <h3>Estado por Fuente</h3>
          <div className="bar-chart">
            {Object.entries(catalog.by_source)
              .sort((a, b) => b[1].total - a[1].total)
              .map(([src, b]) => (
                <div key={src} className="bar-row">
                  <span className="bar-label" title={src}>{src}</span>
                  <div className="bar-track" style={{ display: "flex", overflow: "hidden" }}>
                    <div className="bar-fill status-bar" data-status="done" title={`Done: ${b.done}`} style={{ width: `${pct(b.done, b.total)}%` }} />
                    <div className="bar-fill status-bar" data-status="pending" title={`Pending: ${b.pending}`} style={{ width: `${pct(b.pending, b.total)}%` }} />
                    <div className="bar-fill status-bar" data-status="error" title={`Error: ${b.error}`} style={{ width: `${pct(b.error, b.total)}%` }} />
                  </div>
                  <span className="bar-value">{fmt(b.total)}</span>
                </div>
              ))}
          </div>
          <div style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--text-muted)", display: "flex", gap: "1rem" }}>
            <span>🟢 done</span><span>🟡 pending</span><span>🔴 error</span>
          </div>
        </div>

        <div className="chart-card">
          <h3>Estado por Tipo</h3>
          <div className="bar-chart">
            {Object.entries(catalog.by_tipo)
              .sort((a, b) => b[1].total - a[1].total)
              .map(([tp, b]) => (
                <div key={tp} className="bar-row">
                  <span className="bar-label" title={tp}>{tp}</span>
                  <div className="bar-track" style={{ display: "flex", overflow: "hidden" }}>
                    <div className="bar-fill status-bar" data-status="done" style={{ width: `${pct(b.done, b.total)}%` }} />
                    <div className="bar-fill status-bar" data-status="pending" style={{ width: `${pct(b.pending, b.total)}%` }} />
                    <div className="bar-fill status-bar" data-status="error" style={{ width: `${pct(b.error, b.total)}%` }} />
                  </div>
                  <span className="bar-value">{fmt(b.total)}</span>
                </div>
              ))}
          </div>
        </div>
      </div>

      {/* Mapeo 660k */}
      <div className="chart-card" style={{ marginTop: "1.5rem" }}>
        <h3>Mapeo Universo Legal (~660k)</h3>
        <p className="page-subtitle" style={{ marginTop: 0 }}>
          {fmt(mapeo_660k.actual_total)} de {fmt(mapeo_660k.objetivo_total)} estimados ·{" "}
          {pct(mapeo_660k.actual_total, mapeo_660k.objetivo_total).toFixed(2)}% del universo
        </p>
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ width: "100%", fontSize: "0.9rem" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Fuente</th>
                <th style={{ textAlign: "right" }}>Actual</th>
                <th style={{ textAlign: "right" }}>Objetivo</th>
                <th style={{ textAlign: "center", width: "30%" }}>% Objetivo</th>
                <th style={{ textAlign: "right" }}>Faltante</th>
              </tr>
            </thead>
            <tbody>
              {mapeo_660k.fuentes.map((f) => (
                <tr key={f.fuente}>
                  <td style={{ textAlign: "left" }} title={f.nombre}>{f.nombre}</td>
                  <td style={{ textAlign: "right" }}>{fmt(f.actual)}</td>
                  <td style={{ textAlign: "right" }}>{fmt(f.objetivo)}</td>
                  <td>
                    <div className="bar-track" style={{ height: "1.1rem" }}>
                      <div className="bar-fill" style={{
                        width: `${Math.min(f.pct_objetivo, 100)}%`,
                        background: f.pct_objetivo >= 100 ? "linear-gradient(90deg, var(--green), #059669)"
                          : f.pct_objetivo >= 50 ? "linear-gradient(90deg, var(--amber), #d97706)"
                          : "linear-gradient(90deg, var(--red), #dc2626)",
                      }} />
                    </div>
                    <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>{f.pct_objetivo.toFixed(2)}%</span>
                  </td>
                  <td style={{ textAlign: "right" }}>{fmt(f.faltante)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recursos VM + Calidad */}
      <div className="charts-row">
        <div className="chart-card">
          <h3><HardDrive className="icon" size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />Recursos VM</h3>
          {/* RAM */}
          <div style={{ marginBottom: "1rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: "0.9rem" }}>
              <span>RAM</span>
              <span style={{ color: "var(--text-muted)" }}>
                {fmt(vm.mem_used_mb)} / {fmt(vm.mem_total_mb)} MB ({vm.mem_pct?.toFixed(1)}%)
              </span>
            </div>
            <div className="bar-track" style={{ height: "1.4rem", borderRadius: 6 }}>
              <div style={{
                width: `${vm.mem_pct ?? 0}%`,
                height: "100%",
                borderRadius: 6,
                background: ramColor,
                transition: "width 0.5s ease",
              }} />
            </div>
          </div>
          {/* Disco */}
          <div style={{ marginBottom: "1rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: "0.9rem" }}>
              <span>Disco (/)</span>
              <span style={{ color: "var(--text-muted)" }}>
                {vm.disk_used} / {vm.disk_total} ({vm.disk_pct}%)
              </span>
            </div>
            <div className="bar-track" style={{ height: "1.4rem", borderRadius: 6 }}>
              <div style={{
                width: `${vm.disk_pct ?? 0}%`,
                height: "100%",
                borderRadius: 6,
                background: diskColor,
                transition: "width 0.5s ease",
              }} />
            </div>
          </div>
          {/* Load average */}
          <div style={{ fontSize: "0.9rem", color: "var(--text-secondary)" }}>
            <strong>Load average:</strong>{" "}
            <span style={{ color: (vm.load_1 ?? 0) > 8 ? "#b3261e" : "inherit" }}>{vm.load_1?.toFixed(2)}</span>{" "}
            / {vm.load_5?.toFixed(2)} / {vm.load_15?.toFixed(2)}
            <span style={{ color: "var(--text-muted)", marginLeft: 8 }}>(1/5/15 min)</span>
          </div>
        </div>

        <div className="chart-card">
          <h3><AlertTriangle className="icon" size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />Calidad del Corpus</h3>
          <div className="stats-grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 0 }}>
            <div className="stat-card" style={{ borderTopColor: "#3b82f6" }}>
              <div className="stat-value">{fmt(calidad.parsed_revisados)}</div>
              <div className="stat-label">Parsed revisados</div>
            </div>
            <div className="stat-card" style={{ borderTopColor: calidad.pct_vacios > 10 ? "#b3261e" : "#b7791f" }}>
              <div className="stat-value">{fmt(calidad.vacios)}</div>
              <div className="stat-label">Corpus vacíos</div>
            </div>
          </div>
          <div style={{ marginTop: "1rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: "0.9rem" }}>
              <span>% Vacíos (0 artículos y 0 texto)</span>
              <span style={{ color: calidad.pct_vacios > 10 ? "#b3261e" : "inherit" }}>
                {calidad.pct_vacios.toFixed(2)}%
              </span>
            </div>
            <div className="bar-track" style={{ height: "1.4rem", borderRadius: 6 }}>
              <div style={{
                width: `${Math.min(calidad.pct_vacios, 100)}%`,
                height: "100%",
                borderRadius: 6,
                background: calidad.pct_vacios > 10 ? "#b3261e" : "#b7791f",
                transition: "width 0.5s ease",
              }} />
            </div>
          </div>
        </div>
      </div>

      {/* Stores detail */}
      <div className="charts-row">
        <div className="chart-card">
          <h3><Database className="icon" size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />Qdrant (Vector Store)</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem", fontSize: "0.95rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>legal_corpus</span>
              <strong>{fmt(qdrant.legal_corpus)} chunks</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>legal_corpus__docreps</span>
              <strong>{fmt(qdrant.legal_corpus__docreps)} docreps</strong>
            </div>
          </div>
        </div>

        <div className="chart-card">
          <h3><Network className="icon" size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />Neo4j (Knowledge Graph)</h3>
          {neo4j.ok ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", fontSize: "0.95rem" }}>
              {Object.entries(neo4j.nodes_by_label).map(([lb, n]) => (
                <div key={lb} style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>{lb}</span>
                  <strong>{fmt(n)} nodos</strong>
                </div>
              ))}
              <div style={{ display: "flex", justifyContent: "space-between", borderTop: "1px solid var(--border)", paddingTop: "0.4rem", marginTop: "0.2rem" }}>
                <span>Relaciones</span>
                <strong>{fmt(neo4j.relationships_total)}</strong>
              </div>
            </div>
          ) : (
            <p style={{ color: "#b3261e", fontSize: "0.9rem" }}>⚠️ Neo4j no disponible: {neo4j.error}</p>
          )}
        </div>
      </div>

      <p style={{ textAlign: "center", color: "var(--text-muted)", fontSize: "0.8rem", marginTop: "1rem" }}>
        Generado: {data.generated_at}
      </p>
    </div>
  );
}

// ── StatCard reutilizable (igual a Dashboard) ─────────────────────────────

function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: string; color: string }) {
  return (
    <div className="stat-card" style={{ borderTopColor: color }}>
      <div className="stat-icon" style={{ color }}>{icon}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
