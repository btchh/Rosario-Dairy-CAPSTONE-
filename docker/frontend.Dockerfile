# syntax=docker/dockerfile:1
# The named frontend context is supplied by the Compose build override.
ARG NODE_IMAGE=node:24-alpine
ARG NGINX_IMAGE=nginx:stable-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY --from=frontend package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
# Copy only build inputs; local node_modules, .env files and Git stay outside.
COPY --from=frontend index.html vite.config.ts postcss.config.mjs tsconfig.json ./
COPY --from=frontend src ./src
COPY --from=frontend assets ./assets
ENV VITE_API_BASE_URL=/backend
RUN npm run build

FROM ${NGINX_IMAGE} AS runtime
COPY --from=build /app/dist/ /usr/share/nginx/html/
COPY docker/frontend.nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
