# Integration readiness and production onboarding

Status: mock VPN and a fail-closed 3x-ui scaffold; implemented Crypto Pay adapter tested with
controlled fixtures; no real credentials supplied. Card/SBP remains an explicit unavailable stub.

## Production VLESS / 3x-ui: exact owner checklist

Obtain these facts through the operator's secure configuration process, not in logs or screenshots:

- [ ] Panel HTTPS URL including any path prefix, trusted certificate setup and network allowlist.
- [ ] Exact 3x-ui version/build/fork and matching official API specification or sanitized examples.
- [ ] Username/password or documented API credentials; credential rotation/auth-session semantics.
- [ ] Inbound ID for each server; confirm whether one panel manages all listed servers.
- [ ] Sanitized inbound configuration: VLESS protocol, transport (TCP/WS/gRPC/etc.), stream settings,
  flow, path/service name, client lookup/update semantics and concurrent inbound editing rules.
- [ ] Public hostname/IP and **public port** for each server; confirm NAT/forwarding if relevant.
- [ ] TLS/REALITY: security mode, SNI/server names, ALPN if needed, certificate trust, REALITY public
  key / short ID / fingerprint / flow as applicable. Private keys remain on the server or secret store.
- [ ] Subscription configuration: whether an actual subscription service exists, HTTPS base URL,
  path/token format, client-to-subscription mapping and what can be returned to users.
- [ ] Capacity assumptions: maximum provisioned clients, active clients/connections, traffic limits,
  oversubscription and whether a disabled client still consumes panel capacity.
- [ ] Clock synchronization and **hard per-client expiry** supported/enforced by the panel/proxy.
- [ ] API timeouts/rate limits, health endpoint/operation, session expiry, error/not-found shapes.

Implementation / acceptance checklist (required before allowing production startup):

- [ ] Implement the existing `ThreeXUIVlessProvider` methods using that exact documented API.
  No endpoints are currently guessed or called. Domain services and VlessProvider remain unchanged.
- [ ] Pass the committed UUID to create; create disabled. Lookup first on every reconciliation.
  Lookup failure is UNKNOWN, never a reason to create another UUID or assume absence.
- [ ] Confirm client ownership/inbound on lookup; a UUID conflict must fail closed.
- [ ] Enable sets `paid_until` on the actual VPN server. Kill the worker and prove access expires
  remotely. Without this, the PAYG downtime policy is unsafe and deployment must not proceed.
- [ ] Repeat enable/disable/delete and test timeout **after** the remote operation took effect.
  Reconciliation must observe the same client and converge without duplicate creation/disable.
- [ ] Protect whole-inbound edits against lost updates, if the chosen API requires them.
- [ ] Configure `THREEXUI_BASE_URL`, `THREEXUI_USERNAME`, `THREEXUI_PASSWORD` and `ENCRYPTION_KEY`;
  inject any additional transport settings only after their contract is established.
- [ ] Verify URI/subscription/QR on actual supported VLESS clients and measure traffic/capacity.
- [ ] Only then replace the explicit 3x-ui startup rejection with an implemented-provider readiness
  check; set `VLESS_PROVIDER=threexui`, `APP_ENV=production`, run migrations and the live checklist.
- [ ] Set up backups, secret/key restore, worker/integration-error alerts and a rollback procedure.

`ServerSpec` includes server ID, host, inbound ID and port. Panel-specific TLS/transport/subscription
construction belongs inside the adapter, not Telegram handlers. Multiple independent panels would
need explicit adapter configuration keyed by server ID; do not guess their credentials or routing.

## Production CryptoBot / Crypto Pay: exact checklist

