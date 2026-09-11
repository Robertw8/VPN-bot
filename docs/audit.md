# Phase 7 — integration audit and product hardening

The existing project was inspected directly. The layered aiogram / service / SQLAlchemy / provider
architecture was retained. No replacement project, speculative provider endpoints or repository
wrapper layer was introduced. Source files were present without Git metadata, so no commit/PR
or Git-based diff is claimed.

## Findings and disposition

| Finding in inspected code | Risk / impact | Change |
| --- | --- | --- |
| Monetary parser multiplied arbitrary Decimal input under the default precision and compared its limit through binary float | A sufficiently long fractional value could be rounded into an accepted cent amount; float violated the money policy | Strict bounded decimal grammar, integer conversion to kopecks; Decimal only for formatting; centralized percent formatting |
| Signed malformed webhook payload / request_date could raise AttributeError | Technical exception instead of a controlled rejection | Typed invoice model, envelope/type/URL/signature validation, bounded payload size, safe DomainError |
| Webhook age limit was 5 minutes while official delivery retries last 3 days | Legitimate delayed notifications could be discarded | Accept signed requests within 3 days plus 5-minute tolerance; exact-once settlement remains DB-backed; manual check remains available |
| CREATE reconciliation blindly repeated create_client, trusting the provider's idempotence contract | A poorly implemented real adapter could create a second client after timeout | Lookup by the committed UUID before creation; lookup failure is not absence; existing client is reused and forced disabled before finishing provisioning |
| A disable timeout could be retried despite a successful remote disable | Repeated external effects and misleading integration tests | Reconcile by remote state; already disabled/absent client needs no second disable; retain ERROR + intent while lookup/mutation is unknown |
| Worker started before Telegram authentication, and standalone worker lacked managed termination | Jobs could run while frontend startup failed; cleanup was incomplete | Config/DB/auth preflight, shared managed runtime, bounded Telegram preflight, SIGTERM handling, tasks before HTTP/DB shutdown |
| Dispatcher could close Bot HTTP session before background work finished | Background requests fail during shutdown | Application owns shutdown order; sequential update handling avoids orphan handler tasks; polling closes before worker/session/engine cleanup |
| Every process launched jobs without a distributed leader guard | Redundant concurrent batches / external activity even though financial row locks existed | PostgreSQL advisory transaction lock around a complete job batch; row locks and unique ledger keys retained |
| Ledger financial provenance existed only in string keys | Weak protection against future service regressions with different keys | Nullable unique payment_id for principal credit, unique billing_config_id + billing_period and required links for these kinds; legacy keys backfilled |
| Referral assignment was immutable only by application convention | Direct/future writes could reassign ancestry or introduce cycles | Attribution is included in initial INSERT; DB trigger prohibits changes; referrer must have a smaller internal ID |
| Server connection specification had no public port | Real VLESS adapter lacked a necessary connection parameter | Server.port, validation, migration, ServerSpec.port and guided input; used server connection fields cannot change |
| JSON was the primary admin interface | End administrator could not perform routine operations safely | Guided forms, record cards, preview/save, back/cancel, integer/decimal validation; plain forms for lookup, adjustments and invoice recovery; withdrawal buttons |
| Old admin buttons / stale edit snapshots could affect a later form or overwrite another admin | Accidental modifications | Per-step nonces, one-use save, optimistic updated_at check; settings compare edited keys against their original values |
| Old enable button could pay for a new period after state changed | Unintended activation/payment from a stale keyboard | Enable buttons carry config revision checked under its DB lock; old legacy enable buttons require reopening the card |
| Username was refreshed only on /start | Admin search could continue showing a previous username | Profile names refreshed from authenticated Telegram updates; Telegram ID remains the identity |
| Only a generic 0.4-second throttle protected promo input | Unnecessary promo brute-force capacity | Five attempts per minute per account in addition to generic throttling |
| Exceptions were logged only by class | Insufficient production diagnosis | Structured origin frames, cause class, SQLSTATE and HTTP status; no exception bodies, SQL params, tokens or VLESS credentials; framework warning/error text is suppressed |
| Docker application image had never been built/run | Deployment assumptions were unproven | Real image build, Compose PostgreSQL, migrations, seed, infrastructure validation, local HTTP polling probe, healthcheck and SIGTERM test |

## Invariants retained and independently checked

- Unique Telegram ID; first /start attribution and bonuses occur once.
- Exact integer balances; ledger and wallet change commit together; no negative wallets.
- Unique `(provider, external_payment_id)`; repeated/concurrent paid events credit principal once.
- Promo limits serialized by promo row lock with unique `(promo_id, user_id, ordinal)`.
- PAYG and subscription modes remain separate. Rolling 24-hour prepayment resumes from current
  time after downtime; it does not retroactively charge periods without remotely authorized access.
- Billing periods survive process restart; UTC arithmetic prevents DST/calendar-day duplication.
- Insufficient funds stops desired renewal, creates one notification and reconciles remote disable.
- Withdrawal funds are reserved once, and rejection refunds once; paid/rejected requests do not reopen.
- Admin rights checked in callbacks and service methods; VPN/payment ownership checked on reads/writes.
- Sponsor errors fail closed; support/information and `/cancel` remain available.
- Plain Telegram text is used; user/provider content is not interpolated into HTML/Markdown parse mode.

## Remaining production limitations

- 3x-ui is an explicit fail-closed adapter scaffold. Startup rejects selecting it until implemented
  and verified. The mock never provides real VPN connectivity.
- Card/SBP provider is still unselected. Refunds, chargebacks and automatic banking payouts are absent.
- No real Telegram/Crypto Pay credentials were supplied; live external E2E remains unverified.
- Webhook verification and settlement entry points exist, but no public HTTP webhook receiver is deployed.
- One polling frontend is supported. Memory FSM and promo rate limit reset on restart; additional
  frontend processes require shared FSM/rate limiting. Advisory locking covers jobs, not Telegram's
  one-poller-per-token rule. Sequential handling trades throughput for simple bounded shutdown.
- Healthcheck proves local process liveness plus DB connectivity, not Telegram/VPN/payment readiness.
- Notifications are at-least-once; a crash after Telegram delivery and before commit can repeat a message.
- Repeated integration failures remain visible as ERROR. There is no alerting stack/backoff policy
  beyond the worker interval, and prolonged enable failures require explicit audited compensation.
- Referral registration bonuses still require a business anti-abuse policy for multiple real Telegram accounts.
- VPS capacity, panel concurrency, remote expiry, TLS/REALITY and client interoperability need real tests.
- Backup/restore, key rotation, infrastructure monitoring, receipts/legal terms and retention policies
  remain operator responsibilities. Financial accounting against processor statements is not automated.

See [verification.md](verification.md) for actual commands/results, and
[integrations.md](integrations.md) for exact production onboarding checklists.
