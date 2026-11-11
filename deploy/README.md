# ILER Bot deployment

Operator notes for `deploy/`. The design is in `docs/superpowers/specs/2026-11-11-iler-bot-design.md` §8–9.

## Install

```sh
curl -fsSL https://github.com/shadowgod1/iler-bot/releases/latest/download/install.sh | sudo bash
# or from an unpacked bundle:
sudo bash deploy/install.sh [--port 8080] [--dir /opt/iler] [--offline images.tar]
```

- When the bundle is downloaded by `install.sh` itself, the bundle is checked against the release's
  `SHA256SUMS`. The install stops if the checksum does not match. If `SHA256SUMS` cannot be fetched,
  it prints a warning and goes on.
- Secrets are generated into `<dir>/.env` (mode 600). Keep a copy of this file somewhere safe (see
  "Restore" below).
- **First run.** At the end, the installer prints a one-time setup link:
  `http://<lan-ip>:<port>/setup?token=<ILER_SETUP_TOKEN>`. Only someone with this link can create the
  owner account. If the token is empty or wrong, `POST /api/setup/owner` returns 403. Once the owner
  exists, the endpoint returns 409. On Windows, the desktop shortcut opens this link.

## Network

- Only `iler-web` publishes a port (the admin UI). Before the stack starts, `install.sh` restricts
  that port to private and loopback sources. It does this with iptables and ip6tables rules in
  Docker's `DOCKER-USER` chain (chain `ILER-ADMIN`, comment `iler-admin`). The rules are applied on
  every boot by `iler-firewall.service`, which runs after `docker.service`. When ufw is active, the
  same restriction is also written to `/etc/ufw/after.rules` and `after6.rules`.
- Docker networks:
  - `iler`: web, core, postgres, redis and evolution-api.
  - `updater_net`: core and iler-updater only. Evolution and the databases cannot reach the updater,
    which holds `docker.sock`.
- Redis requires `ILER_REDIS_PASSWORD`.
- On Windows (mirrored WSL networking), a Hyper-V firewall rule allows the port only from RFC1918,
  ULA, link-local and loopback addresses.

## Updates and rollback

The admin UI's "Update" button calls `iler-updater`. The updater then runs these steps in order:

1. Takes a backup through core.
2. Pulls the new images.
3. Restarts core and web.
4. Waits for `/health`.
5. If the health check fails, rolls back to the previous image tags.

**Rollback reverts images only, never the database.** Migrations run when core starts, so a new
release may already have migrated the schema before it failed its health check. That is why:

- **DB migrations must stay backward compatible for one release.** Release N+1 may add tables,
  columns with defaults and indexes. It must not drop or rename anything that release N still reads
  or writes. Destructive changes ship one release later, once nothing uses the old shape.
- The name of the pre-update backup is kept in the job record. `GET /update/<job_id>` on the updater
  returns it as `backup` (the log mentions it too). If the data itself must go back, restore that
  backup by hand:

  ```sh
  sudo bash /opt/iler/deploy/install.sh --restore /opt/iler/backups/<backup name>
  ```

- If the channel file (`stable.env`) has `ILER_CORE_DIGEST`, `ILER_WEB_DIGEST` and
  `ILER_UPDATER_DIGEST` (`sha256:<64 hex>`), images are pulled as `tag@digest`. Without these
  entries, images are pulled by tag.

## Backups

- Daily backup at 04:00 (club time zone), kept for 14 days in `<dir>/backups`. Each archive holds
  `pg_dump -Fc` of both databases, a Redis RDB snapshot and a `manifest.json` with SHA-256 checksums.
- The result of the last backup is shown in two places: `last_status` in `GET /api/backups` and
  `backup` in `GET /api/dashboard`. If a scheduled backup fails and SMTP is configured, an e-mail
  alert is sent.

## Restore

```sh
sudo bash deploy/install.sh --restore /path/iler-backup-YYYYMMDD-HHMMSS.tar.gz --env /path/to/original/.env
```

- Restores both databases. It also restores the Redis snapshot (the WhatsApp session) into the redis
  volume while redis is stopped. A restore started from the admin UI restores only the databases and
  reports `"redis": "skipped"`.
- Stored secrets (LLM keys, SMTP password, Senet key) are encrypted with `ILER_SECRET_KEY` from the
  **original** server's `.env`. `--env` imports that key, and only that key. Without it, the
  secrets cannot be decrypted. Core logs `secret key mismatch`, treats the secrets as unset, and
  they must be entered again in the admin UI.

## Uninstall

```sh
sudo bash /opt/iler/deploy/install.sh --uninstall [--purge]
```

This also removes the firewall rules and `iler-firewall.service`.
