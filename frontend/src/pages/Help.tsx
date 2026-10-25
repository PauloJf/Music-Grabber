import { Link } from "react-router-dom";
import type { ReactNode } from "react";

const SECTIONS = [
  ["start", "Getting started"],
  ["find", "Finding and queueing music"],
  ["states", "What the badges mean"],
  ["inbox", "Queue and Inbox"],
  ["playlists", "Watching Spotify playlists"],
  ["account", "Accounts and people"],
  ["settings", "Settings"],
  ["fix", "When something goes wrong"],
  ["safe", "Keeping it safe"],
] as const;

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="help-sec" style={{ marginTop: 30, scrollMarginTop: 80 }}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export default function Help() {
  return (
    <>
      <section className="hero" style={{ paddingBottom: 0 }}>
        <div className="eyebrow">Help</div>
        <h1>How Music Grabber works</h1>
        <p>It finds songs on Deezer, downloads the ones your library is missing, tags them and files them in your music folder.</p>
      </section>

      <nav aria-label="On this page" className="row" style={{ margin: "16px 0 0", gap: 8, flexWrap: "wrap" }}>
        {SECTIONS.map(([id, label]) => <a key={id} className="btn" href={`#${id}`}>{label}</a>)}
      </nav>

      <div style={{ maxWidth: 720 }}>
        <Section id="start" title="Getting started">
          <ol>
            <li><b>Sign in</b> with your username and password. The very first visit asks you to create the administrator account, using the server's <code>API_TOKEN</code> to prove you own the installation. Your browser stays signed in for this tab; <b>Sign out</b> (on your <Link to="/account">Account</Link> page) ends it. The API token still works as a sign-in for scripts and recovery.</li>
            <li>Open <Link to="/settings">Settings</Link> and add your <b>Deezer ARL</b> (the login cookie from your own Deezer account). Press <i>Test</i> to check it. Without it, downloads fall back to YouTube, which sounds worse.</li>
            <li>Check the <b>output format</b> and the <b>music folder layout</b>. Files are converted to one format so your library stays consistent.</li>
            <li>Optional: connect <b>Spotify</b> to watch playlists, and set a ntfy topic to get a phone notification when a song needs your decision.</li>
          </ol>
          <p>If <b>Dry run</b> is on (a banner says so), searching and scoring happen but nothing is downloaded. Turn it off in Settings when you're ready.</p>
        </Section>

        <Section id="find" title="Finding and queueing music">
          <ul>
            <li>Type in the search box on <Link to="/">Discover</Link> (press <kbd>/</kbd> anywhere to jump to it). Results are grouped into songs, artists and albums.</li>
            <li>Open an <b>artist</b> to see top songs, albums and related artists. Open an <b>album</b> to see every track.</li>
            <li>Press <b>▶</b> on a song for a 30-second preview. Only one plays at a time.</li>
            <li>Press the <b>download</b> button on a song, or <b>Queue album</b> on an album. You get an instant confirmation with an <b>Undo</b> for a few seconds.</li>
            <li>Tick several songs to queue them together.</li>
            <li>Songs already in your library are marked and are not queued twice.</li>
          </ul>
        </Section>

        <Section id="states" title="What the badges mean">
          <dl>
            <dt><b>In library</b></dt><dd>Already in your music folder. Nothing to do.</dd>
            <dt><b>Queued / Downloading</b></dt><dd>Waiting its turn, or being fetched and converted. The activity tray at the bottom shows live progress.</dd>
            <dt><b>Needs review</b></dt><dd>The match wasn't certain. Open the <Link to="/inbox">Inbox</Link> and pick the right version.</dd>
            <dt><b>Done</b></dt><dd>Downloaded, tagged and filed.</dd>
            <dt><b>Failed</b></dt><dd>Something went wrong; the reason is shown. Retry it from the <Link to="/queue">Queue</Link>.</dd>
          </dl>
        </Section>

        <Section id="inbox" title="Queue and Inbox">
          <ul>
            <li>The <b>Queue</b> lists everything in progress, waiting, finished or failed. You can retry or remove items.</li>
            <li>The <b>Inbox</b> holds songs where several candidates scored close. Press <b>▶</b> on a candidate to hear 30 seconds of it (and the ▶ next to the title for the original), then pick <i>Download this</i> on the right one, search with different words, or skip it. A YouTube candidate has a <i>Listen</i> link instead, since it has no preview.</li>
            <li>Removing something from the queue never deletes a file that was already filed in your library.</li>
          </ul>
        </Section>

        <Section id="playlists" title="Watching Spotify playlists">
          <p>In <Link to="/playlists">Library</Link>, paste a Spotify playlist link and press <i>Watch playlist</i>. Music Grabber checks the playlist regularly, compares it with your library and queues what's missing. Use <i>Sync</i> to check now, or <i>Queue missing</i> to queue the gaps by hand. Connecting Spotify (Settings) is only needed for private playlists and automatic syncing.</p>
        </Section>

        <Section id="account" title="Accounts and people">
          <ul>
            <li>On <Link to="/account">Account</Link> (your name in the header) you can pick a light or dark theme, set the preview volume and change your password. Changing it signs your other devices out.</li>
            <li><b>Administrators</b> can also add people, make someone an administrator or a regular user, reset a password and delete an account. Regular users can search, queue and handle the Inbox, but can't open Settings.</li>
            <li>Forgot the only administrator's password? On the server run <code>python -m music_grabber users passwd NAME</code>.</li>
          </ul>
        </Section>

        <Section id="settings" title="Settings">
          <ul>
            <li><b>Sources:</b> Deezer first, then YouTube, in the order you choose; Deezer quality (MP3 128, MP3 320 or FLAC; 320 and FLAC need a paid Deezer account).</li>
            <li><b>Matching:</b> two thresholds. Above the higher one a match is accepted automatically; between them it goes to your Inbox; below the lower it's ignored.</li>
            <li><b>Notifications:</b> a ntfy server and topic. Secrets are shown masked and never sent back to your browser.</li>
            <li>Changes apply immediately unless Settings says a restart is needed.</li>
          </ul>
        </Section>

        <Section id="fix" title="When something goes wrong">
          <ul>
            <li><b>"Can't reach Music Grabber":</b> the server is down or restarting. The page retries on its own.</li>
            <li><b>Signed out suddenly:</b> your session expired (after 14 days without use), someone reset your password, or the server's <code>API_TOKEN</code> changed. Sign in again.</li>
            <li><b>Too many attempts:</b> several wrong passwords or tokens in a row lock the address for a minute. Wait, then try again.</li>
            <li><b>Everything fails on Deezer:</b> the ARL probably expired. Get a fresh one and test it in Settings.</li>
            <li><b>No previews or covers:</b> a browser extension or network is blocking Deezer's image/audio servers.</li>
            <li><b>A song was matched wrongly:</b> remove it from the Queue, then search again in the Inbox with different words.</li>
          </ul>
        </Section>

        <Section id="safe" title="Keeping it safe">
          <ul>
            <li>Use a long, unique password. Anyone with the <code>API_TOKEN</code> is an administrator, so keep it private too.</li>
            <li>Serve it over <b>HTTPS</b> (a reverse proxy such as Caddy or Traefik) if it's reachable outside your home network. Don't expose it directly to the internet without that.</li>
            <li>Use only your own Deezer account, and download music you're entitled to have.</li>
          </ul>
        </Section>
      </div>
    </>
  );
}
