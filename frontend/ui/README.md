# cricket-rag UI

The match centre and Pandit chat for cricket-rag, ported from [cricbot](https://github.com/piyushdash94/cricbot)'s
UI and restyled in a blue-and-white scorecard theme. React 19 on [vinext](https://github.com/cloudflare/vinext).

## Run

Three processes: cricbot's API (archive, match pages, stats), cricket-rag's API (Pandit), and this UI.

```bash
# 1. cricbot checkout: archive, match detail and stats on :8000
python -m backend.apis

# 2. this repo: Pandit on :8100, forwards every other /api path to cricbot (CRICBOT_API)
uv run cricrag serve

# 3. the UI on :3000, proxies /api to :8100 (override with CRICRAG_API)
cd frontend/ui && npm install && npm run dev
```

Node.js `>=22.13.0`.

- `npm run build`: production build
- `npm test`: build, then check the server-rendered page
- `npm run lint`
