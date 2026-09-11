import argparse
import asyncio
import signal
from contextlib import suppress
from typing import Any

import structlog
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from app.bot import (
    admin,
    admin_flows,
    admin_operations,
    community,
    navigation,
    payments,
    start,
    vpn,
)
from app.bot.middleware import ContextMiddleware
from app.bot.transport import TelegramTransport
from app.config import Settings
from app.db.session import database
from app.domain.errors import DomainError
from app.health import HEALTH_FILE, heartbeat
from app.jobs.worker import run
from app.logging import configure_logging, error_details
from app.services.billing import BillingService
from app.services.payments import PaymentService
from app.services.vpn import VpnService
from app.startup import validate_config, validate_database

log = structlog.get_logger()


def build_dispatcher(settings: Settings, sessions: Any) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    middleware = ContextMiddleware(sessions, settings)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    vpn_service = VpnService(sessions, settings)
    dp["vpn_service"] = vpn_service
    dp["billing_service"] = BillingService(sessions, vpn_service)
    dp["payment_service"] = PaymentService(sessions, settings)
    dp.include_routers(
        navigation.router,
        admin_flows.router,
        admin_operations.router,
        community.router,
        admin.router,
        start.router,
        vpn.router,
        payments.router,
    )
    return dp


async def launch(worker_only: bool = False, infrastructure_only: bool = False) -> None:
    configure_logging()
    # Keep validation failures free of raw Pydantic input values and secrets.
    try:
        settings = Settings()
        validate_config(settings, require_token=not infrastructure_only)
    except Exception as exc:
        log.error("startup_validation_failed", **error_details(exc))
        raise SystemExit(
            str(exc)
            if isinstance(exc, DomainError)
            else "Некорректная конфигурация. Проверьте .env."
        ) from None
    sessions = database(settings.database_url)
    bot: Bot | None = None
    dp: Dispatcher | None = None
    tasks: list[asyncio.Task[Any]] = []
    stopped = asyncio.Event()
    polling_started = False
    loop = asyncio.get_running_loop()

    async def stop() -> None:
        stopped.set()
        if polling_started and dp is not None:
            with suppress(RuntimeError):
                await dp.stop_polling()

    # Requests made during preflight are bounded; the same signal path covers worker-only mode.
    def on_signal() -> None:
        tasks.append(asyncio.create_task(stop()))

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal)
    try:
        await validate_database(sessions)
        dp = build_dispatcher(settings, sessions)
        if infrastructure_only:
            log.info(
                "infrastructure_validation_passed",
                routers=len(dp.sub_routers),
                telegram_authenticated=False,
            )
            return
        bot = Bot(settings.bot_token.get_secret_value())
        bot.session.middleware(TelegramTransport())
        async with asyncio.timeout(15):
            await bot.get_me()
            if not worker_only and (await bot.get_webhook_info()).url:
                raise DomainError("У бота включён webhook. Отключите его перед polling-запуском.")
        if stopped.is_set():
            return
        tasks.append(
            asyncio.create_task(
                run(
                    sessions,
                    dp["vpn_service"],
                    dp["billing_service"],
                    bot,
                    settings.billing_interval_seconds,
                )
            )
        )
        tasks.append(asyncio.create_task(heartbeat()))
        log.info("runtime_started", worker_only=worker_only)
        if worker_only:
            await stopped.wait()
        else:
            polling_started = True
            # Process each update to completion: no orphan handler tasks on SIGTERM.
            await dp.start_polling(
                bot, close_bot_session=False, handle_signals=False, handle_as_tasks=False
            )
    except (DomainError, TelegramAPIError, TimeoutError) as exc:
        log.error("startup_or_runtime_failure", **error_details(exc))
        raise SystemExit(
            str(exc)
            if isinstance(exc, DomainError)
            else "Telegram недоступен или отклонил токен. Проверьте настройки и соединение."
        ) from None
    except Exception as exc:
        log.error("runtime_failure", **error_details(exc))
        raise SystemExit(
            "Не удалось запустить приложение. Проверьте БД, миграции и настройки."
        ) from None
    finally:
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(*(t for t in tasks if t is not current), return_exceptions=True)
        if dp:
            await dp.storage.close()
            await dp.fsm.events_isolation.close()
        if bot:
            await bot.session.close()
        await sessions.kw["bind"].dispose()
        with suppress(FileNotFoundError):
            HEALTH_FILE.unlink()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)
        log.info("runtime_closed", resources_closed=True)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-infrastructure",
        action="store_true",
        help="Validate DB/migrations/routers without contacting Telegram",
    )
    args = parser.parse_args()
    await launch(infrastructure_only=args.check_infrastructure)


if __name__ == "__main__":
    asyncio.run(main())
