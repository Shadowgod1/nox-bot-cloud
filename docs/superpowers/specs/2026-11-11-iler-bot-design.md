# ILER Bot — on-prem WhatsApp AI-ассистент (дизайн v1)

Дата: 2026-11-11 · Статус: черновик на ревью

## 1. Цель

Однотенантный WhatsApp AI-ассистент, который **разворачивается у клиента на его сервере одной кнопкой** (Windows или Linux) и дальше работает автономно. Первый тип клиента — компьютерные клубы (SENET).

Критерии успеха v1:
1. Чистый сервер → установщик → через ≤15 минут бот отвечает в WhatsApp. Нужны только интернет, ключ LLM и телефон клуба для QR.
2. Админ клуба с ресепшн-ПК в локальной сети настраивает бота, видит переписки, перехватывает диалог и подтверждает заявки на бронь.
3. Наружу ходят только запросы к LLM API, WhatsApp (через Evolution), SENET API (если включён) и реестр образов при обновлении. Данные клиента никуда не уходят.
4. Обновление по кнопке в админке с автоматическим бэкапом перед ним.

## 2. Не входит в v1

Мультитенантность, биллинг/тарифы, CRM-интеграции (Bitrix и др.), PDF-документы, RAG по эталонам (векторы), Instagram/Telegram-каналы, локальная LLM, удалённый доступ к админке из интернета, лицензирование.

## 3. Происхождение и нейминг

Архитектурные решения и уроки взяты из существующего SaaS-продукта, код ядра пишется заново. **Ни одно имя не совпадает с исходным продуктом**: в идентификаторах, таблицах, путях API, переменных окружения, образах и UI запрещены названия исходного продукта и компании. Бренд — **ILER**. Нейтральные имена: `iler-core`, `iler-web`, таблицы без префикса, переменные `ILER_*`, API `/api/*`. В CI стоит grep-проверка `scripts/check-names.sh` на запрещённые строки. Сам список в репозиторий не коммитится: локально он лежит в неотслеживаемом `.forbidden-names`, в CI — в секрете `ILER_FORBIDDEN_NAMES`.

## 4. Архитектура

```
 Ресепшн-ПК (браузер) ──LAN:8080──► iler-web (nginx: SPA + /api proxy)
                                        │
                                        ▼
 WhatsApp ◄──► evolution-api ──webhook──► iler-core (FastAPI: API + движок + воркеры)
                   │                       │        │
                 redis                  postgres   LLM API (OpenAI/Gemini), SENET API
                   └──── postgres (db: evolution) ─┘
 iler-updater (docker socket) ◄── iler-core (internal HTTP, только по кнопке)
```

### Контейнеры (docker compose, одна внутренняя сеть `iler`)

| Сервис | Образ | Порт наружу | Том |
|---|---|---|---|
| iler-web | `ghcr.io/<owner>/iler-web:<ver>` (nginx-alpine) | `${ILER_HTTP_PORT:-8080}` | — |
| iler-core | `ghcr.io/<owner>/iler-core:<ver>` (python 3.12-slim) | нет | — |
| postgres | `postgres:16-alpine` (pin digest), 2 БД: `iler`, `evolution` | нет | `pg_data` |
| redis | `redis:7-alpine` (pin), `--appendonly yes --maxmemory-policy noeviction` | нет | `redis_data` |
| evolution-api | `evoapicloud/evolution-api` **v2.3.7 по digest**, никогда `latest` | нет | `evo_instances` |
| iler-updater | `ghcr.io/<owner>/iler-updater:<ver>` | нет | docker.sock, `/opt/iler` |
| iler-backup | iler-core образ, режим `backup` (cron внутри) | нет | `backups` (bind `/opt/iler/backups`) |

Требования к железу: минимум 2 vCPU / 4 ГБ RAM / 40 ГБ SSD, рекомендуется 4 / 8 / 100.

## 5. iler-core

Python 3.12, FastAPI, asyncpg, httpx, pydantic-settings. **Один процесс, один воркер uvicorn**: буфер склейки хранится в памяти. Структура:

