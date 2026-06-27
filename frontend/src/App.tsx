import { BrowserRouter as Router, Routes, Route, Link, useLocation } from "react-router-dom";
import CatalogTable from "./components/CatalogTable";
import DocumentView from "./components/DocumentView";
import Dashboard from "./components/Dashboard";
import GlobalGraph from "./components/GlobalGraph";
import HierarchyGraph from "./components/HierarchyGraph";
import Biblioteca from "./components/Biblioteca";
import Fuentes from "./components/Fuentes";
import Jurimetria from "./components/Jurimetria";
import Monitor from "./components/Monitor";
import DeepAgent from "./components/DeepAgent";
import { Scale, LayoutDashboard, BookOpen, Network, Library, Database, BarChart3, Activity, Brain, Layers } from "lucide-react";
import "./App.css";

function Sidebar() {
  const location = useLocation();
  const isActive = (path: string) => location.pathname === path ? "nav-item active" : "nav-item";

  return (
    <aside className="sidebar">
      <div className="brand">
        <Scale className="logo" size={28} />
        <div>
          <h1>Cerebro Legal</h1>
          <span className="brand-sub">IA Jurídica Colombia</span>
        </div>
      </div>
      <nav>
        <Link to="/" className={isActive("/")}>
          <LayoutDashboard className="icon" size={18} />
          <span>Dashboard</span>
        </Link>
        <Link to="/biblioteca" className={isActive("/biblioteca")}>
          <Library className="icon" size={18} />
          <span>Biblioteca</span>
        </Link>
        <Link to="/catalog" className={isActive("/catalog")}>
          <BookOpen className="icon" size={18} />
          <span>Directorio Legal</span>
        </Link>
        <Link to="/global" className={isActive("/global")}>
          <Network className="icon" size={18} />
          <span>Red Global</span>
        </Link>
        <Link to="/hierarchy" className={isActive("/hierarchy")}>
          <Layers className="icon" size={18} />
          <span>Jerarquía</span>
        </Link>
        <Link to="/fuentes" className={isActive("/fuentes")}>
          <Database className="icon" size={18} />
          <span>Fuentes</span>
        </Link>
        <Link to="/jurimetria" className={isActive("/jurimetria")}>
          <BarChart3 className="icon" size={18} />
          <span>Jurimetría</span>
        </Link>
        <Link to="/monitor" className={isActive("/monitor")}>
          <Activity className="icon" size={18} />
          <span>Monitor</span>
        </Link>
        <Link to="/agent" className={isActive("/agent")}>
          <Brain className="icon" size={18} />
          <span>Agente IA</span>
        </Link>
      </nav>
      <div className="sidebar-footer">
        <p>Bodega Legal v1.0</p>
      </div>
    </aside>
  );
}

function App() {
  return (
    <Router>
      <div className="app-container">
        <Sidebar />
        <main className="content">
          <div className="view-container">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/biblioteca" element={<Biblioteca />} />
              <Route path="/catalog" element={<CatalogTable />} />
              <Route path="/norm/:id" element={<DocumentView />} />
              <Route path="/global" element={<GlobalGraph />} />
              <Route path="/hierarchy" element={<HierarchyGraph />} />
              <Route path="/fuentes" element={<Fuentes />} />
              <Route path="/jurimetria" element={<Jurimetria />} />
              <Route path="/monitor" element={<Monitor />} />
              <Route path="/agent" element={<DeepAgent />} />
            </Routes>
          </div>
        </main>
      </div>
    </Router>
  );
}

export default App;
