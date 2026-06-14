import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import axios from "axios";
import ReactMarkdown from "react-markdown";
import ForceGraph2D from "react-force-graph-2d";
import { ArrowLeft } from "lucide-react";

const API = "http://localhost:8000";

/** Measure a container so ForceGraph2D gets an explicit width/height
 *  (it renders a blank canvas otherwise). */
function useElementSize() {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () =>
      setSize({ width: el.clientWidth, height: el.clientHeight || 500 });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, size };
}

const stripArticulo = (n: string) =>
  (n || "").replace(/^\s*art[ií]culo\s+/i, "").trim() || n;

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
      // Dedupe repeated articles (parser sometimes emits the same one twice)
      // and avoid the "Artículo Artículo" doubling.
      const seen = new Set<string>();
      rawText = docData.articles
        .filter((a: any) => {
          const k = a.canonical_id || a.art_id || a.number;
          if (seen.has(k)) return false;
          seen.add(k);
          return true;
        })
        .map((a: any) => {
          const num = stripArticulo(a.number || "?");
          const title = a.title ? ` — ${a.title}` : "";
          return `### Artículo ${num}${title}\n\n${a.text}`;
        })
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
      {data.chunks.map((chunk: any, idx: number) => {
        const vig = chunk.estado_vigencia;
        const vigStyle =
          vig === "derogado"
            ? { bg: "rgba(179,38,30,0.10)", fg: "#b3261e" }
            : vig === "modificado"
            ? { bg: "rgba(183,121,31,0.12)", fg: "#b7791f" }
            : { bg: "rgba(29,107,83,0.10)", fg: "#1d6b53" };
        return (
        <div key={chunk.chunk_id || idx} className="vector-chunk" style={{ marginBottom: '1rem', padding: '1rem', border: '1px solid var(--border)', borderRadius: 'var(--radius)', background: 'var(--bg-card)' }}>
          <div className="chunk-header" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', marginBottom: '0.5rem', borderBottom: '1px solid var(--border)', paddingBottom: '0.5rem', gap: '0.5rem' }}>
            <span style={{ fontWeight: 700, color: 'var(--accent)', fontFamily: 'var(--serif)' }}>{chunk.section || chunk.title || `Chunk ${idx+1}`}</span>
            <div style={{ display: 'flex', gap: '0.4rem', fontSize: '0.72rem', flexWrap: 'wrap', alignItems: 'center' }}>
                {vig && <span style={{ background: vigStyle.bg, color: vigStyle.fg, padding: '2px 8px', borderRadius: '12px', fontWeight: 600 }}>{vig}</span>}
                {chunk.tipo && <span style={{ background: 'var(--bg-hover)', color: 'var(--text-secondary)', padding: '2px 8px', borderRadius: '12px' }}>{chunk.tipo} {chunk.numero} de {chunk.anio}</span>}
                {chunk.magistrado && <span style={{ background: 'var(--bg-hover)', color: 'var(--text-secondary)', padding: '2px 8px', borderRadius: '12px' }}>MP: {chunk.magistrado}</span>}
                <span style={{ color: 'var(--text-dim)' }}>{chunk.char_count?.toLocaleString()} chars</span>
            </div>
          </div>
          <div className="chunk-text" style={{ fontSize: '0.9rem', lineHeight: '1.7', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{chunk.text}</div>
        </div>
        );
      })}
    </div>
  );
}

const GROUP_COLORS: Record<string, string> = {
  norma: "#1d6b53",
  titulo: "#0f766e",
  capitulo: "#0e7490",
  articulo: "#3f9d7e",
  seccion: "#6b4fa0",
  modificacion: "#b3261e",
  afecta: "#c2410c",
  sentencia: "#b7791f",
  sentencia_citada: "#b7791f",
  citacion: "#8a6d3b",
  magistrado: "#a23b72",
  resumen: "#6b7280",
};

const GROUP_LABELS: Record<string, string> = {
  norma: "Norma",
  titulo: "Título",
  capitulo: "Capítulo",
  articulo: "Artículo",
  seccion: "Sección",
  modificacion: "Modificada por",
  afecta: "Deroga / modifica a",
  sentencia: "Sentencia",
  sentencia_citada: "Sentencia citada",
  citacion: "Norma citada",
  magistrado: "Magistrado",
  resumen: "Resumen",
};

function GraphTab({ data }: { data: any }) {
  const { ref, size } = useElementSize();
  const fgRef = useRef<any>(null);

  if (!data || !data.nodes || data.nodes.length === 0) {
    return <p style={{ color: "var(--text-muted)" }}>No hay datos de grafo para este documento.</p>;
  }

  const presentGroups = Array.from(new Set(data.nodes.map((n: any) => n.group))) as string[];

  return (
    <div ref={ref} className="graph-wrap" style={{ minHeight: 500 }}>
      <div className="graph-legend">
        {presentGroups
          .filter((g) => GROUP_LABELS[g])
          .map((g) => (
            <div className="legend-item" key={g}>
              <span className="legend-dot" style={{ background: GROUP_COLORS[g] || "#94a3b8" }} />
              {GROUP_LABELS[g]}
            </div>
          ))}
      </div>
      {size.width > 0 && (
        <ForceGraph2D
          ref={fgRef}
          graphData={data}
          width={size.width}
          height={size.height || 500}
          nodeColor={(node: any) => GROUP_COLORS[node.group] || "#94a3b8"}
          nodeLabel={(node: any) => `${GROUP_LABELS[node.group] || node.group}: ${node.name}`}
          nodeVal={(node: any) => node.val || 3}
          nodeRelSize={5}
          linkLabel={(link: any) => link.label}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={1}
          linkColor={() => "rgba(60, 70, 81, 0.22)"}
          backgroundColor="#faf9f6"
          cooldownTicks={80}
          onEngineStop={() => fgRef.current?.zoomToFit(400, 40)}
        />
      )}
    </div>
  );
}
