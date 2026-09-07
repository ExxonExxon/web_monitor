# Website Monitor — Development Plan

> A self-hosted, Docker-based website and service monitoring application for personal use.
> Channels are pluggable: alerts go to **Telegram today**, but the architecture treats "how the user is notified" as a swappable channel, so **email, webhooks, etc.** can be added or swapped in later with zero changes to the monitoring engine.

---

# 1. Project Overview

## 1.1 Purpose

Website Monitor is a self-hosted monitoring application that continuously probes websites and web services and notifies the user through one or more **notification channels** (initially Telegram) when a monitored service is unavailable or unhealthy.

The application is intended primarily for personal use.

It should be:

- Simple to deploy
- Simple to configure
- Reliable
- Lightweight
- Highly configurable
- Docker-native
- Usable on both Raspberry Pi and Mac
- Capable of monitoring many websites
- Accessible through a web dashboard
- Manageable through a CLI
- Designed to avoid false-positive alerts
- **Channel-agnostic** — notification transport is a plug-in, not a hard dependency

The application must not depend on a third-party monitoring service. It may, however, depend on third-party *notification* services the user already has (Telegram Bot API, an SMTP server, etc.).

---

# 2. Core Philosophy

## 2.1 Reliability Over Complexity

The monitoring engine is the most important part of the application.

A beautiful dashboard is useless if the monitoring engine misses outages or produces unreliable results.

The monitoring engine must therefore:

- Handle temporary failures gracefully
- Avoid duplicate alerts
- Persist important state
- Recover correctly after application restarts
- Handle network errors safely
- Handle timeouts
- Continue monitoring other websites if one monitor fails
- Never crash because of one invalid monitor configuration
- **Never lose an alert it committed to send** (outbox pattern, see §6.4)

## 2.2 Configuration Over Hardcoding

Monitoring behaviour should be configurable rather than hardcoded wherever practical.

Different monitors may have different:

- URLs
- Names
- Schedules
- Timeout values
- Health rules
- Retry behaviour
- Notification channel selection / overrides

Notification behaviour is configurable too:

- Which channels receive which event types
- Per-channel and per-event message templates

## 2.3 Personal Tool, Not SaaS

This application is designed for one person and a handful of notification destinations.

It is not initially intended to be:

- A multi-user SaaS
- A public monitoring service
- A commercial monitoring platform
- A team collaboration application

Avoid unnecessary enterprise functionality. **However**, the architecture must not make future expansion impossible — a single user today, but a channel registry, an event bus, and a typed core domain (not god-objects) so features like new check types, new channels, or config-from-dashboard can be added without a rewrite.

## 2.4 Changeability Over Lock-In

The cost of any integration point (notification transport, check type, storage) should be contained behind a narrow interface so the obvious future swaps stay cheap:

