"""Financial invariants and lookup indexes."""

import sqlalchemy as sa
from alembic import op

revision = "9edfc2a661bb"
down_revision = "60f77c25a94d"
branch_labels = None
depends_on = None

CONSTRAINTS = [
    ("users", "valid_status", "status IN ('ACTIVE','BLOCKED')"),
    ("users", "no_self_referral", "referrer_id IS NULL OR referrer_id <> id"),
    ("users", "earned_nonnegative", "referral_earned >= 0"),
    ("ledger", "nonzero_amount", "amount <> 0"),
    ("servers", "valid_provider", "provider_type IN ('mock','threexui')"),
    (
        "tariffs",
        "required_price",
        "(type = 'PAYG' AND daily_price IS NOT NULL) OR (type = 'SUBSCRIPTION' AND fixed_price IS NOT NULL AND duration_days IS NOT NULL)",
    ),
    ("vpn_configs", "valid_status", "status IN ('ACTIVE','DISABLED','DELETED','ERROR')"),
    (
        "vpn_configs",
        "valid_mode",
        "mode IN ('PAYG','SUBSCRIPTION') AND price > 0 AND billing_sequence >= 0",
    ),
    (
        "vpn_configs",
        "valid_operation",
        "operation IS NULL OR operation IN ('CREATE','ENABLE','DISABLE','DELETE')",
    ),
    ("payments", "valid_status", "status IN ('PENDING','PAID','CANCELLED','EXPIRED','FAILED')"),
    ("payments", "valid_currency", "currency = 'RUB'"),
    ("promo_codes", "valid_type", "type IN ('BALANCE_BONUS','DISCOUNT_PERCENT','FREE_DAYS')"),
    ("withdrawals", "valid_status", "status IN ('PENDING','APPROVED','REJECTED','PAID')"),
]


def upgrade():
    for table, name, expr in CONSTRAINTS:
        op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, expr)
    op.create_index("ix_users_username_lower", "users", [sa.text("lower(username)")])
    op.create_index("ix_payments_paid_at", "payments", ["paid_at"])


def downgrade():
    op.drop_index("ix_payments_paid_at", "payments")
    op.drop_index("ix_users_username_lower", "users")
    for table, name, _ in reversed(CONSTRAINTS):
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
