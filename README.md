# Газгольдер CRM

FastAPI + SQLite, веб-интерфейс на `/`, Swagger на `/docs`.

## Запуск
    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8000

Переменные окружения (см. `.env.example`): `API_KEY` (заголовок `X-API-Key` или `?api_key=`), `WEBHOOK_URL`, `DB_PATH`.

## Сущности
- `referrers` — кто приводит (вознаграждение по умолчанию)
- `clients` — клиент, 2 телефона, Telegram, откуда пришёл, адрес, кто привёл
- `tanks` — газгольдеры клиента (объём, марка, серийник, интервал заправки в днях, дата установки, дата следующего ТО)
- `refills` — заявки/заправки: статус (`new|scheduled|done|cancelled`), даты, литры, цена за литр, итого, себестоимость, вознаграждение, оплата, `next_refill_date`

Автоматика в заявке: привёдший берётся из клиента, интервал из газгольдера, `total_price = литры * цена`, `next_refill_date = done_date + interval_days`, при `done` без даты ставится сегодня.

## API (всё под `/api`)
| метод | путь |
|---|---|
| GET/POST | `/refills`, `/clients`, `/tanks`, `/referrers` |
| GET/PATCH/DELETE | `/<сущность>/{id}` |
| GET | `/refills?status=&client_id=&referrer_id=&date_from=&date_to=&q=` |
| GET | `/clients/overview` — клиенты со сводкой (последняя/следующая заправка, выручка) |
| GET | `/clients/{id}/card` — карточка: клиент, газгольдеры со сводкой, история, итоги |
| GET | `/referrers/overview` — привёдшие: клиенты, заправки, выручка |
| GET | `/due?days=7` — кому пора заправляться (с просроченными) |
| GET | `/stats` — выручка, прибыль, по привёдшим, по месяцам, неоплаченные |
| GET | `/export/refills.csv` |

## Интеграции
- Webhook: при `WEBHOOK_URL` на каждое событие шлётся POST `{"event": "refill.created|refill.updated|refill.done", "data": {...заявка...}}` (подходит для n8n).
- Напоминания: n8n по крону раз в день дёргает `/api/due?days=3` и шлёт сообщения клиентам.
- Создание заявки из формы/бота: `POST /api/refills`.

## Вход по паролю и хостинг
Локально без `USERS` вход выключен. Для хостинга задай в `.env` (см. `.env.example`):

    USERS=mark:пароль1,friend:пароль2

Появится страница `/login`, сессия 30 дней (httponly-cookie, подпись HMAC), после 10 неверных попыток блок на 10 минут. `API_KEY` остаётся для интеграций (n8n шлёт `X-API-Key`), `/docs` тоже под паролем.

Деплой на VPS:

    cp .env.example .env   # заполни USERS
    docker compose up -d --build

База лежит в `./data/crm.db` (бэкап = копия файла). Порт слушает только 127.0.0.1, наружу ставь HTTPS-прокси, иначе пароль уйдёт открытым текстом. Пример Caddy (`/etc/caddy/Caddyfile`):

    crm.example.com {
        reverse_proxy 127.0.0.1:8000
    }
