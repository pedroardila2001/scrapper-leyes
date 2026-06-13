import { BrowserRouter as Router, Routes, Route, Link, useLocation } from "react-router-dom";
import CatalogTable from "./components/CatalogTable";
import DocumentView from "./components/DocumentView";
import Dashboard from "./components/Dashboard";
import GlobalGraph from "./components/GlobalGraph";
import { Scale, LayoutDashboard, BookOpen, Network } from "lucide-react";
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
        <Link to="/catalog" className={isActive("/catalog")}>
          <BookOpen className="icon" size={18} />
          <span>Directorio Legal</span>
        </Link>
        <Link to="/global" className={isActive("/global")}>
          <Network className="icon" size={18} />
          <span>Red Global</span>
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
              <Route path="/catalog" element={<CatalogTable />} />
              <Route path="/norm/:id" element={<DocumentView />} />
              <Route path="/global" element={<GlobalGraph />} />
            </Routes>
          </div>
        </main>
      </div>
    </Router>
  );
}

export default App;
