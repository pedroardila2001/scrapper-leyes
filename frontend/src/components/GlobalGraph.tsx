import { useEffect, useState } from "react";
import axios from "axios";
import ForceGraph2D from "react-force-graph-2d";
import { useNavigate } from "react-router-dom";

const API = "http://localhost:8000";

export default function GlobalGraph() {
  const [data, setData] = useState<any>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    axios
      .get(`${API}/api/graph/global?limit=2000`)
      .then((res) => {
        setData(res.data);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Global graph error:", err);
        setError("Error al cargar la red global. Asegúrate de que la API y Neo4j estén funcionando.");
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div style={{ padding: "2rem", color: "var(--text-muted)" }}>Cargando la red global (esto puede tardar unos segundos)...</div>;
  }

  if (error) {
    return <div style={{ padding: "2rem", color: "var(--red)" }}>{error}</div>;
  }

  const colorMap: Record<string, string> = {
    norma: "#3b82f6",
    sentencia: "#f59e0b",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%" }}>
      <div style={{ paddingBottom: "1rem" }}>
        <h2 className="page-title">Red de Conocimiento Global</h2>
        <p className="page-subtitle">
          Exploración macroscópica de {data.nodes.length} normas y sentencias con {data.links.length} interconexiones. Haz clic en un nodo para ver su red específica.
        </p>
      </div>
      <div style={{ flex: 1, background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", overflow: "hidden" }}>
        <ForceGraph2D
          graphData={data}
          nodeAutoColorBy="group"
          nodeColor={(node: any) => colorMap[node.group] || "#64748b"}
          nodeLabel={(node: any) => `${node.name}`}
          nodeVal={(node: any) => node.val || 5}
          linkLabel={(link: any) => link.label}
          linkDirectionalArrowLength={3.5}
          linkDirectionalArrowRelPos={1}
          linkColor={() => "rgba(148, 163, 184, 0.4)"}
          backgroundColor="#0f172a"
          onNodeClick={(node: any) => {
            if (node.suin_id) {
              navigate(`/norm/${node.suin_id}`);
            } else {
              alert(`Este nodo (${node.name}) no tiene un documento descargado todavía.`);
            }
          }}
        />
      </div>
    </div>
  );
}
