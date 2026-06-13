import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import axios from "axios";
import ReactMarkdown from "react-markdown";
import ForceGraph2D from "react-force-graph-2d";
import { ArrowLeft } from "lucide-react";

const API = "http://localhost:8000";

export default function DocumentView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("texto");
  const [docData, setDocData] = useState<any>(null);
  const [vectorData, setVectorData] = useState<any>(null);
  const [graphData, setGraphData] = useState<any>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);

    Promise.all([
      axios.get(`${API}/api/norms/${id}/text`).catch(() => null),
      axios.get(`${API}/api/norms/${id}/vectors`).catch(() => null),
      axios.get(`${API}/api/norms/${id}/graph`).catch(() => null),
    ]).then(([textRes, vecRes, graphRes]) => {
      if (textRes) setDocData(textRes.data);
      if (vecRes) setVectorData(vecRes.data);
      if (graphRes) setGraphData(graphRes.data);
      setLoading(false);
    });
  }, [id]);

  if (loading) {
    return <div style={{ padding: "2rem", color: "var(--text-muted)" }}>Cargando documento...</div>;
  }

  // Build readable text from the parsed data
  let rawText = "";
  if (docData) {
    if (docData.raw_text) {
      rawText = docData.raw_text;
    } else if (docData.consideraciones) {
      rawText = `## Consideraciones\n\n${docData.consideraciones}\n\n`;
      if (docData.resuelve) rawText += `## Resuelve\n\n${docData.resuelve}`;
    } else if (docData.articles && docData.articles.length > 0) {
      rawText = docData.articles
        .map((a: any) => `### Artículo ${a.number || "?"}\n\n${a.text}`)
        .join("\n\n---\n\n");
    }
    if (!rawText) rawText = "No hay texto procesado disponible para este documento.";
  }

  const catalog = docData?._catalog || {};

  return (
    <div className="document-view">
      {/* Left: Document Text */}
      <div className="doc-panel">
        <div className="panel-header">
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <button className="back-btn" onClick={() => navigate(-1)}>
              <ArrowLeft size={18} />
            </button>
            <h3>
              {catalog.tipo || "Documento"} {catalog.numero || id} de {catalog.anio || ""}
            </h3>
          </div>
        </div>
        <div className="panel-content markdown-body">
          <ReactMarkdown>{rawText}</ReactMarkdown>
        </div>
      </div>

      {/* Right: Tabs */}
      <div className="doc-panel">
        <div className="panel-header" style={{ padding: 0 }}>
          <div className="tabs">
            <button className={`tab ${tab === "texto" ? "active" : ""}`} onClick={() => setTab("texto")}>
              Metadatos
            </button>
            <button className={`tab ${tab === "vectores" ? "active" : ""}`} onClick={() => setTab("vectores")}>
              Chunks ({vectorData?.total_chunks || 0})
            </button>
            <button className={`tab ${tab === "grafo" ? "active" : ""}`} onClick={() => setTab("grafo")}>
              Grafo ({graphData?.nodes?.length || 0} nodos)
            </button>
          </div>
        </div>
        <div className="panel-content">
          {tab === "texto" && <MetadataTab docData={docData} catalog={catalog} />}
          {tab === "vectores" && <VectorsTab data={vectorData} />}
          {tab === "grafo" && <GraphTab data={graphData} />}
        </div>
      </div>
    </div>
  );
}

