"""Phase 7: financial provenance, referral immutability, server port."""

import sqlalchemy as sa
from alembic import op

revision = "af730acb7790"
down_revision = "9edfc2a661bb"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("servers", sa.Column("port", sa.Integer(), nullable=False, server_default="443"))
    op.create_check_constraint(op.f("ck_servers_port_range"), "servers", "port BETWEEN 1 AND 65535")
    op.add_column("ledger", sa.Column("payment_id", sa.Integer(), nullable=True))
    op.add_column("ledger", sa.Column("billing_config_id", sa.Integer(), nullable=True))
    op.add_column("ledger", sa.Column("billing_period", sa.Integer(), nullable=True))
    # Backfill only the established immutable ledger key formats; fail if legacy data is malformed.
    op.execute(
        "UPDATE ledger SET payment_id = split_part(key, ':', 2)::integer WHERE kind = 'payment_paid' AND key ~ '^payment:[0-9]+$'"
    )
    op.execute(
        "UPDATE ledger SET billing_config_id = split_part(key, ':', 2)::integer, billing_period = split_part(key, ':', 3)::integer WHERE kind = 'billing_charge' AND key ~ '^billing:[0-9]+:[0-9]+$'"
    )
    op.create_foreign_key(
        op.f("fk_ledger_payment_id_payments"), "ledger", "payments", ["payment_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_ledger_billing_config_id_vpn_configs"),
        "ledger",
        "vpn_configs",
        ["billing_config_id"],
        ["id"],
    )
    op.create_unique_constraint(op.f("uq_ledger_payment_id"), "ledger", ["payment_id"])
    op.create_unique_constraint(
        op.f("uq_ledger_billing_config_id"), "ledger", ["billing_config_id", "billing_period"]
    )
    op.create_check_constraint(
        op.f("ck_ledger_financial_links"),
        "ledger",
        "(kind <> 'payment_paid' OR payment_id IS NOT NULL) AND (kind <> 'billing_charge' OR (billing_config_id IS NOT NULL AND billing_period IS NOT NULL))",
    )
    op.create_check_constraint(
        op.f("ck_users_referral_order"), "users", "referrer_id IS NULL OR referrer_id < id"
    )
    op.execute("""CREATE FUNCTION forbid_referrer_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.referrer_id IS DISTINCT FROM OLD.referrer_id THEN
        RAISE EXCEPTION 'referrer is immutable' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute(
        "CREATE TRIGGER users_referrer_immutable BEFORE UPDATE OF referrer_id ON users FOR EACH ROW EXECUTE FUNCTION forbid_referrer_change()"
    )


def downgrade():
    op.execute("DROP TRIGGER users_referrer_immutable ON users")
    op.execute("DROP FUNCTION forbid_referrer_change()")
    op.drop_constraint(op.f("ck_users_referral_order"), "users", type_="check")
    op.drop_constraint(op.f("ck_ledger_financial_links"), "ledger", type_="check")
    op.drop_constraint(op.f("uq_ledger_payment_id"), "ledger", type_="unique")
    op.drop_constraint(op.f("uq_ledger_billing_config_id"), "ledger", type_="unique")
    op.drop_constraint(op.f("fk_ledger_payment_id_payments"), "ledger", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_ledger_billing_config_id_vpn_configs"), "ledger", type_="foreignkey"
    )
    op.drop_column("ledger", "payment_id")
    op.drop_column("ledger", "billing_config_id")
    op.drop_column("ledger", "billing_period")
    op.drop_constraint(op.f("ck_servers_port_range"), "servers", type_="check")
    op.drop_column("servers", "port")
