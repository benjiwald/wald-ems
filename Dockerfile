FROM node:20-slim AS builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY src/ src/
COPY migrations/ migrations/
COPY next.config.ts tsconfig.json postcss.config.mjs components.json ./
RUN npm run build

# ── Production Image ─────────────────────────────────────────────────
FROM python:3.11-slim

# Node.js ins Python-Image installieren
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y -qq --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ems

# Python Dependencies
COPY ems-client/requirements.txt ems-client/requirements.txt
RUN pip install --no-cache-dir -r ems-client/requirements.txt

# Next.js Standalone aus Builder-Stage
COPY --from=builder /app/.next/standalone ./dashboard/
COPY --from=builder /app/.next/static ./dashboard/.next/static
COPY --from=builder /app/public ./dashboard/public 2>/dev/null || true

# Python Client + Migrations + Config
COPY ems-client/ ems-client/
COPY migrations/ migrations/
COPY wald-ems.yaml.example wald-ems.yaml.example
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7777

VOLUME ["/data"]

ENV PORT=7777
ENV HOSTNAME=0.0.0.0
ENV WALD_EMS_CONFIG=/data/wald-ems.yaml
ENV NODE_ENV=production

ENTRYPOINT ["/entrypoint.sh"]
