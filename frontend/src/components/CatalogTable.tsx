import { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";

const API = "http://localhost:8000";
const PAGE_SIZE = 30;

interface CatalogItem {
  id: number;
  suin_id: string;
  tipo: string;
  numero: string;
  anio: string;
  entidad: string;
  vigencia: string;
  scrape_status: string;
  corte: string;
}

export default function CatalogTable() {
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [tipoFilter, setTipoFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const navigate = useNavigate();

  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (tipoFilter) params.set("tipo", tipoFilter);
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(offset));

      const res = await axios.get(`${API}/api/catalog?${params}`);
      setItems(res.data.items);
      setTotal(res.data.total);
    } catch (e) {
      console.error("API error", e);
    }
  }, [search, tipoFilter, offset]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
    setOffset(0);
  };

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const statusColor = (s: string) => {
    if (s === "done") return { borderColor: "#10b981", color: "#10b981" };
    if (s === "pending") return { borderColor: "#f59e0b", color: "#f59e0b" };
    if (s === "error") return { borderColor: "#ef4444", color: "#ef4444" };
    return { borderColor: "#64748b", color: "#64748b" };
  };

  return (
    <div className="catalog-container">
      <h2 className="page-title">Directorio Legal</h2>

      <div className="search-bar">
        <input
          type="text"
          className="search-input"
          placeholder="Buscar por número, año o entidad (ej. C-274, 2013, Ley 100...)"
          value={search}
          onChange={handleSearch}
        />
        <select
          className="search-input"
          style={{ flex: "0 0 160px" }}
          value={tipoFilter}
          onChange={(e) => { setTipoFilter(e.target.value); setOffset(0); }}
        >
          <option value="">Todos los tipos</option>
          <option value="LEY">Leyes</option>
          <option value="DECRETO">Decretos</option>
          <option value="SENTENCIA">Sentencias</option>
          <option value="RESOLUCION">Resoluciones</option>
          <option value="ACTO LEGISLATIVO">Actos Legislativos</option>
        </select>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>Tipo</th>
            <th>Número</th>
            <th>Año</th>
            <th>Entidad / Corte</th>
            <th>Vigencia</th>
            <th>Estado</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} onClick={() => item.suin_id && navigate(`/norm/${item.suin_id}`)}>
              <td><span className="tag">{item.tipo}</span></td>
              <td style={{ fontWeight: 600, color: "var(--text-main)" }}>{item.numero}</td>
              <td>{item.anio}</td>
              <td>{item.entidad || item.corte || "—"}</td>
              <td style={{ color: "var(--text-dim)", fontSize: "0.8rem" }}>{item.vigencia || "—"}</td>
              <td>
                <span className="tag" style={statusColor(item.scrape_status)}>
                  {item.scrape_status}
                </span>
              </td>
            </tr>
          ))}
          {items.length === 0 && (
            <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--text-dim)", padding: "2rem" }}>
              Sin resultados
            </td></tr>
          )}
        </tbody>
      </table>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "0.8rem", color: "var(--text-dim)" }}>
          {total.toLocaleString()} normas encontradas
        </span>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            className="back-btn"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            <ChevronLeft size={18} />
          </button>
          <span style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>
            Página {page} de {totalPages || 1}
          </span>
          <button
            className="back-btn"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