```
core/iler/
  main.py            # app, lifespan, роутеры, воркеры
  config.py          # ILER_* env
  db/                # пул, мигратор, SQL-миграции core/migrations/NNNN_*.sql
  auth/              # пользователи, пароли (argon2), JWT в httpOnly cookie
  api/               # REST для админки (раздел 7)
  whatsapp/          # клиент Evolution, вебхук, статус/QR/pairing, монитор соединения
  engine/            # буфер, сборка промпта, цикл LLM+tools, отправка
  llm/               # провайдеры: openai.py, gemini.py, общий интерфейс + fake для тестов
  tools/             # реестр инструментов + встроенные + плагины (senet/)
  workers/           # inbound retry, outbound reconcile, connection monitor, booking timeout
  backup/            # pg_dump/redis snapshot/retention/restore
  templates/         # шаблоны ботов (computer_club.yaml, generic.yaml)
```

### 5.1 Поток сообщения
1. Evolution шлёт вебхук `POST /internal/evolution/webhook`. Проверяется заголовок `apikey`, и сеть: путь не проксируется из iler-web. Событие сохраняется в `inbound_events` (уникальный `evo_message_id`), сервер отвечает 200 сразу. Rate limit на этот путь не действует (урок: 429 → шторм ретраев → OOM).
2. Фоновая обработка: фильтры (группы, статусы, `fromMe`). Если `fromMe` пришло не от бота, отвечает оператор: диалог ставится на паузу на `handover_pause_minutes`. Сообщение пишется в `messages`, диалог в `conversations` создаётся или обновляется.
3. Буфер склейки на каждый чат: каждое новое сообщение перезапускает таймер `buffer_delay_seconds` (3–120, по умолчанию 8).
4. Допуск к ответу: бот не на паузе, диалог не перехвачен, рабочие часы (вне часов действует `after_hours_mode`: `reply` / `silent` / `fixed_message`).
5. Сборка промпта: системный промпт продукта (в коде) + профиль бота (персона, инструкции, услуги/цены, FAQ, адрес, часы) + контекст хода (текущее время в TZ клуба, имя клиента, последние N сообщений, `memory_messages`, по умолчанию 20).
6. Цикл LLM с **нативными tools**: до 4 итераций tool-call → результат → повторный вызов. Таймауты, ретраи, fallback-модель, затем аварийный провайдер (если задан второй ключ).
7. Маскирование ПДн перед вызовом LLM (телефоны, e-mail, номера карт/ИИН) и восстановление в ответе.
8. Отправка: разбиение по `[SPLIT]`/абзацам, `presence: composing` с паузой по длине текста. Запись в `outbound_intents` (идемпотентный ключ) → `sendText` → статус `sent` / `failed` / `uncertain`.
9. Журнал: `ai_requests` (модель, токены, латентность, ошибки), `tool_calls`.

### 5.2 Монитор WhatsApp
Каждую минуту проверяется `connectionState` и отдельно учитываются ошибки отправки (урок: полуоткрытая сессия рапортует `open`, но отправка падает). При разрыве дольше 5 минут: баннер в админке, а также сообщение админу, если доступен другой канал (e-mail по SMTP, если настроен). Повторное подключение — без удаления инстанса (урок: нельзя удалять и пересоздавать инстанс на уже открытой сессии).

### 5.3 Инструменты (tools)
Интерфейс плагина:
```python
class Tool(Protocol):
    name: str                      # snake_case, уникальное
    description: str               # для LLM
    parameters: dict               # JSON Schema
    def enabled(self, settings) -> bool: ...
    async def run(self, ctx: ToolContext, args: dict) -> ToolResult: ...  # ToolResult(content: str|dict, user_visible: bool=False)
```
Реестр собирает включённые инструменты на каждый ход. Ошибка инструмента возвращается модели как текст `error: ...` (fail-open), ход не падает.

Встроенные в v1:
- `create_booking_request(date, time, duration_hours, seats, zone?, name?, comment?)` создаёт строку в `booking_requests` (`pending`) и отправляет админу в WhatsApp (номер или группа из настроек) сообщение `Заявка #12: … Ответьте "+12" или "-12 причина"`. Клиенту бот пишет, что заявка передана.
- `handover_to_operator(reason)` ставит диалог в статус `needs_operator`, уведомляет админа и включает паузу.
- `get_club_info(topic)` возвращает секции профиля (цены, железо, адрес, акции) для длинных справочников, чтобы не раздувать промпт.

