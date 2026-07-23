# BambuBabu technical reference

Last reconciled with the code and prototype evidence: 2026-07-24. The resumable hardware checkpoint is [docs/prototype_status.md](docs/prototype_status.md).

This document describes the implementation that exists in this repository. It separates enforced invariants from future work and from behavior that still needs physical hardware validation.

## 1. System boundary

BambuBabu is a single-process FastAPI application with background threads for queue polling, slicing, printer handoff, MQTT callbacks, and maintenance. It serves a static dashboard and JSON API from the same origin.

```text
browser or parent app
        |
        | HTTP on loopback only while auth is pending
        v
FastAPI + SQLite/WAL
  |       |        |
  |       |        +-- maintenance: retention + online backups
  |       +----------- queue: CAS transitions + reconciliation
  +------------------- dashboard and structured logs
                 |
                 +-- OrcaSlicer AppRun via xvfb-run
                 +-- FTPS upload with public-key pin
                 +-- MQTT QoS 1 with trusted device certificate
                                  |
                            P1S / A1 Mini
```

Authentication is not implemented in this service by explicit project decision. `AUTHENTICATION_MODE=external-pending` forces a loopback bind and wildcard CORS is rejected. The future authenticated parent should proxy to loopback and impose member ownership/admin policy before enabling remote access.

## 2. Components

| Component | Responsibility |
|---|---|
| `backend/main.py` | lifecycle, health, routers, static frontend |
| `backend/config.py` | typed settings, secret handling, unsafe-start rejection |
| `backend/api/jobs.py` | streaming admission, listing, cancellation, job logs |
| `backend/api/printers.py` | live/durable status, guarded idle acknowledgement, plate clearance, history |
| `backend/api/logs.py` | structured log array used by the dashboard |
| `backend/core/complexity.py` | mesh metrics, build-volume checks, preferred printer |
| `backend/core/slicer.py` | portable Orca preset resolution and headless slicing |
| `backend/core/printer.py` | pinned TLS, FTPS transfer, MQTT publish/start proof |
| `backend/core/printer_manager.py` | MQTT report reconciliation and physical state |
| `backend/core/queue_processor.py` | background lifecycle orchestration and fallback |
| `backend/core/maintenance.py` | retention, orphan cleanup, backup schedule |
| `backend/db/crud.py` | compare-and-swap transitions and database operations |
| `backend/db/session.py` | WAL configuration, sessions, seed state, SQLite backup |

## 3. Startup invariants

`Settings.validate_runtime()` runs before printers or workers start. Startup fails when:

- pending-auth mode binds anywhere except `127.0.0.1`, `localhost`, or `::1`;
- CORS contains `*`;
- live printers and mock slicing are enabled together;
- a printer IP, serial, access code, MQTT certificate path, or FTPS pin is missing/placeholder;
- a printer address is not a private/link-local literal IP;
- a trusted MQTT certificate file is absent;
- an FTPS pin is not a `sha256//` SPKI hash;
- real slicing is enabled but the Orca executable, complete BBL profile tree, or `xvfb-run` is missing.

Numeric configuration is constrained by Pydantic: ports are valid, limits and intervals are positive, complexity is 0-100, and concurrent handoffs cannot exceed the two supported printers.

The supported interpreter is CPython 3.12. `.python-version` declares that development constraint, `scripts/bootstrap_dev.sh` creates or validates a 3.12 `.venv`, and the Pi installer rejects an OS, architecture, or Python minor version outside Ubuntu 24.04 ARM64 with Python 3.12. This prevents dependency installation from silently falling back to unsupported native builds.

## 4. Job state model

### States

| State | Meaning |
|---|---|
| `pending` | upload committed; waiting for analysis |
| `analysing` | trimesh metrics are being computed |
| `slicing` | OrcaSlicer or fallback re-slicing is in progress |
| `queued` | a printer-specific printable `.gcode.3mf` is ready |
| `uploading` | FTPS handoff owns the printer slot |
| `starting` | MQTT command was acknowledged; physical start is unproven |
| `printing` | a newer printer report confirmed `PREPARE`/`RUNNING` |
| `attention` | physical outcome is ambiguous; dispatch is blocked |
| `completed` | `FINISH` observed; plate still requires clearance |
| `failed` | deterministic processing/printer failure or admin resolution |
| `rejected` | the analysed object cannot fit any printer |
| `cancelled` | safe pre-print cancellation won the race |

### Transaction semantics

All lifecycle changes are checked against a central allowed-transition graph, then use one SQL `UPDATE ... WHERE id=? AND status IN (...)`. An illegal edge or row count other than one raises `JobTransitionError`. This compare-and-swap rule prevents a cancelled/terminal job from being revived by an older worker and prevents two dispatchers from owning the same job.

