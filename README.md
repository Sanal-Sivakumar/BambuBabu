# BambuBabu

BambuBabu is a local-first print queue for a Bambu Lab P1S and A1 Mini. It accepts STL uploads, validates and analyses each mesh, selects a printer, slices with a pinned OrcaSlicer installation, transfers the result over FTPS, and starts printing only after an authoritative MQTT state report.

This repository contains a hardened local automation core. Authentication is intentionally deferred to the parent project that will host it. Until that integration exists, the service is forced to loopback and must be reached locally or through an SSH tunnel. It is not safe or supported to expose the current API directly to a LAN or the internet.

## Current status

Implemented and covered by automated tests:

- no committed printer IPs, serials, access codes, certificates, or live public-key pins;
- startup refusal for placeholder credentials, wildcard CORS, untrusted printer TLS identities, mock slicing with live printers, or unauthenticated network binding;
- chunked uploads with byte, queue, and total-storage limits plus structural STL validation;
- atomic compare-and-swap job transitions and cancellation-race protection;
- restart reconciliation for interrupted analysis, slicing, upload, start, and printing states;
- fail-closed MQTT publish and start confirmation before a job becomes `printing`;
- physical plate-clear tracking, ambiguous-state quarantine, and safe cross-printer re-slicing;
- structured database logs consumed directly by the dashboard;
- SQLite WAL mode, busy timeout, consistent online backups, retention, and orphan cleanup;
- a hash-locked Python environment and checksum-pinned OrcaSlicer installer for Ubuntu 24.04 ARM64;
- 38 automated API, lifecycle, routing, printer-failure, and storage tests.

Still requiring deployment work:

- rotate both real printer LAN access codes from their screens;
- capture each device's MQTT certificate and FTPS public-key pin on a trusted LAN;
- run the installer and a real slice/print/stop/restart exercise on the target Pi and printers;
- add member/admin authentication and ownership rules in the parent application as the final phase.

Automated tests use fake printers and mock slicing. They verify orchestration logic, not real printer firmware or physical safety.

## Security boundary

With `AUTHENTICATION_MODE=external-pending`, BambuBabu accepts only a loopback bind such as `127.0.0.1`. The default is same-origin only and wildcard CORS is rejected. Printer access codes are Pydantic secrets, never included in command arguments, and are not logged. MQTT trusts an explicitly captured printer certificate; FTPS uses a SHA-256 public-key pin even though the device certificate is self-signed.

The current API does not distinguish members from administrators. In the future host application:

- members should be allowed to submit jobs and view/cancel only their own safe pre-print jobs;
- administrators should own printer state, plate clearance, attention resolution, all-job views, logs, stats, quotas, and retention settings.

Do not work around the loopback check. Use an SSH tunnel during this phase:

```bash
ssh -L 8000:127.0.0.1:8000 <pi-user>@<pi-address>
```

Then open `http://127.0.0.1:8000` on the client machine.

## Supported deployment

- Raspberry Pi 5 or comparable ARM64 machine
- Ubuntu 24.04 ARM64
- Python 3.12
- Bambu Lab P1S and A1 Mini in LAN mode
- OrcaSlicer 2.4.2 ARM64, downloaded and verified by the installer

The installer deliberately targets the official Ubuntu 24.04 ARM64 OrcaSlicer artifact. Raspberry Pi OS is not claimed as a verified target for that binary.
It checks the OS, architecture, and Python minor version before downloading OrcaSlicer and refuses untested combinations.

## Fresh-Pi installation

1. Clone this repository onto the Pi.

2. On each printer, enable LAN mode and rotate its access code. Never reuse a value that appeared in source control, documentation, chat, screenshots, or logs.

3. Run the installer from the checkout:

```bash
sudo -v
./scripts/install_pi.sh
```

The installer:

- installs OS prerequisites;
- downloads OrcaSlicer 2.4.2 ARM64 and verifies its pinned SHA-256;
- extracts the full Orca profile inheritance tree;
- creates a clean virtual environment from `requirements.lock` with hash checking;
- creates `/var/lib/bambubabu` with restrictive permissions;
- installs a hardened `bambubabu.service` unit with a private systemd runtime directory for headless OrcaSlicer, without starting it;
- copies `.env.example` to `.env` with mode `0600` only when `.env` does not exist.

The headless slicer prerequisites include `xvfb`, its `xauth` helper, and the `libopengl0`/`libglu1-mesa` runtime libraries; the health endpoint does not report slicing ready unless they are installed.

4. On a trusted, isolated LAN, capture each printer identity immediately after rotating its access code:

```bash
./scripts/capture_printer_identity.sh <p1s-ip> /var/lib/bambubabu/certs/p1s-mqtt.pem
./scripts/capture_printer_identity.sh <a1-mini-ip> /var/lib/bambubabu/certs/a1-mini-mqtt.pem
```

Each command prints an FTPS value beginning with `sha256//` and saves the complete MQTT certificate chain. Put the certificate path and printed pin into `.env`. This is trust-on-first-use: perform it only while you control the local network.

5. Edit `.env` and replace every `replace_me` value:

```bash
chmod 600 .env
nano .env
```

