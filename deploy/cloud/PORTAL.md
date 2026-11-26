# Портал Nox Bot (bot.noxsw.kz) — инструкция для техника

Портал — это сайт, где компании подают заявку на WhatsApp-бота, сотрудники Nox одобряют её, а бот
создаётся на облачном сервере сам, по адресу `https://<адрес>.noxsw.kz`.

## Как устроено

| Часть | Где | Что делает |
|---|---|---|
| `web` (nginx + сайт) | контейнер, `127.0.0.1:8100` | единственный вход снаружи: туннель Cloudflare `bot.noxsw.kz → http://127.0.0.1:8100` |
| `api` (FastAPI) | контейнер, без docker-сокета и без root | заявки, вход, письма, очередь заданий; внутренний API для провизионера на `127.0.0.1:8099` (через туннель и nginx недоступен) |
| `db` (Postgres 16) | контейнер, только внутренняя сеть | данные портала, том `nox-portal_pg_data` |
| `nox-provisioner` | systemd на хосте, root, раз в 30 с | забирает одобренные заявки, запускает `iler-client.sh`, ставит ключ LLM, создаёт адрес в туннеле и DNS, сообщает результат порталу |

Секреты:

- `/opt/nox-portal/.env` (root, 600) — настройки портала, SMTP, `NOX_SECRET_KEY`, пароль БД.
- `/etc/nox-portal/provisioner.env` (root, 600) — токен провизионера, ключ Anthropic по умолчанию, токен Cloudflare.
- `/etc/nox-portal/provisioner-key.pem` (root, 600) — закрытый ключ. Им провизионер расшифровывает
  персональные ключи Anthropic клиентов. Портал хранит такой ключ только зашифрованным открытым ключом
  (`/opt/nox-portal/provisioner-pub.pem`) и стирает его после успешного создания бота.

Ключи и токены набирайте прямо на сервере. Не отправляйте их в чаты, тикеты и документы.

## 1. Установка

Нужен облачный сервер с Docker и распакованным релизом iler-bot в `/opt/iler/bundle`
(папка с `compose.yml`, `deploy/` и `images.tar`). Порталу нужна папка `portal/` из того же репозитория.

```sh
cd /opt/iler/bundle            # или клон репозитория iler-bot
sudo bash deploy/cloud/portal-install.sh
```

Скрипт можно запускать повторно: секреты и данные сохраняются, образы пересобираются.
Он создаёт `/opt/nox-portal`, генерирует секреты, поднимает контейнеры (проект `nox-portal`) и ставит
`nox-provisioner.service` с таймером.

Проверка: `curl -fsS http://127.0.0.1:8100/health` выводит `{"status":"ok",...}`.

## 2. Почта (SMTP)

Откройте `sudo nano /opt/nox-portal/.env` и заполните:

```
NOX_EMAIL_MODE=smtp
NOX_SMTP_HOST=smtp.example.kz
NOX_SMTP_PORT=587            # 465 для SSL
NOX_SMTP_SECURITY=starttls   # starttls | ssl | none
NOX_SMTP_USER=bot@example.kz
NOX_SMTP_PASSWORD=...        # пароль ящика или пароль приложения
NOX_SMTP_FROM=bot@example.kz
NOX_STAFF_NOTIFY=ops@example.kz,boss@example.kz   # кому писать о новых заявках
```

Затем перезапустите: `sudo bash deploy/cloud/portal-install.sh`.
Чтобы письма не попадали в спам, у домена отправителя должны быть настроены SPF и DKIM.

`NOX_EMAIL_MODE=log` нужен только для проверки на тестовом стенде: письма с кодами и ссылками
пишутся в лог (`cd /opt/nox-portal && sudo docker compose logs api`). На боевом сервере этот режим не включайте.

## 2a. Оплата по счёту и тарифы

