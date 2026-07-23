# BambuBabu implementation plan and evidence

Last reconciled: 2026-07-23.

This is a status document, not a wishlist. `Complete` means implemented and covered by local automated checks. `Deployment validation` means code exists but still needs the target Pi or physical printers. Authentication remains deliberately deferred to the final host-integration phase.

## Product objective

Provide a safe local automation core for two Bambu Lab printers that can accept an STL, route and slice it correctly, start exactly one physical print only after authoritative confirmation, preserve ambiguous state across failure/restart, and require human plate clearance before reuse.

## Non-negotiable invariants

1. No printer credential or serial has a usable source-controlled default.
2. An unconfirmed command never becomes `printing`.
3. A restart never blindly replays upload/start work.
4. A cancelled job cannot be revived by an older worker.
5. A file sliced for one printer is never sent to the other.
6. An ambiguous or completed physical plate blocks dispatch until inspected.
7. Mock slice output cannot reach live printers.
8. Pending-auth mode cannot bind to a non-loopback address.
9. Model upload and storage growth are bounded.
10. Database backup includes committed WAL state and is not world-readable.

## Phase status

| Phase | Status | Evidence in repository | Remaining external action |
|---|---|---|---|
| Credential removal and startup safety | Complete in code | empty secret defaults, `.env.example`, `check_secrets.sh`, runtime validation | rotate both real LAN codes |
| Printer TLS identity | Complete in code | trusted MQTT certificate, FTPS SPKI pin, capture script | capture/verify each physical device on trusted LAN |
| Auth and member/admin policy | Deferred by product decision | loopback enforcement and documented capability contract | implement in parent project last |
| Transactional lifecycle | Complete | compare-and-swap transitions, refreshed identity map, transaction-scoped logs/state | exercise under real load |
| MQTT fail-closed start | Complete | QoS 1 acknowledgment, idle precondition, newer PREPARE/RUNNING proof | verify firmware reports on both models |
| Restart reconciliation | Complete | safe CPU retry, handoff quarantine, printing ownership restore | controlled Pi restart tests |
| Cancellation and plate races | Complete | cancellable-state CAS, retained files, plate/current-job invariants | physical workflow validation |
| Cross-printer fallback | Complete | fit check, source-availability rule, reservation, target re-slice | real Orca outputs on both printers |
| Portable slicing | Complete for installer | pinned artifact/checksum, extracted full BBL tree, printer-specific output | fresh Ubuntu 24.04 ARM64 run |
| Logs API/UI | Complete | structured `/api/logs/all` array and escaped dashboard rendering | browser smoke test on Pi |
| Upload/storage hardening | Complete | streaming limit, active/storage quotas, STL envelope validation, retention/orphans | tune quotas for production volume |
| SQLite durability | Complete | WAL, busy timeout, online backup, rotation, restrictive modes | restore drill on target storage |
| Automated tests and security scans | Complete locally | `tests/`, Ruff, warnings-as-errors, pip-audit, Bandit | add CI runner if repository workflow permits |
| Documentation reconciliation | Complete | README, technical reference, troubleshooting, this plan, routing spec | update again with hardware evidence |

## Delivered work

### 1. Credentials and exposure

- Removed all concrete printer network values and access codes from tracked docs/configuration.
- Converted access codes and SMTP password to `SecretStr`.
- Removed access code from curl argv; it is supplied over stdin configuration.
- Added credential-shape scanning for tracked changes.
- Added device identity pinning for both MQTT and FTPS.
- Forced unauthenticated deployments to loopback and rejected wildcard CORS.
- Moved production writes to `/var/lib/bambubabu`; systemd sees the checkout read-only.

Physical rotation cannot be performed from this repository. Old codes must be changed on the printers, not merely edited in `.env`.

### 2. Lifecycle and printer truth

- Added `starting` and `attention` states.
- Replaced read-then-write status mutation with conditional SQL updates.
- Required a connected, idle printer and acknowledged QoS 1 publish.
- Required a newer MQTT `PREPARE`/`RUNNING` report before `printing`.
- Mapped timeout/restart ambiguity to `attention`, with the printer slot and plate blocked.
- Treated `IDLE` without `FINISH` as ambiguous instead of complete.
- Kept completed/failed job ownership until explicit physical clearance.

### 3. Queue concurrency and fallback

- Kept the slicer single-worker to avoid concurrent heavy Pi workloads.
- Added up to two independent handoff workers so both printers can receive work without blocking the polling loop.
- Reserved job and printer slot transactionally before transfer.
- Protected cancellation from worker revival.
- Added fallback reservation, bounding-box fit check, source-availability check, and mandatory target-profile re-slicing.
- Returned a failed fallback slice to its original preferred queue.

### 4. Slicing and deployment

- Replaced host-specific paths with environment-controlled paths.
- Pinned OrcaSlicer version, asset URL, and SHA-256.
- Extracted the complete BBL profile inheritance tree and run the extracted `AppRun`.
- Added a hash-locked, fully resolved production dependency file.
- Added an idempotent versioned `/opt/bambubabu/orca` layout.
- Added a hardened systemd unit and separate writable runtime home.

### 5. Admission, retention, and database

