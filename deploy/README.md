# Deploy Templates

- `deploy/nginx-mobile-codex.conf` is the local Windows nginx config used by the helper scripts.
- `deploy/Caddyfile.example` is an optional reverse-proxy example for users who prefer Caddy.
- `deploy/nginx-mobile-codex.conf.example` is an optional public-edge nginx example.

Keep the application itself on `127.0.0.1` and prefer a private network entrypoint.
