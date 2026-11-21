# syntax=docker/dockerfile:1
# ------------------------------------------------------------------------------
# IntelliCredit frontend image (Next.js 15, standalone output).
#
# Build context is the REPOSITORY ROOT (not frontend/):
#   docker build -f deploy/frontend.Dockerfile -t intellicredit-frontend:dev .
#
# NEXT_PUBLIC_* variables are inlined at BUILD time by Next.js, so the API URL
# is passed as a build arg (docker-compose.yml forwards it from .env).
# ------------------------------------------------------------------------------

# --- deps: install node_modules from the lockfile ------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# --- build: compile the Next.js standalone bundle -------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ .
# Ensure the public dir exists even if the app ships no static assets yet.
RUN mkdir -p public

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# --- runner: minimal non-root runtime ------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
