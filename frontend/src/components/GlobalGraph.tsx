import { useEffect, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { useNavigate } from "react-router-dom";

const API = "";

// Colores por nivel jerárquico
const NIVEL_COLORES: Record<number, string> = {
  1: "#1a237e", // Constitución
  2: "#283593", // Bloque Constitucional
  3: "#1565c0", // Leyes
  4: "#0277bd", // Decretos
  5: "#00838f", // Actos administrativos
  6: "#2e7d32", // Jurisprudencia
  7: "#558b2f", // Territorial
};

const NIVEL_NOMBRES: Record<number, string> = {
  1: "Constitución",
  2: "Bloque Constit.",
  3: "Leyes",
  4: "Decretos",
  5: "Actos Admin",
  6: "Jurisprudencia",
  7: "Territorial",
};

export default function GlobalGraph() {
  const fgRef = useRef<any>(null);
  const navigate = useNavigate();
  const [data, setData] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [linkFilter, setLinkFilter] = useState<string>("all");
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/graph/global?limit=3000`)
      .then((r) => r.json())
      .then((d) => {
        // Filtrar links por tipo si es necesario
        let links = d.links || [];
        let nodes = d.nodes || [];

        // Ordenar nodes por nivel para mejor layout
        nodes = nodes.sort((a: any, b: any) => (a.nivel || 5) - (b.nivel || 5));

        setData({ nodes, links });
        setStats(d.stats);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  // Filtrar links
  const filteredData = (() => {
    if (linkFilter === "all") return data;
    const links = data.links.filter((l: any) => {
      if (linkFilter === "cita") return l.label === "CITA_A";
      if (linkFilter === "control") return l.label === "CONTROLA" || l.label?.includes("DECLARA");
      if (linkFilter === "similar") return l.label === "SIMILAR_A";
      if (linkFilter === "jerarquia") return l.label === "PERTENECE_AL_NIVEL" || l.label === "REGLAMENTA" || l.label === "DESARROLLA";
      return true;
    });
    return { nodes: data.nodes, links };
  })();

  const handleNodeClick = (node: any) => {
    setSelectedNode(node);
  };

  const handleNodeDoubleClick = (node: any) => {
    if (node.suin_id) {
      navigate(`/norm/${node.suin_id}`);
    }
  };

  return (
    <div className="page-wrap">
      <h1 className="page-title">Grafo Jerárquico del Ordenamiento Jurídico</h1>
      <p className="muted">
        Red de conocimiento organizada por jerarquía normativa. Constitución arriba, actos administrativos al centro, jurisprudencia y normativa territorial abajo.
      </p>

      {/* Controles */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "12px", flexWrap: "wrap", alignItems: "center" }}>
        <button
          className="chip-clear"
          onClick={() => {
            const fg = fgRef.current;
            if (fg) fg.zoomToFit(400, 60);
          }}
        >
          🎯 Centrar
        </button>

        <span style={{ fontSize: "13px", color: "#666", marginRight: "4px" }}>Relaciones:</span>
        {[
          { key: "all", label: "Todas" },
          { key: "cita", label: "Citas" },
          { key: "control", label: "Control constitucional" },
          { key: "similar", label: "Similitud temática" },
          { key: "jerarquia", label: "Jerarquía" },
        ].map((opt) => (
          <button
            key={opt.key}
            className="chip-clear"
            style={{
              background: linkFilter === opt.key ? "var(--accent)" : "",
              color: linkFilter === opt.key ? "#fff" : "",
            }}
            onClick={() => setLinkFilter(opt.key)}
          >
            {opt.label}
          </button>
        ))}

        {stats && (
          <span style={{ marginLeft: "auto", fontSize: "12px", color: "#888" }}>
            {stats.ingeridas} nodos · {stats.aristas} aristas
          </span>
        )}
      </div>

      {/* Leyenda de niveles */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "12px", flexWrap: "wrap", padding: "8px 12px", background: "#faf9f6", borderRadius: "8px", border: "1px solid var(--border)" }}>
        {Object.entries(NIVEL_NOMBRES).map(([nivel, nombre]) => (
          <div key={nivel} style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "12px" }}>
            <div style={{ width: "12px", height: "12px", borderRadius: "50%", background: NIVEL_COLORES[Number(nivel)] }} />
            <span>{nombre}</span>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: "16px" }}>
        {/* Grafo */}
        <div style={{ flex: 1, height: "70vh", background: "#faf9f6", borderRadius: "12px", border: "1px solid var(--border)", overflow: "hidden", position: "relative" }}>
          {loading ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#999" }}>
              Cargando grafo jerárquico...
            </div>
          ) : (
            <ForceGraph2D
              ref={fgRef}
              graphData={filteredData}
              height={window.innerHeight * 0.65 || 600}
              nodeRelSize={5}
              nodeId="id"
              nodeLabel={(node: any) =>
                `<div style="background:#fff;padding:6px 10px;border-radius:6px;box-shadow:0 2px 8px rgba(0,0,0,0.15);font-size:12px">${node.name}<br/><span style="color:#888;font-size:10px">Nivel ${node.nivel || "?"} · ${node.rama || "?"}</span></div>`
              }
              nodeColor={(node: any) => node.color || NIVEL_COLORES[node.nivel] || "#999"}
              onNodeClick={handleNodeClick}
              onNodeDoubleClick={handleNodeDoubleClick}
              linkWidth={(link: any) => (link.label === "CONTROLA" ? 2 : 1)}
              linkColor={(link: any) => {
                if (link.label === "CONTROLA") return "rgba(198, 40, 40, 0.4)";
                if (link.label === "SIMILAR_A") return "rgba(29, 107, 83, 0.15)";
                if (link.label === "CITA_A") return "rgba(60, 70, 81, 0.12)";
                if (link.label === "PERTENECE_AL_NIVEL" || link.label === "REGLAMENTA") return "rgba(21, 101, 192, 0.3)";
                return "rgba(60, 70, 81, 0.12)";
              }}
              linkLabel={(link: any) => link.label}
              linkDirectionalArrowLength={4}
              linkDirectionalArrowRelPos={1}
              linkCurvature={(link: any) => (link.label === "SIMILAR_A" ? 0.3 : 0)}
              backgroundColor="#faf9f6"
              cooldownTicks={300}
              warmupTicks={100}
              enableNodeDrag={true}
              enableZoomInteraction={true}
              enablePanInteraction={true}
              minZoom={0.3}
              maxZoom={8}
              onEngineStop={() => {
                if (!initialized && fgRef.current) {
                  setInitialized(true);
                  const fg = fgRef.current;
                  // Layout jerárquico: posicionar Y según nivel
                  setTimeout(() => {
                    // Agrupar nodos por nivel en bandas horizontales
                    const nivelGroups: Record<number, any[]> = {};
                    fg.props.graphData.nodes.forEach((n: any) => {
                      const nv = n.nivel || 5;
                      if (!nivelGroups[nv]) nivelGroups[nv] = [];
                      nivelGroups[nv].push(n);
                    });
                    // El layout natural del force graph + colores ya muestra la jerarquía
                    fg.d3Force("charge").strength(-150);
                    fg.d3Force("link").distance((l: any) => {
                      if (l.label === "CONTROLA") return 80;
                      if (l.label === "PERTENECE_AL_NIVEL" || l.label === "REGLAMENTA") return 50;
                      if (l.label === "SIMILAR_A") return 60;
                      return 100;
                    });
                    fg.d3ReheatSimulation();
                    setTimeout(() => fg.zoomToFit(400, 80), 800);
                  }, 200);
                }
              }}
            />
          )}
          {/* Hint */}
          <div style={{ position: "absolute", bottom: "8px", left: "12px", fontSize: "11px", color: "#aaa" }}>
            Click = seleccionar · Doble-click = abrir · Scroll = zoom · Arrastra = mover
          </div>
        </div>

        {/* Panel lateral */}
        {selectedNode && (
          <div className="side-card" style={{ width: "280px", padding: "16px", background: "#fff", borderRadius: "12px", border: "1px solid var(--border)" }}>
            <h3 style={{ marginTop: 0, fontSize: "15px" }}>{selectedNode.name}</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "13px" }}>
              <div>
                <strong>Nivel jerárquico:</strong>{" "}
                <span style={{ color: selectedNode.color }}>
                  {NIVEL_NOMBRES[selectedNode.nivel] || `Nivel ${selectedNode.nivel}`}
                </span>
              </div>
              {selectedNode.rama && <div><strong>Rama:</strong> {selectedNode.rama}</div>}
              {selectedNode.tipo && <div><strong>Tipo:</strong> {selectedNode.tipo}</div>}
              {selectedNode.numero && <div><strong>Número:</strong> {selectedNode.numero}</div>}
              {selectedNode.anio && <div><strong>Año:</strong> {selectedNode.anio}</div>}
              <div>
                <strong>Estado:</strong>{" "}
                {selectedNode.ingerido ? "✅ Ingerido" : "⏳ Referenciado"}
              </div>

              {/* Badge de color del nivel */}
              <div style={{ marginTop: "8px" }}>
                <div style={{
                  display: "inline-block",
                  padding: "3px 10px",
                  borderRadius: "12px",
                  background: selectedNode.color,
                  color: "#fff",
                  fontSize: "11px",
                  fontWeight: 600,
                }}>
                  Nivel {selectedNode.nivel} · {NIVEL_NOMBRES[selectedNode.nivel]}
                </div>
              </div>

              {selectedNode.suin_id ? (
                <button
                  className="chip-clear"
                  style={{ marginTop: "10px", width: "100%", background: "var(--accent)", color: "#fff", textAlign: "center" }}
                  onClick={() => navigate(`/norm/${selectedNode.suin_id}`)}
                >
                  Abrir documento →
                </button>
              ) : (
                <div style={{ marginTop: "10px", padding: "8px", background: "#fff3e0", borderRadius: "6px", fontSize: "12px", color: "#e65100" }}>
                  ⏳ Documento pendiente de descarga
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