function MetadataTab({ docData, catalog }: { docData: any; catalog: any }) {
  return (
    <div>
      <h3 style={{ marginBottom: "1rem", fontSize: "0.9rem", color: "var(--text-main)" }}>
        Información del Documento
      </h3>
      <div className="metadata-grid">
        <span className="metadata-key">Tipo</span>
        <span className="metadata-value">{catalog.tipo}</span>
        <span className="metadata-key">Número</span>
        <span className="metadata-value">{catalog.numero}</span>
        <span className="metadata-key">Año</span>
        <span className="metadata-value">{catalog.anio}</span>
        <span className="metadata-key">Entidad</span>
        <span className="metadata-value">{catalog.entidad || "—"}</span>
        <span className="metadata-key">Vigencia</span>
        <span className="metadata-value">{catalog.vigencia || "—"}</span>
        <span className="metadata-key">Estado Scrape</span>
        <span className="metadata-value">{catalog.scrape_status}</span>
        <span className="metadata-key">SUIN ID</span>
        <span className="metadata-value">{catalog.suin_id}</span>
        {docData?.corte && (
          <>
            <span className="metadata-key">Corte</span>
            <span className="metadata-value">{docData.corte}</span>
          </>
        )}
        {docData?.sala && (
          <>
            <span className="metadata-key">Sala</span>
            <span className="metadata-value">{docData.sala}</span>
          </>
        )}
        {docData?.magistrado_ponente && (
          <>
            <span className="metadata-key">Ponente</span>
            <span className="metadata-value">{docData.magistrado_ponente}</span>
          </>
        )}
      </div>

      {docData?.citaciones && docData.citaciones.length > 0 && (
        <div style={{ marginTop: "1.5rem" }}>
          <h3 style={{ fontSize: "0.9rem", color: "var(--text-main)", marginBottom: "0.5rem" }}>
            Citaciones Extraídas ({docData.citaciones.length})
          </h3>
          <div className="citations-list">
            {docData.citaciones.map((c: string, i: number) => (
              <span key={i} className="citation-tag">{c}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function VectorsTab({ data }: { data: any }) {
  if (!data || !data.chunks || data.chunks.length === 0) {
    return <p style={{ color: "var(--text-muted)" }}>No hay chunks vectorizados para este documento.</p>;
  }

  return (
    <div>
      <p style={{ fontSize: "0.8rem", color: "var(--text-dim)", marginBottom: "1rem" }}>
        Estos son los fragmentos de texto que se almacenan como vectores en la base de datos para búsqueda semántica.
      </p>
      {data.chunks.map((chunk: any, idx: number) => (
        <div key={chunk.chunk_id || idx} className="vector-chunk" style={{ marginBottom: '1rem', padding: '1rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--bg-card)' }}>
          <div className="chunk-header" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', marginBottom: '0.5rem', borderBottom: '1px solid var(--border)', paddingBottom: '0.5rem', gap: '0.5rem' }}>
            <span style={{ fontWeight: 'bold', color: '#60a5fa' }}>{chunk.section || chunk.title || `Chunk ${idx+1}`}</span>
            <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.75rem', flexWrap: 'wrap' }}>
                {chunk.tipo && <span style={{ background: '#1e293b', color: '#e2e8f0', padding: '2px 8px', borderRadius: '12px' }}>{chunk.tipo} {chunk.numero} de {chunk.anio}</span>}
                {chunk.corte && <span style={{ background: '#1e293b', color: '#e2e8f0', padding: '2px 8px', borderRadius: '12px' }}>{chunk.corte}</span>}
                {chunk.magistrado && <span style={{ background: '#1e293b', color: '#e2e8f0', padding: '2px 8px', borderRadius: '12px' }}>MP: {chunk.magistrado}</span>}
                <span style={{ color: 'var(--text-muted)' }}>{chunk.char_count?.toLocaleString()} chars</span>
            </div>
          </div>
          <div className="chunk-text" style={{ fontSize: '0.9rem', lineHeight: '1.6', color: 'var(--text-main)', whiteSpace: 'pre-wrap' }}>{chunk.text}</div>
        </div>
      ))}
    </div>
  );
}

function GraphTab({ data }: { data: any }) {
  if (!data || !data.nodes || data.nodes.length === 0) {
    return <p style={{ color: "var(--text-muted)" }}>No hay datos de grafo para este documento.</p>;
  }

  const colorMap: Record<string, string> = {
    norma: "#3b82f6",
    titulo: "#14b8a6",
    capitulo: "#0ea5e9",
    articulo: "#10b981",
    sentencia: "#f59e0b",
    seccion: "#8b5cf6",
    modificacion: "#ef4444",
    citacion: "#a855f7",
    magistrado: "#ec4899",
  };

  return (
    <div style={{ height: "100%", width: "100%", minHeight: 500 }}>
      <ForceGraph2D
        graphData={data}
        nodeAutoColorBy="group"
        nodeColor={(node: any) => colorMap[node.group] || "#64748b"}
        nodeLabel={(node: any) => `${node.name}`}
        nodeVal={(node: any) => node.val || 3}
        linkLabel={(link: any) => link.label}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={1}
        linkColor={() => "rgba(148, 163, 184, 0.3)"}
        backgroundColor="#0f172a"
        width={undefined}
        height={500}
      />
    </div>
  );
}
