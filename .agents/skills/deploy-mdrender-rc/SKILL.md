---
name: deploy-mdrender-rc
description: Deploy (or update/rollback) the MDRender push server on the oracle-cloud host to a specific GHCR image tag. Use when: the user asks to deploy/update an RC (or any tagged image) to the server, after cutting one with the cut-rc-release skill. Rootful podman + systemd, no compose, no docker.
---

# deploy-mdrender-rc

Push-server deployment model on `oracle-cloud` (161.33.93.37, ssh alias
`oracle-cloud`):

- **Image**: `ghcr.io/githuba42r/mdrender-server:<TAG>` - the tag is pinned in
  the systemd unit (`/etc/systemd/system/mdrender-push.service`), *never*
  `latest`.
- **Runtime**: rootful podman (no docker; docker was uninstalled 2026-10-07),
  one systemd unit, no compose.
- **Config**: `/opt/apps/mdrender/server/push.env` (KEY=VALUE, passed to the
  container via `--env-file`). Secrets/state are there plus the bind-mounted
  data dir.
- **State**: `/opt/apps/mdrender/server/data` (bind-mounted to `/data/push` in
  the container - DB, admin, accounts all persist across deploys).
- **Edge**: nginx terminates TLS for `push.a42r.com` + serves the static site
  `mdrender.a42r.com`; Cloudflare fronts both (DNS-01 certs under
  `/etc/letsencrypt/live/push.a42r.com/`).

## Deploy / update / rollback steps

1. (Optional but recommended) Pre-pull so downtime is one restart:
   ```
   ssh oracle-cloud 'sudo podman pull ghcr.io/githuba42r/mdrender-server:<TAG>'
   ```
2. Point the unit at the tag:
   ```
   ssh oracle-cloud 'sudo sed -i "s#ghcr.io/githuba42r/mdrender-server:[^ ]*#ghcr.io/githuba42r/mdrender-server:<TAG>#" /etc/systemd/system/mdrender-push.service'
   ssh oracle-cloud 'sudo systemctl daemon-reload && sudo systemctl restart mdrender-push'
   ```
   Note: `systemctl reload` does not help for unit-file edits; always
   `daemon-reload` then `restart` (systemd recreates the container via
   `ExecStartPre=-podman rm -f`).
3. Verify (each must pass before declaring success):
   ```
   ssh oracle-cloud 'systemctl is-active mdrender-push'
   ssh oracle-cloud 'sudo podman inspect mdrender-push --format "{{.ImageName}} {{.Status}}"'
   ssh oracle-cloud 'curl -sS http://127.0.0.1:18081/api/health'                                   # {"ok":true}
   curl -sS https://push.a42r.com/api/health                                                       # {"ok":true} (over CF+nginx)
   curl -sS https://push.a42r.com/api/server/policy                                                # {"encryption":"on"}
   ssh oracle-cloud 'sudo journalctl -u mdrender-push --since "-2 min" --no-pager | tail -20'
   ```
4. Rollback: re-run step 2 with the previous tag (that image is still in
   podman's local store).

## Known-good state (2026-10-07)

- Image: `ghcr.io/githuba42r/mdrender-server:1.1.3-rc.2`
- Admin: username `admin`, email `philg@thenetworktech.net`.
- The production `docker-compose.yml` in `/opt/apps/mdrender/server/` is
  retired (kept for reference only; the docker packages were purged).
- App-side IP trust: nginx realip (`/etc/nginx/conf.d/cloudflare-real-ip.conf`)
  resolves `CF-Connecting-IP`; the push vhost *overwrites* `X-Forwarded-For`
  with `$remote_addr`; the app runs with `TRUST_PROXY=true`
  (ProxyFix x_for=1) and waitress passes proxy headers through
  (`clear_untrusted_proxy_headers=not TRUST_PROXY` in `server/run.py`).
  Breaking any of these three links makes bans/lockouts key on the proxy's
  address again - verify with a ban round-trip:
  ban the host's own egress IP via the admin `/bans` form, confirm
  `https://push.a42r.com/api/health` goes `403` for it while
  `curl -H "X-Forwarded-For: 203.0.113.99" http://127.0.0.1:18081/api/health`
  still returns `200`, then delete the ban from the bans admin page and
  confirm the public `health` returns `200` again.
