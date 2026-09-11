# Deployment and recovery — Phase 8

Status: deployment preparation, not a live production acceptance certificate.
The real 3x-ui adapter is blocked until the exact panel contract is supplied and implemented.
Never select mock to bypass production validation.

## Components and process strategy

- PostgreSQL 16 with a persistent volume. Expose its port only on localhost/private network.
- One aiogram polling frontend per BOT_TOKEN. The default bot also runs jobs.
- PostgreSQL advisory transaction lock covers each full health/observation/billing/reconciliation/
  notification batch. Additional standalone `python -m app.jobs.worker` processes share that lock,
  but are unnecessary for this MVP. Do not scale polling replicas.
- Current FSM and rate limits are process-local. Restart cancels unfinished forms; users reopen them.
- No web admin, Redis, Kubernetes or public payment webhook server is required or included.

## Configuration and secrets

Keep `.env` mode 0600 outside source control and image build context. Configure BOT_TOKEN,
ADMIN_TELEGRAM_IDS, ENCRYPTION_KEY, database credentials, exact THREEXUI_VERSION/BASE_URL/USERNAME/
PASSWORD and, when ready, Crypto Pay token/network. Use a URL-safe random database password or
properly URL-encode credentials in DATABASE_URL; Compose currently interpolates POSTGRES_PASSWORD
into the connection URL. Changing the environment password does not rotate an existing DB role.

Use the same persistent ENCRYPTION_KEY after restore: a DB backup alone cannot decrypt stored
connection strings/withdrawal details. Store and test key recovery separately. Never rotate the
key by simply overwriting `.env`; existing ciphertext requires an explicit re-encryption procedure.
Keep database credentials and panel credentials separate. Public connection parameters belong in
the server DB record, not shared ENV. UUID-bearing subscription URLs are credentials and must not
be stored in plain server configuration; only a public subscription base is allowed there.

## First rollout

1. Obtain the acceptance inputs in integrations.md and complete real-provider checks. Provision
   backups, logging and restricted network access before enabling paying users.
2. Pin a tested application image/release and record its digest, dependency locks and migration head.
   The current Dockerfile uses a moving Python minor tag; production operators should pin the tested
   digest and update it deliberately. Preserve the previous application image.
3. Configure Compose `.env`. Start PostgreSQL and wait for its healthcheck:

   ```bash
   docker compose up -d --wait postgres
   docker compose build
   docker compose run --rm bot alembic upgrade head
   docker compose run --rm bot python -m app.cli seed
   docker compose run --rm bot alembic check
   docker compose run --rm bot python -m app.main --check-infrastructure
   docker compose up -d bot
   docker compose logs --tail 100 bot
   ```

4. Verify getMe/polling startup, absence of a conflicting webhook and healthy process/DB checks.
   A tokenless run must fail clearly. The healthcheck does not prove VPN or payment availability.
5. Run the controlled Telegram checklist with the tester initiating every interaction. Confirm
   remote expiry, paid accounting and recovery before inviting customers.

Current Compose bot policy is `on-failure:3`, `init: true`, SIGTERM grace period 35 seconds.
Monitor exhausted restart attempts. Configure PostgreSQL's host/service restart policy in the
operator deployment (the development Compose file currently has no DB restart policy).

## Logs and monitoring

Collect container stdout/stderr with bounded retention and rotation. Preserve structured error
class, source frames, method/operation, server/config ID and safe transport metadata. Do not collect
full updates, passwords, invoice bodies, private subscription URLs or VLESS URIs.
Alert on startup_validation_failed, server_health_failed, vpn_needs_repair, vpn_observation_failed,
integration_failure, worker_failure and repeated payment/Telegram errors. API timeout or unknown
state is never an indication of successful provisioning. This repo does not deploy an alert backend.

Observe server.health + health_checked_at and config.reconcile_issue + remote_checked_at.
OFFLINE blocks new placement, not deletion of old configs. Health refresh does not change the
administrator's edit revision. UNKNOWN is an untested server; operator onboarding must confirm it.
Notifications are at-least-once, so a crash can repeat a message without repeating a ledger entry.

## Backup and restore

Choose RPO/RTO explicitly. A nightly pg_dump permits up to a day of loss; use WAL/PITR if that is
unacceptable. Dump is transactionally consistent, but capture all deployment secrets separately.
Encrypt backup storage, restrict access, retain multiple generations and test restoration regularly.

Example using PostgreSQL inside Compose (use an access-controlled backup directory):

```bash
umask 077
docker compose exec -T postgres pg_dump -U vpn -Fc --no-owner --no-acl vpn > backup.dump
# Restore into a NEW database, never blindly over the running production database.
docker compose exec -T postgres createdb -U vpn vpn_restore_trial
docker compose exec -T postgres pg_restore -U vpn --exit-on-error --no-owner --no-acl \
  -d vpn_restore_trial < backup.dump
```

Point a separate application check at the restored DB, apply migrations, run alembic check, compare
ledger/wallet totals and row counts, verify sequences and decrypt known samples using the restored
key. Do not run its polling/jobs against production providers. Database roles/permissions are not
included with --no-owner/--no-acl; provision them separately. Delete the trial DB only after review.

An actual automated probe is supplied in `scripts/backup_probe.py`. It creates two unique audit DBs,
seeds synthetic entities, dumps/restores, compares every table's rows, checks migrations and a new
ID, then removes only those two DBs. Run from the project root after starting the isolated audit
PostgreSQL on localhost:55433:

```bash
.venv/bin/python scripts/backup_probe.py
```

Phase 8 restore passed; exact result is recorded in verification.md. This does not establish
recovery time for a production-size dataset or restoration of external panel state.

## Upgrade and rollback

1. Record image digest/migration head; back up DB and verify key availability.
2. Stop the bot/jobs cleanly (`docker compose stop -t 35 bot`) and verify process exit.
3. Apply reviewed migrations once, then check schema. Do not auto-migrate from every replica.
4. Start the new image; inspect logs/health, perform tester smoke checks and monitor accounting.
5. On failure, stop new processes. Prefer rolling back to the previous compatible image while
   retaining additive schema. Review compatibility before starting it.
6. Do not automatically downgrade financial constraints or overwrite DB with an older dump.
   Restoring an old snapshot discards later transactions and can reapply remote effects: reconcile
   payment statements, VPN UUIDs/expiry and ledger with the operator before resuming writes.
7. Phase 8 migration is additive, but previous code does not honor new reconcile_issue billing
   holds. Rolling back to Phase 7 requires keeping jobs stopped until those holds are reviewed.

Remote paid_until expiry must protect access while processes are stopped. If the actual panel
cannot enforce absolute paid expiry reliably, production PAYG remains blocked.