Keep `HOST=127.0.0.1`, `AUTHENTICATION_MODE=external-pending`, and `MOCK_SLICER=false` for live operation.

6. Validate that no credential-shaped value is tracked, then start the service:

```bash
./scripts/check_secrets.sh
sudo systemctl enable --now bambubabu
sudo systemctl status bambubabu
curl http://127.0.0.1:8000/api/health
```

The health response is `degraded` until the database, slicer, curl, and both live MQTT connections are ready.

## Development without printers

Create the exact Python 3.12 development environment and install the development set:

```bash
./scripts/bootstrap_dev.sh
.venv/bin/python -m pytest -o addopts='' -W error
.venv/bin/python -m ruff check backend tests
```

If an earlier command created `.venv` with Python 3.13 or 3.14, replace only that disposable environment:

```bash
./scripts/bootstrap_dev.sh --recreate
```

The bootstrap uses `uv` when available (including a managed 3.12 runtime), otherwise it uses an installed `python3.12`. `.python-version` also pins compatible tools to 3.12. Do not force PyO3 forward compatibility: this release and its lock file are validated on 3.12.

For a local mock server, create a development `.env` with `PRINTERS_ENABLED=false`, `MOCK_SLICER=true`, loopback binding, and writable paths owned by your account. Mock slicing copies the STL to a fake `.3mf` and must never be paired with live printers; startup rejects that combination.

## Job lifecycle

The normal path is:

```text
pending -> analysing -> slicing -> queued -> uploading -> starting -> printing
        -> rejected                                      -> completed
        -> cancelled                                     -> failed
                                                        -> attention
```

`starting` means the QoS 1 command was acknowledged but the physical start has not yet been proven. Only a newer printer report showing `PREPARE` or `RUNNING` permits `printing`. Timeout, interrupted handoff, or an unexplained return to `IDLE` blocks dispatch in `attention` until an administrator physically inspects and clears the printer.

Completed and failed jobs retain the printer's `current_job_id` and keep `plate_cleared=false`. The next job cannot start until the plate-clear control succeeds.

## Routing

Objects larger than 256 x 256 x 256 mm are rejected. Objects that do not fit the A1 Mini's 180 x 180 x 180 mm volume are assigned to the P1S. Remaining objects go to the P1S when their complexity score exceeds `COMPLEXITY_THRESHOLD`; otherwise the A1 Mini is preferred.

If the preferred printer is unavailable and the other printer is idle, cleared, and large enough, BambuBabu re-slices the original STL for the other printer before dispatch. It never sends a file sliced for one printer to the other. See [docs/printer_selection_algorithm.md](docs/printer_selection_algorithm.md).

## Data and backups

Production runtime data is outside the read-only checkout:

```text
/var/lib/bambubabu/
  bambubabu.db
  backups/
  certs/
  logs/
  sliced/
  uploads/
```

SQLite runs in WAL mode with foreign keys, a 30-second busy timeout, and `synchronous=NORMAL`. The maintenance worker creates a consistent SQLite online backup at startup and at the configured interval, retains the newest `DB_BACKUP_KEEP` files, removes expired terminal-job artifacts, removes stale partial uploads, and removes old unreferenced crash/cancellation leftovers. Database records and structured event logs are retained when model files expire.

## API summary

| Method | Path | Current purpose | Future capability |
|---|---|---|---|
| `POST` | `/api/jobs` | Stream and submit an STL | member |
| `GET` | `/api/jobs` | List jobs | member-owned/admin-all |
| `GET` | `/api/jobs/{id}` | Job state | member-owned/admin |
| `DELETE` | `/api/jobs/{id}` | Cancel a safe pre-print job | member-owned/admin |
| `GET` | `/api/jobs/{id}/logs` | Job events | member-owned/admin |
| `GET` | `/api/printers` | Live and durable printer state | member read/admin |
| `POST` | `/api/printers/{id}/plate-cleared` | Resolve physical plate state | admin |
| `GET` | `/api/printers/{id}/history` | Printer history | admin |
| `GET` | `/api/logs/all` | Structured system events | admin |
| `GET` | `/api/health` | Dependency readiness | operations |
| `GET` | `/api/stats` | Aggregate counts | admin |

The future capabilities are documentation for the final authentication phase; they are not enforced by this standalone service yet.

## Repository map

```text
backend/api/                 HTTP endpoints
backend/core/                analysis, slicing, MQTT/FTPS, queue, maintenance
backend/db/                  SQLAlchemy models, transactions, SQLite backup
backend/email/               escaped lifecycle email templates
deploy/bambubabu.service     hardened systemd template
docs/                        implementation status and routing specification
frontend/                    dependency-free dashboard
scripts/                     installer, identity capture, secret check
tests/                       API, printer, queue, routing, storage tests
requirements.lock            hash-locked production dependency graph
```

For internals and invariants, read [TECHNICAL_DETAILS.md](TECHNICAL_DETAILS.md). For operational failures, read [TROUBLESHOOT.md](TROUBLESHOOT.md). The evidence-backed remaining work is tracked in [docs/implementation_plan.md](docs/implementation_plan.md).
