import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

export interface Toast { id: number; kind: "info" | "ok" | "err"; text: string; action?: { label: string; run: () => void } }
interface Api { push: (t: Omit<Toast, "id">, ms?: number) => void; dismiss: (id: number) => void }
const Ctx = createContext<Api | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback((t: Omit<Toast, "id">, ms = t.action ? 7000 : t.kind === "err" ? 6000 : 3500) => {
    const id = next.current++;
    setToasts((cur) => [...cur.slice(-2), { ...t, id }]);
    window.setTimeout(() => dismiss(id), ms);
  }, [dismiss]);
  const api = useMemo(() => ({ push, dismiss }), [push, dismiss]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "info" ? "" : t.kind}`}>
            <span className="grow">{t.text}</span>
            {t.action && <button onClick={() => { t.action!.run(); dismiss(t.id); }}>{t.action.label}</button>}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): Api {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast outside ToastProvider");
  return v;
}
