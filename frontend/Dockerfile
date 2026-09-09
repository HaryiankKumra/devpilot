# syntax=docker/dockerfile:1

# =============================================================================
# DevPilot frontend image (production).
#
# The React app is a bundle of static files, so the runtime stage is just nginx
# -- no Node, no npm, nothing to exploit at runtime.
#
# For local development, Compose runs the Vite dev server instead (see
# `Dockerfile.dev`), because this image bakes the API URL in at build time.
# =============================================================================

# --- Stage 1: build the bundle ----------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

# `npm ci` installs exactly the lockfile, which is what makes builds
# reproducible. Copying only the manifests first keeps this layer cached.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite inlines `VITE_*` variables at build time, so the API URL is a build
# argument rather than a runtime environment variable.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# --- Stage 2: serve ----------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null || exit 1