На старте клиенты платят по счёту банковским переводом, онлайн-эквайринга нет. Порядок такой:
заявка → код из письма → клиент выбирает тариф → портал выставляет счёт (письмо и страница
`/invoice`, её можно распечатать или сохранить в PDF) → статус «Ждёт оплаты». Когда деньги пришли,
сотрудник нажимает в карточке заявки «Отметить оплату» и вводит сумму и номер платёжки. Платёж
записывается в журнал, а бот сразу встаёт в очередь на создание. Оплата и одобрение — одно действие.

- Тарифы (название, цена в ₸ в месяц, коины) лежат в одном месте:
  `portal/backend/nox_portal/tariffs.py`. Цены в репозитории — заготовки, их нужно утвердить
  до запуска. После правки запустите `portal-install.sh` ещё раз. Уже выставленные счета сохраняют свою сумму.
- Реквизиты продавца для счёта — `NOX_SELLER_*` в `/opt/nox-portal/.env` (название, БИН, банк, ИИК,
  БИК, Кбе, адрес, телефон). В репозитории только заглушки. Срок оплаты — `NOX_INVOICE_DUE_DAYS`.
- Журнал платежей (таблица `payments`) только дополняется: изменить или удалить строку база не даст.
  Ошибку или возврат оформляют новой строкой с отрицательной суммой (пока через SQL, кнопки в интерфейсе нет).
- «Одобрить без оплаты» остаётся для пробного периода и партнёров. Такие одобрения попадают в журнал
  действий с пометкой `payment: null`.

## 3. Cloudflare

### Токен API

Cloudflare → My Profile → API Tokens → Create Token → Custom token:

| Тип | Ресурс | Право |
|---|---|---|
| Account | Cloudflare Tunnel | Edit |
| Zone | DNS | Edit |

В Zone Resources выберите только зону `noxsw.kz`, в Account Resources — только аккаунт Nox.

Также нужны:

- **Account ID** — на странице зоны noxsw.kz справа (Overview → API);
- **Zone ID** — там же;
- **Tunnel ID** — Zero Trust → Networks → Tunnels → `iler-cloud-01` (UUID).

Впишите их в `sudo nano /etc/nox-portal/provisioner.env`: `CF_API_TOKEN`, `CF_ACCOUNT_ID`,
`CF_ZONE_ID`, `CF_TUNNEL_ID`. Туда же впишите `NOX_DEFAULT_ANTHROPIC_KEY`: это общий ключ для
клиентов без своего ключа.

Провизионер для каждого бота:

1. читает конфигурацию туннеля (`GET /accounts/{account}/cfd_tunnel/{tunnel}/configurations`);
2. добавляет правило `<адрес>.noxsw.kz → http://127.0.0.1:<порт>` перед последним правилом
   `http_status:404` и отправляет весь список обратно (`PUT` на тот же адрес). Остальные правила
   сохраняются;
3. создаёт DNS-запись `CNAME <адрес> → <tunnel-id>.cfargotunnel.com` с проксированием (оранжевое облако).

Если на этот адрес уже есть чужая DNS-запись, провизионер её не трогает и выдаёт ошибку.

Проверка настроек (ничего не меняет):

```sh
sudo bash -c 'set -a; . /etc/nox-portal/provisioner.env; python3 /opt/nox-portal/bin/nox-provisioner.py check'
```

После успешной проверки включите таймер: `sudo systemctl enable --now nox-provisioner.timer`.

### Адрес самого портала

Добавьте его один раз вручную: Zero Trust → Networks → Tunnels → `iler-cloud-01` → Public Hostname → Add.
Subdomain `bot`, Domain `noxsw.kz`, Service **HTTP** `127.0.0.1:8100`.

Внутренний порт `8099` в туннель не добавляйте никогда.

## 4. Учётные записи сотрудников

Учётную запись сотрудника можно создать только командой на сервере, на сайте регистрации для них нет:

