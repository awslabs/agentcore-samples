import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import LoginPage from './pages/LoginPage';
import ChatPage from './pages/ChatPage';
import AdjusterConsole from './pages/AdjusterConsole';
import AdminMemory from './pages/AdminMemory';

const has = (groups: string[] | undefined, g: string) => !!groups?.includes(g);

function homeFor(groups?: string[]) {
  if (has(groups, 'admin')) return '/admin';
  if (has(groups, 'adjuster')) return '/adjuster';
  return '/';
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { session } = useAuth();
  if (!session) return <Navigate to="/login" replace />;
  // Non-policyholders belong in their own views.
  if (has(session.groups, 'admin')) return <Navigate to="/admin" replace />;
  if (has(session.groups, 'adjuster')) return <Navigate to="/adjuster" replace />;
  return <>{children}</>;
}

function GroupRoute({ group, children }: { group: string; children: React.ReactNode }) {
  const { session } = useAuth();
  if (!session) return <Navigate to="/login" replace />;
  if (!has(session.groups, group)) return <Navigate to={homeFor(session.groups)} replace />;
  return <>{children}</>;
}

export default function App() {
  const { session } = useAuth();
  const home = session ? homeFor(session.groups) : '/';

  return (
    <Routes>
      <Route path="/login" element={session ? <Navigate to={home} replace /> : <LoginPage />} />
      <Route path="/" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
      <Route path="/adjuster" element={<GroupRoute group="adjuster"><AdjusterConsole /></GroupRoute>} />
      <Route path="/admin" element={<GroupRoute group="admin"><AdminMemory /></GroupRoute>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
