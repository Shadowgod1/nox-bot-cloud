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
# LAN / cloud proxy snippets; the entrypoint copies one to /tmp/iler-proxy-mode.conf (nginx.conf includes it)
COPY nginx/proxy-lan.conf nginx/proxy-cloud.conf /etc/nginx/iler/
# default (LAN) so nginx -t / --entrypoint nginx work without the entrypoint; owned by nginx so the
# entrypoint (USER nginx) can overwrite it
COPY --chown=nginx:nginx nginx/proxy-lan.conf /tmp/iler-proxy-mode.conf
COPY nginx/iler-web-entrypoint.sh /usr/local/bin/iler-web-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/iler-web-entrypoint.sh && chmod 755 /usr/local/bin/iler-web-entrypoint.sh
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html

USER nginx
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
ENTRYPOINT ["/usr/local/bin/iler-web-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
