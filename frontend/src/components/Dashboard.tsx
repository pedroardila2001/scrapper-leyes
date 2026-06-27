import { useEffect, useState } from "react";
import axios from "axios";
import { BookOpen, Scale, Database, FileCheck } from "lucide-react";

const API = "";

interface Stats {
  total_norms: number;
  leyes: number;
  sentencias: number;
  scraped_done: number;
  by_tipo: { tipo: string; count: number }[];
  by_scrape_status: { scrape_status: string; count: number }[];
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    axios
      .get(`${API}/api/stats`)
      .then((res) => setStats(res.data))
      .catch(() => setError("No se pudo conectar con la API. Asegúrate de ejecutar: uvicorn src.scrapper_leyes.api.main:app --port 8000"));
  }, []);

  if (error) {
    return (
      <div className="dashboard">
        <div className="error-card">
          <h2>⚠️ API no disponible</h2>
          <p>{error}</p>
          <code>uvicorn src.scrapper_leyes.api.main:app --reload --port 8000</code>
        </div>
      </div>
    );
  }

  if (!stats) return <div className="dashboard"><p>Cargando estadísticas...</p></div>;

  return (
    <div className="dashboard">
      <h2 className="page-title">Panel de Control</h2>
      <p className="page-subtitle">Vista general de tu bodega legal de IA</p>

      <div className="stats-grid">
        <StatCard icon={<BookOpen />} label="Total Normas" value={stats.total_norms} color="#3b82f6" />
        <StatCard icon={<Scale />} label="Leyes" value={stats.leyes} color="#10b981" />
        <StatCard icon={<Database />} label="Sentencias" value={stats.sentencias} color="#f59e0b" />
        <StatCard icon={<FileCheck />} label="Procesadas" value={stats.scraped_done} color="#8b5cf6" />
      </div>

      <div className="charts-row">
        <div className="chart-card">
          <h3>Distribución por Tipo</h3>
          <div className="bar-chart">
            {stats.by_tipo.map((item) => (
              <div key={item.tipo} className="bar-row">
                <span className="bar-label">{item.tipo}</span>
                <div className="bar-track">
                  <div
                    className="bar-fill"
                    style={{ width: `${Math.min((item.count / stats.total_norms) * 100, 100)}%` }}
                  />
                </div>
                <span className="bar-value">{item.count.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="chart-card">
          <h3>Estado del Pipeline</h3>
          <div className="bar-chart">
            {stats.by_scrape_status.map((item) => (
              <div key={item.scrape_status} className="bar-row">
                <span className="bar-label">{item.scrape_status}</span>
                <div className="bar-track">
                  <div
                    className="bar-fill status-bar"
                    data-status={item.scrape_status}
                    style={{ width: `${Math.min((item.count / stats.total_norms) * 100, 100)}%` }}
                  />
                </div>
                <span className="bar-value">{item.count.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: number; color: string }) {
  return (
    <div className="stat-card" style={{ borderTopColor: color }}>
      <div className="stat-icon" style={{ color }}>{icon}</div>
      <div className="stat-value">{value.toLocaleString()}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
