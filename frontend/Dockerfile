# syntax=docker/dockerfile:1
# Build aplikacji React (Vite) + serwowanie statyki przez nginx bez roota.

FROM node:24.22.3-alpine3.24 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31.7-alpine3.24
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
# Obraz bazowy działa jako użytkownik nginx (UID 101).