Обработка ответа админа: входящее сообщение с номера админа или из группы админов по шаблону `^[+-]\s*#?(\d+)` не идёт в LLM. Заявка переходит в `confirmed` / `rejected`, бот пишет клиенту итоговое сообщение (шаблон из настроек, при отказе с причиной). Те же действия доступны кнопками в админке. Таймаут `booking_request_timeout_min` (по умолчанию 15): клиенту приходит «администратор скоро свяжется», в админке ставится флаг.

Плагин SENET (`tools/senet/`, выключен без ключа):
- Настройки: `senet.orgname`, `senet.app_key` (хранится зашифрованным).
- HTTP-клиент: `https://{orgname}.api.enes.tech/`, заголовок `Authorization: key <app_key>`, таймаут 3 с, кэш 30 с.
- `senet_free_workstations(zone?)` использует `GET /workstations/`. Разбор полей защитный: точная схема ответа неизвестна до получения доступа к `/docs` клуба, поэтому адаптер изолирован в `senet/mapping.py` и покрыт тестом на фикстуре. Фикстуру обновляем, когда появится реальный ответ.
- Кнопка «Проверить подключение» в админке.
- Бронь в SENET через API в v1 не делается (метод не подтверждён), бронь идёт через `create_booking_request`.

### 5.4 Модель данных (основные таблицы)
`users(id, email, password_hash, name, role[owner|operator], created_at, last_login_at)`
`settings(key text pk, value jsonb, updated_at)` — профиль бота, LLM, часы, уведомления, SENET и т. д.; секреты шифруются (AES-GCM, ключ `ILER_SECRET_KEY`)
`wa_instance(id=1, evo_name, phone, status, last_seen_at, last_error)`
`conversations(id, chat_jid uniq, phone, name, status[active|paused|needs_operator|closed], paused_until, last_message_at, unread)`
`messages(id, conversation_id, direction[in|out], author[client|bot|operator|system], text, media jsonb, evo_message_id, created_at)`
`inbound_events(id, evo_message_id uniq, payload jsonb, status, attempts, error, created_at)`
`outbound_intents(id, idem_key uniq, conversation_id, text, status, attempts, evo_message_id, error, created_at)`
`booking_requests(id, conversation_id, payload jsonb, status[pending|confirmed|rejected|expired], admin_note, created_at, decided_at, decided_by)`
`ai_requests(id, conversation_id, provider, model, input_tokens, output_tokens, latency_ms, error, created_at)`
`tool_calls(id, ai_request_id, name, args jsonb, result jsonb, error, created_at)`
`audit_log(id, user_id, action, details jsonb, created_at)`

Миграции — нумерованные SQL-файлы, применяются при старте, с advisory-lock.

## 6. iler-web (админка)

React 19 + Vite + TypeScript + Tailwind. Экраны и подходы к UI переносим из исходного продукта по смыслу, тексты и имена свои.

1. **Первый запуск** (если пользователей нет): владелец (e-mail + пароль) → ключ LLM (OpenAI или Gemini, с проверкой) → шаблон бота («Компьютерный клуб» / «Универсальный») → подключение WhatsApp (QR или 8-значный код) → готово.
2. **Вход.**
3. **Обзор:** статус WhatsApp, бот вкл/выкл, сегодня (диалоги, ответы, заявки), ошибки.
4. **Диалоги:** список и чат, ответ оператора, «перехватить» / «вернуть боту», статусы. Обновление через SSE `/api/events`.
5. **Заявки на бронь:** список, подтвердить или отклонить, фильтры.
6. **Настройки бота:** персона и имя, инструкции (редактор промпта), услуги и цены, FAQ, адрес, рабочие часы, задержка склейки, пауза после оператора, приветствие, шаблоны ответов на заявки.
7. **Тестовый чат:** диалог с ботом без WhatsApp, с показом вызовов инструментов.
8. **Интеграции:** SENET (orgname, ключ, проверка), уведомления админу (номер или группа WhatsApp, SMTP).
9. **Система:** LLM-провайдер, модели и ключи, пользователи (owner/operator), версия и «Обновить», бэкапы (список, скачать, восстановить), журнал.

## 7. API (контракт web ↔ core)

Все пути с префиксом `/api`. Авторизация через cookie `iler_session` (JWT HS256, 12 ч, sliding). CSRF: заголовок `X-ILER-CSRF` = значение cookie `iler_csrf` для мутаций.

