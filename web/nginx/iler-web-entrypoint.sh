#!/bin/sh
# Picks the proxy mode for nginx.conf, then runs the command (nginx).
#   cloud: ILER_PUBLIC_URL is set or ILER_HTTP_BIND is a loopback address (instance behind the tunnel)
#   lan:   everything else (default; also when compose passes neither variable)
set -eu
mode=lan
case "${ILER_HTTP_BIND:-}" in
  127.*|::1|"[::1]") mode=cloud ;;
esac
if [ -n "${ILER_PUBLIC_URL:-}" ]; then
  mode=cloud
fi
cp "/etc/nginx/iler/proxy-$mode.conf" /tmp/iler-proxy-mode.conf
echo "iler-web: proxy mode $mode" >&2
exec "$@"