- Stream uploads to disk in bounded chunks and fsync before atomic rename.
- Enforce per-file, active-job, and aggregate storage limits.
- Validate binary STL length or ASCII STL envelope before admission.
- Normalize untrusted human text and hide email from job API responses.
- Enable SQLite foreign keys, WAL, 30-second busy timeout, and normal synchronous mode.
- Use SQLite's online backup API, microsecond filenames, rotation, and owner-only modes.
- Clean terminal artifacts, stale partial uploads, and old unreferenced crash/cancellation files.

### 6. API and UI

- Made `/api/logs/all` return the structured array the frontend expects.
- Escaped log/job/error text before HTML insertion.
- Added `starting` and `attention` status presentation.
- Added explicit confirmation before plate clearance.
- Based the sidebar readiness indicator on `/api/health` rather than any successful endpoint.
- Removed remote font dependencies.

## Automated acceptance evidence

The current local suite has 38 tests covering:

- API upload success and invalid inputs;
- byte limit, active queue quota, and response PII removal;
- cancellation compare-and-swap race;
- structured logs contract;
- plate clearance and active-print refusal;
- pending-auth loopback, CORS, live/mock, and pinned identity settings;
- disconnected/rejected/timed-out/confirmed MQTT starts;
- deterministic handoff release and ambiguous handoff blocking;
- restart state reconciliation;
- preferred/fallback/no-steal routing;
- identity-map refresh after atomic transition and terminal-state graph enforcement;
- confirmed finish and missing-finish quarantine;
- WAL mode, consistent protected backup, terminal retention, and orphan cleanup.

Required commands:

```bash
.venv/bin/python -m pytest -o addopts='' -W error
.venv/bin/python -m ruff check backend tests
uvx pip-audit -r requirements.lock
uvx bandit -q -r backend -x tests
bash -n scripts/*.sh
scripts/check_secrets.sh
```

Current result: tests pass, Ruff passes, no known dependency vulnerabilities are reported, Bandit reports no unsuppressed finding, shell syntax passes, and the tracked secret-pattern check passes.

## Final authentication phase contract

This phase starts only when the parent project is selected and its identity/session model is known. Do not add a second standalone user database prematurely.

Required behavior:

### Member

- submit under the authenticated member identity; do not trust form-provided ownership;
- view only owned jobs and safe public printer availability;
- cancel only owned jobs while state is in the cancellable set;
- never read another member's email, description, errors, or detailed events.

### Admin

- list and inspect all jobs;
- view system/printer logs, stats, and history;
- clear a physical plate and resolve `attention`;
- operate retention, quotas, printer identity, and service diagnostics;
- see audit events for every privileged action.

### Integration requirements

- parent proxy is the only network-facing component;
- BambuBabu remains on loopback or a private Unix socket;
- trusted identity is conveyed through a mechanism that direct clients cannot forge;
- CSRF protection applies to browser-authenticated mutation requests;
- API tests cover anonymous denial, cross-member denial, member ownership, admin-only mutation, and audit attribution;
- public job forms no longer select `user_email` as authority.

The current `user_name`/`user_email` form fields are notification metadata only. They are not an authorization mechanism.

## Deployment validation plan

These steps are not complete until evidence is collected on the target hardware.

1. Install fresh Ubuntu 24.04 ARM64 on the Pi and update OS packages.
2. Clone a reviewed revision and run `scripts/install_pi.sh` twice to prove idempotence.
3. Rotate both printer LAN codes and capture MQTT/FTPS identities on a controlled LAN.
4. Confirm service refuses one intentionally wrong certificate, pin, access code, and non-loopback bind.
5. Slice one known STL for each printer; inspect the 3MF and Orca output.
6. Print one job per printer and record observed MQTT `gcode_state` sequence.
7. Verify a disconnected MQTT session cannot publish or mark printing.
8. Restart during analysis and confirm safe retry.
9. In a controlled non-printing scenario, restart during handoff and confirm `attention` without replay.
10. Complete a print, verify the plate remains blocked, physically clear it, then verify next dispatch.
11. Make the preferred printer unavailable and confirm fallback re-slices for the other printer.
12. Fill a test quota and verify 413/429/507 responses without partial-file leakage.
13. Create and restore a SQLite backup, then run `PRAGMA integrity_check`.
14. Verify systemd restart, read-only checkout, owner-only runtime files, and log rotation.

## Known limitations after this phase

- No authentication or role enforcement inside BambuBabu yet.
- No physical stop/cancel API for an already-started print; current cancellation is deliberately pre-print only.
- Routing uses an axis-aligned bounding box and does not search alternative model rotations.
- Overhang complexity counts faces rather than surface area and is affected by triangulation density.
- Printer protocol behavior may vary by firmware; hardware validation is still required.
- Email is optional and send failure does not roll back a physical print transition.
- SQLite is appropriate for one Pi/service process, not a multi-host control plane.

## Exit criteria for production exposure

BambuBabu may be considered for network-facing production use only after all are true:

- parent authentication and member/admin authorization tests pass;
- real device access codes are rotated and no historic value remains valid;
- device identities are pinned and recovery procedure is exercised;
- the complete deployment validation plan has evidence;
- backup restore and plate-clear procedures are accepted by operators;
- an operator can identify and resolve `attention` without guessing;
- documentation is updated with actual OS, Pi, printer firmware, and observed protocol versions.
