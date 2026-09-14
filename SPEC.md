# Webhook Delivery Platform — SPEC.md

## 1. Project Overview
**Name:** `hookdaemon`

A production-style webhook delivery platform that reliably accepts events from applications and delivers them to customer-defined webhook endpoints.

The project focuses on backend engineering and distributed-system concepts:
- asynchronous processing
- background workers
- Redis wake-up signals
- PostgreSQL persistence
- retries and exponential backoff
- idempotency
- rate limiting
- request signing
- delivery tracking
- failure handling
- observability
- Docker and CI/CD
### Pitch
> A reliable webhook delivery service that accepts events, queues them for asynchronous delivery, retries failures automatically, and provides complete delivery history and observability.

---
## 2. Why This Project

Instead of building another CRUD application, this project demonstrates problems that real backend infrastructure must solve.

The important engineering questions are:
- How do we avoid losing events?
- What happens when the destination is unavailable?
- How do we retry safely?
- How do we prevent duplicate processing?
- How do we control delivery rate?
- How do we track every delivery attempt?
- How do we recover from worker failures?

---
## 3. Core Architecture

```mermaid
flowchart LR
    C[API Client] -->|POST /v1/events| A[FastAPI]
    A -->|single transaction| PG[(Postgres<br/>source of truth)]
    PG -.->|dispatcher polls<br/>next_attempt_at| D[Dispatcher]
    D -->|LPUSH| R[(Redis<br/>wake-up signal)]
    R -->|BRPOP| W[Worker]
    W -.->|fallback: direct poll<br/>when Redis unavailable| PG
    W -->|HMAC-signed POST| EP[Customer Endpoint]
    W -->|SKIP LOCKED claim<br/>record attempt| PG
    RP[Reaper] -.->|recover stale<br/>in_progress rows| PG
```

**Postgres is the source of truth, not Redis.** Redis is only a wake-up signal. If Redis dies, deliveries must not vanish.

**Delivery is at-least-once, never exactly-once.** A worker crash between the outbound HTTP request and recording success causes a retry, and the receiver sees the delivery twice. Receivers must dedupe on `X-Webhook-ID`.

- Redis is **not required for correctness** and **not required for eventual delivery**. Normal path: `Postgres → Dispatcher → Redis → Worker`. Fallback path when Redis is unavailable: **workers poll Postgres directly on a 5-second interval** in addition to `BRPOP`. With Redis down, deliveries still happen; the p50 wake-up delay degrades from ~milliseconds to up to 5 seconds. Durability is unaffected.

- Worker claim uses `SELECT ... FOR UPDATE SKIP LOCKED`. This **prevents two workers from simultaneously claiming the same eligible delivery row** — not "one HTTP delivery ever." Two HTTP deliveries can still occur after a crash; that is the at-least-once property described above.
- Process topology: **1 dispatcher + N delivery workers + 1 reaper**. N is configurable via environment. v1 ships with N≥2 to prove multi-worker safety.

---
## 4. MVP
### 4.1 Endpoint Management

Users register endpoints to receive events.
```http
POST   /v1/endpoints
GET    /v1/endpoints
GET    /v1/endpoints/{id}
PATCH  /v1/endpoints/{id}
DELETE /v1/endpoints/{id}
```

**Request body**:
```json
{
  "url": "https://example.com/webhooks",
  "description": "Order events"
}
```

**Response (201 Created)**:
```json
{
  "id": "ep_01HX...",
  "url": "https://example.com/webhooks",
  "description": "Order events",
  "status": "active",
  "secret": "<one-time>",           // returned only here
  "created_at": "2026-09-14T10:00:00Z"
}
```

All `/v1/*` routes require:
```http
Authorization: Bearer <api_key>
```
**Validation at creation:**
- URL must parse and use `https://` scheme. `http://` is rejected unless `ALLOW_INSECURE_HTTP=true` (dev only).
- Embedded credentials (`https://user:pass@host/...`) are rejected. The userinfo component must be absent.
- URL is normalized: lowercase scheme and host, strip default ports. Path and query are preserved as-is. Fragments and userinfo are rejected, not stripped.
- Reject URL fragments (`#...`).
- Hostname must resolve to a public IP; the SSRF blocklist from §9 is applied at creation, not only at delivery.
- Max URL length: 2048 characters.
- Description: max 500 characters. Optional.

**Secret lifecycle:**
- The server generates the HMAC signing secret on endpoint creation (`secrets.token_bytes(32)`, base64url-encoded).
- The plaintext secret is returned **once** in the `POST /v1/endpoints` response body.
- The stored form is `secret_encrypted` (Fernet).
- `GET /v1/endpoints/{id}` returns endpoint metadata but never the secret.
- Secret rotation is Future Work (`POST /v1/endpoints/{id}/rotate-secret`).
- If a tenant loses the secret, the correct recovery is delete + recreate the endpoint.
- The `whsec_` prefix is part of the credential format (helps secret scanners identify it), not part of the cryptographic secret bytes used in HMAC computation.

**Endpoint deletion race:** deletion prevents *new* delivery attempts from being scheduled. A request already in flight when deletion commits cannot be cancelled — the receiver may see one final delivery. This is accepted; the alternative (distributed cancellation) is disproportionate for v1.

**Delete transaction:** `DELETE /v1/endpoints/{id}` runs in a single transaction: lock the endpoint row, set `status='deleted'` and `deleted_at=now()`, then move all `pending` deliveries of that endpoint to `dead_letter` with `last_error='endpoint deleted'`. Commit. This makes the outcome deterministic:

- delete wins the race → no new claim is possible
- claim wins the race → the in-progress attempt may finish; on failure → dead_letter

### 4.2 Event Creation
```http
POST /v1/events
Idempotency-Key: <unique_key>
```

Request body:
```json
{
  "event_type": "order.created",
  "payload": {
    "order_id": 123,
    "amount": 4999
  }
}
```

The API must:
1. Validate the request.
2. Persist the event AND the delivery rows in **one transaction**.
3. Best-effort `LPUSH` delivery IDs to Redis (log and continue on failure).
4. Return `202 Accepted` with `event_id`.
5. If the tenant has **zero active endpoints**, the event is still persisted and `202 Accepted` is returned with `event_id`. No delivery rows are created. This is a deliberate choice: it preserves ingestion semantics for tenants mid-onboarding.

**Payload size enforcement at ingress:** the HTTP layer enforces `MAX_PAYLOAD_BYTES` (default 262144) *before* JSON parsing. Requests with `Content-Length` above the limit are rejected with `413 Payload Too Large` without reading the body. For chunked requests, the server aborts once the accumulated size exceeds the limit. The DB-level `CHECK` constraint remains as defense in depth.

`GET /v1/events/{id}` returns the event with `delivery_count: 0`.

---

## 5. Delivery Worker
Worker and dispatcher run as **separate processes** from the API.

### Dispatcher
Runs every 5 seconds. Finds due deliveries and wakes workers via Redis.
```sql
SELECT id FROM deliveries
WHERE status = 'pending' AND next_attempt_at <= now()
ORDER BY next_attempt_at
LIMIT 100
```

For each row: `LPUSH` the delivery ID to Redis.
If Redis is down, log a warning and continue — the next poll will retry.

