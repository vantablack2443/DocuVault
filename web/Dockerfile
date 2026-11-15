# Static build served by an unprivileged nginx. In production the same build
# output goes to a static host; this image exists for `docker compose up`.
FROM node:20-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31-alpine AS runtime
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