After a bulk transition, the ORM identity-map object is refreshed before callers continue. Timestamps are attached to the transition that owns them. Printer state, the transition, and its structured event log are committed in the same transaction wherever they describe one logical decision.

Cancellation is accepted only in `pending`, `analysing`, `slicing`, or `queued`. Files are not immediately deleted because a worker may still hold an open path. Retention handles them later, and unreferenced worker output is removed by orphan cleanup.

## 5. Restart reconciliation

The queue reconciles durable state before its polling loop starts:

| Persisted state | Restart action |
|---|---|
| `analysing`, `slicing` | reset to `pending`; safe CPU work retries |
| `uploading`, `starting` | move to `attention`; retain job/printer ownership and block plate |
| `printing` | preserve `printing`; restore printer ownership and blocked plate |
| `completed` below 100% | preserve terminal state and reconcile progress to 100% |
| other terminal/queued/pending | leave unchanged |

Upload/start operations are never automatically replayed after a restart because the physical printer may already have received the file or command. This is the core fail-closed rule.

An MQTT `RUNNING` report can recover a matching `starting` or `attention` job to `printing`. `FINISH` completes it. `FAILED` fails it. `IDLE` while the durable job is `printing`, without a preceding `FINISH`, moves the job to `attention` and keeps the printer blocked.

## 6. Upload admission and validation

Uploads are read in 1 MiB chunks to a UUID-named `.part` file. The service never trusts the client filename as a storage path. It enforces:

- `.stl` suffix;
- valid email syntax;
- name, description, and filename length limits;
- control-character removal before logs/email/UI;
- `MAX_STL_SIZE_MB` during streaming, not after buffering;
- `MAX_ACTIVE_JOBS` before admission;
- combined upload/sliced `MAX_STORAGE_MB` before and after streaming;
- exact binary STL length from the triangle count, or an ASCII `solid`/`endsolid` envelope.

The partial file is fsynced, validated, and atomically renamed before the job transaction commits. Any exception removes both partial and final paths. Trimesh performs full mesh parsing during analysis; malformed geometry then becomes a deterministic failed job.

The job API intentionally omits `user_email` from public responses. Email remains in SQLite for lifecycle notification and must become ownership-protected data in the final auth phase.

## 7. Analysis and routing

Trimesh produces face count, absolute volume, axis-aligned bounding-box extents, and face-normal overhang ratio. The score is:

```text
0.40 * min(face_count / 500000, 1) * 100
+ 0.40 * overhang_ratio * 100
+ 0.20 * min(volume_cm3 / 300, 1) * 100
```

Objects outside 256 x 256 x 256 mm are rejected. Objects outside the A1 Mini's 180 x 180 x 180 mm volume are forced to P1S. Otherwise a score above `COMPLEXITY_THRESHOLD` selects P1S and a score at or below it selects A1 Mini.

Queue order within a printer is shortest estimated time first, then submission time. See `docs/printer_selection_algorithm.md` for routing and fallback details.

## 8. Portable slicing

After verifying Ubuntu 24.04 ARM64 and Python 3.12, the production installer downloads the pinned OrcaSlicer 2.4.2 Ubuntu 24.04 ARM64 AppImage, verifies its hard-coded SHA-256, and extracts it under `/opt/bambubabu/orca/2.4.2`. Runtime executes the extracted `AppRun`, avoiding a FUSE mount.

`SLICER_PROFILES_DIR` points at the complete extracted `resources/profiles/BBL` tree. Bambu machine, process, and filament presets inherit from other JSON files, so copying three isolated files is insufficient. The selected presets are:

| Printer | Machine | Process | Filament |
|---|---|---|---|
| P1S | `Bambu Lab P1S 0.4 nozzle.json` | `0.20mm Standard @BBL X1C.json` | `Bambu PLA Basic @base.json` |
| A1 Mini | `Bambu Lab A1 mini 0.4 nozzle.json` | `0.20mm Standard @BBL A1M.json` | `Bambu PLA Basic @BBL A1M.json` |

Orca runs through `xvfb-run`, with an argument array, `shell=False`, captured output, and a ten-minute timeout. Ubuntu also needs `xauth`, `libopengl0`, and `libglu1-mesa`; startup and health validate these dependencies. The systemd unit creates a private `/run/bambubabu` directory and supplies it as `XDG_RUNTIME_DIR`. Each Orca invocation receives its own temporary writable directory below `logs/orca`: Orca writes numbered diagnostics and lock-like state relative to its working directory, so sharing one directory can cause filesystem conflicts. This preserves the read-only checkout and isolates retries/fallback profiles. The P1S uses Orca's declared default `0.20mm Standard @BBL X1C` process; the P1P process explicitly rejects the P1S with CLI return `-17`. Output is named `<job-uuid>-<printer>.gcode.3mf`, then inspected as a ZIP archive for `Metadata/plate_1.gcode` before it is eligible for upload. This prevents a generic/non-printable 3MF from reaching the printer and prevents a fallback slice from being mistaken for the preferred-printer slice. Missing/incompatible profiles, runtime dependencies, malformed output, or an absent plate G-code entry fail closed.

