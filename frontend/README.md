# TraceMind AI — web app

Next.js 16 (App Router, Cache Components) · TypeScript · Tailwind 4 · shadcn/ui · React Flow · Recharts · Three.js.

```bash
npm install
BACKEND_URL=http://127.0.0.1:8000 npm run dev      # API must be running
npm run lint && npm run typecheck && npm run build
E2E_BASE_URL=http://localhost:3000 npx playwright test
```

`/api/v1/*` is rewritten to `BACKEND_URL` (read at build time). See the [project README](../README.md) and [docs](../docs).
