# Telegram VLESS VPN bot

Существующий MVP на Python 3.13+, aiogram 3, PostgreSQL, SQLAlchemy async и Alembic.
Phase 8 добавляет typed inbound config в БД, наблюдение remote state и server health,
защиту Telegram API transport и проверенное восстановление PostgreSQL.
Реальная панель и live-токены ещё не предоставлены.
**Реальный VPN пока не подключён: mock URI не даёт VPN-доступ.**

## Запуск

Нужны Python 3.13+, запущенный Docker Engine и собственный Telegram Bot.
Создайте его через [@BotFather](https://t.me/BotFather), командой `/newbot`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.lock
pip install --no-deps -e .
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Если `.env` уже существует, сохраните его: не перезаписывайте токены и ENCRYPTION_KEY.
Заполните `BOT_TOKEN`, `ADMIN_TELEGRAM_IDS=[123456789]`, `ENCRYPTION_KEY` результатом команды.
Для development оставьте `VLESS_PROVIDER=mock`, `CRYPTOBOT_ENABLED=false`.
Секреты не попадают в Git/Docker build context. Сохраните резервную копию ключа отдельно от БД.

```bash
docker compose up -d --wait postgres
alembic upgrade head
python -m app.cli seed
python -m app.main
```

`DATABASE_URL` из примера рассчитан на localhost:5432, пользователь/пароль/БД `vpn`.
`POSTGRES_PORT` меняет опубликованный порт Compose; при локальном Python-запуске
также измените порт в `DATABASE_URL`. Во время аудита создан локальный `.env` без токенов
с портом **55433** и отдельный Compose-проект `vpn-bot-audit`; это не production-конфигурация.

## Полный Docker-запуск

```bash
docker compose build
docker compose up -d --wait postgres
docker compose run --rm bot alembic upgrade head
docker compose run --rm bot python -m app.cli seed
docker compose run --rm bot python -m app.main --check-infrastructure
docker compose up -d bot
```

`--check-infrastructure` проверяет конфигурацию, БД, миграции и регистрацию routers,
не обращаясь к Telegram. Обычный запуск без BOT_TOKEN завершается понятной ошибкой.
С валидным токеном startup сначала выполняет getMe/getWebhookInfo; при чужом webhook
не удаляет его автоматически и просит оператора устранить конфликт.

Контейнер работает от непривилегированного пользователя, использует init, healthcheck и
35 секунд для остановки. Повторные неуспешные старты ограничены тремя перезапусками.
Healthcheck — heartbeat процесса + доступность БД; состояние внешних провайдеров он не подтверждает.
База публикуется только на `127.0.0.1`. Пароль `vpn` — только для локальной разработки.
`POSTGRES_PASSWORD` можно поменять в `.env`; для пароля со спецсимволами обеспечьте URL-кодирование
в DATABASE_URL. Миграции применяются явно до приложения. Не запускайте два polling-процесса одного токена.

## Первый пользовательский сценарий

1. `/start` регистрирует пользователя и показывает баланс/меню либо проверку каналов.
2. `/admin → VPN-серверы → Добавить`: название, страна, `mock.example`, порт 443,
   пропустить inbound, лимит клиентов, приоритет → предпросмотр → сохранить.
3. `Мои конфигурации → Создать VLESS → тариф → сервер/автовыбор`.
4. Получите URI и QR. Конфигурация создаётся отключённой; оплатите первый период кнопкой включения.
5. Проверьте просмотр, отключение, включение и удаление. Старые кнопки оплаты требуют обновить карточку.

Для проверки без платежей: `/admin → Пользователи → Начислить / списать` либо:

```bash
python -m app.cli dev-credit 123456789 --amount 100000 --key first-demo-credit
```

В этой **CLI** сумма в копейках; повторный ключ не создаёт второе начисление.
Dev credit и `seed --demo-server` запрещены вне development. `seed` создаёт только недостающие
начальные настройки/тарифы, не перезаписывает существующие. Стартовые редактируемые цены:
6,66 ₽/сутки, 199 ₽/30 дней, 1990 ₽/365 дней. Бонусы изначально выключены.

## Что реализовано

- Русское меню и Telegram-админка с обычными FSM-формами, карточками и подтверждением.
- Пользователи, неизменяемый referrer, основной/реферальный кошельки, ledger.
- Mock VLESS, выбор/лимиты серверов, URI/QR, ownership, удаление и восстановление операций по UUID.
- PAYG, пакеты, финансовая идемпотентность и singleton job batch через PostgreSQL advisory lock.
- Crypto Pay invoice/status adapter, бонусы, рефералы, восстановление неопределённого invoice.
- Атомарные промокоды, sponsor gate, внутренние переводы, резервирование/обработка выводов.
- Миграции, startup validation, структурированная диагностика без секретов, Docker и CI.

3x-ui — заготовка без выдуманных endpoints; startup с этим провайдером сейчас закрыт.
Карта/СБП — явный stub. Публичного HTTP webhook receiver нет, хотя проверка подписи и
единая точка settlement реализованы. DISCOUNT_PERCENT/FREE_DAYS зарезервированы, но не включены.

## Архитектура и финансовые правила

`app/bot/` — Telegram/FSM; `app/services/` — бизнес-операции; `app/db/` — ORM;
`app/integrations/` — внешние контракты; `app/jobs/` — фоновые задачи;
`app/domain/` — деньги/валидация/ошибки. Отдельный repository-слой, дублирующий SQLAlchemy, не добавлялся.

Суммы — целые копейки BIGINT; проценты — базисные пункты. Все изменения баланса проходят
через ledger под блокировкой пользователя. Principal payment связан с уникальным payment_id;
billing — с уникальной парой config/period. Дополнительно сохраняются уникальные ключи операций.
Суммы/валюта/payload/external ID сверяются перед зачислением платежа. Бонусы — отдельные проводки.

PAYG — предоплата на **24 часа от активации**, а не списание по календарной полуночи.
Пакет — разовая покупка 30/365 дней с ручным продлением. Эти режимы не смешиваются.
После простоя worker покупает текущий период, не списывая задним числом неоказанное время.
Реальный VPN обязан отключать клиента по remote expiry независимо от worker.
Цена/длительность фиксируются в конфигурации при создании; правка тарифа влияет на новые конфигурации.

Отключение прекращает продление; повторное включение в уже оплаченном периоде бесплатно.
При нехватке средств сохраняются отключение и одно уведомление. Пополнение само по себе
не включает ранее отключённый VPN. Удаление не возвращает неиспользованное время.
Ошибку включения после списания компенсируют отдельным admin credit с комментарием.

UUID и намерение VPN-операции фиксируются **до HTTP**. При восстановлении CREATE сначала
ищется тот же UUID. Timeout lookup — неизвестность, а не отсутствие. Отключённый/удалённый
на сервере клиент не требует повторного отключения. Неопределённая операция остаётся ERROR
до согласования. Атомарной транзакции между PostgreSQL и внешним API не существует.

Worker работает внутри бота; отдельный дополнительный процесс — `python -m app.jobs.worker`.
Advisory lock допускает лишь одну активную пачку jobs на БД. Финансовые row locks/uniqueness
сохраняются независимо от него. Уведомления at-least-once: сообщение может повториться при
сбое после отправки до commit, финансовая проводка — нет.

FSM и rate limits локальны и сбрасываются при restart. Один polling frontend обрабатывает
updates последовательно, что упрощает shutdown, но ограничивает throughput долгими интеграциями.
Для масштабирования потребуются общая FSM/лимиты и отдельное проектирование конкурентного frontend.

## Проверки

```bash
source .venv/bin/activate
make check
export TEST_DATABASE_URL='postgresql+asyncpg://vpn:vpn@localhost:5432/vpn'
pytest -q
alembic check
```

Используйте свою тестовую БД и порт. Без TEST_DATABASE_URL DB-тесты пропускаются — это не полный прогон.
Каждый тест применяет **настоящие Alembic migrations** в случайной схеме PostgreSQL, затем удаляет
только её. Права `CREATE SCHEMA` нужны только тестовому пользователю. SQLite не используется
как доказательство корректности row locks, advisory locks или DB triggers.

[verification.md](docs/verification.md) разделяет ACTUALLY VERIFIED / NOT YET VERIFIED.
Тесты не заменяют живую оплату и VPN-трафик. CI настроен на Python 3.13/PostgreSQL;
удалённый CI не запускался, поскольку workspace не содержит Git-репозиторий/remote.

## Документы и подключение production

- [Аудит: найденные риски и исправления](docs/audit.md).
- [Админка: поля, кнопки, деньги, подтверждения](docs/admin.md).
- [Точные production-checklists VLESS и Crypto Pay](docs/integrations.md).
- [Фактически выполненные проверки](docs/verification.md).

Для Crypto Pay создайте testnet-приложение, задайте `CRYPTOBOT_ENABLED=true`, токен и
`CRYPTOBOT_TESTNET=true`, пройдите живую оплату/повторную проверку. Mainnet требует отдельного токена
и `CRYPTOBOT_TESTNET=false`. При timeout создания invoice не повторяйте создание: используйте
admin recovery с проверкой внешнего ID. Основание реализации —
[официальная Crypto Pay API документация](https://help.send.tg/en/articles/10279948-crypto-pay-api).

До production нужны данные реального VPN, реализация/проверка hard expiry и адаптера,
Telegram/Crypto Pay credentials, проверка клиента, резервное копирование, мониторинг и
процедуры финансовой сверки. Домен нужен, если будет развёрнут HTTP subscription/webhook service;
для текущего polling/manual-payment-check отдельный публичный HTTP сервер не требуется.

## Phase 8 — эксплуатационная подготовка

- [Развёртывание, backup/restore, rollout и rollback](docs/deployment.md)
- [Контролируемая проверка настоящего Telegram](docs/live-telegram.md)
- [3x-ui config, remote expiry, reconciliation и Crypto Pay testnet](docs/integrations.md)
- [Фактические результаты и непроверенные интеграции](docs/verification.md)

Обычный pytest исключает `cryptobot_live`. Для явного testnet smoke:
`CRYPTOBOT_LIVE_CONFIRM=TESTNET .venv/bin/pytest -m cryptobot_live -q`.
Он создаёт только тестовый счёт; оплату выполняет тестировщик вручную.
