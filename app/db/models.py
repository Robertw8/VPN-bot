import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, now


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("balance >= 0 AND referral_balance >= 0", name="nonnegative_balances"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    referral_balance: Mapped[int] = mapped_column(BigInteger, default=0)
    referral_earned: Mapped[int] = mapped_column(BigInteger, default=0)
    referral_code: Mapped[str] = mapped_column(
        String(32), unique=True, default=lambda: uuid.uuid4().hex
    )
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)


class Ledger(Base):
    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    wallet: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), unique=True)
    billing_config_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_configs.id"))
    billing_period: Mapped[int | None] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(180), unique=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("billing_config_id", "billing_period"),
        CheckConstraint("wallet IN ('main', 'referral')", name="wallet"),
        CheckConstraint("balance_after >= 0", name="nonnegative"),
    )


class Server(TimestampMixin, Base):
    __tablename__ = "servers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(64))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=443, server_default="443")
    provider_type: Mapped[str] = mapped_column(String(16), default="mock")
    external_inbound_id: Mapped[str | None] = mapped_column(String(64))
    subscription_base_url: Mapped[str | None] = mapped_column(Text)
    max_clients: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    connection_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    health: Mapped[str] = mapped_column(String(16), default="UNKNOWN", server_default="UNKNOWN")
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("max_clients IS NULL OR max_clients > 0", name="capacity"),)


class Tariff(TimestampMixin, Base):
    __tablename__ = "tariffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(16))
    duration_days: Mapped[int | None] = mapped_column(Integer)
    daily_price: Mapped[int | None] = mapped_column(BigInteger)
    fixed_price: Mapped[int | None] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        CheckConstraint(
            "(type = 'PAYG' AND daily_price > 0 AND duration_days IS NULL AND fixed_price IS NULL) OR (type = 'SUBSCRIPTION' AND duration_days > 0 AND fixed_price > 0 AND daily_price IS NULL)",
            name="pricing",
        ),
    )


class VpnConfig(TimestampMixin, Base):
    __tablename__ = "vpn_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), index=True)
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id"))
    external_client_id: Mapped[str] = mapped_column(
        String(36), unique=True, default=lambda: str(uuid.uuid4())
    )
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100), default="Мой VPN")
    protocol: Mapped[str] = mapped_column(String(16), default="VLESS")
    status: Mapped[str] = mapped_column(String(16), default="DISABLED", index=True)
    desired_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reconcile_issue: Mapped[str | None] = mapped_column(String(40))
    remote_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provisioned: Mapped[bool] = mapped_column(Boolean, default=False)
    connection_uri: Mapped[str | None] = mapped_column(Text)
    subscription_url: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(String(16))
    price: Mapped[int] = mapped_column(BigInteger)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    billing_sequence: Mapped[int] = mapped_column(Integer, default=0)
    operation: Mapped[str | None] = mapped_column(String(16))
    __table_args__ = (Index("ix_vpn_billing", "status", "paid_until"),)


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    external_payment_id: Mapped[str | None] = mapped_column(String(100), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    payment_url: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("provider", "external_payment_id"),
        CheckConstraint("amount > 0", name="positive_amount"),
    )


class PromoCode(TimestampMixin, Base):
    __tablename__ = "promo_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(32), default="BALANCE_BONUS")
    value: Mapped[int] = mapped_column(BigInteger)
    max_activations: Mapped[int] = mapped_column(Integer)
    activations_count: Mapped[int] = mapped_column(Integer, default=0)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "value > 0 AND max_activations > 0 AND per_user_limit > 0 AND activations_count >= 0 AND activations_count <= max_activations",
            name="limits",
        ),
    )


class PromoActivation(Base):
    __tablename__ = "promo_activations"
    id: Mapped[int] = mapped_column(primary_key=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("promo_id", "user_id", "ordinal"),)


class Sponsor(TimestampMixin, Base):
    __tablename__ = "sponsors"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str] = mapped_column(String(100))
    invite_url: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class SystemSetting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class Withdrawal(TimestampMixin, Base):
    __tablename__ = "withdrawals"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    payment_details: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_by: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (CheckConstraint("amount > 0", name="positive_amount"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(180), unique=True)
    text: Mapped[str] = mapped_column(Text)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# Defense in depth for writes outside the application service layer.
for _model, _name, _expression in [
    (User, "valid_status", "status IN ('ACTIVE','BLOCKED')"),
    (User, "no_self_referral", "referrer_id IS NULL OR referrer_id <> id"),
    (User, "referral_order", "referrer_id IS NULL OR referrer_id < id"),
    (
        Ledger,
        "financial_links",
        "(kind <> 'payment_paid' OR payment_id IS NOT NULL) AND (kind <> 'billing_charge' OR (billing_config_id IS NOT NULL AND billing_period IS NOT NULL))",
    ),
    (Server, "health_state", "health IN ('UNKNOWN', 'HEALTHY', 'DEGRADED', 'OFFLINE')"),
    (Server, "port_range", "port BETWEEN 1 AND 65535"),
    (User, "earned_nonnegative", "referral_earned >= 0"),
    (Ledger, "nonzero_amount", "amount <> 0"),
    (Server, "valid_provider", "provider_type IN ('mock','threexui')"),
    (
        Tariff,
        "required_price",
        "(type = 'PAYG' AND daily_price IS NOT NULL) OR (type = 'SUBSCRIPTION' AND fixed_price IS NOT NULL AND duration_days IS NOT NULL)",
    ),
    (VpnConfig, "valid_status", "status IN ('ACTIVE','DISABLED','DELETED','ERROR')"),
    (
        VpnConfig,
        "valid_mode",
        "mode IN ('PAYG','SUBSCRIPTION') AND price > 0 AND billing_sequence >= 0",
    ),
    (
        VpnConfig,
        "valid_operation",
        "operation IS NULL OR operation IN ('CREATE','ENABLE','DISABLE','DELETE')",
    ),
    (Payment, "valid_status", "status IN ('PENDING','PAID','CANCELLED','EXPIRED','FAILED')"),
    (Payment, "valid_currency", "currency = 'RUB'"),
    (PromoCode, "valid_type", "type IN ('BALANCE_BONUS','DISCOUNT_PERCENT','FREE_DAYS')"),
    (Withdrawal, "valid_status", "status IN ('PENDING','APPROVED','REJECTED','PAID')"),
]:
    cast(Table, _model.__table__).append_constraint(CheckConstraint(_expression, name=_name))
Index("ix_users_username_lower", func.lower(User.username))
Index("ix_payments_paid_at", Payment.paid_at)