```
GET  /api/setup/state                        -> {needs_setup: bool}
POST /api/setup/owner      {email,password,name}
POST /api/auth/login       {email,password}  POST /api/auth/logout   GET /api/auth/me
POST /api/auth/password    {old,new}
GET  /api/dashboard
GET  /api/whatsapp/status                    -> {status, phone, last_error}
POST /api/whatsapp/connect                   -> {qr_base64?} ; POST /api/whatsapp/pairing {phone} -> {code}
POST /api/whatsapp/logout
GET  /api/conversations?status=&q=&cursor=   GET /api/conversations/{id}/messages?cursor=
POST /api/conversations/{id}/send {text}     POST /api/conversations/{id}/takeover   POST /api/conversations/{id}/release
GET  /api/bookings?status=                   POST /api/bookings/{id}/confirm {note?}  POST /api/bookings/{id}/reject {note?}
GET  /api/settings/bot   PUT /api/settings/bot
GET  /api/settings/llm   PUT /api/settings/llm   POST /api/settings/llm/test
GET  /api/settings/notify PUT /api/settings/notify
GET  /api/integrations/senet PUT /api/integrations/senet POST /api/integrations/senet/test
POST /api/test-chat {messages:[...]} -> {reply, tool_calls}
GET  /api/templates      POST /api/templates/{id}/apply
GET  /api/users POST /api/users DELETE /api/users/{id}          (owner)
GET  /api/system/version  POST /api/system/update  GET /api/system/update/status   (owner)
GET  /api/backups POST /api/backups GET /api/backups/{name} POST /api/backups/{name}/restore (owner)
GET  /api/events  (SSE: message.new, conversation.updated, booking.updated, whatsapp.status)
GET  /health  (без /api, для healthcheck)
```
Ошибки возвращаются в формате `{error:{code,message}}`. Схемы pydantic, OpenAPI на `/api/openapi.json`. web генерирует типы из него.

## 8. Установка, сеть, обновления, бэкапы

### Linux (`deploy/install.sh`, Ubuntu 22.04/24.04, Debian 12)
`curl -fsSL <url>/install.sh | sudo bash` (или запуск из архива):
1. Проверка ОС, RAM, диска и свободного порта.
2. Установка Docker Engine + compose plugin, если их нет.
3. Каталог `/opt/iler`, генерация `.env` (секреты через `openssl rand`), `docker login ghcr.io` read-only токеном из поставки.
4. `docker compose pull && up -d`, ожидание `/health`.
5. Правило ufw (если ufw активен): порт админки открыт только для частных подсетей.
6. Вывод адресов `http://localhost:8080` и `http://<lan-ip>:8080`, публикация mDNS `iler.local` через avahi (если доступен).
7. systemd не нужен: у compose `restart: unless-stopped`, а Docker включён в автозапуск.

Флаги: `--port`, `--dir`, `--offline <images.tar>` (установка без реестра), `--uninstall` (с подтверждением; тома не удаляются без `--purge`).

### Windows (`deploy/windows/install.ps1` → `ILER-Setup.exe` через Inno Setup)
Поддержка: Windows 10 22H2 / 11 Pro, Windows Server 2022+. Windows Server 2019 не поддерживается (там нет WSL2).
1. Проверка прав администратора и виртуализации.
2. Включение WSL и VirtualMachinePlatform. Если нужна перезагрузка, ставится задача RunOnce на продолжение установки.
3. `wsl --install -d Ubuntu-24.04 --no-launch`, создание пользователя без интерактива.
4. Копирование поставки внутрь WSL и запуск того же `install.sh`.
5. Сеть: на Win11 22H2+ — `networkingMode=mirrored` в `.wslconfig`, иначе `netsh interface portproxy` на IP WSL плюс задача, которая обновляет его при старте. Правило Windows Firewall: порт админки, профиль Private/Domain, только локальные подсети.
6. Автозапуск: задача планировщика «при старте системы» поднимает WSL-дистрибутив (Docker стартует внутри через systemd в WSL).
7. Ярлык «ILER Bot — админка» на рабочем столе.

### Обновления
`iler-updater` слушает только внутреннюю сеть и принимает запросы от iler-core с общим секретом. По кнопке: бэкап → `pull` новых тегов из `deploy/channel` (stable) → `up -d` → проверка `/health` → при неудаче откат на прежние теги. Версия поставки лежит в `/opt/iler/VERSION`. Автоматических обновлений нет.