- **Telegram → email** (or webhook, ntfy, Pushover…) = add one provider class + config.
- **HTTP check → TCP/TLS/keyword/JSON-field check** = add one check strategy.
- **SQLite → Postgres** = swap the persistence adapter (not planned for v1; just don't smear SQL through the domain).

---

# 3. High-Level Architecture

Single-process design: one Python process runs the async monitoring loop, the web API, and the dashboard. No separate worker process, no message broker. Simple to run, easy to reason about, plenty for a personal tool. Components are separated as modules so the worker *could* be split out later without redesign.

```text
┌──────────────────────────────────────────────────────────────┐
│                        ONE PROCESS                           │
│                                                              │
│  ┌─────────────┐    ┌───────────────────┐   ┌─────────────┐  │
│  │  Scheduler  │───▶│  Check Strategies │   │  Web App    │  │
│  │  (asyncio   │    │   (http v1, …)    │   │  (FastAPI)  │  │
│  │   loop)     │    └─────────┬─────────┘   │  API + UI   │  │
│  └──────┬──────┘              │             └──────┬──────┘  │
│         │               probe result               │         │
│         ▼                     │                     │         │
│  ┌─────────────┐             ▼                     │         │
│  │  Per-monitor│    ┌───────────────────┐          │         │
│  │  State      │◀───│  State Machine    │          │         │
│  │  Machine    │    │  (false-positive  │          │         │
│  └──────┬──────┘    │   suppression)    │          │         │
│         │           └─────────┬─────────┘          │         │
│         │                domain events             │         │
│         ▼                     ▼                     │         │
│  ┌──────────────────────────────────────┐           │         │
│  │         Outbox / Event Log           │◀──────────┘  CLI    │
│  │   (persisted; survives restarts)     │         (typer,     │
│  └──────────────┬───────────────────────┘          same code) │
│                 ▼                                       │
│  ┌─────────────────────────────┐                        │
│  │   Notification Dispatcher   │                        │
│  └──────┬───────────────┬──────┘                        │
│         │               │                               │
│  ┌──────▼──────┐  ┌─────▼──────┐        ┌──────────┐    │
│  │ Telegram    │  │ SMTP Email │  …     │ Webhook  │    │
│  │ Provider    │  │ Provider   │        │ Provider │    │
│  └─────────────┘  └────────────┘        └──────────┘    │
│   (v1: enabled)    (v2: future,       (future)          │
│    requires no        proves the                        │
│    engine change)     interface)                        │
│                                                         │
│  ┌─────────────────────────────────────┐                │
│  │         Persistence (SQLite)        │                │
│  │  monitor_state · checks · outbox ·  │                │
│  │  events · channel_state             │                │
│  └─────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
                    Docker image (amd64 + arm64)
```

## 3.1 Component responsibilities

| Component | Responsibility | Key rule |
|---|---|---|
| **Scheduler** | Ticks each monitor on its interval, with jitter + concurrency limits | One monitor failing never blocks others |
| **Check Strategy** | Knows how to probe one kind of target | `HTTPCheck` v1; returns a typed `CheckResult` |
| **State Machine** | Decides UP / DEGRADED / DOWN from a series of results | Alerts only on confirmed transitions |
| **Outbox** | Persists every notification event before sending | "Committed to send" == "survives restart" |
| **Notification Dispatcher** | Routes events to configured channel providers, retries failures | Channel-agnostic; owns no transport logic |
| **Channel Provider** | Knows how to deliver one message type | Telegram (v1), SMTP/email (v2), webhook (future) |
| **Web App** | Read + light management of monitors; check history | No engine logic inside HTTP handlers |
| **CLI** | Operator actions from a terminal | Thin wrapper over the same service functions |
| **Persistence** | SQLite via a repository layer | Domain never touches SQL directly |

---

# 4. Key Design Decisions & Trade-offs

Locked unless marked **[open]** — see Decision Log §15.

| # | Decision | Rationale |
|---|---|---|
| D1 | Single process, no broker | Simple, robust, right size for personal use |
| D2 | Monitor definitions live in a **YAML config file**; runtime state in SQLite **[open — see §15.1]** | Git-versionable, reviewable, "configuration over hardcoding"; dashboard is read-mostly in v1 |
| D3 | Notifications flow through a persisted **outbox** | An alert is a durable fact, not a side effect. Restart/crash-safe delivery |
| D4 | Notification **channel** = pluggable provider behind one interface | Telegram↔email swap costs nothing architectural |
| D5 | Alert only on **confirmed** state transitions (threshold of consecutive failures) | Core false-positive defence |
| D6 | SQLite in WAL mode, single writer, short transactions | Concurrent CLI + web + loop reads, simple deployment |
| D7 | HTTP checks first; check type is an interface | v2 check types without touching the loop |
| D8 | Same code path for scheduler, CLI, and web (no duplicated logic) | CLI `check` and scheduled check must behave identically |

## 4.1 What is deliberately NOT built (v1)

- Multi-user auth, teams, RBAC
- Distributed scheduling, cluster mode
- Grafana/Prometheus metrics export
- Per-user dashboards
- Dashboard CRUD of monitors (config file is source of truth in v1)
- Flapping detection / adaptive thresholds (reserved for v2; basic cooldown covers v1)

---

# 5. Domain Model

Typed core, decoupled from transport, storage, and schedule.

```text
Monitor
  id: str            # stable slug, e.g. "my-blog" — survives config moves
  name: str
  check_type: "http"                 # strategy selector (§7)
  check_config: {url, method, headers, expected_status, ...}
  interval_s: int                    # probe cadence
  timeout_s: float
  retries: int                       # consecutive failures to confirm DOWN (D5)
  cooldown_s: int                    # min gap between DOWN and re-alert cycles
  channels: [str] | null             # override; null = global defaults
  enabled: bool

MonitorState            # persisted, keyed by monitor.id
  current_status: UP | DOWN | PAUSED | UNKNOWN
  consecutive_failures: int
  last_check_at: datetime | null
  last_result: CheckResult | null
  down_since: datetime | null
  last_alert_at: datetime | null     # re-alert cooldown bookkeeping

CheckResult            # one probe outcome (pure data)
  ok: bool
  status_code: int | null
  latency_ms: float | null
  error: str | null                  # timeout / dns / connect / tls ...
  checked_at: datetime

DomainEvent            # emitted by the state machine, consumed by dispatcher
  id: uuid
  type: SERVICE_DOWN | SERVICE_RECOVERED | CHECK_*   # (cooldown resets on recovered)
  monitor_id: str
  payload: dict   # context: name, url, latency, error, duration_down, resolved_at ...
  occurred_at: datetime

ChannelConfig          # from config/env, one entry per active channel
  name: str            # e.g. "telegram-home", "work-email"
  type: telegram | smtp_email
  settings: {...}      # provider-specific; secrets from env vars, never the file
  events: [SERVICE_DOWN, SERVICE_RECOVERED]   # which events this channel hears
```

## 5.1 Storage layout (SQLite, WAL)

| Table | Purpose | Notes |
|---|---|---|
| `monitor_state` | one row per monitor: current status, counters | survives restart; scheduler rehydrates from here |
| `checks` | rolling probe history | pruned by retention policy (default ~30 days) |
| `outbox` | notification events awaiting/in delivery | the durability guarantee (§6.4) |
| `deliveries` | per-channel delivery status for each outbox event | enables per-channel retry and "did email go out?" |
| `channel_state` | provider bookkeeping (e.g. Telegram chat reachable) | reserved |

Config file is **not** mirrored to the DB in v1 (D2). The scheduler reads config each cycle so a config edit applies without rebuild, and revalidates on change (reload = log + apply on next tick).

---

# 6. Notification System (pluggable channels)

This is the layer designed to absorb "switch from Telegram to email" and every later channel.

## 6.1 Core principle

The engine knows nothing about Telegram, SMTP, or webhooks. It emits **domain events** into the outbox. The dispatcher turns events into channel-agnostic **envelopes**, and providers handle transport.

```python
# contracts/notify.py — the only interface providers implement
@dataclass(frozen=True)
class Envelope:
    event: DomainEvent
    title: str
    body: str  # rich-text; provider decides how to render/truncate
    severity: str  # "down" | "recovered" | "info"
    occurred_at: datetime
    meta: dict  # extra fields; a provider may use or ignore


class ChannelProvider(Protocol):
    kind: str  # "telegram", "smtp_email", ...

    async def send(self, envelope: Envelope, config: ChannelConfig) -> None: ...


def build_envelope(event: DomainEvent, monitor: Monitor) -> Envelope: ...  # single template source
```

Templates are per **event type**, not per channel. Formatting differences (Telegram markdown vs plain-text email) are the provider's job at render time, driven by a per-channel `format` hint on the envelope.

## 6.2 Flow

1. State machine flips a monitor DOWN → writes `SERVICE_DOWN` event to `outbox` + `deliveries` rows for each channel configured to hear it.
2. Dispatcher polls/awaits pending outbox events.
3. `build_envelope` produces the canonical message.
4. For each channel row: dispatcher calls `provider.send(envelope, channel_config)`.
5. Success → mark delivered. Failure → keep pending, retry with backoff (max attempts then mark `failed` and log loudly — never silently drop).

## 6.3 Telegram provider (v1)

- Transport: plain `POST https://api.telegram.org/bot<TOKEN>/sendMessage` via `httpx`. No heavyweight SDK needed.
- Config via env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (allowed to be read into `settings`).
- Renders title/body with Telegram-style markdown (escaped); sends to one chat.
- Delivery failures are retried by the dispatcher, not thrown away.

## 6.4 Outbox guarantees (why alerts never "vanish")

- An alert event is **committed to the DB before any network call** (outbox + deliveries rows).
- After a crash/restart the dispatcher resumes pending events — nothing is lost because it was "in memory".
- Retry/backoff lives in the dispatcher. After `max_attempts` for a *channel*, the event is marked failed for that channel (other channels still deliver). Operator sees failed deliveries in CLI/dashboard.
- Anti-spam is handled **upstream** in the state machine (thresholds + cooldown), never by dropping events downstream.

## 6.5 Adding a channel later (the point of all this)

"Switch to email" = implement `SMTPEmailProvider` (kind `smtp_email`), register it in the provider registry, add an env-driven `smtp_email` channel to config, set `events`. Done — no changes to scheduler, state machine, outbox, dashboard, or CLI. To *swap* rather than add: remove the Telegram channel config (or leave both enabled).

---

# 7. Check Strategies (extensible probing)

`check_type` selects a strategy. Only HTTP ships in v1, but a second type must not require editing the scheduler.

```python
class CheckStrategy(Protocol):
    async def run(self, cfg: CheckConfig, timeout_s: float) -> CheckResult: ...
```

| Check type | v1? | Notes |
|---|---|---|
| `http` | ✅ | GET/HEAD, expected status (default 200), optional request headers, latency captured |
| `tcp` | later | connect to host:port |
| `tls` | later | cert validity / days-until-expiry warnings |
| `keyword` | later | body contains/absent match — builds on `http` fetch |

---

# 8. False-Positive & Reliability Engineering

The engine's job is trustworthy signal. Rules, in priority order:

1. **Confirm before alerting.** A monitor is DOWN only after `retries` *consecutive* failures (default 3, each retry on the monitor's own interval — i.e. alert after ~3 missed probes, configurable).
2. **Alert on transition, not on condition.** Emit `SERVICE_DOWN` exactly once when it *enters* DOWN. While already DOWN, later failures do **not** re-alert. Emit `SERVICE_RECOVERED` when it leaves DOWN.
3. **Cooldown on recovery.** After a DOWN→UP→DOWN cycle, suppress a new DOWN alert until `cooldown_s` has passed since the last alert — kills flap-noise from a restarting service.
4. **Degraded ≠ down.** A single blip or timeout never alerts. Timeouts count as failures but must reach the `retries` threshold.
5. **Startup honesty.** On boot, a monitor with no history starts UNKNOWN and does its first full probe before deciding anything; no alert storms on restart. Persisted state (§5.1) means a monitor that was DOWN at shutdown resumes DOWN **without** re-alerting.
6. **Isolation.** A malformed monitor config is rejected at load, logged, and skipped — the loop keeps going.
7. **Notification independence.** A failing channel never mutates monitor state and never prevents other channels from delivering.
8. **Time.** All decisions/comparisons use a single clock source (injected), timezone-aware, so tests can fast-forward.

---

# 9. Configuration

Layered: low-level defaults → `config/monitors.yaml` → environment (secrets + global knobs).

- `config/monitors.yaml` — monitor definitions, channel list, event→channel routing, template overrides. Commented example ships with the repo.
- `.env` / environment — secrets and transport settings: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, future SMTP creds, `TZ`, retention days, log level.
- One top-level `check` command the user can run to validate config before boot: prints parsed monitors and per-monitor issues.

Monitor example:

```yaml
monitors:
  - id: my-blog
    name: My Blog
    check_type: http
    check_config:
      url: https://blog.example.com
      expected_status: 200
    interval_s: 60
    timeout_s: 10
    retries: 3
    cooldown_s: 300
channels:
  - name: telegram-home
    type: telegram
    events: [service_down, service_recovered]
    # token/chat id from TELEGRAM_* env vars
```

---

# 10. Web Dashboard

- FastAPI serves a small JSON API + one HTML page. No build step: Jinja2 + a vanilla/HTMX sprinkle + one CSS file. Lightweight and RPi-friendly.
- Views:
  - **Overview** — all monitors, status badge, latency, last check, mini history sparkline; auto-refreshes every N s.
  - **Monitor detail** — check history table, current state, config summary, manual **Check now** and **Pause/Resume**.
  - **Deliveries** — recent outbox events and per-channel delivery status (surface "alert failed to send").
- Read-mostly in v1 (config file is source of truth); an **edit link is a link to the config file path** rather than a half-baked CRUD.
- Listens on the container port; simple token gate via env if exposed beyond localhost.

---

# 11. CLI

A Typer CLI (`wm …`), running in the container or via `docker compose exec`, sharing the service layer so behaviour matches the scheduler exactly.

```
wm status                  # table: monitor, status, since, latency, last check
wm check <id>              # run one probe now (same code path as scheduler)
wm pause <id> | wm resume <id>
wm history <id>            # recent checks
wm deliveries              # pending/failed notifications
wm send-test [<channel>]   # push a test message through a channel
wm config-check            # validate monitors.yaml and print parsed model
```

---

# 12. Deployment (Docker)

## 12.1 Docker First

Designed to run in Docker from day one:

```text
docker compose up -d
```

## 12.2 Image

- Multi-stage `Dockerfile`: build → slim runtime (`python:3.12-slim`).
- Multi-arch via `docker buildx` for `linux/amd64` (Mac) and `linux/arm64` (Raspberry Pi). CI or a `build.sh` produces/pushes both.
- Non-root user, `--stop-grace-period` aligned with a clean scheduler shutdown (drain in-flight probes, flush state).

## 12.3 Compose

```yaml
services:
  monitor:
    image: web-monitor:latest
    env_file: .env
    ports: ["8080:8080"]
    volumes:
      - ./config:/app/config:ro        # monitors.yaml
      - ./data:/app/data               # SQLite (WAL), survives restarts
    restart: unless-stopped
```

## 12.4 Ops properties

- Container restarts: state machine rehydrates from SQLite; pending outbox events resume (§6.4).
- Config reload: scheduler re-reads `monitors.yaml` each tick (no rebuild required); invalid edits leave the last good config active and log the error.
- Graceful `SIGTERM`: loop finishes current probes, flushes, exits cleanly.

---

# 13. Tech Stack (recommended — confirm before Phase A)

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | fast to iterate, async-native, RPi-friendly |
| Scheduling | asyncio loop + per-monitor timers (custom, ~150 LOC) | no apscheduler magic; full control of retry/jitter/backoff; or apscheduler if loop gets complex |
| Probing | `httpx.AsyncClient` | timeouts, redirects, TLS control |
| Web | FastAPI + Uvicorn | typed API, OpenAPI, runs in same process |
| Frontend | Jinja2 + HTMX + one CSS file | no node build step, tiny image |
| CLI | Typer | thin, typed, shares service layer |
| Persistence | SQLite (WAL) via a repository layer | zero-ops, durable, single file |
| Validation | Pydantic v2 + pydantic-settings | config + env typed at the boundary |
| Testing | pytest, pytest-asyncio, `respx` (mock httpx), Freezegun/clock injection | fast, no network |
| Tooling | `uv`, `ruff`, `mypy --strict` | modern, strict gates |

---

# 14. Testing Strategy

1. **State machine** — table-driven tests over result sequences: blips don't alert, threshold hits alert exactly once, recovery clears, cooldown suppresses flaps, restart while DOWN doesn't re-alert. This is the highest-value test suite in the repo.
2. **Outbox/dispatcher** — event survives simulated crash (write → new dispatcher instance → delivers), retries with backoff, per-channel failure isolation.
3. **Providers** — `respx`-mocked httpx: correct endpoint, payload, markdown escaping; SMTP provider (when built) mocked the same way.
4. **Scheduler** — fake clock, N monitors, asserts per-monitor cadence, isolation when one monitor throws.
5. **API/UI** — `httpx.AsyncClient`/`ASGITransport` against the app with SQLite in-memory/tmp; a Playwright smoke for the overview page rendering at mobile + desktop width.
6. **CLI** — invoke Typer runner against the same service layer.
7. **Gate** — `ruff`, `mypy --strict`, and `pytest` must all pass before a milestone is "done".

---

# 15. Milestones & Build Order

Vibe-coded reality: an AI agent does **not** reliably build this in one pass. Every milestone is sized so a single pass has a small surface and ends green (lint + typecheck + tests). Do not skip the gate of a milestone; later milestones assume the earlier one is solid.

> Execution rule for AI passes: one milestone (or one task inside a big one) per pass; never "and also refactor X / add Y" in the same pass. Lock decisions in §15 log before the milestone that depends on them. Run the full gate each time.

**Phase A — Foundations (decision lock + skeleton)**
- Lock §13 stack + §15.1 decision. Scaffold: `uv` project, `pyproject.toml`, `app/` layout, pydantic-settings with env, ruff/mypy/pytest config, CI-able `make`/script gates, multi-arch-ready `Dockerfile` + `docker-compose.yml`, empty FastAPI app with `/healthz`.
- Gate: `uv sync`, ruff/mypy/pytest green, `docker compose up` serves `/healthz`.

**Phase B — Domain core (no I/O)**
- Models + Pydantic schemas (Monitor, MonitorState, CheckResult, DomainEvent, ChannelConfig), monitor config loader/validator (`wm config-check`), state machine with full unit coverage, single clock source.
- Gate: state machine suite green; no network or DB in this phase.

**Phase C — Probing + scheduler**
- `HTTPCheck` strategy, probe runner, asyncio scheduler with jitter/concurrency/graceful shutdown, isolation on bad monitors, `wm check <id>`.
- Gate: mocked-network tests; scheduler cadence + isolation tests green.

**Phase D — Persistence + outbox**
- SQLite schema + repository layer, WAL, retention pruning, state rehydration on boot, outbox + deliveries writes, crash-resume dispatcher.
- Gate: restart/delivery tests green (fake clock + tmp DB).

**Phase E — Telegram channel end-to-end**
- `Envelope` building, provider registry, `TelegramProvider`, dispatcher retry/backoff, `wm send-test telegram`. Proof the §6 design holds.
- Gate: `respx`-mocked provider tests; manual `wm send-test` delivers a real message once.

**Phase F — Dashboard**
- FastAPI routes (status/history/deliveries), Jinja overview page with status + sparkline + auto-refresh, monitor detail with check-now/pause, token gate, `wm deliveries`.
- Gate: API tests + one Playwright smoke at 390px and 1440px.

**Phase G — Hardening + real-world soak**
- Structured logging, TZ handling, config hot-reload validation, non-root image, compose polish, README quickstart.
- Gate: full suite; then **soak**: run Compose against a fake target, kill/restart the service mid-outage, verify exactly one DOWN alert, one RECOVERED alert, nothing lost; observe for 24h+ for false positives.

**Phase H — Channel swap proof (optional, strongly recommended)**
- Add `SMTPEmailProvider` behind the same interface to *prove* §6.5 (email alerts while Telegram stays, or fully swap). No engine changes expected — if engine changes are needed, that's a design bug to fix before v2 features.

---

# 16. Decision Log

Every open/notable decision the builder hits goes here **before** the code that depends on it. Keep it terse.

| Status | Decision | Context |
|---|---|---|
| 🔒 Locked | Single process, outbox notifications, pluggable channel providers, YAML monitor config v1, SQLite WAL, confirm-before-alert thresholds | §4 |
| 🔒 Locked | Defaults: retries=3, cooldown=300s, retention=30d, interval=60s | sensible single-user defaults, all configurable |
| 🔒 Locked (Phase A) | **YAML config file** is the monitor source of truth; DB-backed monitors revisited only on a multi-user pivot (AI-editable + reviewable diffs won the comparison) | D2 |
| 🔒 Locked (Phase A) | **Custom asyncio scheduler** (per-monitor timers, injected clock) — not apscheduler | §13 |
| 🔒 Locked (Phase A) | Repo workflow: `main` (stable) + `develop` (integration) + `feature/*` branches via PR; merge `develop`→`main` + tag at each green milestone | §15 |
| ❓ Open (Phase H) | Which second channel to build as the swap-proof (email is the named candidate) | §6.5 |

---

# 17. Non-Goals Tracker (kept honest)

Delete items only when a real need appears — not because a feature is "easy".

- ~~(reserved)~~ Multi-user auth — no.
- Dashboard CRUD of monitors — v1 = config file.
- Flapping detection / adaptive thresholds — v2 (cooldown covers v1).
- Metrics export — no.
- Third-party monitoring dependency — never.
