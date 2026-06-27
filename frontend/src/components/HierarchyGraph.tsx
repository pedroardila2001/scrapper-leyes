import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { Layers, ChevronRight, ChevronDown, PieChart, ListOrdered, RefreshCw, ExternalLink } from "lucide-react";

const API = "";
const REFRESH_MS = 30_000;

interface Nivel {
  nivel: number;
  nombre: string;
  color: string;
  descripcion: string;
  count: number;
}

interface Nodo {
  id: string | number;
  tipo: string;
  numero?: string | number | null;
  anio?: number | null;
  nivel: number;
  rama?: string | null;
  suin_id?: string | null;
  name?: string | null;
}

interface RamaInfo {
  count: number;
  color?: string;
  label?: string;
}

interface HierarchyData {
  niveles: Nivel[];
  nodes: Nodo[];
  links_jerarquia: { source_nivel: number; target_nivel: number; type: string }[];
  total_nodos: number;
  rama_poder: Record<string, RamaInfo | number>;
}

// Paleta consistente para ramas del poder cuando el API no trae color
const RAMA_COLORS: Record<string, string> = {
  legislativo: "#1d6b53",
  ejecutivo: "#b7791f",
  judicial: "#6b4fa0",
  control: "#b3261e",
  territorial: "#558b2f",
  organismos: "#0277bd",
  otros: "#6b7280",
};

const RAMA_LABELS: Record<string, string> = {
  legislativo: "Legislativo",
  ejecutivo: "Ejecutivo",
  judicial: "Judicial",
  control: "Órganos de Control",
  territorial: "Entidades Territoriales",
  organismos: "Organismos",
  otros: "Otros",
};

function ramaColor(rama: string | null | undefined): string {
  if (!rama) return "#9aa0aa";
  const key = String(rama).toLowerCase();
  return RAMA_COLORS[key] ?? "#9aa0aa";
}

function ramaLabel(rama: string | null | undefined): string {
  if (!rama) return "Sin rama";
  const key = String(rama).toLowerCase();
  return RAMA_LABELS[key] ?? String(rama);
}

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

// Construye un slice de donut (path SVG) entre dos ángulos
function donutSlice(cx: number, cy: number, rOuter: number, rInner: number, startAngle: number, endAngle: number) {
  const startOuter = polarToCartesian(cx, cy, rOuter, endAngle);
  const endOuter = polarToCartesian(cx, cy, rOuter, startAngle);
  const startInner = polarToCartesian(cx, cy, rInner, endAngle);
  const endInner = polarToCartesian(cx, cy, rInner, startAngle);
  const largeArc = endAngle - startAngle <= 180 ? "0" : "1";
  return [
    "M", startOuter.x, startOuter.y,
    "A", rOuter, rOuter, 0, largeArc, 0, endOuter.x, endOuter.y,
    "L", endInner.x, endInner.y,
    "A", rInner, rInner, 0, largeArc, 1, startInner.x, startInner.y,
    "Z",
  ].join(" ");
}