`MOCK_SLICER=true` only copies the STL and is for orchestration tests. Startup forbids mock slicing when live printer integration is enabled.

## 9. Printer transport

### MQTT

Paho MQTT connects to port 8883 using TLS 1.2+ behavior from Python's default context. The context trusts the explicitly captured device certificate, requires certificate validation, and disables hostname comparison because the LAN device certificate is not issued for its local IP. The access code is the MQTT password and is held as a `SecretStr` in configuration.

Reconnect attempts stop and replace the previous Paho client before creating another. Shutdown requests broker disconnect before stopping Paho's network loop, preventing an open MQTT socket from consuming the systemd stop timeout. Connect/disconnect callbacks are ignored unless they belong to the current client, so a delayed callback from an old socket cannot mark a newer connection offline.

The non-mutating `pushall` status refresh publishes at QoS 0 because Bambu LAN brokers commonly do not acknowledge its QoS 1 form. Physical start commands publish to `device/<serial>/request` with QoS 1. A start publish fails unless the client is connected, the broker accepts it, and `wait_for_publish()` confirms it within `MQTT_PUBLISH_TIMEOUT_SECONDS`.

Before a start command, the latest live state must be `idle`. The client records the MQTT report version, publishes `project_file`, then waits for a strictly newer report showing `PREPARE`, `RUNNING`, or mapped `printing`. `FAILED` raises a rejected-start error. A missing QoS 1 acknowledgement, a report timeout, or an interrupted handoff raises an unconfirmed-start error because the printer may already be acting on the command. The queue maps every ambiguous start error to `attention`, never `printing` or ordinary failure.

Some firmware retains the last `FAILED` value after the printer has physically returned to an idle screen. BambuBabu never converts that state automatically. A guarded operator acknowledgement is available only for a connected, jobless printer with a confirmed-clear plate. The acknowledgement suppresses the stale value, but a new start attempt immediately restores strict `FAILED` handling.

`FINISH` is an authoritative completion event and sets the job progress to 100%. Restart reconciliation also repairs older completed rows left at the firmware's common pre-finish value of 99%. The live printer becomes `idle`, while durable `current_job_id` and `plate_cleared=false` continue blocking dispatch until a human confirms physical removal. Clearing the plate releases that durable ownership; a retained firmware `FINISH` value cannot block the next start.

### FTPS

Sliced files upload to implicit FTPS port 990 with curl. The access code is passed through curl's stdin configuration, not process arguments. The remote filename is URL-quoted, argv is separated, and shell interpretation is disabled.

Bambu LAN certificates are self-signed, so curl's normal CA/hostname validation cannot establish identity. The connection uses `--insecure` only together with the required `--pinnedpubkey sha256//...` SPKI pin. The pin authenticates the public key before credentials or file content are accepted. Transfer uses timeouts and up to three attempts.

Identity capture is trust-on-first-use. `scripts/capture_printer_identity.sh` must be run on a controlled LAN immediately after credential rotation. It stores the complete MQTT certificate chain (device certificate plus its self-signed BBL CA) so Python can validate the presented chain. A changed certificate or public key later causes a hard connection/transfer failure and requires physical re-verification.

## 10. Printer slot and plate invariants

A job can dispatch only when all are true:

- durable printer status is `idle`;
- `plate_cleared=true`;
- `current_job_id` is empty;
- the next job is atomically changed from `queued` to `uploading`.

The printer slot is claimed in the same transaction as `uploading`. On deterministic failure before any ambiguous start, the job fails and the slot can be released. Once a start may have reached the printer, uncertainty blocks the slot in `attention`.

`FINISH` and `FAILED` retain `current_job_id` and set `plate_cleared=false`. The plate-clear endpoint rejects active states. For an `attention` job, successful physical inspection/clearance resolves the job to `failed`, records `plate_cleared_at`, and releases the slot. There is no automatic assumption that an idle-looking plate is physically empty.

## 11. Cross-printer fallback

Fallback is considered only when the target printer is idle, cleared, and unowned. A queued job assigned to the other printer is eligible only if:

- its preferred/source printer is not currently idle, cleared, and unowned;
- its measured bounding box fits the target;
- another slicing worker has not reserved the job.

The job is atomically reserved as `slicing`, re-sliced from the original STL with the target profile, then reassigned and returned to `queued`. A fallback slicing failure returns it to the original preferred queue and logs a warning, but suppresses further fallback retries for that job; it can still dispatch to its original printer using its already-valid 3MF. This prevents repeated slicer attempts while the preferred printer is busy. A preferred printer that is available always keeps its own work.

