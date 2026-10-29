import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { useAuth } from "./lib/auth";
import { useMe } from "./lib/hooks";
import Album from "./pages/Album";
import Account from "./pages/Account";
import Artist from "./pages/Artist";
import Discover from "./pages/Discover";
import ForYou from "./pages/ForYou";
import Help from "./pages/Help";
import Inbox from "./pages/Inbox";
import { LibraryAlbum, LibraryAlbums, LibraryArtist, LibraryArtists } from "./pages/LibraryBrowse";
import Login from "./pages/Login";
import PlaylistDetail from "./pages/PlaylistDetail";
import Owned from "./pages/Owned";
import Playlists from "./pages/Playlists";
import Queue from "./pages/Queue";
import Releases from "./pages/Releases";
import Settings from "./pages/Settings";

export default function App() {
  const { authed } = useAuth();
  if (!authed) return <Login />;
  return <Authed />;
}

function Authed() {
  const me = useMe();
  const isAdmin = !me.data || me.data.user.role === "admin";
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ForYou />} />
        <Route path="/discover" element={<Discover />} />
        <Route path="/for-you" element={<Navigate to="/" replace />} />
        <Route path="/artist/:id" element={<Artist />} />
        <Route path="/album/:id" element={<Album />} />
        <Route path="/queue" element={<Queue />} />
        <Route path="/releases" element={<Releases />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/inbox/:id" element={<Inbox />} />
        <Route path="/library/artists" element={<LibraryArtists />} />
        <Route path="/library/artists/:name" element={<LibraryArtist />} />
        <Route path="/library/albums" element={<LibraryAlbums />} />
        <Route path="/library/albums/:artist/:album" element={<LibraryAlbum />} />
        <Route path="/playlists" element={<Playlists />} />
        <Route path="/playlists/owned" element={<Owned />} />
        <Route path="/playlists/:id" element={<PlaylistDetail />} />
        <Route path="/help" element={<Help />} />
        <Route path="/account" element={<Account />} />
        <Route path="/settings" element={isAdmin ? <Settings /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
