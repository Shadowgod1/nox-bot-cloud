# ILER Bot

On-prem WhatsApp AI-ассистент для бизнеса (первый сценарий — компьютерные клубы). Ставится одной кнопкой на сервер клиента (Linux или Windows), админка открывается в браузере в локальной сети.

- Дизайн: [docs/superpowers/specs/2026-11-11-iler-bot-design.md](docs/superpowers/specs/2026-11-11-iler-bot-design.md)
- План: [docs/superpowers/plans/2026-11-11-iler-bot-v1.md](docs/superpowers/plans/2026-11-11-iler-bot-v1.md)

## Структура

| Каталог | Что внутри |
|---|---|
| `core/` | iler-core: FastAPI (API, движок, воркеры), миграции, тесты |
| `web/` | iler-web: админка (React + Vite) |
| `deploy/` | установщики Linux/Windows, compose-окружение |
| `updater/` | сервис обновлений по кнопке |
| `fake-evolution/` | тестовый двойник Evolution API для e2e |

## Разработка core

```bash
docker run -d --name iler-test-pg -p 55432:5432 -e POSTGRES_PASSWORD=test postgres:16-alpine
cd core && python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

Перед коммитом: `bash scripts/check-names.sh`.