```sh
cd /opt/nox-portal && sudo docker compose exec api python manage.py create-staff --email ops@example.kz --name "Имя"
cd /opt/nox-portal && sudo docker compose exec api python manage.py set-staff-password --email ops@example.kz
cd /opt/nox-portal && sudo docker compose exec api python manage.py disable-staff --email ops@example.kz
cd /opt/nox-portal && sudo docker compose exec api python manage.py list-staff
```

Пароль вводится скрыто, его длина — не меньше 10 символов. Вход для сотрудников: `https://bot.noxsw.kz/staff/login`.

## 5. Проверка всей цепочки

1. Откройте `https://bot.noxsw.kz/register` и подайте заявку на свою почту с адресом вроде `test-nox`.
2. Введите код из письма и выберите тариф. Придут письмо со счётом и уведомление на `NOX_STAFF_NOTIFY`;
   счёт откроется по кнопке «Открыть счёт (PDF)».
3. Войдите как сотрудник, откройте заявку (фильтр «Ждёт оплаты»), нажмите «Отметить оплату» и
   укажите сумму и номер платёжки.
4. В течение минуты статус станет «Создаётся», а через несколько минут — «Работает».
   Ход работы: `sudo journalctl -u nox-provisioner -f`.
5. Придёт письмо «Ваш бот готов» с адресом панели и одноразовой ссылкой. Откройте
   `https://test-nox.noxsw.kz`: панель должна загрузиться по HTTPS.
6. Удалите тестового бота: в карточке заявки нажмите «Удалить бота» и введите адрес. Проверьте, что
   правило и CNAME исчезли в Cloudflare.

Если создание не удалось, статус станет «Ошибка». Текст ошибки виден в карточке заявки и в
`journalctl -u nox-provisioner`. Исправьте причину и нажмите «Повторить создание». Если бот уже был
установлен, повтор его не пересоздаёт, а только доделывает оставшиеся шаги.

## 6. Резервная копия базы портала

```sh
sudo mkdir -p /opt/nox-portal/backups
cd /opt/nox-portal && sudo docker compose exec -T db pg_dump -U portal -d portal -Fc \
  > /opt/nox-portal/backups/portal-$(date +%Y%m%d-%H%M).dump
```

Ежедневно в 03:00 по Алматы (`/etc/cron.d/nox-portal-backup`):

```
CRON_TZ=Asia/Almaty
0 3 * * * root cd /opt/nox-portal && docker compose exec -T db pg_dump -U portal -d portal -Fc > /opt/nox-portal/backups/portal-$(date +\%Y\%m\%d).dump && find /opt/nox-portal/backups -name 'portal-*.dump' -mtime +14 -delete
```

Вместе с копией базы сохраняйте `/opt/nox-portal/.env` и всю папку `/etc/nox-portal/`. Без
`NOX_SECRET_KEY` не расшифровать неотправленные ссылки владельцев, а без `provisioner-key.pem` —
ключи клиентов, которые стоят в очереди.

Восстановление:

```sh
cd /opt/nox-portal && sudo docker compose exec -T db pg_restore -U portal -d portal --clean --if-exists < portal-YYYYMMDD.dump
```

## 7. Обслуживание

| Задача | Команда |
|---|---|
| логи портала | `cd /opt/nox-portal && sudo docker compose logs -f api web` |
| логи провизионера | `sudo journalctl -u nox-provisioner -n 200` |
| запустить провизионер сейчас | `sudo systemctl start nox-provisioner.service` |
| обновить портал | `git pull` (или новый бандл), затем `sudo bash deploy/cloud/portal-install.sh` |
| остановить портал | `cd /opt/nox-portal && sudo docker compose stop` |

Если провизионер не прислал результат за 30 минут, задание помечается как ошибка с текстом про
таймаут. Тогда посмотрите `journalctl` и нажмите «Повторить».

Порты новых ботов выбираются из диапазона `NOX_PORT_MIN`–`NOX_PORT_MAX` (по умолчанию 8110–8999).
Занятые порты пропускаются: и у работающих, и у остановленных клиентов. Порты 8099 и 8100 занимает
портал.
