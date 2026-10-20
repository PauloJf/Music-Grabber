import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { useAuth } from "./lib/auth";
import Inbox from "./pages/Inbox";
import Login from "./pages/Login";
import PlaylistDetail from "./pages/PlaylistDetail";
import Playlists from "./pages/Playlists";
import Queue from "./pages/Queue";

export default function App() {
  const { authed } = useAuth();
  if (!authed) return <Login />;
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/inbox" replace />} />
        <Route path="/playlists" element={<Playlists />} />
        <Route path="/playlists/:id" element={<PlaylistDetail />} />
        <Route path="/queue" element={<Queue />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/inbox/:id" element={<Inbox />} />
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Routes>
    </Layout>
  );
}
