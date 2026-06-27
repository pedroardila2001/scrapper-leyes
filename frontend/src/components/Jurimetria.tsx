import { useEffect, useState } from "react";
import axios from "axios";

const API = "";

function fmt(n: number | null | undefined) {
  if (n == null) return "—";
  return n.toLocaleString("es-CO");
}

// Etiquetas legibles para el sentido del fallo.
const FALLO_LABEL: Record<string, string> = {
  DECLARA_INEXEQUIBLE: "Inexequible",
  INEXEQUIBLE: "Inexequible",
  DECLARA_EXEQUIBLE: "Exequible",
  EXEQUIBLE: "Exequible",
  DECLARA_EXEQUIBLE_CONDICIONADA: "Exequible condicionada",
  EXEQUIBLE_CONDICIONADA: "Exequible condicionada",
};
const FALLO_COLOR: Record<string, string> = {
  Inexequible: "#b91c1c",
  Exequible: "#166534",
  "Exequible condicionada": "#b7791f",
};

type Bucket = { valor: string; n: number };

function BarTable({ title, data, accent = "#1d6b53" }: { title: string; data: Bucket[]; accent?: string }) {
  const max = Math.max(1, ...data.map((d) => d.n));
  return (
    <section style={{ marginTop: "1.5rem" }}>
      <h3 style={{ fontFamily: "Lora, serif", margin: "0 0 0.5rem", borderBottom: "2px solid var(--border)", paddingBottom: "0.3rem" }}>
        {title}
      </h3>
      {data.length === 0 ? (
        <p style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>Sin datos.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
          {data.map((d) => (
            <div key={d.valor} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 240px) 1fr auto", gap: "0.6rem", alignItems: "center", fontSize: "0.85rem" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.valor}>{d.valor}</span>
              <div style={{ background: "var(--border)", borderRadius: "4px", height: "0.85rem", overflow: "hidden" }}>
                <div style={{ width: `${(d.n / max) * 100}%`, height: "100%", background: accent, borderRadius: "4px" }} />
              </div>
              <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--text-muted)", minWidth: "3ch", textAlign: "right" }}>{fmt(d.n)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default function Jurimetria() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [materia, setMateria] = useState("");

  const load = (m: string) => {
    axios
      .get(`${API}/api/jurimetria`, { params: { tipo: "SENTENCIA", top: 15, ...(m ? { materia: m } : {}) } })
      .then((r) => setData(r.data))
      .catch(() => setError("No se pudo cargar la jurimetría."));
  };

  useEffect(() => { load(""); }, []);

  if (error) return <div style={{ padding: "2rem", color: "var(--red)" }}>{error}</div>;
  if (!data) return <div style={{ padding: "2rem", color: "var(--text-muted)" }}>Cargando jurimetría…</div>;

  const fallo: Bucket[] = (data.sentido_del_fallo || []).reduce((acc: Bucket[], s: any) => {
    const label = FALLO_LABEL[s.sentido] || s.sentido;
    const ex = acc.find((a) => a.valor === label);
    if (ex) ex.n += s.n; else acc.push({ valor: label, n: s.n });
    return acc;
  }, []);

  return (
    <div style={{ paddingBottom: "2rem" }}>
      <h2 className="page-title">Jurimetría</h2>
      <p className="page-subtitle">
        Estadística <em>descriptiva</em> del corpus jurisprudencial. Distribuciones por corte, año,
        materia y magistrado (catálogo) + sentido del fallo (grafo).
      </p>

      {/* Resumen */}
      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", margin: "1.25rem 0" }}>
        {[
          { k: "Sentencias catalogadas", v: fmt(data.total_catalogadas), accent: "#1d6b53" },
          { k: "Con texto ingerido", v: fmt(data.con_texto_ingerido), accent: "#b7791f" },
          { k: "Fallos tipificados (grafo)", v: fmt(data.cobertura?.fallos_tipificados), accent: "#3f4651" },
        ].map((c) => (
          <div key={c.k} style={{ flex: "1 1 200px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "1rem 1.25rem" }}>
            <div style={{ fontSize: "1.7rem", fontWeight: 700, color: c.accent, fontFamily: "Lora, serif" }}>{c.v}</div>
            <div style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: "0.25rem" }}>{c.k}</div>
          </div>
        ))}
      </div>

      {/* Filtro por materia */}
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", margin: "0.5rem 0 1rem" }}>
        <input
          value={materia}
          onChange={(e) => setMateria(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load(materia)}
          placeholder="Filtrar por materia (p.ej. Tutela)…"
          style={{ flex: "1 1 280px", padding: "0.4rem 0.6rem", border: "1px solid var(--border)", borderRadius: "6px", fontSize: "0.85rem" }}
        />
        <button onClick={() => load(materia)} style={{ padding: "0.4rem 0.9rem", border: "1px solid var(--border)", borderRadius: "6px", background: "var(--bg-card)", cursor: "pointer" }}>Filtrar</button>
        {materia && <button onClick={() => { setMateria(""); load(""); }} style={{ padding: "0.4rem 0.6rem", border: "none", background: "none", color: "var(--text-muted)", cursor: "pointer" }}>limpiar</button>}
      </div>

      {/* Sentido del fallo (destacado, con colores) */}
      <section style={{ marginTop: "0.5rem" }}>
        <h3 style={{ fontFamily: "Lora, serif", margin: "0 0 0.5rem", borderBottom: "2px solid var(--border)", paddingBottom: "0.3rem" }}>Sentido del fallo</h3>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          {fallo.length === 0 ? <p style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>Sin fallos tipificados aún.</p> :
            fallo.map((f) => (
              <div key={f.valor} style={{ padding: "0.5rem 1rem", borderRadius: "8px", border: `1px solid ${FALLO_COLOR[f.valor] || "#999"}`, color: FALLO_COLOR[f.valor] || "#333" }}>
                <div style={{ fontSize: "1.5rem", fontWeight: 700, fontFamily: "Lora, serif" }}>{fmt(f.n)}</div>
                <div style={{ fontSize: "0.8rem" }}>{f.valor}</div>
              </div>
            ))}
        </div>
      </section>

      <BarTable title="Por año" data={data.por_anio} accent="#3f4651" />
      <BarTable title="Por materia" data={data.por_materia} accent="#1d6b53" />
      <BarTable title="Por magistrado ponente" data={data.por_magistrado} accent="#7c5e10" />

      <p style={{ marginTop: "1.5rem", fontSize: "0.78rem", color: "var(--text-muted)", borderTop: "1px solid var(--border)", paddingTop: "0.75rem" }}>
        ⚠️ {data.nota_metodologica}
      </p>
    </div>
  );
}
