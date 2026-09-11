# Phase 8 verification record — 2026-09-09

## ACTUALLY VERIFIED

The original 59 Phase 7 cases remain in the ordinary suite. Phase 8 adds typed VLESS URI,
provider-error, remote-expiry, reconciliation, server-health/capacity and Telegram transport tests.
No real Telegram, 3x-ui or Crypto Pay credentials were present during these checks.

| Check | Observed result |
| --- | --- |
| Host pytest against Compose PostgreSQL 16 | **96 passed, 1 live test deselected in 10.75s** |
| Linux pytest in the built Python 3.13.15 image | **96 passed, 1 live test deselected in 8.77s** |
| Explicit `pytest -m cryptobot_live` without token/confirmation | 1 skipped as designed; no invoice created |
| `ruff check .` | Passed |
| `ruff format --check .` | 85 files already formatted |
| `mypy app` | No issues in 54 source files |
| `pip check` | No broken requirements |
| Docker build | Application image actually rebuilt successfully |
| `alembic upgrade head` in application container | Phase 8 migration applied successfully |
| `alembic check` in application container | No new upgrade operations detected |
| `python -m app.main --check-infrastructure` in container | DB revision validated; 8 routers registered; resources closed |
| Real aiogram polling against a local Telegram-compatible HTTP fixture | Entered polling and reached healthy |
| SIGTERM to that probe | Exit 0; HTTP closed; DB released; health file removed; 0 pending tasks |
| PostgreSQL custom-format dump and restore | Passed in two newly created isolated databases |

The Telegram fixture exercises real aiogram request transport and application lifecycle without
contacting Telegram or users. It is not a live Telegram acceptance result. The healthcheck proves
process heartbeat plus DB connectivity, not Telegram/VPN/payment readiness.

## Backup/restore evidence

The probe created a source database and a separate fresh restore database, then:

1. applied all migrations and seeded three tariffs, settings and one mock server;
2. created one user, ledger credit and provisioned mock VPN;
3. produced a `pg_dump -Fc --no-owner --no-acl` backup;
4. restored it with `pg_restore --exit-on-error`;
5. ran `alembic upgrade head` and `alembic check` on the restored database;
6. compared serialized rows from every application table and the Alembic revision;
7. inserted another user to prove restored sequences advance;
8. removed only the two UUID-named audit databases.

Observed result: SHA-256 before/after
`2582f80e6efbf1ac7e62c6042fb19d353b9a3bbdfa169cc8b41585ca2269c382`, revision
`b826ac07d881`, identical row counts, sequence check true, dump size 52,520 bytes. The dump contains
synthetic data only and remains at `/private/tmp/vpn_backup_87134fc0be88.dump` for this audit run.
This validates the procedure and schema, not production-scale recovery time or external 3x-ui state.

Repeat with the isolated audit database using:

```bash
docker compose -p vpn-bot-audit up -d --wait postgres
.venv/bin/python scripts/backup_probe.py
```

## Tests added in Phase 8

- VLESS URI round trips for TCP, REALITY, WebSocket and gRPC; IPv6 and escaping; rejection of
  duplicate/unknown/missing fields; no inferred optional parameters.
- Exact paid-until conversion to a future UTC millisecond timestamp without unlimited expiry.
- Provider classification for authentication, permission, not-found, conflict, validation,
  unavailable, timeout and ambiguous mutations; complete HTTPS panel configuration validation.
- Remote observation: active/active, absent, unknown, disabled requiring review, safe restore of the
  same UUID, unpaid remote access, unknown/excess expiry and no automatic create.
- Health refresh, health-aware deterministic selection, capacity, stable admin edit revision and
  rejection of connection-setting changes on used servers.
- UTF-8 callback-data limit, 4096 UTF-16 message chunks, plain-text output, expired/duplicate
  callbacks, stale edit fallback, non-swallowed infrastructure errors and user selection from an
  admin card without retyping an internal ID.
- Opt-in Crypto Pay TESTNET invoice creation/status smoke marker, excluded from ordinary pytest.

Tests create isolated random PostgreSQL schemas and apply the real Alembic chain. Never point them
at production. The standard command is:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://vpn:vpn@127.0.0.1:55433/vpn .venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pip check
```

The Linux check uses mounted tests plus the project pytest configuration because tests reside
outside `/app`:

```bash
docker compose -p vpn-bot-audit run --rm --user root \
  -e TEST_DATABASE_URL=postgresql+asyncpg://vpn:vpn@postgres:5432/vpn \
  -v "$PWD/tests:/checks:ro" \
  -v "$PWD/requirements-dev.lock:/checks-deps.lock:ro" \
  bot sh -c 'pip install --quiet --no-cache-dir -r /checks-deps.lock && python -m pytest -q -c /app/pyproject.toml /checks -p no:cacheprovider'
```

Migrations now are:

- `60f77c25a94d`: initial schema;
- `9edfc2a661bb`: existing business constraints;
- `af730acb7790`: financial provenance, server port and immutable referral attribution;
- `b826ac07d881`: typed inbound JSON storage, server health observations and reconciliation diagnostics.

## NOT YET VERIFIED

- Real BOT_TOKEN authentication, `/start` and the requested user/admin Telegram walkthrough.
- Real sponsor-channel membership and bot permissions, Telegram-delivered QR/media, and behavior
  against actual deleted/old Telegram messages. See `live-telegram.md` for the controlled runbook.
- Exact 3x-ui version/API, authentication, inbound lookup, client CRUD, remote absolute expiry,
  connection URI/client compatibility, health and real VPN traffic. The adapter remains fail-closed.
- Crypto Pay TESTNET invoice creation, manual payment, duplicate settlement, expiry and create-timeout
  recovery. The opt-in smoke test skipped because token/confirmation were absent.
- Crypto Pay mainnet and Card/SBP. No mainnet action was attempted; Card/SBP remains a stub.
- Production-data migration, production-size backup RTO, restore of secrets/panel state, credential
  rotation, load/soak tests, alerts and remote CI.
- Multiple polling frontends. The supported topology remains one frontend; FSM and rate limits are
  process-local. Job batches retain the PostgreSQL advisory lock.

Mock and fixture-backed results are not evidence that external integrations work in production.
The remaining credentials and acceptance inputs are listed in `integrations.md` and
`live-telegram.md`; deployment/rollback instructions are in `deployment.md`.
