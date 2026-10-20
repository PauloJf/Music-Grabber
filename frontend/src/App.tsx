import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { useAuth } from "./lib/auth";
import Album from "./pages/Album";
import Artist from "./pages/Artist";
import Discover from "./pages/Discover";
import Inbox from "./pages/Inbox";
import Login from "./pages/Login";
import PlaylistDetail from "./pages/PlaylistDetail";
import Playlists from "./pages/Playlists";
import Queue from "./pages/Queue";
import Settings from "./pages/Settings";

export default function App() {
  const { authed } = useAuth();
  if (!authed) return <Login />;
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Discover />} />
        <Route path="/artist/:id" element={<Artist />} />
        <Route path="/album/:id" element={<Album />} />
        <Route path="/queue" element={<Queue />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/inbox/:id" element={<Inbox />} />
        <Route path="/playlists" element={<Playlists />} />
        <Route path="/playlists/:id" element={<PlaylistDetail />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