export default function HierarchyGraph() {
  const [data, setData] = useState<HierarchyData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [view, setView] = useState<"nivel" | "rama">("nivel");
  const [expandedNivel, setExpandedNivel] = useState<number | null>(null);
  const [selectedNodo, setSelectedNodo] = useState<Nodo | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const navigate = useNavigate();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = () => {
    axios
      .get<HierarchyData>(`${API}/api/graph/hierarchy`)
      .then((res) => {
        setData(res.data);
        setLastUpdate(new Date());
        setError("");
      })
      .catch((err) => {
        console.error("Hierarchy error:", err);
        setError("No se pudo cargar la jerarquía. Verifica que la API esté activa.");
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchData();
    intervalRef.current = setInterval(fetchData, REFRESH_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Agregado por rama del poder
  const ramaStats = useMemo(() => {
    if (!data?.nodes) return [];
    const acc: Record<string, { count: number; color: string; label: string }> = {};
    for (const n of data.nodes) {
      const key = (n.rama || "otros").toLowerCase();
      if (!acc[key]) {
        acc[key] = { count: 0, color: ramaColor(n.rama), label: ramaLabel(n.rama) };
      }
      acc[key].count += 1;
    }
    // Enriquecer con datos de rama_poder si vienen del API
    if (data.rama_poder) {
      for (const [k, v] of Object.entries(data.rama_poder)) {
        const key = k.toLowerCase();
        const count = typeof v === "number" ? v : v?.count ?? 0;
        if (!acc[key]) {
          acc[key] = {
            count,
            color: (typeof v === "object" && v?.color) || ramaColor(k),
            label: (typeof v === "object" && v?.label) || ramaLabel(k),
          };
        } else if (typeof v === "object" && v?.color) {
          acc[key].color = v.color;
        }
      }
    }
    return Object.entries(acc)
      .map(([key, v]) => ({ key, ...v }))
      .sort((a, b) => b.count - a.count);
  }, [data]);

  // Nodos del nivel expandido
  const expandedNodes = useMemo(() => {
    if (!data?.nodes || expandedNivel == null) return [];
    return data.nodes.filter((n) => n.nivel === expandedNivel);
  }, [data, expandedNivel]);

  const handleSelectNivel = (nivel: number) => {
    setExpandedNivel((prev) => (prev === nivel ? null : nivel));
    setSelectedNodo(null);
  };

  const handleSelectNodo = (nodo: Nodo) => {
    setSelectedNodo(nodo);
  };

  const handleNavigate = (nodo: Nodo) => {
    if (nodo.suin_id) {
      navigate(`/norm/${nodo.suin_id}`);
    }
  };

  if (loading && !data) {
    return (
      <div className="dashboard">
        <p style={{ color: "var(--text-muted)" }}>Cargando jerarquía del ordenamiento jurídico...</p>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="dashboard">
        <div className="error-card">
          <h2>⚠️ API no disponible</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const maxCount = Math.max(...data.niveles.map((n) => n.count), 1);

  return (
    <div className="dashboard hierarchy-view">
      {/* Header */}
      <div className="hierarchy-head">
        <div>
          <h2 className="page-title">Jerarquía del Ordenamiento Jurídico</h2>
          <p className="page-subtitle">
            {data.total_nodos.toLocaleString("es-CO")} normas distribuidas en {data.niveles.length} niveles
            {lastUpdate && (
              <span className="last-update"> · actualizado {lastUpdate.toLocaleTimeString("es-CO")}</span>
            )}
          </p>
        </div>

        <div className="hierarchy-controls">
          <div className="view-toggle" role="tablist" aria-label="Modo de visualización">
            <button
              type="button"
              role="tab"
              aria-selected={view === "nivel"}
              className={view === "nivel" ? "active" : ""}
              onClick={() => setView("nivel")}
            >
              <ListOrdered size={15} /> Por nivel jerárquico
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "rama"}
              className={view === "rama" ? "active" : ""}
              onClick={() => setView("rama")}
            >
              <PieChart size={15} /> Por rama del poder
            </button>
          </div>
          <button className="refresh-btn" onClick={fetchData} title="Actualizar ahora">
            <RefreshCw size={15} /> Refrescar
          </button>
        </div>
      </div>

      <div className="hierarchy-layout">
        {/* Columna principal */}
        <div className="hierarchy-main">
          {view === "nivel" ? (
            <div className="chart-card hierarchy-card">
              <h3>Pirámide jerárquica</h3>
              <div className="pyramid">
                {data.niveles
                  .slice()
                  .sort((a, b) => a.nivel - b.nivel)
                  .map((nivel) => {
                    const widthPct = Math.max((nivel.count / maxCount) * 100, 6); // min 6% para visibilidad
                    const isExpanded = expandedNivel === nivel.nivel;
                    return (
                      <div key={nivel.nivel} className={`pyramid-row ${isExpanded ? "expanded" : ""}`}>
                        <button
                          type="button"
                          className="pyramid-bar"
                          style={{
                            width: `${widthPct}%`,
                            background: `linear-gradient(135deg, ${nivel.color}, ${nivel.color}dd)`,
                            borderColor: isExpanded ? nivel.color : "transparent",
                          }}
                          onClick={() => handleSelectNivel(nivel.nivel)}
                          title={nivel.descripcion}
                        >
                          <span className="pyramid-level">Nivel {nivel.nivel}</span>
                          <span className="pyramid-name">{nivel.nombre}</span>
                          <span className="pyramid-count">{nivel.count.toLocaleString("es-CO")}</span>
                          {isExpanded ? <ChevronDown size={14} className="pyramid-chev" /> : <ChevronRight size={14} className="pyramid-chev" />}
                        </button>
                      </div>
                    );
                  })}
              </div>
              <p className="pyramid-hint">
                <Layers size={13} /> El ancho de cada nivel refleja la cantidad de normas. Haz clic para explorar los documentos.
              </p>
            </div>
          ) : (
            <div className="chart-card hierarchy-card">
              <h3>Distribución por rama del poder público</h3>
              <RamaDonut stats={ramaStats} onSelect={(key) => {
                if (data.nodes) {
                  const found = data.nodes.find((n) => (n.rama || "otros").toLowerCase() === key);
                  if (found) {
                    setExpandedNivel(null);
                    setSelectedNodo(found);
                  }
                }
              }} />
            </div>
          )}

          {/* Lista de nodos del nivel expandido */}
          {view === "nivel" && expandedNivel != null && (
            <div className="chart-card nodes-card">
              <div className="nodes-head">
                <h3>
                  {data.niveles.find((n) => n.nivel === expandedNivel)?.nombre ?? `Nivel ${expandedNivel}`} ·{" "}
                  {expandedNodes.length.toLocaleString("es-CO")} documentos
                </h3>
                <button className="chip-clear" onClick={() => setExpandedNivel(null)}>Cerrar</button>
              </div>
              {expandedNodes.length === 0 ? (
                <p className="muted">No hay documentos ingeridos para este nivel.</p>
              ) : (
                <div className="nodes-list">
                  {expandedNodes.slice(0, 500).map((n) => {
                    const isSelected = selectedNodo?.id === n.id;
                    return (
                      <button
                        key={n.id}
                        type="button"
                        className={`node-row ${isSelected ? "selected" : ""}`}
                        onClick={() => handleSelectNodo(n)}
                        onDoubleClick={() => handleNavigate(n)}
                      >
                        <span className="node-tipo">{n.tipo}</span>
                        <span className="node-nombre">
                          {n.numero != null ? n.numero : ""}{n.anio != null ? ` de ${n.anio}` : ""}
                          {n.name && n.name !== `${n.tipo} ${n.numero ?? ""} ${n.anio ?? ""}`.trim() ? (
                            <span className="node-name-sub"> · {n.name}</span>
                          ) : null}
                        </span>
                        <span className="node-rama" style={{ background: ramaColor(n.rama), color: "#fff" }}>
                          {ramaLabel(n.rama)}
                        </span>
                      </button>
                    );
                  })}
                  {expandedNodes.length > 500 && (
                    <p className="muted nodes-truncated">Mostrando 500 de {expandedNodes.length.toLocaleString("es-CO")}.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Panel lateral derecho */}
        <aside className="hierarchy-side">
          <div className="chart-card side-card">
            <h3>Detalle del documento</h3>
            {selectedNodo ? (
              <div className="node-detail">
                <div className="detail-row">
                  <span className="detail-key">Tipo</span>
                  <span className="detail-val">{selectedNodo.tipo}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Número</span>
                  <span className="detail-val">{selectedNodo.numero ?? "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Año</span>
                  <span className="detail-val">{selectedNodo.anio ?? "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Nivel</span>
                  <span className="detail-val">Nivel {selectedNodo.nivel} · {data.niveles.find((n) => n.nivel === selectedNodo.nivel)?.nombre ?? "—"}</span>
                </div>
                <div className="detail-row">
                  <span className="detail-key">Rama</span>
                  <span className="detail-val">
                    <span className="node-rama" style={{ background: ramaColor(selectedNodo.rama), color: "#fff" }}>
                      {ramaLabel(selectedNodo.rama)}
                    </span>
                  </span>
                </div>
                {selectedNodo.name && (
                  <div className="detail-row">
                    <span className="detail-key">Nombre</span>
                    <span className="detail-val">{selectedNodo.name}</span>
                  </div>
                )}
                {selectedNodo.suin_id && (
                  <div className="detail-row">
                    <span className="detail-key">SUIN</span>
                    <span className="detail-val mono">{selectedNodo.suin_id}</span>
                  </div>
                )}
                <button
                  className="open-doc-btn"
                  disabled={!selectedNodo.suin_id}
                  onClick={() => handleNavigate(selectedNodo)}
                >
                  <ExternalLink size={15} /> {selectedNodo.suin_id ? "Abrir documento" : "Sin documento descargado"}
                </button>
              </div>
            ) : (
              <p className="muted">Selecciona un documento de la lista para ver su detalle y navegar a su ficha.</p>
            )}
          </div>

          {/* Resumen de niveles */}
          <div className="chart-card side-card">
            <h3>Resumen por nivel</h3>
            <div className="level-summary">
              {data.niveles.slice().sort((a, b) => a.nivel - b.nivel).map((n) => (
                <div key={n.nivel} className="summary-row">
                  <span className="summary-dot" style={{ background: n.color }} />
                  <span className="summary-name">{n.nombre}</span>
                  <span className="summary-count">{n.count.toLocaleString("es-CO")}</span>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

// ── Donut chart por rama del poder ────────────────────────────────────────
function RamaDonut({
  stats,
  onSelect,
}: {
  stats: { key: string; count: number; color: string; label: string }[];
  onSelect: (key: string) => void;
}) {
  const total = stats.reduce((s, r) => s + r.count, 0) || 1;
  const [hovered, setHovered] = useState<string | null>(null);

  const cx = 140;
  const cy = 140;
  const rOuter = 120;
  const rInner = 70;

  let angle = 0;
  const slices = stats.map((s) => {
    const sweep = (s.count / total) * 360;
    const start = angle;
    const end = angle + sweep;
    angle = end;
    return { ...s, start, end, mid: (start + end) / 2 };
  });

  const active = stats.find((s) => s.key === hovered);
  const activePct = active ? (active.count / total) * 100 : 0;

  return (
    <div className="rama-donut-wrap">
      <div className="rama-donut">
        <svg viewBox="0 0 280 280" width="280" height="280" role="img" aria-label="Distribución por rama del poder">
          {slices.map((s) => {
            // Evitar slice completo (360°) que rompe el path
            const end = s.end - s.start >= 360 ? s.end - 0.001 : s.end;
            const isHover = hovered === s.key;
            return (
              <path
                key={s.key}
                d={donutSlice(cx, cy, rOuter, rInner, s.start, end)}
                fill={s.color}
                opacity={hovered && !isHover ? 0.45 : 1}
                stroke="#fff"
                strokeWidth={2}
                style={{ cursor: "pointer", transition: "opacity 0.15s ease" }}
                onMouseEnter={() => setHovered(s.key)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onSelect(s.key)}
              >
                <title>{`${s.label}: ${s.count.toLocaleString("es-CO")} (${((s.count / total) * 100).toFixed(1)}%)`}</title>
              </path>
            );
          })}
          <text
            x={cx}
            y={cy - 6}
            textAnchor="middle"
            style={{ fontSize: "1.5rem", fontWeight: 700, fill: "var(--text-main)", fontFamily: "var(--serif)" }}
          >
            {active ? active.count.toLocaleString("es-CO") : total.toLocaleString("es-CO")}
          </text>
          <text
            x={cx}
            y={cy + 16}
            textAnchor="middle"
            style={{ fontSize: "0.78rem", fill: "var(--text-muted)" }}
          >
            {active ? `${active.label} · ${activePct.toFixed(1)}%` : "Total documentos"}
          </text>
        </svg>
      </div>

      <div className="rama-legend">
        {stats.map((s) => (
          <button
            key={s.key}
            type="button"
            className="rama-legend-row"
            onMouseEnter={() => setHovered(s.key)}
            onMouseLeave={() => setHovered(null)}
            onClick={() => onSelect(s.key)}
          >
            <span className="summary-dot" style={{ background: s.color }} />
            <span className="rama-legend-label">{s.label}</span>
            <span className="rama-legend-count">{s.count.toLocaleString("es-CO")}</span>
            <span className="rama-legend-pct">{((s.count / total) * 100).toFixed(1)}%</span>
          </button>
        ))}
      </div>
    </div>
  );
}
