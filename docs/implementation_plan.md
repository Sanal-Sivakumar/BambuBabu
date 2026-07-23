# BambuBabu implementation plan and evidence

Last reconciled: 2026-07-24. Live pause/resume details are in [prototype_status.md](prototype_status.md).

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
| Credential removal and startup safety | Complete in code; deployed | empty secret defaults, `.env.example`, `check_secrets.sh`, runtime validation | maintain rotation procedure; confirm any value ever disclosed is invalid |
| Printer TLS identity | Complete; deployed | trusted MQTT certificate chains, FTPS SPKI pins, capture script | re-verify physically after certificate/key or firmware changes |
| Auth and member/admin policy | Deferred by product decision | loopback enforcement and documented capability contract | implement in parent project last |
| Transactional lifecycle | Complete; A1 happy path proven | compare-and-swap transitions, refreshed identity map, transaction-scoped logs/state | restart/fault/load drills |
| MQTT fail-closed start | Complete; A1 proven | QoS 1 handling, ambiguous-start quarantine, idle precondition, newer PREPARE/RUNNING proof | physical P1S and fault injection |
| Restart reconciliation | Complete | safe CPU retry, handoff quarantine, printing ownership restore | controlled Pi restart tests |
| Cancellation and plate races | Complete; A1 plate flow proven | cancellable-state CAS, retained files, plate/current-job invariants | cancellation race and ambiguous plate drills on Pi |
| Cross-printer fallback | Complete in code; both slices proven | fit check, source-availability rule, reservation, compatible target re-slice, retry suppression | physical fallback exercise |
| Portable slicing | Complete; deployed | OS/architecture/Python guards, pinned artifact/checksum, full BBL tree, private workspaces, printable archive validation | repeat on a second fresh image/recovery install |
| Logs API/UI | Complete; deployed | structured `/api/logs/all` array and escaped dashboard rendering | longer browser soak and error-state checks |
| Upload/storage hardening | Complete | streaming limit, active/storage quotas, STL envelope validation, retention/orphans | tune quotas for production volume |
| SQLite durability | Complete | WAL, busy timeout, online backup, rotation, restrictive modes | restore drill on target storage |
| Automated tests and security scans | Complete locally | `tests/`, Ruff, warnings-as-errors, pip-audit, Bandit | add CI runner if repository workflow permits |
| Documentation reconciliation | Current | README, technical reference, troubleshooting, prototype checkpoint, this plan, routing spec | update after every remaining hardware drill |

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
- Mapped a missing QoS 1 PUBACK to ambiguous `attention` because the A1 may still execute the command.
- Treated `IDLE` without `FINISH` as ambiguous instead of complete.
- Kept completed/failed job ownership until explicit physical clearance.
- Added guarded acknowledgement for a physically inspected, jobless stale `FAILED` report; a new start restores strict failure handling.
- Normalized `FINISH` to live idle while durable plate ownership remains blocked, and reconciled completed progress to 100%.

### 3. Queue concurrency and fallback

- Kept the slicer single-worker to avoid concurrent heavy Pi workloads.
- Added up to two independent handoff workers so both printers can receive work without blocking the polling loop.
- Reserved job and printer slot transactionally before transfer.
- Protected cancellation from worker revival.
- Added fallback reservation, bounding-box fit check, source-availability check, and mandatory target-profile re-slicing.
- Returned a failed fallback slice to its original preferred queue and suppressed repeated fallback attempts for that job.

### 4. Slicing and deployment

- Replaced host-specific paths with environment-controlled paths.
- Pinned OrcaSlicer version, asset URL, and SHA-256.
- Extracted the complete BBL profile inheritance tree and run the extracted `AppRun`.
- Added a hash-locked, fully resolved production dependency file.
- Added a Python 3.12 developer bootstrap, `.python-version`, and fail-fast Pi runtime checks.
- Added an idempotent versioned `/opt/bambubabu/orca` layout.
- Added a hardened systemd unit and separate writable runtime home.
- Added private per-invocation Orca working directories and rejected output without `Metadata/plate_1.gcode`.
- Corrected the P1S process to Orca's compatible `0.20mm Standard @BBL X1C` preset.

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
- Added explicit guarded confirmation before clearing a stale jobless printer failure.
- Based the sidebar readiness indicator on `/api/health` rather than any successful endpoint.
- Removed remote font dependencies.

## Automated acceptance evidence

The current local suite has 49 tests covering:

