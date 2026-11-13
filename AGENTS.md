<!-- bmad:context -->
<!-- Verified 2026-11-13 against f98c4e6. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## iler-bot

On-prem single-tenant WhatsApp AI assistant installed at a client site (Windows 11 via WSL2, or Ubuntu Server 24.04). Python 3.12 FastAPI core (`core/`), React 19/Vite admin (`web/`), docker compose deploy (`deploy/`, `compose.yml`). Documentation, ADRs, runbooks and the bug log live in the separate repo `../iler-vault`, not here.

## Policy

- Never write names of the company's production SaaS (old project/org names) in code, docs, commits or UI; brand is "ILER" in Latin letters. Run `scripts/check-names.sh` before committing (list comes from the untracked `.forbidden-names`).
- Never commit `.forbidden-names`, `.env` files or real API keys; examples use placeholders like `sk-ant-api03-…`.
- LLM API keys belong to ILER and live only in the server `.env` (`ILER_*_KEY`, set by `deploy/set-llm-keys.sh`); never store them in the DB or expose them in the admin UI or API responses.
- Push to `main` directly after tests pass; no PR required.
- New docs, ADRs, release notes and every bug found in testing or review go to `../iler-vault` (`docs/`, `decisions/`, `bugs/README.md`, `releases/README.md`), not to `iler-bot/docs`.
- BMAD planning artifacts: keep `_bmad/` committed; copy finished documents from `_bmad-output/` into `../iler-vault`.

## Running and verifying

- Never report work as done without running the checks for what you touched: core tests, web `tsc`+`vitest`+`build`, PowerShell parse check, installer compile; for installer/deploy changes say plainly that a real install was not run.
- Core tests need Postgres on 55432: `docker start iler-test-pg`, then from `core/`: `ILER_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres .venv/Scripts/python.exe -m pytest -q` — use `127.0.0.1` (localhost hangs on IPv6 under WSL mirrored networking) and the venv Python (system Python has no pytest).
- PowerShell parse check: `powershell.exe -NoProfile -Command '$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path deploy/windows/install.ps1),[ref]$null,[ref]$e);"errors=$($e.Count)"'` must print `errors=0`.
- Installer compile runs from the staged bundle `build/win`: `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/work" -w /work amake/innosetup /DAppVersion=<ver> /DSpan deploy/windows/ILER-Setup.iss`; full release steps: `../iler-vault/runbooks/release.md`.
- The local test install runs in WSL distro `Ubuntu-24.04` at `/opt/iler`; inspect it with `wsl -d Ubuntu-24.04 -u root -- …`, never with the host Docker Desktop.

## Conventions that differ from defaults

- `iler-core` runs exactly one uvicorn worker (message buffer is in memory); never add workers or a second core replica.
- `deploy/windows/install.ps1` and `ILER-Setup.iss`: UTF-8 with BOM and CRLF; all `.sh`: LF. Check bytes after editing.
- `install.ps1` targets Windows PowerShell 5.1: no `&&`, `??`, ternary.

## Known pitfalls

- PS 5.1: `& native 2>&1` under `$ErrorActionPreference='Stop'` throws on any stderr line — use `Invoke-Wsl` or a local `Continue` and check `$LASTEXITCODE`.
- PowerShell variables are case-insensitive: never name a local like a parameter (`$path` overwrote `$Path`).
- `wsl.exe -- cmd <path>` goes through the distro shell, which eats backslashes in unquoted args; pass Windows paths with forward slashes (`ConvertTo-WslArg`).
- Inno `[Code]`: a line starting with `[` is parsed as a section tag and breaks the compile — keep array literals on the previous line.

<!-- /bmad:context -->
