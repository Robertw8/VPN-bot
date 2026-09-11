# Controlled live Telegram acceptance

No BOT_TOKEN was present in Phase 8; none of the real Telegram scenarios below is claimed passed.
The tester must initiate interactions. Do not send broadcasts or contact other users.

1. Put a test bot's BOT_TOKEN and your Telegram ID in ADMIN_TELEGRAM_IDS in local `.env`.
   Use development + mock VPN, testnet payments only, a dedicated DB and this bot only.
2. Migrate/seed. Start one polling instance; confirm getMe and getWebhookInfo succeed, and no second
   process polls that token. Startup never deletes an existing webhook automatically.
3. Tester sends /start, including an account without username. Confirm balance, active VPN count,
   My VPN, Create VPN, tariffs, topup, promo, referral and support navigation.
4. Admin: create/edit a test server and tariff, preview/back/cancel/save, tap Save twice and use an
   old keyboard. Change balance from a user card without typing an internal ID. Check unauthorized
   admin callbacks with a tester-controlled non-admin account, not a third-party user.
5. Create mock VPN, open card, receive URI and QR, disable/delete with confirmation. Mock URI
   is visibly test data and does not grant network access. Verify ownership with two controlled users.
6. Configure a tester-owned sponsor channel and bot membership permissions. Check unsubscribed,
   subscribed, API failure and cancel/back navigation; remove the test sponsor after the check.
7. Exercise tariffs, promo valid/invalid/duplicate attempts, referral link/counter, topup options,
   withdrawal form/cancel and administrator decision preview. Never mark a real withdrawal paid
   unless the transfer actually happened outside this app.
8. Use names containing `<>&_*`, Cyrillic/emoji, username=None, duplicate callbacks and stale/deleted
   messages. Replies must be plain text, controls usable, and API failures logged without secrets.
9. Stop with SIGTERM; verify resource closure and absence of pending tasks. Restart, reopen forms
   and confirm balances/configs persist. The in-memory FSM intentionally resets at restart.

Current guardrails: keyboard builder enforces 1–64 UTF-8 bytes; outgoing plain SendMessage is split
at at most 4096 UTF-16 units with controls only on the last chunk. Known expired callback and
unchanged edit failures are handled narrowly; deleted editable message falls back to a new message.
Other Telegram errors are logged and not automatically retried, avoiding duplicate side effects.
The product currently creates new messages; edit fallback is preparation for stale editable UI.
Do not include full URIs, tokens or private message text in acceptance evidence.

Record timestamp, environment, tester-owned account role, action, expected/actual outcome and a
sanitized error class. Mark each item PASS/FAIL/NOT RUN; fix only reproduced defects.
