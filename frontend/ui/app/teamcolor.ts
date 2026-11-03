// Team colours that stay readable on every theme.
//
// A franchise colour is used as-is where it fills a shape with its own text on top (a badge, a chip: the ink is
// picked for it). Where it is drawn on the theme's panel (chart lines, bars, coloured text), it is first nudged
// toward white on dark themes or toward black on light themes until it reaches a minimum contrast.

export type ThemeId = "floodlit" | "night" | "pitch" | "pavilion" | "pinkball";

// The panel and banner colours of each theme (the same values as the tokens in globals.css).
const PANEL: Record<ThemeId, string> = { floodlit: "#ffffff", night: "#121e33", pitch: "#ffffff", pavilion: "#fffdf9", pinkball: "#1c1424" };
const BANNER: Record<ThemeId, [string, string]> = {
  floodlit: ["#06357a", "#0b5ed7"], night: ["#08224f", "#0e3f8f"], pitch: ["#0d3b24", "#1f6b3f"],
  pavilion: ["#4a0f22", "#7d1d39"], pinkball: ["#2a0f2e", "#6d1450"],
};

let current: ThemeId = "floodlit";
/** Called by the app on every render, before any child reads a colour. */
export function setInkTheme(theme: ThemeId) { current = theme; }

function rgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1];
  return [0, 2, 4].map((i) => Number.parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}
function hex([r, g, b]: number[]) {
  return `#${[r, g, b].map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("")}`;
}
function luminance(c: [number, number, number]) {
  const [r, g, b] = c.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a: string, b: string): number {
  const x = rgb(a), y = rgb(b);
  if (!x || !y) return 21;
  const [l1, l2] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (l1 + 0.05) / (l2 + 0.05);
}

/** Text colour for words written on a team-colour fill. */
export function inkOn(fill: string): string {
  return contrast(fill, "#ffffff") >= contrast(fill, "#111827") ? "#ffffff" : "#111827";
}

/** The team colour, adjusted until it reads against the current theme's panel (min 3:1, WCAG for graphics). */
export function readable(color: string, min = 3, theme: ThemeId = current): string {
  const base = rgb(color);
  if (!base) return color;
  const bg = PANEL[theme];
  if (contrast(color, bg) >= min) return color;
  const target = luminance(rgb(bg)!) < 0.2 ? [255, 255, 255] : [0, 0, 0];
  for (let t = 0.08; t <= 1; t += 0.08) {
    const mixed = hex(base.map((v, i) => v + (target[i] - v) * t));
    if (contrast(mixed, bg) >= min) return mixed;
  }
  return hex(target);
}

/** True when a team colour would melt into the current banner: the badge then gets a stronger ring. */
export function lowOnBanner(color: string, theme: ThemeId = current): boolean {
  return Math.min(...BANNER[theme].map((b) => contrast(color, b))) < 1.8;
}