### Бэкапы
Ежедневно в 04:00 (время клуба): `pg_dump -Fc` обеих БД + снимок redis RDB (сессия WhatsApp, без неё пришлось бы заново подключать номер) + manifest с SHA-256. Хранятся 14 дней в `/opt/iler/backups`. Восстановление из админки или `install.sh --restore <file>`.

## 9. Безопасность
- Наружу открыт только iler-web на порту админки, файрвол пускает только LAN. Evolution, postgres и redis не публикуются.
- Пароли хэшируются argon2id; блокировка после 10 неудачных попыток на 15 минут; cookie `HttpOnly`, `SameSite=Strict`.
- Секреты в `settings` шифруются (`ILER_SECRET_KEY` из `.env`, права 600).
- Никаких сидов с фиксированными паролями или токенами; владелец создаётся только в мастере первого запуска.
- Логи без ПДн: телефоны маскируются.
- Вебхук Evolution доступен только внутри docker-сети, nginx не проксирует `/internal/*`.

## 10. Тестирование
- **core:** pytest. Есть `FakeLLM` (скриптуемые ответы и tool-calls) и `FakeEvolution` (httpx MockTransport). Покрытие: склейка, допуск (часы/пауза/перехват), цикл tools, заявки и ответы админа `+N`/`-N`, идемпотентность отправки, маскирование ПДн, миграции на чистой БД (postgres в testcontainers или в сервисе CI), auth/CSRF, маппинг SENET на фикстуре.
- **web:** vitest на ключевые хуки и формы; сборка и typecheck в CI.
- **e2e smoke (CI):** `docker compose -f compose.yml -f compose.test.yml up` с `fake-evolution` вместо настоящего Evolution → мастер первого запуска через API → входящее сообщение → ответ ушёл в fake-evolution.
- **deploy:** shellcheck, PSScriptAnalyzer; `install.sh` прогоняется в CI на ubuntu-runner с локально собранными образами (`--offline`).
- Запрещённые имена: grep в CI.

## 11. Уроки из исходного продукта (обязательны)
1. Evolution закрепляется по digest, никогда `latest`.
2. Не удалять и не пересоздавать инстанс при открытой сессии; pairing только из состояния `close`.
3. Подписаться на `CONNECTION_UPDATE`. В v2 `data.instance` — строка.
4. Мониторить ошибки отправки, а не только `connectionState`.
5. Вебхук исключён из rate limit, событие сохраняется до обработки, сервер отвечает 200 сразу.
6. Нельзя удалять инстанс в Evolution до успешного удаления в БД.
7. Redis Evolution: `appendonly`, `noeviction`, бэкапится.
8. Время в Evolution может быть смещено, поэтому опираемся на своё время получения.

## 12. Открытые вопросы (не блокируют v1)
- Лицензия Evolution API 2.3.7 при перепродаже on-prem (логотип, уведомление об использовании). Нужна юридическая проверка до первого коммерческого клиента.
- Полная схема SENET API: методы броней, вебхуки, лимиты. Выясняется после получения доступа к `/docs` клуба.
- Права на переиспользование подходов из исходного продукта — на усмотрение владельца проекта.

## 13. Разбиение на параллельные потоки

Владение каталогами не пересекается, контракт между потоками — разделы 5.3, 5.4 и 7.

| Поток | Каталоги | Результат |
|---|---|---|
| A. Ядро | `core/` кроме `core/iler/tools/senet/` | API, движок, WhatsApp, воркеры, бэкап, тесты, Dockerfile |
| B. Админка | `web/` | все экраны раздела 6, Dockerfile с nginx.conf |
| C. Поставка | `deploy/`, `.github/workflows/`, `compose*.yml`, `updater/`, `fake-evolution/` | compose, установщики Linux и Windows, updater, CI, e2e smoke |
| D. Интеграции | `core/iler/tools/senet/`, `core/iler/templates/` | плагин SENET, шаблоны ботов, тесты |

Порядок: сначала поток A коммитит каркас (интерфейс Tool, модели настроек, OpenAPI-заглушки), затем B, C и D идут параллельно с A. Финал: интеграция, e2e, ревью кода и проверка безопасности.
