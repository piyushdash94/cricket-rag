import vinext from "vinext";
import { defineConfig } from "vite";

// cricket-rag's API (`uv run cricrag serve`): Pandit plus cricbot's archive, match pages and stats, one process.
const apiTarget = process.env.CRICRAG_API ?? "http://127.0.0.1:8000";

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    server: {
      allowedHosts: [".ngrok-free.app", ".ngrok.app"],
      proxy: { "/api": { target: apiTarget, changeOrigin: true } },
    },
    plugins: [
      vinext(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        config: { main: "./worker/index.ts", compatibility_flags: ["nodejs_compat"] },
      }),
    ],
  };
});
