# =============================================================================
# AtlasCore Frontend — Multi-stage Dockerfile
# =============================================================================
# Uses Node 24 (matching .nvmrc).
# Stage 'deps':  install dependencies
# Stage 'build': build Next.js app
# Stage 'dev':   development with hot-reload
# Stage 'prod':  production server (standalone output)
# =============================================================================

ARG NODE_VERSION=24

# =============================================================================
# Stage: deps
# =============================================================================
FROM node:${NODE_VERSION}-slim AS deps

WORKDIR /app

# Copy package files first for cache efficiency
COPY package.json package-lock.json* ./

RUN npm install

# =============================================================================
# Stage: dev
# =============================================================================
FROM node:${NODE_VERSION}-slim AS dev

WORKDIR /app

# Copy deps from previous stage
COPY --from=deps /app/node_modules ./node_modules

# Copy source
COPY . .

EXPOSE 3100

ENV PORT=3100 \
    HOSTNAME="0.0.0.0"

CMD ["npm", "run", "dev", "--", "-p", "3100"]

# =============================================================================
# Stage: build
# =============================================================================
FROM node:${NODE_VERSION}-slim AS build

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Build Next.js application
RUN npm run build

# =============================================================================
# Stage: prod
# =============================================================================
FROM node:${NODE_VERSION}-slim AS prod

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3100 \
    HOSTNAME="0.0.0.0"

# Copy only what's needed to run the standalone server
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

# Non-root user
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

USER nextjs

EXPOSE 3100

CMD ["node", "server.js"]
