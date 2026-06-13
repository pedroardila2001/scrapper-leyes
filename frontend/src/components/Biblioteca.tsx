import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { ChevronRight, Search, Building2, Landmark, Scale } from "lucide-react";

const API = "http://localhost:8000";

interface Entidad { nombre: string; key: string; total: number; }
interface Cabeza { nombre: string; total: number; entidades: Entidad[]; }
interface Rama { nombre: string; total: number; sectores: Cabeza[]; }
interface Tree { total: number; ramas: Rama[]; }

const norm = (s: string) =>
  s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

const ramaIcon = (nombre: string) => {
  if (nombre.includes("Judicial")) return <Scale size={16} />;
  if (nombre.includes("Legislativa")) return <Landmark size={16} />;
  return <Building2 size={16} />;
};

export default function Biblioteca() {
  const [tree, setTree] = useState<Tree | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [openRamas, setOpenRamas] = useState<Set<string>>(new Set());
  const [openCabezas, setOpenCabezas] = useState<Set<string>>(new Set());
  const navigate = useNavigate();

  useEffect(() => {
    axios
      .get(`${API}/api/biblioteca`)
      .then((res) => setTree(res.data))
      .catch(() => setError("No se pudo cargar la biblioteca."));
  }, []);

  // Filter the tree by the search query (accent-insensitive).
  const filtered = useMemo(() => {
    if (!tree) return null;
    const q = norm(query.trim());
    if (!q) return tree;
    const ramas = tree.ramas
      .map((rama) => {
        const sectores = rama.sectores
          .map((sec) => {
            const matchSec = norm(sec.nombre).includes(q);
            const ents = matchSec
              ? sec.entidades
              : sec.entidades.filter((e) => norm(e.nombre).includes(q));
            return ents.length || matchSec ? { ...sec, entidades: ents } : null;
          })
          .filter(Boolean) as Cabeza[];
        const matchRama = norm(rama.nombre).includes(q);
        return sectores.length || matchRama
          ? { ...rama, sectores: matchRama ? rama.sectores : sectores }
          : null;
      })
      .filter(Boolean) as Rama[];
    return { total: tree.total, ramas };
  }, [tree, query]);

  const searching = query.trim().length > 0;
  const isRamaOpen = (n: string) => searching || openRamas.has(n);
  const isCabezaOpen = (n: string) => searching || openCabezas.has(n);

  const toggle = (set: Set<string>, key: string, setter: (s: Set<string>) => void) => {
    const next = new Set(set);
    next.has(key) ? next.delete(key) : next.add(key);
    setter(next);
  };

  const goEntidad = (rama: string, cabeza: string, e: Entidad) =>
    navigate(
      `/catalog?rama=${encodeURIComponent(rama)}&cabeza=${encodeURIComponent(cabeza)}` +
        `&entidad_norm=${encodeURIComponent(e.key)}&label=${encodeURIComponent(e.nombre)}`
    );

  const goCabeza = (rama: string, cabeza: string) =>
    navigate(
      `/catalog?rama=${encodeURIComponent(rama)}&cabeza=${encodeURIComponent(cabeza)}` +
        `&label=${encodeURIComponent(cabeza)}`
    );

  if (error) return <div className="biblioteca"><p className="muted">{error}</p></div>;
  if (!filtered) return <div className="biblioteca"><p className="muted">Cargando biblioteca…</p></div>;

  return (
    <div className="biblioteca">
      <header className="biblioteca-head">
        <h2 className="page-title">Biblioteca</h2>
        <p className="page-subtitle">
          {filtered.total.toLocaleString()} documentos organizados por entidad emisora.
        </p>
        <div className="lib-search">
          <Search size={16} className="lib-search-icon" />
          <input
            placeholder="Escribe para buscar entidad o sector…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </header>

      <div className="lib-tree">
        {filtered.ramas.map((rama) => (
          <div key={rama.nombre} className="lib-rama">
            <button className="lib-row lib-row-rama" onClick={() => toggle(openRamas, rama.nombre, setOpenRamas)}>
              <ChevronRight size={16} className={`chev ${isRamaOpen(rama.nombre) ? "open" : ""}`} />
              <span className="lib-icon">{ramaIcon(rama.nombre)}</span>
              <span className="lib-name">{rama.nombre}</span>
              <span className="lib-count">{rama.total.toLocaleString()}</span>
            </button>

            {isRamaOpen(rama.nombre) && (
              <div className="lib-children">
                {rama.sectores.map((sec) => {
                  const ckey = `${rama.nombre}/${sec.nombre}`;
                  return (
                    <div key={ckey} className="lib-cabeza">
                      <button className="lib-row lib-row-cabeza" onClick={() => toggle(openCabezas, ckey, setOpenCabezas)}>
                        <ChevronRight size={14} className={`chev ${isCabezaOpen(ckey) ? "open" : ""}`} />
                        <span className="lib-name">{sec.nombre}</span>
                        <span className="lib-count">{sec.total.toLocaleString()}</span>
                      </button>

                      {isCabezaOpen(ckey) && (
                        <div className="lib-children">
                          <button className="lib-row lib-row-all" onClick={() => goCabeza(rama.nombre, sec.nombre)}>
                            Ver todos ({sec.total.toLocaleString()})
                          </button>
                          {sec.entidades.map((e) => (
                            <button
                              key={e.key}
                              className="lib-row lib-row-entidad"
                              onClick={() => goEntidad(rama.nombre, sec.nombre, e)}
                            >
                              <span className="lib-name">{e.nombre}</span>
                              <span className="lib-count">{e.total.toLocaleString()}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
