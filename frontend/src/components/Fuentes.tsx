import { useEffect, useState } from "react";
import axios from "axios";

const API = "http://localhost:8000";

const ESTADO_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  operativo: { bg: "#dcfce7", fg: "#166534", label: "operativo" },
  parcial: { bg: "#fef9c3", fg: "#854d0e", label: "parcial" },
  andamiaje: { bg: "#ffedd5", fg: "#9a3412", label: "andamiaje" },
  pendiente: { bg: "#fee2e2", fg: "#991b1b", label: "pendiente" },
};

function fmt(n: number | null | undefined) {
  if (n == null) return "—";
  return n.toLocaleString("es-CO");
}

export default function Fuentes() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    axios
      .get(`${API}/api/sources`)
      .then((r) => setData(r.data))
      .catch(() => setError("No se pudo cargar el mapa de fuentes."));
  }, []);

  if (error) return <div style={{ padding: "2rem", color: "var(--red)" }}>{error}</div>;
  if (!data) return <div style={{ padding: "2rem", color: "var(--text-muted)" }}>Cargando fuentes…</div>;

  const pct = data.total_disponible
    ? ((data.total_ingerido / data.total_disponible) * 100).toFixed(2)
    : "0";

  return (
    <div style={{ paddingBottom: "2rem" }}>
      <h2 className="page-title">Fuentes del Sistema Legal Colombiano</h2>
      <p className="page-subtitle">
        Universo mapeado y verificado por acceso. Volumen <em>disponible</em> = lo que existe
        para descubrir (<strong>medido</strong> = conteo real contra la fuente; <strong>~estimado</strong> =
        cifra de spike cuando el discoverer existe pero falta correr el conteo completo);
        {" "}<em>ingerido</em> = lo que ya tiene texto en el sistema.
      </p>

      {/* Resumen */}
      <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", margin: "1.25rem 0" }}>
        {[
          {
            k: "Documentos en el universo",
            v: fmt(data.total_disponible),
            sub: `${fmt(data.total_medido)} medidos · ~${fmt(data.total_estimado)} estimados`,
            accent: "#1d6b53",
          },
          {
            k: "Ingeridos (con texto)",
            v: `${fmt(data.total_ingerido)}`,
            sub: `${pct}% del universo`,
            accent: "#b7791f",
          },
          {
            k: "Fuentes con conector",
            v: `${data.fuentes_con_conector}/${data.total_fuentes}`,
            sub: `${data.fuentes_operativas} operativas/parciales · ${data.fuentes_con_conector - data.fuentes_operativas} en andamiaje`,
            accent: "#3f4651",
          },
        ].map((c) => (
          <div key={c.k} style={{ flex: "1 1 220px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: "1rem 1.25rem" }}>
            <div style={{ fontSize: "1.7rem", fontWeight: 700, color: c.accent, fontFamily: "Lora, serif" }}>{c.v}</div>
            <div style={{ color: "var(--text-muted)", fontSize: "0.85rem", marginTop: "0.25rem" }}>{c.k}</div>
            {c.sub && <div style={{ color: "var(--text-muted)", fontSize: "0.72rem", marginTop: "0.15rem", opacity: 0.85 }}>{c.sub}</div>}
          </div>
        ))}
      </div>

      {/* Capas */}
      {data.capas.map((capa: any) => (
        <section key={capa.capa} style={{ marginTop: "1.75rem" }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", borderBottom: "2px solid var(--border)", paddingBottom: "0.4rem" }}>
            <h3 style={{ fontFamily: "Lora, serif", margin: 0 }}>{capa.label}</h3>
            <span style={{ color: "var(--text-muted)", fontSize: "0.9rem" }}>{fmt(capa.volumen)} docs</span>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "0.5rem", fontSize: "0.9rem" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-muted)" }}>
                <th style={{ padding: "0.4rem 0.5rem" }}>Fuente</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Modo</th>
                <th style={{ padding: "0.4rem 0.5rem", textAlign: "right" }}>Disponible</th>
                <th style={{ padding: "0.4rem 0.5rem", textAlign: "right" }}>Ingerido</th>
                <th style={{ padding: "0.4rem 0.5rem" }}>Estado</th>
              </tr>
            </thead>
            <tbody>
              {capa.fuentes.map((f: any) => {
                const st = ESTADO_STYLE[f.estado] || ESTADO_STYLE.pendiente;
                return (
                  <tr key={f.key} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "0.45rem 0.5rem", fontWeight: 500 }}>{f.nombre}</td>
                    <td style={{ padding: "0.45rem 0.5rem", color: "var(--text-muted)" }}>{f.modo}</td>
                    <td style={{ padding: "0.45rem 0.5rem", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {f.volumen_disponible == null ? "—" : (
                        <span title={f.volumen_calidad === "estimado" ? "Estimado (sin conteo completo)" : "Medido contra la fuente"}>
                          {f.volumen_calidad === "estimado" && <span style={{ color: "var(--text-muted)" }}>~</span>}
                          {fmt(f.volumen_disponible)}
                        </span>
                      )}
                    </td>
                    <td style={{ padding: "0.45rem 0.5rem", textAlign: "right", fontVariantNumeric: "tabular-nums", color: f.ingerido ? "var(--text)" : "var(--text-muted)" }}>{f.ingerido || "—"}</td>
                    <td style={{ padding: "0.45rem 0.5rem" }}>
                      <span style={{ background: st.bg, color: st.fg, padding: "0.1rem 0.5rem", borderRadius: "999px", fontSize: "0.78rem", fontWeight: 600 }}>{st.label}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
