// Pre-render the match centre for a static host (the Hugging Face Space serves ui/ with FastAPI StaticFiles).
//
//   npm run build && node scripts/render-static.mjs <out-dir>
//
// Copies dist/client to <out-dir> and writes <out-dir>/index.html from the built server's render of "/".
// vinext inlines @font-face rules with the build machine's absolute font paths
// (e.g. D:/Python/.../.vinext/fonts/<family>/<file>.woff2), which no browser can load from a Space.
// Those are rewritten to the copies the client build ships under /assets/_vinext_fonts/.
import { cp, mkdir, readdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = path.resolve(process.argv[2] ?? path.join(ui, "dist", "static"));

const { default: worker } = await import(new URL(`file://${path.join(ui, "dist", "server", "index.js")}`).href);
const response = await worker.fetch(
  new Request("http://localhost/", { headers: { accept: "text/html" } }),
  { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
  { waitUntil() {}, passThroughOnException() {} },
);
if (response.status !== 200) throw new Error(`render failed: HTTP ${response.status}`);
let html = await response.text();

const fonts = path.join(ui, "dist", "client", "assets", "_vinext_fonts");
let rewritten = 0;
html = html.replace(/url\((['"]?)([^)'"]*?)[\\/]\.vinext[\\/]fonts[\\/]([^)'"]+)\1\)/g, (_, q, _root, rest) => {
  const file = rest.replace(/\\/g, "/");
  if (!existsSync(path.join(fonts, file))) throw new Error(`font not in the client build: ${file}`);
  rewritten += 1;
  return `url(${q}/assets/_vinext_fonts/${file}${q})`;
});
if (/\.vinext[\\/]fonts|[A-Z]:\/|\/home\/|\/Users\//.test(html.match(/@font-face[\s\S]*?}/g)?.join("") ?? "")) {
  throw new Error("a font URL still points at the build machine");
}

await mkdir(out, { recursive: true });
await cp(path.join(ui, "dist", "client"), out, { recursive: true });
await writeFile(path.join(out, "index.html"), html);
const assets = await readdir(path.join(out, "assets"));
console.log(`index.html (${html.length} bytes, ${rewritten} font URLs rewritten) + ${assets.length} asset entries -> ${out}`);