If multiple dispatchers run, the same `delivery_id` may be LPUSHed twice. Duplicate notifications are tolerated because delivery ownership is established atomically in PostgreSQL — the worker's `SKIP LOCKED` claim is the actual gate, not Redis.

**Notification dedupe:** before `LPUSH`, the dispatcher sets a short-lived key `notify:{delivery_id}` with `SET NX EX 30`. If the key already exists, the dispatcher skips the push. This caps the number of duplicate notifications Redis can accumulate when a delivery stays pending longer than one poll interval. Correctness is unaffected either way — Postgres remains the gate. If `SET NX` succeeds but `LPUSH` fails, the dedupe key stays set for up to 30 seconds and the next dispatcher poll may skip this delivery. Correctness is unaffected — Postgres remains the recovery mechanism. Latency may increase by up to the dedupe TTL.

### Worker claim (atomic)

The worker receives a `delivery_id` from Redis (or `None` in fallback mode). It runs one of two claim queries:

**Path A — targeted claim (Redis notification received):** claim that specific delivery. If it is not claimable (already claimed, not pending/due, or endpoint no longer active), the notification is discarded and the worker returns to `BRPOP`.

```sql
UPDATE deliveries d
SET status       = 'in_progress',
    lease_token  = :lease_token,
    locked_at    = now(),
    locked_by    = :worker_id,
    updated_at   = now()
WHERE d.id = :delivery_id
  AND d.status = 'pending'
  AND d.next_attempt_at <= now()
  AND EXISTS (
      SELECT 1 FROM endpoints e
      WHERE e.id = d.endpoint_id AND e.status = 'active'
  )
RETURNING d.*;
```
The targeted claim uses the same `statement_timeout=5s` as the generic claim to avoid lock contention.

If `0 rows` returned, the notification was stale or already consumed — discard and continue.

**Path B — generic claim (Redis fallback / no notification):** claim any
eligible delivery.

```sql
WITH candidate AS (
    SELECT d.id
    FROM deliveries d
    JOIN endpoints e ON e.id = d.endpoint_id
    WHERE d.status = 'pending'
      AND d.next_attempt_at <= now()
      AND e.status = 'active'
    ORDER BY d.next_attempt_at
    FOR UPDATE OF d SKIP LOCKED
    LIMIT 1
)
UPDATE deliveries d
SET status       = 'in_progress',
    lease_token  = :lease_token,
    locked_at    = now(),
    locked_by    = :worker_id,
    updated_at   = now()
FROM candidate c
WHERE d.id = c.id
RETURNING d.*;
```

**Invariant**: a delivery whose endpoint is `deleted` cannot transition to `in_progress`, regardless of path. A deleted endpoint is never newly claimed.

`lease_token` is generated in the worker process (`uuid.uuid4()`) and passed as a bind parameter. Generating it in Python avoids any dependency on the Postgres version's built-in UUID function and makes the ownership token visible in worker logs before the query executes.

Two workers running this concurrently will claim different rows — `SKIP LOCKED` guarantees that no two workers lock the same candidate row inside the CTE, and the UPDATE-...-FROM only touches the one row the CTE selected.

After the claim, the worker has `(delivery_id, lease_token)` in memory and proceeds to the HTTP request.

```text
BRPOP delivery_id from Redis
      ↓
Targeted claim by id (Path A)
      ↓
Claimed?
  ┌───┴───┐
  No      Yes
  │        │
discard   proceed to HTTP
  │
  ↓
continue loop

Fallback path (BRPOP timeout / Redis error):
Generic claim (Path B, LIMIT 1)
```

**Redis fallback:** the worker's main loop is `BRPOP` with a timeout. On `BRPOP` timeout (default 5s) *or* on any Redis connection error, the worker runs the same atomic claim query against Postgres directly. This means the worker does not depend on Redis being available — Redis only shortens wake-up latency.


Each delivery attempt must record:
- attempt number
- timestamp
- HTTP status code
- response time (ms)
- error message + error type
- request and response headers

**Transaction boundary:** the `delivery_attempts` INSERT and the `deliveries` status UPDATE happen in **one transaction**. This prevents the history table and the current state from diverging if the process dies between the two writes.

Graceful shutdown: on `SIGTERM`, stop claiming, finish in-flight requests, exit.

Set `statement_timeout = '5s'` on the claim query to avoid lock contention.

### Lease ownership invariant

Every UPDATE that transitions a delivery out of `in_progress` must filter on `status = 'in_progress'` and the current `lease_token`. `locked_by` is metadata for observability only and is **not** an ownership credential.

```sql
UPDATE deliveries
SET status = 'success', ...
WHERE id = :id
  AND status = 'in_progress'
  AND lease_token = :claimed_lease_token
```

**Why:** every claim generates a fresh `lease_token` (UUID). Any UPDATE that transitions a delivery out of `in_progress` must carry the same token. If the reaper recovered the row and another worker has claimed it, the new token will not match, and the stale worker's UPDATE affects 0 rows. The worker logs the mismatch and exits without corrupting state.

`locked_at` remains for expiration only (the reaper uses it). It is never used as an identity token.

Same rule applies to transitions to `pending` (retry) and `dead_letter`.

### Delivery state machine

Legal transitions only. Enforcement is via **conditional UPDATE in the service layer** (every transition carries `WHERE status = :expected AND lease_token = :token`), backed by property tests. The `delivery_status` enum constrains the *set* of legal values, not the *order* of transitions — illegal ordering is prevented by the conditional WHERE clauses, not the enum.

```text
pending     → in_progress   (worker claim)

in_progress → success       (2xx)
            → pending       (retryable failure, next_attempt_at updated)
            → dead_letter   (max attempts reached)
            → pending       (lease expired — reaper recovery)

success     (terminal)
dead_letter (recoverable — manual retry moves it back to pending)
```

### Endpoint-state-aware transitions

Every transition that would schedule a new attempt (`pending` on retry, `pending` from manual retry, or reaper recovery) must first check the endpoint's status:

```sql
UPDATE deliveries d
SET status = 'pending', ...
WHERE d.id = :id
  AND d.status = 'in_progress'
  AND d.lease_token = :token
  AND EXISTS (
    SELECT 1 FROM endpoints e
    WHERE e.id = d.endpoint_id
      AND e.status = 'active'
  )
  RETURNING d.*;
```

If the endpoint is `deleted`, the delivery transitions to `dead_letter` with `last_error = 'endpoint deleted'` instead. This prevents a late worker or the reaper from scheduling an HTTP attempt against a deleted endpoint.

### Worker heartbeat

