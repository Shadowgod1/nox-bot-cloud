# iler-web: admin SPA served by nginx (non-root, port 8080).

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# Replace the stock config entirely; drop the default site and entrypoint helpers that
# try to rewrite it (they need write access to /etc/nginx, which the nginx user lacks).
RUN rm -f /etc/nginx/conf.d/default.conf \
 && rm -rf /docker-entrypoint.d/* \
 && rm -rf /usr/share/nginx/html/* \
 && chown -R nginx:nginx /var/cache/nginx
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html

USER nginx
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
CMD ["nginx", "-g", "daemon off;"]
