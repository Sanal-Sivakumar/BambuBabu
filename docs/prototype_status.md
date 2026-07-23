# BambuBabu prototype checkpoint

Last reconciled: 2026-07-24.

This is the handoff document for resuming work after the first successful physical print. It records only evidence actually observed on the target Pi and printers. It does not mark the system production-ready.

## Pause state

- Target: Raspberry Pi 5, Ubuntu Server 24.04 ARM64, Python 3.12.
- Access: the service is loopback-only and the dashboard is reached through an SSH tunnel.
- Pi power: the current 3 A supply reported `throttled=0x0` during supervised testing, but a proper 5 V/5 A supply is still required before unattended operation.
- Printer identity: both MQTT certificate chains and FTPS public-key pins were captured and validated on the trusted LAN. No live credential belongs in this repository or documentation.
- Health: database, OrcaSlicer, curl, and both MQTT connections returned healthy.
- A1 Mini: one real whistle job completed successfully from STL upload through physical print. MQTT `FINISH`, completed state, physical plate clearance, and `plate_cleared_at` were observed.
- P1S: real ARM64 slicing now succeeds with the compatible X1C process preset and the output contains `Metadata/plate_1.gcode`. A physical P1S print has not yet been attempted.
- Queue: the accidental test jobs were cancelled offline. At the last observation, both printers had clear plates and no current job.
- Verification: 49 automated tests pass with warnings treated as errors; Ruff, dependency audit, Bandit, shell syntax, Python 3.12 verification, and the secret-pattern check pass.
- Authentication: intentionally deferred. The service must remain on loopback until the parent application's member/admin model is integrated.

The Pi was last confirmed running revision `09d9688` before the break. The testing branch contains later completion-state fixes (`305acdc` and `c64364e`) that must be pulled before another job.

## First action after the break

Do not upload a model first. From an SSH session on the Pi:

```bash
cd ~/BambuBabu
sudo systemctl stop bambubabu
git pull --ff-only origin codex/bambubabu-testing-hardening
sudo systemctl start bambubabu
sleep 5

curl -sS http://127.0.0.1:8000/api/health
curl -sS 'http://127.0.0.1:8000/api/jobs?limit=1'
curl -sS http://127.0.0.1:8000/api/printers
```

Expected checkpoint:

- health is `ok`;
- the completed A1 job is 100% with a plate-clear timestamp;
- both printers are connected, idle, plate-clear, and have no current job;
- there are no `pending`, `slicing`, `queued`, `uploading`, `starting`, `printing`, or `attention` jobs.

If the A1 screen is physically idle but MQTT again reports the old `FAILED` value, use the guarded **I Inspected It — Printer Is Idle** action only after confirming it is cool, motionless, jobless, and clear.

## Remaining prototype validation

Complete these in order, one supervised job or fault at a time:

1. Deploy and verify the completion-state update above.
2. Add a deliberate manual dispatch approval/interlock for prototype mode so an upload cannot immediately become a physical print by surprise.
3. Run one small, explicitly P1S-targeted physical print; verify slice, archive validation, FTPS upload, MQTT start, `PREPARE`/`RUNNING`, `FINISH`, 100%, plate block, and clearance.
4. Restart during analysis and verify safe retry without duplicate work.
5. Simulate an MQTT disconnect before start and verify no command is sent and no job becomes `printing`.
6. Exercise an ambiguous handoff in a non-printing fixture and verify `attention`, retained printer ownership, and no replay after restart.
7. Make the preferred printer unavailable and verify fallback creates a new target-specific `.gcode.3mf`, dispatches once, and never reuses the source file.
8. Exercise cancellation immediately before and during slicing, confirming that no worker revives the job.
9. Reduce test quotas temporarily and verify 413, 429, and 507 responses plus removal of partial files; restore reviewed limits afterward.
10. Restore a protected SQLite backup on the Pi, run `PRAGMA integrity_check`, and verify history, printer state, WAL mode, and file permissions.
11. Reboot the Pi and verify systemd startup, restart reconciliation, MQTT reconnection, owner-only files, log rotation, and an empty dispatch queue.
12. Record Pi OS/kernel, Orca version/hash, printer models, and firmware versions without recording credentials.

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

Proven: fresh Pi installation, real ARM64 slicing for both profiles, real A1 upload/start/print/finish/clear workflow, SSH-tunnel UI, structured logs, and the current automated suite.

Not yet proven: a physical P1S print, physical cross-printer fallback, controlled restart/failure drills, quota behavior on the production filesystem, backup restore, long-duration load, unattended recovery, authentication, or safe network exposure.
