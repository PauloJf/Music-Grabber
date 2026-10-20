import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

// One shared <audio> for 30-second previews: starting another stops the first.
interface P { playing: string | null; progress: number; toggle: (key: string, url: string) => void; stop: () => void }
const Ctx = createContext<P | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);

  const stop = useCallback(() => { audio.current?.pause(); setPlaying(null); setProgress(0); }, []);
  const toggle = useCallback((key: string, url: string) => {
    if (!audio.current) {
      audio.current = new Audio();
      audio.current.addEventListener("timeupdate", () => { const a = audio.current!; setProgress(a.duration ? a.currentTime / a.duration : 0); });
      audio.current.addEventListener("ended", () => { setPlaying(null); setProgress(0); });
      audio.current.addEventListener("error", () => { setPlaying(null); setProgress(0); });
    }
    const a = audio.current;
    if (playing === key) { stop(); return; }
    a.src = url;
    setProgress(0);
    setPlaying(key);
    a.play().catch(() => setPlaying(null));
  }, [playing, stop]);

  useEffect(() => () => { audio.current?.pause(); }, []);
  const value = useMemo(() => ({ playing, progress, toggle, stop }), [playing, progress, toggle, stop]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePlayer(): P {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePlayer outside PlayerProvider");
  return v;
}