Source reviewed during Phase 7:
[official Crypto Pay API documentation](https://help.send.tg/en/articles/10279948-crypto-pay-api)
(API 1.5.2 on the retrieved page). Mainnet/testnet hosts, invoice fiat mode, statuses and
HMAC-SHA256 scheme were checked against this source. No live invoice was created.

- [ ] Create a Crypto Pay application in `@CryptoTestnetBot`, and store its token in `.env`.
- [ ] Set `CRYPTOBOT_ENABLED=true`, `CRYPTOBOT_TESTNET=true`; keep real keys out of source/logs.
- [ ] Confirm the merchant supports fiat RUB invoices paid with supported crypto assets.
- [ ] Create a small invoice through the bot, actually pay it, and check via authenticated getInvoices.
- [ ] Repeat/manual-check concurrently; verify exactly one principal credit, separate configured bonus
  and referral reward, and matching wallet/ledger totals. Rewards apply to invoice face value,
  excluding promotional/admin credits; provider fees do not reduce the displayed topup.
- [ ] Test expiry, delayed paid state, wrong ID/amount/currency/payload and provider outage.
- [ ] Test createInvoice timeout after remote creation. Do not retry creation. Recover through
  admin using the external invoice ID; server-side lookup must match stored payload, RUB and amount.
- [ ] Create a separate mainnet application in `@CryptoBot`. Replace token and set
  `CRYPTOBOT_TESTNET=false`. Transfers/withdrawal API permissions are not required by this MVP.
- [ ] Run a small real mainnet payment and reconcile it against the merchant statement.
- [ ] Define accounting, refund/chargeback, receipt and dispute procedures before charging customers.
- [ ] Monitor pending/ambiguous payments and errors; manual checking is currently the user-facing transport.

If webhooks are desired later:

- [ ] Deploy an actual bounded HTTPS receiver; none is exposed by this project yet.
- [ ] Read the **raw bytes**, cap body size, validate `crypto-pay-api-signature` with
  HMAC-SHA256 using SHA256(token) as the key, and route through `PaymentService.webhook`.
- [ ] Keep a secret unguessable path and production TLS; never expose token in the URL/logs.
- [ ] Map unknown invoices to reconciliation/operator attention rather than silently crediting them.
- [ ] Respect Crypto Pay's documented delivery retry window (up to 3 days). Current verifier accepts
  3 days plus 5-minute tolerance and rejects timestamps more than 5 minutes in the future.
  Financial replay protection comes from locked payment state and DB uniqueness, not request_date alone.
- [ ] Return successful acknowledgement only after settlement commits; replay the same event to prove
  no additional principal credit. Account for external retries when returning non-success responses.

Supported response handling: invoice creation, authenticated getInvoices lookup, active/paid/expired
mapping, strict RUB decimal strings, payload/ID verification, malformed envelope rejection, constant-time
signature comparison and bounded signed-event acceptance. `createInvoice` has no promised idempotency
key comparable to transfer.spend_id: payload is a correlation reference, not a retry guarantee.

## Card / SBP

No provider has been selected; `CardSbpPaymentProvider` deliberately shows a Russian unavailable message.
Required: merchant/sandbox details, official create/status/webhook API, documented idempotency,
merchant ID/signature validation, amount/currency rules, refunds/chargebacks and receipt requirements.
Implement the existing PaymentProvider interface and the same settlement invariants; do not invent APIs.

## Runtime / operations

- One polling frontend per token. Updates are handled sequentially; long integrations limit throughput.
- Job batches use a PostgreSQL advisory transaction lock on a dedicated connection. Only one batch
  executes at a time per database; rollback, cancellation or lost connection releases the lock.
  Extra idle workers may run a subsequent empty/idempotent batch; ledger uniqueness remains mandatory.
- Memory FSM and per-user rate limits are local to the frontend and reset at restart.
- Billing uses UTC rolling periods. After downtime, a new prepaid period starts at recovery time;
  missed unserved time is not back-billed. This depends on the real provider enforcing remote expiry.
- Notifications remain at-least-once. A process crash after sending but before DB commit can repeat
  a message; it does not repeat a financial posting.
- Startup validates configuration, DB migration head, router construction, getMe and absence of a
  conflicting Telegram webhook before jobs/polling. SIGTERM stops polling/jobs before closing resources.
- `python -m app.health` checks process heartbeat plus DB connectivity. It does not attest that Telegram,
  VLESS, Crypto Pay or card/SBP is currently operational.

## Phase 8 additions: typed configuration and state observation

`Server.host`, `port` and `external_inbound_id` are the existing DB equivalents of public_host,
public_port and inbound_id. New `connection_config` JSON is validated as `InboundConfig` through
ServerInput/AdminService; it holds network, security, SNI, fingerprint, REALITY public key/short ID,
flow, WebSocket path/Host, gRPC service_name and subscription base settings. Keep only public
subscription information there; client-specific tokens/URLs remain encrypted on VpnConfig.
Connection changes on used servers are rejected. Existing guided forms preserve these fields;
technical onboarding may supply them via the existing validated administrative input path.

`VlessConnection` serializes/parses explicitly supplied settings. It never picks missing transport,
security, host/port, REALITY or TLS values. Supported serialization subset: tcp, ws, grpc; unknown
networks, query keys and duplicate keys fail closed. A matching inbound adapter must populate it
from actual get_inbound results. No live panel was inspected; no automatic conversion from guessed
3x-ui JSON exists. URI serialization is checked against the Xray project's share-link proposal:
https://github.com/XTLS/Xray-core/discussions/716 . This is not a client compatibility certificate.

ThreeXUIVlessProvider now explicitly exposes authenticate/get_inbound along with the existing
lifecycle. They remain unavailable until the precise version/API is supplied. THREEXUI_VERSION
records that version; no endpoint paths are inferred. Generic transport errors are normalized into
AuthenticationError, PermissionError, NotFound, AlreadyExists, ValidationError, Unavailable, Timeout
and AmbiguousOperation. A real adapter must additionally classify its documented response envelopes;
a failed lookup/NotFound HTTP endpoint does not automatically prove absence of a UUID.

### Remote expiry and reconciliation

The existing contract sends exactly committed paid_until to enable_client. PAYG pays the next
rolling 24-hour window first; subscription pays its selected duration first. No reconciliation
operation charges money or grants later expiry. `remote_expiry_ms` converts an aware future timestamp
to absolute UTC milliseconds, rounds down and rejects unlimited/expired values. The version-specific
adapter must confirm whether that is the panel's actual unit before using it.

Each locked job batch refreshes server health, observes established configurations, then runs billing
and pending intents. The stateless mock is excluded from remote-state observation; stateful fixtures
exercise these cases in tests:

| Local / observed remote | Action |
| --- | --- |
| Entitled active / active with matching expiry | Record successful observation |
| Established client / absent | ERROR + REMOTE_ABSENT; never create automatically |
| Desired disabled or unpaid / enabled | Disable same UUID; preserve prepaid PAYG renewal intent where applicable |
| Entitled active / disabled with restorable=True and known expiry | Restore only to existing paid_until |
| Entitled active / disabled for unknown/quota/manual reason | REMOTE_DISABLED_REVIEW; operator intervention |
| Entitled active / expiry differs, otherwise restorable | Synchronize expiry to existing paid_until |
| Lookup failure / wrong UUID / missing remote expiry | ERROR with diagnostic; no creation/deletion |
| Pending CREATE timeout / UUID exists | Existing durable-intent reconciliation associates same UUID |

The real adapter must set Client.restorable only after ruling out quota, manual/admin and policy
restrictions. Defaults are false. Reconcile issues hold new billing/activation until safely resolved;
operator repair of absent clients is deliberately not automatic. Unknown expiry on an already enabled
remote client requires urgent operator attention; an unimplemented adapter cannot guarantee access
revocation. Real enforcement remains a production acceptance blocker.

Server health values: HEALTHY, DEGRADED (auth/permission problem), OFFLINE (unreachable/failed check),
UNKNOWN (not checked). OFFLINE is excluded from new placement. Existing configs are never deleted
because health failed. Deterministic priority retains the existing convention: smaller number means
higher priority, then fewer ACTIVE local clients, then server ID. Capacity counts every non-deleted
local reservation, including disabled/pending clients. A real adapter must account for unmanaged panel
clients/capacity or require a dedicated inbound; this MVP has no remote capacity inventory API.

### Crypto Pay TESTNET runbook

1. Configure a separate test app token, CRYPTOBOT_TESTNET=true. The opt-in smoke test refuses mainnet.
2. Run `CRYPTOBOT_LIVE_CONFIRM=TESTNET .venv/bin/pytest -m cryptobot_live -q`. This creates one 1 RUB
   testnet invoice and checks returned HTTPS URL, payload, amount and status lookup. It does not pay
   or transfer funds and does not create an application ledger row. Ordinary pytest excludes it.
3. For application E2E, tester opens the bot topup menu and creates an invoice. Open the returned
   payment URL and pay manually using testnet assets. Press status check repeatedly/concurrently.
   Verify one principal ledger credit for provider+external ID and correct configured rewards.
4. Leave another invoice unpaid until the adapter's 3600-second expiry, then check EXPIRED and no credit.
5. For create timeout recovery, use a controlled proxy that loses the response AFTER remote create.
   Do not resend createInvoice. Find the exact test invoice in the merchant UI; open its application
   payment card → Recover, enter the external invoice ID and confirm authenticated amount/RUB/payload
   matching. Repeat checks: one credit only. Do not inject this failure on mainnet.
6. Record sanitized IDs/status/counts, never tokens/full provider bodies. Only after these pass,
   follow the separate manual mainnet onboarding checklist above.

No token was present, so real testnet creation, paid/expired/recovery cases remain NOT RUN.

### Card / SBP integration checklist (still a stub)

- Select the provider explicitly with the owner and obtain a test environment and official API.
- Implement create payment with exact kopecks/currency/idempotency and redirect or QR information.
- Verify raw webhook signature, merchant ID, amount, currency and known payment identity.
- Implement authenticated status mapping including cancellation and pending/ambiguous states.
- Prove concurrent delivery credits once through existing PaymentService settlement and constraints.
- Specify refund behavior if needed; refunds are a new audited business operation, not a silent
  reversal inside the provider adapter. The current domain has no refund API.
- Complete sandbox creation/paid/cancelled/duplicate/outage checks before a controlled live payment.
Billing does not depend on a card provider. Existing create/status/webhook provider methods can be
implemented without changing billing or principal settlement.
