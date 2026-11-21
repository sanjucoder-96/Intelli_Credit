# IntelliCredit frontend

Next.js 15 (App Router) + TypeScript strict + Tailwind CSS v4 + TanStack Query v5 + Zustand v5 + vendored shadcn-style ui primitives + next-themes. See `docs/adr/ADR-0014-frontend-stack.md` for the stack rationale.

Phase 2 ships the skeleton and one working page: a platform status board that polls the backend `GET /api/v1/health` and `GET /api/v1/ready` endpoints — the proof that the frontend/backend contract works. Case workspaces, run timelines (SSE), server-driven dashboards, and the pdf.js document viewer arrive in later phases (`docs/ROADMAP.md`).

## Run locally

Prerequisites: Node.js 20+ (dev machine uses 22/24) and the backend API on port 8000 (`docker compose up api` from the repo root, or run uvicorn from `backend/`).

```bash
cd frontend
cp .env.example .env        # NEXT_PUBLIC_API_URL, defaults to http://localhost:8000
npm install
npm run dev                 # http://localhost:3000
```

Other scripts:

```bash
npm run build       # production build (standalone output for Docker)
npm run start       # serve the production build
npm run lint        # eslint 9 flat config (next/core-web-vitals + next/typescript)
npm run typecheck   # tsc --noEmit
```

## Docker

Built by `deploy/frontend.Dockerfile` (standalone output) and wired into the root `docker-compose.yml` as the `frontend` service on port 3000. `NEXT_PUBLIC_API_URL` is a build-time value (inlined into the client bundle).

## Layout

```
src/
  app/            # App Router: layout (providers), landing page, global tokens
  components/
    platform-status.tsx   # health/ready status board (TanStack Query)
    ui/                   # vendored shadcn-style primitives (button, card, badge)
  lib/
    api.ts        # typed fetch wrapper (replaced by openapi-typescript client in Phase 3)
    store.ts      # Zustand ui-state seam (server state stays in TanStack Query)
    utils.ts      # cn()
```
