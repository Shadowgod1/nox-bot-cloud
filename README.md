# ILER Bot

> Внутреннее (кодовое) имя продукта — **ILER**; клиентский бренд — **Nox Bot** (Nox service, noxsw.kz). См. раздел «Бренды».

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

## Бренды

- **ILER** — внутреннее имя для разработчиков: репозиторий, пакеты и образы (`iler-core`, `iler-web`,
  `iler-updater`), переменные окружения `ILER_*`, путь `/opt/iler`, compose-проект `iler`, папка
  `C:\ProgramData\ILER`, имена задач Планировщика и правил брандмауэра, Inno `AppId`. Их не меняем:
  на них держатся обновление существующих установок, бэкапы и updater.
- **Nox Bot** (коротко «Nox», компания Nox service, https://noxsw.kz) — то, что видит клиент: установщик
  Windows (`Nox-Bot-Setup-<версия>.exe`, имя в «Программах и компонентах», ярлыки), тексты install.ps1,
  `deploy/INSTALL-ru.txt`, PDF-инструкция (`scripts/build-manual.py`).
- Админка выбирает оформление по настройке `ILER_BRAND` (`iler` | `nox`) в `.env` экземпляра;
  Windows-установщик пишет `ILER_BRAND=nox` при новой установке и при обновлении, если ключа ещё нет
  (явно заданное значение не трогает).

