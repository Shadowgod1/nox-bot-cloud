# ILER cloud: several clients on one machine

Notes for the ILER technician. The cloud server is described in `iler-vault/architecture/cloud-infra.md`.
Full server setup (VM, Cloudflare, tunnel) is in `iler-vault/runbooks/cloud-deploy.md`. This file only
covers client instances.

Each client is its own ILER installation:

| What | Value |
|---|---|
| Directory | `/opt/iler/clients/<name>/` (`.env`, `compose.yml`, `deploy/`, `backups/`) |
| docker compose project | `iler-<name>`: containers, volumes (`iler-<name>_pg_data`, ...) and networks are separate for every client |
| Admin port | `127.0.0.1:<port>`, so it is reachable only through the tunnel. No firewall rules and no `iler.local` |
| Public address | `https://<name>.<domain>`, a public hostname in the Cloudflare tunnel |
| LLM keys, coin limit, model prices | the client's own `.env` (`ILER_*_KEY`, `ILER_MONTHLY_COINS`, `ILER_MODEL_PRICES`) |
| Brand | `ILER_BRAND=nox` by default: cloud clients are clients of Nox service (see [Brand](#brand)) |

Docker images are shared by all clients. Postgres, Redis and the WhatsApp gateway are never shared.

## Create a client

From the unpacked release bundle (the folder with `compose.yml`, `deploy/` and, for offline installs,
`images.tar`):

```sh
sudo bash deploy/cloud/iler-client.sh create acme --port 8101 --coins 30000 \
     --public-url https://acme.example.kz
```

- `<name>` may contain only `a-z`, `0-9` and `-` (not first or last), 2–32 characters, so it is a valid DNS label. It also names the project (`iler-acme`).
- `--port` sets the admin port on `127.0.0.1`. Give each client its own port, for example `8101`, `8102`, and so on.
  The script stops before writing anything if the port is in use, if another client already has it
  (stopped clients count too), if the client already exists, or if containers or volumes of
  `iler-<name>` are left over from earlier.
- `--coins` sets the monthly coin limit (`0` means no limit).
- `--public-url` sets the client's public address. With `https://`, session cookies are `Secure` and
  the setup link is printed with this address.
- `--offline FILE` loads images from an archive. By default the script uses `images.tar` next to
  `compose.yml` if it exists; otherwise it pulls from the registry.
- `--prices JSON` overrides the GPT/Gemini prices. The default is
  `{"gpt-5.4-mini": [0.75, 4.5], "gemini-3.8-flash": [0.75, 3.75]}` ($ per 1M tokens). Without prices,
  coins are billed at the highest tariff.
- `--brand nox|iler` sets the admin UI brand. The default for cloud clients is `nox`; use
  `--brand iler` for a client that should see the ILER brand.

The script runs `deploy/install.sh --dir /opt/iler/clients/acme --name iler-acme --bind 127.0.0.1 ...`
and prints the owner's one-time setup link (`https://acme.example.kz/setup?token=...`). Send it to the
owner privately and never post it in group chats.

Bots created from the Nox portal skip the setup link. The provisioner passes the owner from the
application in the environment of `create`: `ILER_OWNER_EMAIL`, `ILER_OWNER_NAME`, `ILER_COMPANY` and
`ILER_OWNER_PASSWORD_HASH` (the argon2id hash of the cabinet password). They are never passed as options,
because the command line is visible in `ps`. The script writes them to the client `.env`. On the first
start core creates the owner from them, so the client logs in with the cabinet e-mail and password. Once
an owner exists, core ignores them. The provisioner empties the hash in `.env` as soon as core has used it.

Then:

1. **LLM keys** (the technician types them; they never go into chats or documents):
   `sudo bash /opt/iler/clients/acme/deploy/set-llm-keys.sh --dir /opt/iler/clients/acme`
2. **Public hostname in the tunnel.** Go to Cloudflare Zero Trust → Networks → Tunnels → `<tunnel>` →
   Public Hostname → Add. Set Subdomain `acme`, Domain `example.kz`, Service **HTTP** `127.0.0.1:8101`.
   Use only one subdomain level, because the free certificate does not cover `a.b.example.kz`. The
   script does not call the Cloudflare API.
3. Open `https://acme.example.kz`. The admin UI must load over HTTPS. Then send the setup link.

## Brand

One codebase, two brands, picked per installation by `ILER_BRAND` in the client's `.env`:

| `ILER_BRAND` | Who | Admin UI |
|---|---|---|
| `iler` (default of `install.sh`) | on-prem installs | ILER wordmark and colours, one light theme |
| `nox` (default of `iler-client.sh create`) | cloud clients of Nox service | Nox name and favicon, the look of the Nox service CRM, light/dark switch in the sidebar |

The brand also changes user-facing texts ("Вход в Nox", "Коины Nox", "обратитесь в Nox service") and
the server's messages to admins (coin alerts, e-mail subjects). Protocol names (`X-ILER-*` headers,
`iler_*` cookies, `ILER_*` settings) stay the same for both.

Re-running `install.sh` without `--brand` and updates through the admin UI keep the value. To switch an
existing client:

```sh
sudo bash /opt/iler/clients/acme/deploy/install.sh --dir /opt/iler/clients/acme --brand iler --yes
```

The admin UI caches the brand in the browser and corrects it after the first request, so after a switch
the old brand may show for a moment on the next page load.

## Behind the tunnel

In cloud mode (`ILER_PUBLIC_URL` set or `ILER_HTTP_BIND=127.x`), nginx in `iler-web` handles the
tunnel's headers:

- It takes the visitor's IP from the tunnel's `CF-Connecting-IP` header, but only when the connection
  comes from loopback or a Docker network (`172.16.0.0/12`, `192.168.0.0/16`). This lets the login rate
  limit (10/min, burst 5) block one visitor at a time instead of everyone together.
- It passes the original scheme (`X-Forwarded-Proto: https`) to the core.

LAN installs ignore these headers.

If Docker's address pools on the machine were changed to `10.x`, the visitor IP is not taken from the
header. All visitors would then share one limit. Keep the default pools.

## Everyday commands

Run the script from the unpacked release bundle, not from inside a client's folder (`remove` deletes
that folder, including its copy of the script):

```sh
sudo bash deploy/cloud/iler-client.sh list          # name, port, status, version
sudo bash deploy/cloud/iler-client.sh stop acme     # pause: containers stop, data stays
sudo bash deploy/cloud/iler-client.sh start acme
sudo bash deploy/cloud/iler-client.sh remove acme   # asks first; --yes skips the question
```

`remove` deletes the client completely: its containers, volumes (database, WhatsApp session), backups
and `/opt/iler/clients/<name>`. Other clients are not touched. Before removing a client, copy the
latest backup from `/opt/iler/clients/<name>/backups/` if it may be needed later. Then delete the
public hostname in the tunnel.

Updates go per client with the "Update" button in that client's admin UI. The updater works only on
its own project (`-p iler-<name>`). Do a canary first: update one client, wait a day, then update the
rest.

To change the public address later, run
`sudo bash /opt/iler/clients/acme/deploy/install.sh --dir /opt/iler/clients/acme --public-url https://new.example.kz --yes`.
The project name cannot be changed.
