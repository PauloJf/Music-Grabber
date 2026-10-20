import type { SVGProps } from "react";

const base = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;
const I = (d: string) => (p: SVGProps<SVGSVGElement>) => <svg {...base} {...p}><path d={d} /></svg>;

export const Search = I("M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.3-4.3");
export const Queue = I("M4 6h16M4 12h10M4 18h7M18 14v6m-3-3h6");
export const Inbox = I("M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5");
export const Library = I("M5 4v16M10 4v16M15 6l4 14");
export const Gear = I("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-1-3-2 .5-1.5-1.5.5-2-3-1-1 2h-2l-1-2-3 1 .5 2L6 8.5 4 8l-1 3 2 1v2l-2 1 1 3 2-.5L7.5 19l-.5 2 3 1 1-2h2l1 2 3-1-.5-2 1.5-1.5 2 .5 1-3-2-1z");
export const Plus = I("M12 5v14M5 12h14");
export const Check = I("M5 12.5l4.5 4.5L19 7.5");
export const X = I("M6 6l12 12M18 6L6 18");
export const Play = (p: SVGProps<SVGSVGElement>) => <svg {...base} fill="currentColor" stroke="none" {...p}><path d="M8 5v14l11-7z" /></svg>;
export const Pause = (p: SVGProps<SVGSVGElement>) => <svg {...base} fill="currentColor" stroke="none" {...p}><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg>;
export const Back = I("M15 5l-7 7 7 7");
export const Retry = I("M4 12a8 8 0 1 1 2.5 5.8M4 19v-5h5");
export const Down = I("M12 4v12m0 0l-5-5m5 5l5-5M5 20h14");
export const Explicit = (p: SVGProps<SVGSVGElement>) => <svg viewBox="0 0 16 16" aria-label="Explicit" {...p}><rect x="1" y="1" width="14" height="14" rx="2" fill="currentColor" /><text x="8" y="12" textAnchor="middle" fontSize="10" fontWeight="700" fill="var(--paper)">E</text></svg>;