## 12. Database and durability

SQLite connections enable:

```text
foreign_keys=ON
busy_timeout=30000
journal_mode=WAL
synchronous=NORMAL
```

The single service process uses thread-safe sessions with `expire_on_commit=False`; each worker/callback opens its own session. Database and backup files are chmod `0600`, and the systemd unit uses `UMask=0077`.

The maintenance worker:

- creates a consistent online backup via SQLite's backup API at startup and every configured interval;
- names backups with UTC microseconds and keeps the newest `DB_BACKUP_KEEP` copies;
- removes STL/3MF artifacts for terminal jobs after `TERMINAL_FILE_RETENTION_DAYS`, except files associated with a still-blocked printer;
- removes stale `.part` uploads;
- removes old unreferenced STL/3MF crash or cancellation leftovers after `ORPHAN_FILE_RETENTION_HOURS`.

Database job rows and structured `LogEntry` rows are not removed by file retention.

## 13. API contracts

All log endpoints return arrays of structured objects. The dashboard consumes `/api/logs/all?limit=80`; job detail events use `/api/jobs/{id}/logs`. The previous file-line/JSON mismatch no longer exists.

Error codes of operational importance:

| Code | Meaning |
|---|---|
| `400` | invalid suffix or STL structure, invalid printer ID |
| `409` | cancellation, plate-clear, or guarded idle-acknowledgement conflict |
| `413` | streamed file exceeds byte limit |
| `422` | form/email validation failure |
| `429` | active queue is full |
| `507` | total model storage quota exhausted |

The future authenticated host must apply these capability boundaries:

| Surface | Member | Admin |
|---|---|---|
| submit job | yes | yes |
| view/cancel own pre-print job | yes | yes |
| view all jobs/emails/logs/stats | no | yes |
| view printer availability | read-only | yes |
| clear plate/resolve attention | no | yes |
| change limits, retention, printer identity | no | yes |

No row-level ownership or role enforcement exists yet. This table is an integration contract, not a current security claim.

## 14. Service hardening

The generated systemd unit runs as the invoking non-root user and sets:

- read-only application/home view and strict system protection;
- a single writable runtime tree at `/var/lib/bambubabu`;
- `NoNewPrivileges`, empty capability bounding set, private `/tmp`, restrictive umask;
- protected kernel tunables/modules/control groups/hostname;
- restricted address families (`AF_UNIX`, `AF_INET`, `AF_INET6`);
- automatic restart on failure.

The dashboard has no remote font/runtime dependencies. Production Python dependencies are fully resolved and hash-locked in `requirements.lock`.

## 15. Health and observability

`GET /api/health` reports database readiness, slicer readiness, curl availability when printers are enabled, and aggregate MQTT connection state. It also reports the explicit auth mode. `ok` means every required dependency is currently ready; otherwise it returns `degraded` in the body.

Application logs rotate at 10 MiB with five backups. Durable lifecycle events are stored in SQLite and shown by the dashboard. User-controlled text is normalized before logging and HTML-escaped in the frontend and email templates.

## 16. Verification

The automated suite currently covers:

- streaming upload, invalid STL/email, byte/active quotas, and response privacy;
- atomic cancellation and stale-worker rejection;
- structured logs API/UI contract;
- plate clearance and active-state refusal;
- loopback, CORS, live/mock separation, and printer identity configuration;
- disconnected MQTT, authoritative start success, timeout, unacknowledged-but-possibly-delivered start, and failure;
- guarded stale-`FAILED` acknowledgement and partial MQTT report handling;
- printer handoff failures and `attention` quarantine;
- restart reconciliation, completed-progress repair, and compare-and-swap identity refresh;
- compatible P1S/A1 profiles, isolated Orca workspaces, printable archive validation, fallback suppression, cross-printer re-slicing, and no-steal behavior;
- `FINISH`, missing-`FINISH`, and physical plate blocking;
- WAL, consistent backup permissions, terminal retention, and orphan cleanup.

The verification commands are:

```bash
.venv/bin/python -m pytest -o addopts='' -W error
.venv/bin/python -m ruff check backend tests
uvx pip-audit -r requirements.lock
uvx bandit -q -r backend -x tests
scripts/check_secrets.sh
```

At reconciliation time, 49 tests, Ruff, dependency audit, Bandit, shell syntax, Python 3.12 verification, and the tracked/untracked secret-pattern check all pass.

Hardware evidence proves the Ubuntu 24.04 ARM64 Pi installation, pinned identities, real Orca output for both printer profiles, dashboard/log compatibility, and one complete A1 Mini upload/start/print/`FINISH`/plate-clear cycle. It does not yet prove a physical P1S print, physical fallback, restart/failure injection, production-filesystem quota/restore drills, long-running load, authentication, or network exposure. See the prototype checkpoint for the ordered remaining work.
