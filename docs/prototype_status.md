# BambuBabu prototype checkpoint

Last reconciled: 2026-08-21.

This is the handoff document for resuming work after the first successful physical print. It records only evidence actually observed on the target Pi and printers. It does not mark the system production-ready.

## Current rebuild state

- Target: replacement Raspberry Pi 5, Ubuntu Server 24.04 ARM64, Python 3.12. The repository installer has completed through dependency and service installation.
- Access: the service is loopback-only and the dashboard is reached through an SSH tunnel.
- Pi power: the former Pi's 3 A supply reported `throttled=0x0` during supervised testing. Power status on the replacement Pi is not yet recorded; a proper 5 V/5 A supply is still required before unattended operation.
- Printer identity: identities captured on the former Pi are historical evidence, not configuration for the replacement. Capture a fresh P1S MQTT chain and FTPS pin on the trusted LAN. No live credential belongs in this repository or documentation.
- Health: replacement-Pi live health has not yet been verified.
- A1 Mini: one real whistle job previously completed successfully from STL upload through physical print, MQTT `FINISH`, completed state, and plate clearance. The printer is now broken. Configure `A1_MINI_ENABLED=false`; do not connect, route, fallback, or test it until repaired and inspected.
- P1S: real ARM64 slicing now succeeds with the compatible X1C process preset and the output contains `Metadata/plate_1.gcode`. A physical P1S print has not yet been attempted.
- Queue: the replacement Pi uses a fresh database; verify it is empty before the first upload.
- Verification: 55 automated tests pass with warnings treated as errors. Ruff, shell syntax, Python 3.12 verification, diff validation, and secret-pattern scanning pass for the P1S-only change.
- Authentication: intentionally deferred. The service must remain on loopback until the parent application's member/admin model is integrated.

GitHub now uses only `main`. The replacement Pi must pull the new per-printer-isolation revision after it is published; do not use the deleted testing branch.

## Next action on the replacement Pi

Do not upload a model first. From an SSH session on the Pi:

```bash
cd ~/BambuBabu
git switch main
git pull --ff-only origin main
```

Then configure and capture only P1S. Required topology:

```text
PRINTERS_ENABLED=true
P1S_ENABLED=true
A1_MINI_ENABLED=false
```

Expected checkpoint before upload: health is `ok`, `enabled_printers` is exactly `["p1s"]`, P1S is connected/idle/clear/unowned, A1 is shown as disabled, and the fresh database contains no active job.

## Remaining prototype validation

Complete these in order, one supervised job or fault at a time:

1. Publish, pull, and verify the P1S-only isolation update on the replacement Pi.
2. Capture fresh P1S TLS identity, configure only P1S, and prove health without any A1 connection attempt.
3. Add a deliberate manual dispatch approval/interlock for prototype mode so an upload cannot immediately become a physical print by surprise.
4. Run one small P1S-only physical print; verify slice, archive validation, FTPS upload, MQTT start, `PREPARE`/`RUNNING`, `FINISH`, 100%, plate block, and clearance.
5. Restart during analysis and verify safe retry without duplicate work.
6. Simulate an MQTT disconnect before start and verify no command is sent and no job becomes `printing`.
7. Exercise an ambiguous handoff in a non-printing fixture and verify `attention`, retained printer ownership, and no replay after restart.
8. After A1 repair, make the preferred printer unavailable and verify fallback creates a new target-specific `.gcode.3mf`, dispatches once, and never reuses the source file.
9. Exercise cancellation immediately before and during slicing, confirming that no worker revives the job.
10. Reduce test quotas temporarily and verify 413, 429, and 507 responses plus removal of partial files; restore reviewed limits afterward.
11. Restore a protected SQLite backup on the Pi, run `PRAGMA integrity_check`, and verify history, printer state, WAL mode, and file permissions.
12. Reboot the Pi and verify systemd startup, restart reconciliation, MQTT reconnection, owner-only files, log rotation, and an empty dispatch queue.
13. Record Pi OS/kernel, Orca version/hash, printer models, and firmware versions without recording credentials.

Do not combine fault tests. Stop after every unexpected physical movement, stale state, repeated retry, or mismatch between the screen and API.

## Before full operation

The prototype should not become unattended or network-facing until all of these are complete:

- both printers have a successful physical acceptance test;
- prototype-mode manual approval exists and is later replaced or explicitly disabled through a reviewed production setting;
- restart, disconnect, ambiguous-start, cancellation, fallback, quota, retention, and backup-restore drills have evidence;
- a 5 V/5 A Raspberry Pi 5 power supply is installed and throttling is monitored;
- the Pi has a stable DHCP reservation, reliable time sync, OS security updates, disk monitoring, and documented recovery access;
- a parent application enforces authenticated member ownership and admin-only printer operations;
- firmware upgrades are staged and followed by a canary print rather than applied blindly;
- a reviewed release/rollback procedure pins application revision, dependency lock, Orca artifact, profiles, and systemd unit together.

## Defect-prevention backlog

These controls reduce future errors rather than merely fixing observed ones:

- add CI for Python 3.12 tests, Ruff, Bandit, dependency audit, shell syntax, and secret scanning on every change;
- add anonymized MQTT report fixtures for each supported printer/firmware and regression tests for partial/stale reports;
- add a real database migration system before the schema changes; `create_all()` is not an upgrade strategy;
- add durable command idempotency/audit identifiers so a start request can be correlated across publish, report, restart, and operator resolution;
- expose structured printer error/HMS fields without logging credentials or complete raw reports;
- add operational metrics and alerts for queue age, repeated failures, MQTT disconnects, disk usage, backup age, temperature anomalies, and Pi throttling;
- add fairness/aging to shortest-job-first routing before sustained multi-user traffic;
- add filament/material, nozzle, plate type, maintenance lockout, and build-volume safety margins to routing and admission;
- keep physical stop on the printer as the emergency authority; any future remote stop must be admin-only, audited, and hardware-tested;
- perform periodic backup restores, credential rotation, TLS pin verification, quota tests, and canary prints;
- require documentation and test updates in the same change whenever lifecycle, routing, printer protocol, installer, or operator behavior changes.

## Evidence boundary

Proven historically: the original fresh Pi installation, real ARM64 slicing for both profiles, real A1 upload/start/print/finish/clear workflow, SSH-tunnel UI, and structured logs. Proven locally now: the 55-test suite and disabled-printer isolation behavior.

Not yet proven: replacement-Pi live health, fresh P1S trust capture, a physical P1S print, physical cross-printer fallback, repaired-A1 reactivation, controlled restart/failure drills, quota behavior on the production filesystem, backup restore, long-duration load, unattended recovery, authentication, or safe network exposure.