- API upload success and invalid inputs;
- byte limit, active queue quota, and response PII removal;
- cancellation compare-and-swap race;
- structured logs contract;
- plate clearance and active-print refusal;
- pending-auth loopback, CORS, live/mock, and pinned identity settings;
- disconnected/rejected/timed-out/confirmed MQTT starts;
- missing PUBACK ambiguity, stale failure acknowledgement, and partial MQTT reports;
- deterministic handoff release and ambiguous handoff blocking;
- restart state reconciliation;
- preferred/fallback/no-steal routing;
- compatible P1S profile selection, private Orca workspaces, and printable 3MF validation;
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
test "$(.venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" = 3.12
```

Current result: 49 tests, Ruff, dependency audit, Bandit, shell syntax, Python 3.12 verification, and the tracked/untracked secret-pattern check all pass. Rerun the same set before a release tag.

## Hardware acceptance evidence

Observed on the target Raspberry Pi 5:

- Ubuntu Server 24.04 ARM64 and Python 3.12 passed installer/runtime checks;
- systemd, private runtime directories, SQLite backup startup, SSH tunnel, health API, dashboard, and structured logs operated correctly;
- both printers connected through pinned MQTT TLS identities and pinned FTPS transport;
- real A1 Mini and P1S profiles produced validated `.gcode.3mf` archives containing `Metadata/plate_1.gcode`;
- one A1 Mini job completed the full upload, start, physical print, `FINISH`, completed-state, and plate-clear workflow;
- stale A1 `FAILED` state was recoverable only through explicit physical acknowledgement;
- the P1S physical print, live fallback, and controlled fault/restart drills remain unproven.

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

Evidence status on the target hardware:

| Validation | Status | Next evidence |
|---|---|---|
| Fresh Ubuntu 24.04 ARM64 install and OS prerequisites | Proven once | repeat installer twice on a disposable/recovery image to prove idempotence |
| Trusted printer identities and live connections | Proven | negative tests for wrong certificate, pin, code, and non-loopback bind |
| Real slice and printable archive inspection for each profile | Proven | retain a small non-secret fixture and expected archive checks |
| A1 Mini physical print, finish, plate block, and clearance | Proven | repeat as a canary after firmware/protocol changes |
| P1S physical print | Pending | one supervised small P1S-targeted job |
| Disconnected MQTT fail-closed behavior | Pending on hardware | disconnect before start; verify no publish/printing transition |
| Restart during analysis | Pending | verify safe CPU retry and one final job |
| Restart during ambiguous handoff | Pending | controlled non-printing fixture; verify `attention` and no replay |
| Physical cross-printer fallback | Pending | make preferred printer unavailable and inspect target re-slice/start |
| Quotas and partial cleanup on production filesystem | Pending | verify 413/429/507 and no leaked `.part` files |
| SQLite backup restore and integrity | Pending | restore protected backup, run integrity/WAL/history checks |
| Reboot, systemd, permissions, logs, long soak | Pending | cold boot plus monitored multi-hour run |

## Known limitations after this phase

- No authentication or role enforcement inside BambuBabu yet.
- No prototype-mode manual dispatch approval; a valid upload can currently progress to physical dispatch automatically.
- No physical stop/cancel API for an already-started print; current cancellation is deliberately pre-print only.
- Routing uses an axis-aligned bounding box and does not search alternative model rotations.
- Overhang complexity counts faces rather than surface area and is affected by triangulation density.
- Printer protocol behavior may vary by firmware; hardware validation is still required.
- Printer error/HMS details are not yet persisted as structured safe fields.
- Schema creation uses SQLAlchemy `create_all()`; add explicit migrations before changing deployed tables.
- Email is optional and send failure does not roll back a physical print transition.
- SQLite is appropriate for one Pi/service process, not a multi-host control plane.

## Exit criteria for production exposure

BambuBabu may be considered for network-facing production use only after all are true:

- parent authentication and member/admin authorization tests pass;
- both printers and physical fallback have recorded acceptance evidence;
- manual prototype approval has been replaced by an explicit reviewed production dispatch policy;
- real device access codes are rotated and no historic value remains valid;
- device identities are pinned and recovery procedure is exercised;
- the complete deployment validation plan has evidence;
- backup restore and plate-clear procedures are accepted by operators;
- an operator can identify and resolve `attention` without guessing;
- documentation is updated with actual OS, Pi, printer firmware, and observed protocol versions.