Each worker writes `worker:<id>:last_seen` to Redis every 10 seconds with a 30s TTL.
A separate monitor (or the API's `/health/ready`) can alert if no worker heartbeat
exists for 60s.

### Worker crash recovery

A worker that dies mid-delivery leaves a row in `in_progress` with a stale `locked_at`. A separate reaper process returns stale rows to `pending` so they are picked up again.

Reaper loop (runs every 60 seconds):

```sql
-- Recoveries that can be retried: endpoint still active
UPDATE deliveries d
SET status        = 'pending',
    locked_at     = NULL,
    locked_by     = NULL,
    lease_token   = NULL,
    next_attempt_at = now(),
    updated_at      = now()

WHERE d.status = 'in_progress'
  AND d.locked_at < now() - interval '5 minutes'
  AND EXISTS (
    SELECT 1 FROM endpoints e
    WHERE e.id = d.endpoint_id AND e.status = 'active'
  )
RETURNING d.id;

-- Recoveries that cannot be retried: endpoint deleted
UPDATE deliveries d
SET status      = 'dead_letter',
    last_error  = 'endpoint deleted',
    locked_at   = NULL,
    locked_by   = NULL,
    lease_token = NULL,
    updated_at  = now()
WHERE d.status = 'in_progress'
  AND d.locked_at < now() - interval '5 minutes'
  AND EXISTS (
    SELECT 1 FROM endpoints e
    WHERE e.id = d.endpoint_id AND e.status = 'deleted'
  )
RETURNING d.id;
```

- Stale threshold: `5 minutes` (well above the 15s HTTP timeout, well above any reasonable worker pause).
- The reaper runs as its own process, same pattern as `dispatcher.py`. If it crashes, it restarts on the same supervisor.
- On reaper recovery, `attempt_count` is not decremented. The delivery simply becomes eligible again.
- Metric: `deliveries_reaped_total` increments each time this query returns a row.
- `lease_token` is cleared on both reaper paths so the row's ownership state is unambiguous.

---
## 6. Retry System

Retry transient failures:

```text
500, 502, 503, 504
timeout
connection failure
```


Do NOT retry:
```text
400, 401, 403, 404, 405, 410, 422
(except `408 Request Timeout` and `429 Too Many Requests` — those retry)
```
**3xx responses** (301, 302, 303, 307, 308) are treated as **permanent failures** and moved directly to `dead_letter` with `error_type='http'`. Redirects are not followed (`follow_redirects=False`).

Retry schedule (8 attempts max):
```text
Attempt 1 → immediate (initial)
Attempt 2 → +10s
Attempt 3 → +30s
Attempt 4 → +2m
Attempt 5 → +10m
Attempt 6 → +30m
Attempt 7 → +1h
Attempt 8 → +6h
then → DEAD_LETTER
```
After a failed attempt `n` (where `1 ≤ n < 8`), the next delay is the value assigned to attempt `n+1` in the table above. Attempt 1 has no delay (it runs immediately when the delivery is claimed).


**Retry-After**: on 429 or 503 responses that include a Retry-After header (delta-seconds or HTTP-date), the server-requested delay is a floor, never a ceiling. Jitter is never applied below it.

Delay calculation, in order:

```text
scheduled_delay = table value for this attempt
jittered_delay  = random.uniform(0, scheduled_delay)      # full jitter
retry_delay     = max(jittered_delay, retry_after_header)
retry_delay     = min(retry_delay, MAX_RETRY_AFTER_SECONDS)  # default 3600
```
Without a `Retry-After` header, `retry_delay = jittered_delay`.

`MAX_RETRY_AFTER_SECONDS` is a **safety cap, not a passthrough**. If a destination returns `Retry-After: 7200` and the cap is 3600, the worker retries at 3600s, not 7200s. This is deliberate — a misconfigured or hostile destination must not be able to park a delivery in the queue for arbitrarily long. Document this in `DECISIONS.md` when the retry module is written.


After the final attempt:
```text
delivery.status = 'dead_letter'
```

---
## 7. Idempotency

Clients may retry the same request. The system must not create duplicate events.

Header:
```http
Idempotency-Key: abc123
```

Rules:
- Key is scoped **per tenant**: unique on `(tenant_id, idempotency_key)`.
- Store the **request body hash** along with the event. On repeat, hash the incoming request body and compare. Same key + same hash → return the original event. Same key + different hash → `409 Conflict`.
- Store `request_hash`, not `response_hash`. The response for a given event is reconstructable from `event_id`; the request hash is not.
- Repeating the same key with the **same** request body returns the same event and the same response (`202 Accepted`).
- Repeating the same key with a **different** request body returns `409 Conflict`. The server does not create a second event. This prevents accidental key reuse across distinct requests.
- Max key length: 255 characters. Longer keys return `400 Bad Request`.
- Max `event_type` length: 100 characters.
- Max tenant name length: 100 characters (also enforced at DB level).
- Max API key name length: 100 characters.


**TTL:** Idempotency keys expire after **24 hours**. An application maintenance loop inside the dispatcher deletes expired keys every hour. No database extensions required. After the idempotency record expires and is deleted, reuse of the same key is treated as a new request — a new event is created. This is a deliberate TTL boundary; clients that need longer idempotency windows must layer their own idempotency on top.

**Tradeoff:** v1 keeps TTL cleanup in the dispatcher to avoid running a fourth process. If cleanup load grows, extract into a dedicated maintenance worker — the dispatcher's job is delivery scheduling, not housekeeping.

---
## 8. Webhook Signing
Every outgoing webhook includes a signature.

Headers:
```http
X-Webhook-ID: del_01HX...
X-Webhook-Timestamp: 1720000000
X-Webhook-Signature: <hex>
X-Webhook-Signature-Version: v1
```
`X-Webhook-ID` is the stable `delivery_id`. The same value is sent on every retry of the same delivery, which is what receivers deduplicate against.

Algorithm:
```python
message = timestamp.encode("ascii") + b"." + raw_request_body_bytes
signature = hmac.new(secret_bytes, message, hashlib.sha256).hexdigest()
```
`timestamp` is the ASCII string form of the same integer sent in `X-Webhook-Timestamp`. `secret_bytes` is the raw signing secret (not base64-decoded unless the reader's implementation says so — this spec defines the secret as UTF-8 bytes of the `whsec_...` value **with the prefix removed**).


Receiver requirements:
- Reject requests where `abs(now - timestamp) > 300s`. This rejects both **stale** and **future-dated** timestamps, closing the replay window in both directions.
- Verify the HMAC using the shared secret.
- Use constant-time comparison.

---
## 9. Timeout & SSRF Handling

Timeouts:
```text
connect = 5s   (httpx connect timeout)
read    = 10s  (httpx read timeout per read)
```

`httpx` does not expose a single end-to-end request deadline; connect/read/write/pool are independent. To enforce a hard ceiling:

- Wrap the outbound request in `asyncio.wait_for(..., timeout=15)`.
- `DELIVERY_TIMEOUT_SECONDS=15` is the **overall** deadline, enforced independently of per-operation httpx timeouts.
- On timeout, the attempt is recorded as `error_type='timeout'` and the delivery follows the retry path.

SSRF protection:
- Block IPv4:
    - `0.0.0.0/8`
    - `10.0.0.0/8`
    - `100.64.0.0/10`      (CGNAT)
    - `127.0.0.0/8`
    - `169.254.0.0/16`     (includes cloud metadata `169.254.169.254`)
    - `172.16.0.0/12`
    - `192.168.0.0/16`
    - `224.0.0.0/4`        (multicast)
    - `240.0.0.0/4`             (reserved)
- Block IPv6:
    - `::1`
    - `fc00::/7`
    - `fe80::/10`
    - IPv4-mapped (`::ffff:0:0/96`)
    - 6to4 (`2002::/16`)
- Resolve DNS once, validate the resolved IP against the blocklist, then connect to the **validated IP** directly with the original hostname sent as the `Host:` header. The hostname is not re-resolved between validation and connection. This is the DNS-rebinding defense **on paper**.
- **Not considered complete until the full SSRF test matrix in §17 passes.** The implementation must use httpx's explicit-IP transport pattern (custom transport / `transport=` kwarg) so the socket connects to the validated IP while TLS validates against the hostname. Both must be verified by tests — one that captures the peer IP, one that asserts TLS verification fails against a mismatched cert.
- TLS certificate verification uses the original hostname (SNI), not the pinned IP.
- Block redirects: `follow_redirects=False`.
- Cap response body at 64 KB via httpx streaming.
- Payload size cap of `MAX_PAYLOAD_BYTES` is enforced at HTTP ingress, not only at the database. See §4.2.
- **Proxy isolation**: the delivery HTTP client uses `trust_env=False` (or equivalent) so environment proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`) cannot silently redirect outbound connections.
- **Multi-address DNS**: the resolver may return multiple A/AAAA records. The implementation validates **every** resolved address against the blocklist and rejects the request if **any** address is disallowed. It never falls back from a validated address to an unvalidated one.
- **Oversized response**: if the response body exceeds 64 KB, the client stops reading, records `error_type='response_too_large'`, and transitions the delivery to `dead_letter`. Oversized responses are not retried in v1.

Timeouts and SSRF blocks become failed attempts and enter the retry system.

---
## 10. Rate Limiting

**Scope: inbound API requests only.** Outbound delivery throttling per destination is a separate concern and is deferred to Future Work (§18).

Defaults:
- **100 inbound API requests / minute / API key**
- **1000 inbound API requests / minute / tenant**

Implementation: Redis sliding window per API key and per tenant.

When the limit is exceeded:
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 30
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1720000030
```

Limits are configurable per tenant. Per-endpoint limits do not apply to the API surface — they would apply to *outbound* deliveries, which is a different mechanism.

**Atomicity:** the sliding-window check-and-increment must be a single Redis operation — a Lua script (`EVAL`/`EVALSHA`) or an equivalent atomic mechanism such as `MULTI`/`EXEC`. Never check-then-increment as two round trips; two concurrent requests can both see "under limit" and both increment.

An integration test fires N+1 concurrent requests against a limit of N and asserts at most N are accepted within the window. Excess requests receive `429` with `Retry-After` and rate-limit headers.

**Config validation**: at startup, the process rejects the configuration if `DEFAULT_API_KEY_RATE_LIMIT > DEFAULT_TENANT_RATE_LIMIT` unless `ALLOW_INVERTED_RATE_LIMITS=true`. A per-key limit larger than the tenant limit is almost always a misconfiguration.

---
## 11. Delivery History
```http
GET /v1/deliveries
GET /v1/deliveries/{id}
```

Response:
```json
{
  "id": "del_01HX...",
  "event_id": "evt_01HX...",
  "endpoint_id": "ep_01HX...",
  "status": "dead_letter",
  "attempt_count": 3,
  "last_status_code": 503,
  "next_retry_at": null,
  "created_at": "2026-08-18T15:30:00Z"
}
```
**Identifier scheme**:
- **API surface**: ULIDs (128-bit, time-sortable), prefixed with a resource tag: evt_, del_, ep_, key_, tnt_.
- **Database**: stored in UUID columns after stripping the prefix. The prefix is a display artifact, not stored.
- **Conversion:** use a ULID library (`python-ulid` or `ulid-py`). ULIDs are Crockford Base32, not hex — `uuid.UUID(hex=...)` will **not** work on a raw ULID string.

  Correct flow:
  - Parse: `ulid.from_str(raw_without_prefix) → 128-bit int → uuid.UUID(int=ulid.int)`
  - Serialize: `ulid.from_uuid(uuid) → str()` then re-prefix `f"evt_{s}"`

  Example:
  ```python
  from ulid import ULID
  import uuid


  def to_uuid(prefixed: str) -> uuid.UUID:
      raw = prefixed.split("_", 1)[1]
      return uuid.UUID(int=ULID.from_str(raw).int)


  def to_prefixed(prefix: str, u: uuid.UUID) -> str:
      return f"{prefix}_{ULID.from_uuid(u)!s}"
  ```

- **Why**: ULIDs give cheap chronological sort for pagination and debugging; UUID storage keeps the schema conventional and FK-friendly.


---
## 12. Manual Retry

```http
POST /v1/deliveries/{id}/retry
```


Behavior:

```sql
UPDATE deliveries d
SET status        = 'pending',
    attempt_count = 0,
    next_attempt_at = now(),
    lease_token   = NULL,
    updated_at    = now()
WHERE d.id = :delivery_id
  AND d.status = 'dead_letter'
  AND EXISTS (
      SELECT 1 FROM endpoints e
      WHERE e.id = d.endpoint_id AND e.status = 'active'
  )
RETURNING d.*;
```
If `0 rows` returned: `409 Conflict` (Problem Details) with `detail='endpoint deleted or delivery not in dead_letter'`.
- **Reset** `attempt_count` = 0 (start a new retry cycle).
- Preserve all historical `delivery_attempts` rows — total attempts remain available as `COUNT(delivery_attempts)`.
- Set `status = 'pending'`, `next_attempt_at = now()`, clear `lease_token`.
- `LPUSH` to Redis (best-effort).
- Return `202 Accepted`.

Do not send the HTTP request inline. Always requeue.

**Why reset:** `max_attempts` governs a single retry cycle. Without a reset, a delivery that has exhausted 8 attempts would immediately dead-letter on the manual retry, making the endpoint useless. Historical attempts stay for audit.

---
## 13. Database Design

Tables:
```text
tenants
api_keys
endpoints
events
deliveries
delivery_attempts
idempotency_keys
```
### tenants
```text
id              UUID PK
name            TEXT
created_at      TIMESTAMPTZ
```
### api_keys
```text
id              UUID PK
tenant_id       UUID FK -> tenants(id)
key_hash        TEXT UNIQUE                    -- SHA-256 of the key
name            TEXT
created_at      TIMESTAMPTZ
last_used_at    TIMESTAMPTZ
revoked_at      TIMESTAMPTZ NULL
role            TEXT NOT NULL DEFAULT 'member' -- 'admin' | 'member'
```
**Key display:** the plaintext key is returned only on `POST /v1/api-keys`. Only the hash is stored. There is no endpoint to retrieve a lost key — rotate instead. `revoked_at` is set on `DELETE`.

**Roles**: `admin` keys may create additional API keys and manage the tenant. `member` keys may only operate on endpoints, events, and deliveries. The bootstrap command creates a tenant and one admin key. Additional tenants are not created via API in v1 (see §14 Bootstrap).

### endpoints
```text
id                UUID PK
tenant_id         UUID FK -> tenants(id)
url               TEXT
secret_encrypted  BYTEA                          -- encrypted at rest (Fernet / libsodium)
description       TEXT
status            TEXT NOT NULL DEFAULT 'active' -- 'active' | 'deleted'
deleted_at        TIMESTAMPTZ NULL               -- set when status = 'deleted'
created_at        TIMESTAMPTZ
updated_at        TIMESTAMPTZ
```

**Delete semantics**: `DELETE /v1/endpoints/{id}` sets `status='deleted'` and `deleted_at=now()`. Rows are never hard-deleted. Delivery history for the endpoint remains queryable for audit.

**What happens to deliveries of a deleted endpoint**:

- `pending` **deliveries that have not been claimed** are transitioned to `dead_letter` with `last_error = 'endpoint deleted'`. No further HTTP attempts are made.

- `in_progress` **deliveries** are allowed to finish the current attempt. If they succeed → `success`. If they fail → `dead_letter` (never requeued). No retries scheduled.

- `success` / `dead_letter` rows are untouched.

- `GET /v1/endpoints` excludes `deleted` by default; `?include_deleted=true` returns them.

- Recreating an endpoint with the same URL yields a new `id`; history is not merged.

### events
```text
id                  UUID PK
tenant_id           UUID FK -> tenants(id)
event_type          TEXT
payload             JSONB
payload_size_bytes  INT          -- reject if > 262144
created_at          TIMESTAMPTZ
```
Note: `payload_size_bytes` is measured in bytes after UTF-8 encoding.

### deliveries
```text
id               UUID PK
event_id         UUID FK -> events(id)
endpoint_id      UUID FK -> endpoints(id)
tenant_id        UUID FK -> tenants(id)
status           delivery_status -- enum: pending | in_progress | success | dead_letter
attempt_count    INT DEFAULT 0
max_attempts     INT DEFAULT 8
next_attempt_at  TIMESTAMPTZ
last_status_code INT NULL
last_error       TEXT NULL
locked_at        TIMESTAMPTZ NULL       -- expiration clock for the reaper
locked_by        TEXT NULL              -- worker identifier (for logs)
lease_token      UUID NULL              -- ownership token; changes on every claim
created_at       TIMESTAMPTZ
updated_at       TIMESTAMPTZ
UNIQUE (event_id, endpoint_id)
```

```sql
CREATE TYPE delivery_status AS ENUM ('pending','in_progress','success','dead_letter');
```

### delivery_attempts
```text
id                UUID PK
tenant_id         UUID FK -> tenants(id)
delivery_id       UUID FK -> deliveries(id)
attempt_number    INT
status_code       INT NULL
response_time_ms  INT NOT NULL    -- every attempt has an elapsed duration
error             TEXT NULL
error_type        TEXT NULL       -- 'timeout' | 'connect' | 'http' | 'dns' | 'ssrf' | 'response_too_large'
request_headers   JSONB           -- sensitive keys redacted before storage
response_headers  JSONB           -- sensitive keys redacted before storage
created_at        TIMESTAMPTZ
```

**Header storage policy (allowlist):** only a fixed, safe set of headers is persisted. Everything else is dropped before storage.

Allowlist:
```text
Content-Type
Content-Length
User-Agent
Accept
X-Webhook-ID
X-Webhook-Timestamp
X-Webhook-Signature-Version
```

**Why allowlist, not denylist**: a denylist assumes every future sensitive header is known in advance. It isn't. An allowlist fails closed — a new auth header introduced by a client or by a future HTTP spec is simply not stored, never leaks. Cost: less debugging signal for exotic headers.


### idempotency_keys

```text
id               UUID PK
tenant_id        UUID FK -> tenants(id)
idempotency_key  TEXT
event_id         UUID FK -> events(id)
request_hash     TEXT             -- SHA-256 of the raw request body bytes
expires_at       TIMESTAMPTZ      -- created_at + 24h
created_at       TIMESTAMPTZ
```

Indexes:
**Column constraints**:
```sql
ALTER TABLE deliveries
  ADD CONSTRAINT deliveries_attempt_count_nonneg  CHECK (attempt_count >= 0),
  ADD CONSTRAINT deliveries_max_attempts_pos      CHECK (max_attempts > 0),
  ADD CONSTRAINT deliveries_event_endpoint_unique UNIQUE (event_id, endpoint_id);

ALTER TABLE events
  ADD CONSTRAINT events_payload_size_max  CHECK (payload_size_bytes <= 262144),
  ADD CONSTRAINT events_type_len          CHECK (length(event_type) BETWEEN 1 AND 100);

ALTER TABLE tenants
  ADD CONSTRAINT tenants_name_len         CHECK (length(name) BETWEEN 1 AND 100);

ALTER TABLE endpoints
  ADD CONSTRAINT endpoints_url_len        CHECK (length(url) <= 2048),
  ADD CONSTRAINT endpoints_desc_len       CHECK (description IS NULL OR length(description) <= 500),
  ADD CONSTRAINT endpoints_status_valid   CHECK (status IN ('active','deleted'));

ALTER TABLE idempotency_keys
  ADD CONSTRAINT idem_key_len             CHECK (length(idempotency_key) <= 255);

ALTER TABLE api_keys
  ADD CONSTRAINT api_keys_name_len        CHECK (length(name) BETWEEN 1 AND 100),
  ADD CONSTRAINT api_keys_role_valid      CHECK (role IN ('admin','member'));

ALTER TABLE delivery_attempts
  ADD CONSTRAINT attempts_delivery_number_unique  UNIQUE (delivery_id, attempt_number),
  ADD CONSTRAINT attempts_number_pos              CHECK (attempt_number > 0),
  ADD CONSTRAINT attempts_response_time_nonneg    CHECK (response_time_ms >= 0);

```

**Foreign-key behavior:**
- `deliveries.endpoint_id` → `ON DELETE RESTRICT`. Endpoints are **never physically deleted** in v1; `DELETE /v1/endpoints/{id}` is a soft-delete (state → `deleted`). `RESTRICT` prevents accidental hard deletion of an endpoint with delivery history.
- `api_keys.tenant_id` → `ON DELETE CASCADE`. Keys have no meaning without a tenant.
- `deliveries.event_id` → `ON DELETE CASCADE`. Deliveries have no meaning without an event.
- `deliveries.tenant_id`, `events.tenant_id`, `endpoints.tenant_id` → `ON DELETE RESTRICT`. Tenants are never hard-deleted in v1.
- `delivery_attempts.delivery_id` → `ON DELETE CASCADE`.

`tenant_id` **consistency invariant**: `deliveries.tenant_id`, `events.tenant_id`, and `endpoints.tenant_id` must always agree. This is enforced in the service layer by always deriving all three from the authenticated tenant on the API request — never from client input. An integration test asserts consistency for every row created during the test run. A DB-level composite FK is possible but deferred; the invariant is documented and tested.

**Audit model (v1):** no public delete operation for tenants; API keys and endpoints are soft-deleted; events, deliveries, and attempts are retained.


```sql
CREATE INDEX ON deliveries (status, next_attempt_at)
  WHERE status = 'pending';
CREATE INDEX ON deliveries (tenant_id, created_at DESC);
CREATE INDEX ON delivery_attempts (delivery_id);
CREATE INDEX ON events (tenant_id, created_at DESC);
CREATE INDEX ON idempotency_keys (expires_at);
CREATE UNIQUE INDEX ON idempotency_keys (tenant_id, idempotency_key);

```


---
## 14. API
All routes are versioned under `/v1/`.

All list endpoints support pagination:
- `?limit=50` (default 50, max 200)
- `?cursor=<opaque>` (returned in response body as `next_cursor`). Cursor encodes `(created_at, id)` from the last row of the previous page; the query is `WHERE (created_at, id) < (:cursor_created_at, :cursor_id) ORDER BY created_at DESC, id DESC LIMIT :limit`. No `OFFSET`.

All error responses use RFC 9457 (Problem Details for HTTP APIs, which obsoletes RFC 7807):

```json
{
  "type": "https://docs.hookdaemon.dev/errors/validation",
  "title": "Validation failed",
  "status": 422,
  "detail": "event_type must be a string",
  "instance": "/v1/events"
}
```
**Error type URIs** use the `https://docs.hookdaemon.dev/errors/<slug>` namespace. If the domain is not yet live, use `urn:hookdaemon:error:<slug>` — the `urn:` scheme is valid per RFC 9457 and does not require a resolvable host.

### Tenants & Keys
```text
GET    /v1/tenants/me
POST   /v1/api-keys
GET    /v1/api-keys
DELETE /v1/api-keys/{id}    # soft revoke; sets revoked_at
```

**API key format**: keys are cryptographically random (`secrets.token_urlsafe(32)` → 256 bits of entropy), prefixed with `hk_live_` for live keys. Only `SHA-256(key)` is stored. The prefix is part of the display format, not the hashed input, unless the reader's implementation documents otherwise — in v1 the full key including prefix is hashed.

**Tenant creation (v1):** the only way to create a tenant is `make bootstrap`. There is no public `POST /v1/tenants`. The bootstrap flow is a privileged CLI operation that runs once on an empty database. Multi-tenant onboarding via API is deferred to Future Work.

**Delete semantics for API keys:** `DELETE /v1/api-keys/{id}` sets `revoked_at=now()` and returns `204 No Content`. The row is never hard-deleted — it remains for audit. Subsequent requests using the revoked key return `401 Unauthorized`. `GET /v1/api-keys` excludes revoked keys by default; `?include_revoked=true` returns them.

**Last admin key protection:** a tenant must always retain at least one active `admin` key. `DELETE /v1/api-keys/{id}` returns `409 Conflict` if the key is the last active admin. Attempting to revoke it does not modify state.

### Endpoints
```text
POST   /v1/endpoints
GET    /v1/endpoints
GET    /v1/endpoints/{id}
PATCH  /v1/endpoints/{id}
DELETE /v1/endpoints/{id}
```
### Events
```text
POST /v1/events
GET  /v1/events
GET  /v1/events/{id}
```
### Deliveries
```text
GET  /v1/deliveries
GET  /v1/deliveries/{id}
POST /v1/deliveries/{id}/retry
```
### System
```text
GET /health/live
GET /health/ready
GET /metrics
```
**Readiness semantics:**
- `/health/live` — returns `200` if the API process responds. No dependency checks.
- `/health/ready` — returns `200` if **Postgres is reachable** and migrations are at head. Redis is checked and reported but **does not gate readiness** (Redis is optional for correctness by design). Response body includes `{postgres: "ok", redis: "ok|unavailable", migrations: "current"}`.

**Worker health is separate from API readiness**. `/health/ready` reports API process and Postgres reachability. It does not guarantee that workers are running. A dedicated check reads the worker heartbeat keys in Redis (`worker:*:last_seen`) and reports `workers: "ok"|"none"|"stale"` in the readiness body. A healthy API with zero workers is not a healthy delivery system; the readiness body surfaces this without gating the API's readiness.

**Auth model:** `/health/live` and `/health/ready` are unauthenticated by design — load balancers need them without credentials. `/metrics` is unauthenticated in v1 but must be network-restricted (private subnet, firewall, or basic-auth in front) in production. Publicly exposed Prometheus metrics leak internal topology and request patterns.

### Bootstrap

The first tenant is created via a one-time CLI command:
`make bootstrap`

This creates a tenant, an admin key, and prints the key to stdout.

**Bootstrap guards:**
- Runs only when no tenant exists. If any tenant row is present, the command exits with `1` and prints `bootstrap refused: database already contains a tenant`.
- Safe to run twice; second run is a no-op that exits non-zero.
- The admin key is printed once. Only its hash is stored.
- There is no public endpoint to create additional tenants in v1. Deferred to Future Work.

---
## 15. Technology Stack
```text
Python 3.11+
FastAPI
pydantic-settings
SQLAlchemy (async)
asyncpg
Alembic
PostgreSQL
Redis
httpx
structlog                    (JSON logs)
prometheus-client            (metrics)
Docker + Docker Compose
pytest
ruff                         (lint)
mypy                         (strict type checking)
pre-commit                   (git hooks: ruff, mypy, hygiene checks)
detect-secrets               (pre-commit secret scanning)
dependabot                   (weekly dependency updates)
GitHub Actions
k6                           (load test)
gunicorn + uvicorn workers   (production serving)
```

Topology: 1 dispatcher + N delivery workers + 1 reaper, all as separate processes. Redis is used as a wake-up signal only. No Celery, no Kafka, no Kubernetes unless the project actually needs them.

### Connection pool

Pool sizes are **per process** and must fit the Postgres `max_connections` budget.

- API (gunicorn workers): `pool_size=5`, `max_overflow=10`
- Worker: `pool_size=3`, `max_overflow=5`
- Dispatcher: `pool_size=2`, `max_overflow=2`
- Reaper: `pool_size=2`, `max_overflow=2`

All: `pool_pre_ping = True`.

**Example budget (assuming Postgres `max_connections=100`):** with `gunicorn -w 2`, worst-case API = 2 × 15 = 30. Plus 1 dispatcher + N delivery workers + 1 reaper. With N=3: workers 3 × 8 = 24, dispatcher 4, reaper 4. Total ≈ 62. Comfortable but must be checked against the actual `max_connections` of the target deployment. Document the budget in `docs/OPERATIONS.md`.

### Production serving
gunicorn -k uvicorn.workers.UvicornWorker -w 2 app.main:app


### Migrations
Run `alembic upgrade head` as a separate deploy step. Do not run on API startup.

### Request correlation
Four identifiers, distinct roles:

| id | Origin | Purpose |
|---|---|---|
| `request_id` | Generated per HTTP request at the API edge | Correlate logs from a single client call |
| `event_id` | Created when an event is ingested | Event identity, stable forever |
| `delivery_id` | Created per (event, endpoint) pair | Delivery identity, stable across retries |
| `attempt_id` | `delivery_attempts.id` | Single HTTP attempt |

All four appear in structured logs. `event_id` and `delivery_id` are persisted in DB rows. A single event can be traced by grepping `event_id=<id>` across API and worker logs. `/v1/deliveries/{id}` returns the full chain: event → deliveries → attempts.

### Metrics cardinality
Prometheus labels are **bounded sets only**: `status`, `error_type`, `endpoint_result_class` (e.g. `2xx|4xx|5xx|timeout|ssrf`), `http_method`, `route`. Never use `tenant_id`, `event_id`, `delivery_id`, `attempt_id`, `url`, or any user-supplied value as a label — those create unbounded cardinality and will OOM the metrics backend. IDs belong in logs and traces, not metric labels.

### Log secrecy policy
The following are **never** logged, at any level, by any process:
- API keys (plaintext or hashed)
- Endpoint secrets (plaintext or `secret_encrypted` bytes)
- `Authorization` headers
- `Cookie` / `Set-Cookie` headers
- Request or response bodies
- `SECRET_ENCRYPTION_KEY` or any HMAC secret material
- Correlation IDs (`request_id`, `event_id`, `delivery_id`, `attempt_id`) are safe and expected in every log line.

---
## 16. Project Structure

```
hookdaemon/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI app factory + startup/shutdown
│   ├── config.py                    # pydantic-settings Settings class
│   ├── db.py                        # async engine, session factory, get_session dep
│   ├── models/
│   │   ├── __init__.py              # import all models so Alembic sees them
│   │   ├── base.py                  # DeclarativeBase + shared mixins
│   │   ├── tenant.py
│   │   ├── api_key.py
│   │   ├── endpoint.py
│   │   ├── event.py
│   │   ├── delivery.py
│   │   ├── delivery_attempt.py
│   │   └── idempotency_key.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── tenant.py
│   │   ├── api_key.py
│   │   ├── endpoint.py
│   │   ├── event.py
│   │   ├── delivery.py
│   │   └── common.py                # Pagination, ProblemDetail, ErrorResponse
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py                  # auth dependency, db dependency, tenant ctx
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py            # aggregates all v1 routers
│   │       ├── tenants.py
│   │       ├── api_keys.py
│   │       ├── endpoints.py
│   │       ├── events.py
│   │       ├── deliveries.py
│   │       └── health.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── ingest.py                # event ingestion + idempotency
│   │   ├── delivery.py              # delivery state transitions
│   │   ├── signing.py               # HMAC sign
│   │   ├── rate_limit.py            # Redis sliding window
│   │   └── ssrf.py                  # URL validation + DNS pinning
│   ├── workers/
│   │   ├── __init__.py
│   │   ├── dispatcher.py            # poll Postgres → LPUSH Redis
│   │   ├── worker.py                # BRPOP Redis → targeted claim → HTTP POST
│   │   ├── retry.py                 # backoff schedule + jitter
│   │   └── heartbeat.py             # async task launched by worker.py
│   └── core/
│       ├── __init__.py
│       ├── logging.py               # structlog config + request_id middleware
│       ├── metrics.py               # prometheus-client registry + counters
│       └── security.py              # Fernet encrypt/decrypt for endpoint secrets
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py          # initial migration
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # pytest fixtures: DevDB, redis, client
│   ├── unit/
│   │   ├── test_retry.py
│   │   ├── test_signing.py
│   │   ├── test_ssrf.py
│   │   └── test_rate_limit.py
│   └── integration/
│       ├── test_events.py
│       ├── test_deliveries.py
│       ├── test_worker.py
│       ├── test_retry_flow.py
│       └── test_dlq_replay.py
├── k6/
│   ├── ingest.js                    # POST /v1/events load test
│   └── delivery.js                  # end-to-end delivery load test
├── docs/
│   ├── ARCHITECTURE.md              # diagram + rationale
│   ├── SECURITY.md                  # HMAC, SSRF, rate limiting
│   ├── TESTING.md                   # unit + integration strategy
│   ├── OBSERVABILITY.md             # logs, metrics, heartbeat
│   ├── DEPLOYMENT.md                # provider-specific deployment guide
│   └── OPERATIONS.md                # runbook: failure modes and recovery steps
├── scripts/
│   ├── bootstrap.py                 # make bootstrap → creates first tenant + key
│   ├── seed.py                      # optional, for local demos
│   └── receiver.py                  # Flask demo: verifies HMAC signatures end to end
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── lint.yml                 # ruff + format + mypy on push and PR
│       └── ci.yml                   # full test + build pipeline
├── .dockerignore
├── .editorconfig
├── .env.example
├── .gitattributes
├── .gitignore
├── .pre-commit-config.yaml          # git hooks: ruff, mypy, detect-secrets, hygiene
├── .python-version                  # Python version pinned by uv
├── .secrets.baseline
├── CHANGELOG.md
├── DECISIONS.md                     # architecture decision records
├── Dockerfile                       # API image
├── Dockerfile.worker                # worker + dispatcher image (same base, different CMD)
├── docker-compose.yml
├── docker-compose.test.yml          # optional: for integration tests
├── Makefile
├── pyproject.toml                   # deps, ruff, mypy, pytest config
├── README.md
├── ROADMAP.md                       # 8-week plan and cut order
├── SPEC.md                          # full specification
├── uv.lock                          # resolved dependency lockfile
```

**Rules for this structure:**

- **One route file per resource.** `events.py` handles only `/v1/events*`. This is a hard boundary — routes do not span resources.
- **Models and schemas: cohesion over file count.** Group related models/schemas together when they form a natural unit; split when a file grows large or concerns diverge. The goal is clear boundaries and low coupling, not an arbitrary "one class per file" rule.
- **Services hold logic.** Routes call services. Services call models.
- **Workers are separate processes.** `dispatcher.py` and `worker.py` are entry points run by `python -m app.workers.dispatcher` / `worker`.
- **`core/` holds only logging, metrics, security.** Nothing else. If you want a util, put it in the module that uses it.


### .env.example
```text
DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/webhooks
REDIS_URL=redis://redis:6379/0
SECRET_ENCRYPTION_KEY=<fernet key>
LOG_LEVEL=INFO
MAX_PAYLOAD_BYTES=262144
DELIVERY_TIMEOUT_SECONDS=15
IDEMPOTENCY_TTL_HOURS=24
DEFAULT_API_KEY_RATE_LIMIT=100
DEFAULT_TENANT_RATE_LIMIT=1000
MAX_ATTEMPTS=8
DISPATCH_INTERVAL_SECONDS=5
MAX_RETRY_AFTER_SECONDS=3600
ALLOW_INSECURE_HTTP=false
```

### Local development

Use [DevDB](https://github.com/BlackStarCodes/devdb) for ephemeral Postgres instances during development.

```bash
devdb start --ttl 300
# prints DATABASE_URL to stdout
```
Integration tests invoke `devdb start` and parse `DATABASE_URL` from stdout, then stop the container at teardown.

---

## 17. Acceptance Criteria

### System properties

```text
✓ durable event storage
✓ asynchronous processing
✓ retry semantics
✓ idempotency
✓ timeout handling
✓ SSRF protection
✓ failure recovery
✓ concurrency (multi-worker safe)
✓ rate limiting
✓ observability
✓ integration testing
```

### Implementation checks

```text
✓ Event is persisted before delivery
✓ Worker processes queued deliveries
✓ Successful delivery is recorded
✓ Failed delivery is retried with exponential backoff + jitter
✓ Maximum retry count is enforced
✓ Failed deliveries become dead-lettered
✓ Webhooks are HMAC-signed with timestamp
✓ Duplicate events are prevented with idempotency
✓ HTTP timeouts are handled
✓ Worker uses SKIP LOCKED claim
✓ Dispatcher survives Redis outage
✓ Delivery history is available
✓ Manual retry works
✓ Rate limiting works
✓ Unit tests exist
✓ Integration tests via DevDB (Postgres) + Docker (Redis)
✓ Docker Compose runs the complete system
✓ CI runs automatically
✓ Project is deployed
✓ Load test numbers in README
✓ README documents architecture and tradeoffs
✓ Two workers run concurrently and claim different deliveries with no duplication
✓ Worker crash between HTTP 200 and DB commit → reaper recovers → same webhook delivered again (at-least-once is preserved; no exactly-once claim)
✓ Concurrent POST /v1/events with the same Idempotency-Key → exactly 1 event, 1 idempotency row, 2× 202 responses referencing the same event_id
✓ Redis LPUSH fails after event commit → dispatcher poll fallback → delivery still occurs
✓ Duplicate notification (same delivery_id LPUSHed twice) → one claim → one HTTP request
✓ Endpoint deleted before claim → no HTTP request sent
✓ Endpoint deleted after claim, in_progress → current attempt allowed to finish; failure → dead_letter
✓ Worker BRPOP fails (Redis down) → worker enters fallback → claims from Postgres → HTTP delivered
✓ Claim/delete race: repeated concurrent runs assert "delete wins → no claim" OR "claim wins → in-progress may finish"
✓ Retry cycle: 8 auto attempts → dead_letter; manual retry resets attempt_count=0 → attempt 9 runs; delivery_attempts has 9+ rows
```

### Security tests

SSRF — verify each is blocked and reported as an `ssrf` attempt:

```text
✓ http://127.0.0.1/
✓ http://localhost/
✓ http://10.0.0.1/
✓ http://172.16.0.1/
✓ http://192.168.1.1/
✓ http://169.254.169.254/
✓ http://[::1]/
✓ http://[fc00::1]/
✓ redirect to private IP (302 → 10.0.0.1)
✓ DNS rebinding (public A → private A after TTL)
```

Tenant isolation — verify each is rejected:

```text
✓ tenant A reads tenant B's events       → 404
✓ tenant A reads tenant B's deliveries   → 404
✓ tenant A modifies tenant B's endpoint  → 404
✓ tenant A retries tenant B's delivery   → 404
```

Header redaction — verify each is stripped:

```text
✓ Authorization
✓ Cookie
✓ Set-Cookie
✓ Proxy-Authorization
✓ X-Api-Key
```

### State-machine invariants
No service operation may produce an illegal transition. Every state pair is covered:

```text
success     → anything            rejected
dead_letter → in_progress/success rejected (only → pending via manual retry)
in_progress → in_progress         rejected
pending     → success             rejected (must pass through in_progress)
```

### At-least-once proof
Integration test sequence:

1. Mock endpoint accepts a delivery, returns 200, and records the `X-Webhook-ID`.
2. Worker is killed (`SIGKILL`) immediately after the HTTP response is received but before the DB commit.
3. Reaper threshold is temporarily set to 1 second for the test.
4. Reaper returns the row to `pending`.
5. A second worker claims it, sends the same request, mock endpoint records a **second** `X-Webhook-ID` with the same value.
6. Assertion: two HTTP requests were received, both with the same `X-Webhook-ID`. This proves at-least-once, not exactly-once.

---

## 18. Future Work
Deferred enhancements. Not planned for v1, not rejected — prioritized after core system is deployed.


1. **Per-endpoint concurrency limit** — cap in-flight deliveries to a single destination to avoid overloading slow receivers.
2. **Webhook pause / resume** — reintroduce a `paused` endpoint status with `POST /v1/endpoints/{id}/pause` and `/resume`.
3. **Scheduled delivery** — `deliver_at` field on event creation.
4. **Delivery dashboard (React)** — total events, successful deliveries, failed, retry rate, average latency.
5. OpenTelemetry tracing
6. Circuit breaker per endpoint
7. Admin CLI (`webhook-cli retry`, `webhook-cli dlq list`)
8. AWS deployment (RDS + ElastiCache + ECS Fargate + Terraform)


---

## 19. Known Limitations

This system is production-style, not production-ready. Honest boundaries:

- **No exactly-once delivery.** Delivery is at-least-once. Receivers must be idempotent on their side using `X-Webhook-ID`.
- **No ordering guarantee.** Deliveries to the same endpoint may arrive out of order after retries.
- **No ordering across endpoints.** Events fan out concurrently.
- **Single Postgres instance.** No replication or failover. Underlying DB outage = full downtime.
- **Redis is a signal, not a queue.** With Redis unavailable, workers fall back to polling Postgres on a 5-second interval; delivery continues but wake-up latency degrades.
- **No horizontal scaling of Postgres.** `SKIP LOCKED` scales workers, not the database.
- **Rate limiting is per API key and per tenant (inbound).** Outbound destination throttling is not implemented.
- **No TLS between API and Postgres/Redis in v1.** Local Docker Compose has no transport encryption. Production deployment should enable Postgres TLS and Redis TLS where supported.
- **Secrets at rest use Fernet with a single static key.** Rotation is manual: decrypt all `endpoints.secret_encrypted` values with the old key, re-encrypt with the new key in a single transaction, then retire the old key. Production would use KMS envelope encryption with per-record data keys so rotation is per-record, not fleet-wide.
- **No admission control.** When `deliveries.pending` grows faster than workers drain it, the queue grows unbounded. At this scale, the operator scales workers or adds endpoint-level throttling. There is no queue-depth cap or automatic shedding.
- **No multi-region.** Single deployment region.

## 20. What I'd Do at 10x Scale

Not built. Documented to show where the design would break first.

| Limit | At 10x | Change |
|---|---|---|
| Postgres write throughput | ~500–1000 events/sec | Partition `events` and `deliveries` by month; move to a sharded or distributed Postgres (Citus, CockroachDB). |
| Dispatcher poll latency | 5s floor | Replace polling with Postgres `LISTEN/NOTIFY` or a dedicated durable queue (NATS JetStream, Kafka). |
| Worker claim contention | Moderate | Increase `max_connections`, tune `statement_timeout`, add connection pooler (PgBouncer). |
| Redis single instance | Memory + throughput | Redis Cluster; or drop Redis entirely and rely on Postgres `LISTEN/NOTIFY`. |
| Delivery attempts table growth | Hundreds of millions of rows | Partition by `created_at`; archive to cold storage after 90 days. |
| Rate limiting Redis hot key | Per-API-key counters | Shard counters per endpoint + time window; use Redis Streams for accurate sliding windows. |
| Single region | Global latency | Region-local workers; ULIDs provide sortable identifiers but do not by themselves solve multi-region consistency — cross-region replication strategy is required. |
| Secret management | Manual rotation | AWS KMS / GCP KMS with envelope encryption; rotate on schedule. |
| Observability | Prometheus local scrape | Remote write to a managed TSDB; add OpenTelemetry tracing for cross-service correlation. |
| Cost | Linear | Batch delivery attempts, tiered storage for old deliveries, autoscale workers by queue depth. |

**The invariant that doesn't change:** Postgres remains the source of truth. Everything else is a scaling lever on top of that decision.

## 21. Demo Receiver

A ~50-line Flask app in `scripts/receiver.py` that receives webhooks and verifies the HMAC signature. Used to demonstrate the full loop end to end. Not part of the service.
