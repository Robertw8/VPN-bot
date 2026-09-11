import asyncio
import hashlib
from datetime import datetime

import structlog
from cryptography.fernet import Fernet
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.base import now
from app.db.models import Server, Tariff, User, VpnConfig
from app.domain.errors import DomainError, Forbidden, IntegrationUnavailable
from app.integrations.vpn.base import ServerSpec, VlessProvider
from app.integrations.vpn.config import InboundConfig
from app.integrations.vpn.errors import AuthenticationError, PermissionError, Unavailable
from app.integrations.vpn.mock import MockVlessProvider
from app.integrations.vpn.threexui import ThreeXUIConfig, ThreeXUIVlessProvider
from app.logging import error_details

log = structlog.get_logger()


class VpnService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        providers: dict[str, VlessProvider] | None = None,
    ) -> None:
        self.sessions, self.settings = sessions, settings
        self.cipher = (
            Fernet(settings.encryption_key.get_secret_value().encode())
            if settings.encryption_key.get_secret_value()
            else None
        )
        self.providers: dict[str, VlessProvider] = providers or {
            "mock": MockVlessProvider(),
            "threexui": ThreeXUIVlessProvider(
                ThreeXUIConfig(
                    base_url=settings.threexui_base_url,
                    version=settings.threexui_version,
                    username=settings.threexui_username,
                    password=settings.threexui_password,
                )
            ),
        }

    def encrypt(self, value: str, provider: str) -> str:
        if self.cipher:
            return "fernet:" + self.cipher.encrypt(value.encode()).decode()
        if provider != "mock":
            raise IntegrationUnavailable()
        return "mock:" + value

    def decrypt(self, value: str) -> str:
        if value.startswith("mock:"):
            return value[5:]
        if self.cipher and value.startswith("fernet:"):
            return self.cipher.decrypt(value[7:].encode()).decode()
        raise IntegrationUnavailable()

    async def owned(
        self, session: AsyncSession, user_id: int, config_id: int, lock: bool = False
    ) -> VpnConfig:
        if not 0 < config_id <= 2147483647:
            raise Forbidden()
        stmt = select(VpnConfig).where(VpnConfig.id == config_id, VpnConfig.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        config = await session.scalar(stmt)
        if not config:
            raise Forbidden()
        return config

    async def select_server(self, session: AsyncSession, server_id: int | None) -> Server:
        # Lock candidate servers in stable order; count AFTER locks are acquired.
        servers = list(
            await session.scalars(
                select(Server)
                .where(Server.active.is_(True), *([Server.id == server_id] if server_id else []))
                .order_by(Server.id)
                .with_for_update()
            )
        )
        choices: list[tuple[int, int, int, Server]] = []
        for server in servers:
            if server.provider_type != self.settings.vless_provider:
                continue
            # A real backend must pass a recent provider check before receiving new clients.
            # UNKNOWN is retained for the stateless development mock and migrated mock fixtures.
            if server.health == "OFFLINE" or (
                server.provider_type != "mock" and server.health != "HEALTHY"
            ):
                continue
            count = (
                await session.scalar(
                    select(func.count())
                    .select_from(VpnConfig)
                    .where(VpnConfig.server_id == server.id, VpnConfig.status != "DELETED")
                )
                or 0
            )
            if server.max_clients is None or count < server.max_clients:
                active_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(VpnConfig)
                        .where(VpnConfig.server_id == server.id, VpnConfig.status == "ACTIVE")
                    )
                    or 0
                )
                choices.append((server.priority, active_count, server.id, server))
        if not choices:
            raise DomainError("Нет доступных серверов. Попробуйте позже.")
        return min(choices, key=lambda item: item[:3])[3]

    async def create(
        self, user_id: int, tariff_id: int, server_id: int | None, key: str
    ) -> VpnConfig:
        async with self.sessions.begin() as session:
            # User lock also serializes two copies of the same creation callback.
            user = (
                await session.scalars(select(User).where(User.id == user_id).with_for_update())
            ).one()
            if user.status != "ACTIVE":
                raise Forbidden()
            existing = await session.scalar(select(VpnConfig).where(VpnConfig.request_key == key))
            if existing:
                if existing.user_id != user_id:
                    raise Forbidden()
                return existing
            tariff = await session.get(Tariff, tariff_id)
            if not tariff or not tariff.active:
                raise DomainError("Тариф недоступен.")
            server = await self.select_server(session, server_id)
            row = VpnConfig(
                user_id=user_id,
                server_id=server.id,
                tariff_id=tariff.id,
                request_key=key,
                name=f"VPN · {server.name}",
                mode=tariff.type,
                price=tariff.daily_price if tariff.type == "PAYG" else tariff.fixed_price,
                duration_days=tariff.duration_days,
                operation="CREATE",
            )
            session.add(row)
            await session.flush()
            config_id = row.id
        await self.reconcile(config_id)
        async with self.sessions() as session:
            return await self.owned(session, user_id, config_id)

    async def request(self, user_id: int, config_id: int, operation: str) -> None:
        if operation not in ("DELETE", "DISABLE"):
            raise ValueError("Invalid operation")
        async with self.sessions.begin() as session:
            row = await self.owned(session, user_id, config_id, lock=True)
            if row.status == "DELETED":
                return
            if row.operation == "DELETE":
                return
            if operation == "DISABLE" and row.status == "DISABLED" and row.operation is None:
                return
            row.desired_enabled = False
            row.operation = operation
        await self.reconcile(config_id)

    async def reconcile(self, config_id: int) -> None:
        # Durable intent is already committed. Each retry converges the SAME UUID.
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(VpnConfig).where(VpnConfig.id == config_id).with_for_update()
            )
            if not row or not row.operation or row.status == "DELETED":
                return
            server = await session.get(Server, row.server_id)
            assert server
            provider = self.providers[server.provider_type]
            try:
                spec = self.server_spec(server)
                async with asyncio.timeout(20):
                    if row.operation == "DELETE":
                        if await provider.get_client(spec, row.external_client_id) is not None:
                            await provider.delete_client(spec, row.external_client_id)
                        row.status, row.disabled_at = "DELETED", now()
                    elif row.operation == "CREATE":
                        client = await provider.get_client(spec, row.external_client_id)
                        if client is None:
                            client = await provider.create_client(spec, row.external_client_id)
                        if client.id != row.external_client_id:
                            raise IntegrationUnavailable()
                        if client.enabled:
                            await provider.disable_client(spec, row.external_client_id)
                        row.connection_uri = self.encrypt(client.uri, server.provider_type)
                        row.subscription_url = (
                            self.encrypt(client.subscription_url, server.provider_type)
                            if client.subscription_url
                            else None
                        )
                        row.provisioned, row.status = True, "DISABLED"
                    elif row.operation == "ENABLE":
                        if row.paid_until and row.paid_until > now() and row.desired_enabled:
                            await provider.enable_client(
                                spec, row.external_client_id, row.paid_until
                            )
                            row.status, row.activated_at = "ACTIVE", now()
                        else:
                            await provider.disable_client(spec, row.external_client_id)
                            row.status, row.desired_enabled = "DISABLED", False
                    else:
                        client = await provider.get_client(spec, row.external_client_id)
                        if client is not None and client.enabled:
                            await provider.disable_client(spec, row.external_client_id)
                        row.status, row.disabled_at = "DISABLED", now()
                    log.info("vpn_" + row.operation.lower(), config_id=row.id)
                    row.operation = None
                    row.reconcile_issue = None
            except Exception as exc:
                row.status = "ERROR"
                log.error(
                    "integration_failure",
                    config_id=row.id,
                    operation=row.operation,
                    **error_details(exc),
                )

    @staticmethod
    def server_spec(server: Server) -> ServerSpec:
        return ServerSpec(
            server.id,
            server.host,
            server.external_inbound_id,
            server.port,
            InboundConfig.model_validate(server.connection_config)
            if server.connection_config
            else None,
        )

    async def observe(self, config_id: int) -> None:
        """Observe established real clients; never create/delete to repair missing/unknown state."""
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(VpnConfig).where(VpnConfig.id == config_id).with_for_update()
            )
            if not row or row.operation or not row.provisioned or row.status == "DELETED":
                return
            server = await session.get(Server, row.server_id)
            assert server
            # The pure development mock has no persistent remote state to compare.
            provider = self.providers[server.provider_type]
            if type(provider) is MockVlessProvider:
                return
            try:
                async with asyncio.timeout(20):
                    spec = self.server_spec(server)
                    client = await provider.get_client(spec, row.external_client_id)
                    row.remote_checked_at = now()
                    if client is None:
                        row.status, row.reconcile_issue = "ERROR", "REMOTE_ABSENT"
                        log.error("vpn_needs_repair", config_id=row.id, reason=row.reconcile_issue)
                        return
                    if client.id != row.external_client_id:
                        raise Unavailable()
                    user = await session.get(User, row.user_id, with_for_update=True)
                    entitled = bool(
                        user
                        and user.status == "ACTIVE"
                        and row.desired_enabled
                        and row.paid_until
                        and row.paid_until > now()
                    )
                    if not entitled:
                        if client.enabled:
                            await provider.disable_client(spec, row.external_client_id)
                        row.status, row.reconcile_issue = "DISABLED", None
                        # Expired desired_enabled is retained for the next prepaid PAYG window.
                        return
                    assert row.paid_until
                    if client.expires_at is None or client.expires_at.utcoffset() is None:
                        row.status, row.reconcile_issue = "ERROR", "REMOTE_EXPIRY_UNKNOWN"
                        log.error("vpn_needs_repair", config_id=row.id, reason=row.reconcile_issue)
                        return
                    if not client.enabled and not client.restorable:
                        row.status, row.reconcile_issue = "ERROR", "REMOTE_DISABLED_REVIEW"
                        log.warning(
                            "vpn_needs_repair", config_id=row.id, reason=row.reconcile_issue
                        )
                        return
                    if not client.enabled or client.expires_at != row.paid_until:
                        # Same UUID, only already-paid entitlement; never charge here.
                        await provider.enable_client(spec, row.external_client_id, row.paid_until)
                    row.status, row.reconcile_issue = "ACTIVE", None
            except Exception as exc:
                row.status, row.reconcile_issue = "ERROR", "REMOTE_UNKNOWN"
                log.error("vpn_observation_failed", config_id=row.id, **error_details(exc))

    async def observe_all(self) -> None:
        last_id = 0
        while True:
            async with self.sessions() as session:
                ids = list(
                    await session.scalars(
                        select(VpnConfig.id)
                        .where(
                            VpnConfig.id > last_id,
                            VpnConfig.provisioned.is_(True),
                            VpnConfig.status != "DELETED",
                            VpnConfig.operation.is_(None),
                        )
                        .order_by(VpnConfig.id)
                        .limit(100)
                    )
                )
            if not ids:
                return
            for config_id in ids:
                await self.observe(config_id)
            last_id = ids[-1]

    async def refresh_health(self) -> None:
        async with self.sessions() as session:
            servers = list(await session.scalars(select(Server).order_by(Server.id)))
        for server in servers:
            try:
                async with asyncio.timeout(10):
                    healthy = await self.providers[server.provider_type].healthcheck(
                        self.server_spec(server)
                    )
                health = "HEALTHY" if healthy else "OFFLINE"
            except (AuthenticationError, PermissionError) as exc:
                health = "DEGRADED"
                log.error("server_health_failed", server_id=server.id, **error_details(exc))
            except Exception as exc:
                health = "OFFLINE"
                log.error("server_health_failed", server_id=server.id, **error_details(exc))
            async with self.sessions.begin() as session:
                current = await session.get(Server, server.id, with_for_update=True)
                if current:
                    await session.execute(
                        update(Server)
                        .where(Server.id == current.id)
                        .values(
                            health=health, health_checked_at=now(), updated_at=Server.updated_at
                        )
                    )

    async def reconcile_pending(self) -> None:
        last_id = 0
        while True:
            async with self.sessions() as session:
                ids = list(
                    await session.scalars(
                        select(VpnConfig.id)
                        .where(VpnConfig.operation.is_not(None), VpnConfig.id > last_id)
                        .order_by(VpnConfig.id)
                        .limit(100)
                    )
                )
            if not ids:
                break
            for config_id in ids:
                await self.reconcile(config_id)
            last_id = ids[-1]


def paid_until_text(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M UTC") if value else "не оплачено"


def config_revision(row: VpnConfig) -> str:
    return hashlib.sha256(row.updated_at.isoformat().encode()).hexdigest()[:12]
