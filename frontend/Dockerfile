FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# An empty base URL keeps browser requests on the same origin. Nginx proxies
# /api and /health to the backend service at runtime.
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

FROM nginx:1.27-alpine

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
  CMD wget -qO- http://127.0.0.1/health/live >/dev/null || exit 1
