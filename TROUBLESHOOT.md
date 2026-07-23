# BambuBabu troubleshooting

Last reconciled with the code: 2026-07-23.

Start with the service log and health endpoint:

```bash
sudo systemctl status bambubabu
sudo journalctl -u bambubabu -n 100 --no-pager
curl -s http://127.0.0.1:8000/api/health
```

Do not paste `.env`, access codes, serial numbers, captured certificates, SMTP credentials, or full printer URLs into an issue or chat. Redact job email addresses as well.

## Service refuses to start

### Placeholder or missing live values

Live mode intentionally refuses incomplete configuration. Replace every printer value in `.env`, including:

- private LAN IP;
- serial number;
- newly rotated access code;
- captured MQTT certificate path;
- FTPS `sha256//` public-key pin.

Check file permissions without printing its contents:

```bash
stat -c '%a %U %G %n' .env
```

Expected mode is `600`.

### Unauthenticated mode must bind to loopback

If the log says pending auth requires loopback, restore:

```text
HOST=127.0.0.1
AUTHENTICATION_MODE=external-pending
```

Do not change the auth label just to bypass the check. Use an SSH tunnel from another machine:

```bash
ssh -L 8000:127.0.0.1:8000 <pi-user>@<pi-address>
```

### Wildcard CORS is rejected

Leave `CORS_ORIGINS=` empty for the same-origin dashboard. The current unauthenticated service does not support `*`.

### Mock slicing cannot use live printers

Choose one valid combination:

```text
PRINTERS_ENABLED=true
MOCK_SLICER=false
```

or, for development only:

```text
PRINTERS_ENABLED=false
MOCK_SLICER=true
```

The mock slicer creates a fake `.3mf`; sending that output to a printer is prohibited by startup validation.

## Capturing or refreshing printer TLS identity

Rotate the LAN access code on the physical printer first. On a trusted, isolated LAN run:

```bash
./scripts/capture_printer_identity.sh <printer-ip> /var/lib/bambubabu/certs/<printer>-mqtt.pem
```

The script refuses to overwrite an existing certificate. To refresh identity after a verified printer certificate/key change, move the previous file to a protected backup, capture a new one, compare the displayed subject/issuer/fingerprint, then update the FTPS pin in `.env`.

If capture returns no certificate:

```bash
ip route get <printer-ip>
nc -vz <printer-ip> 8883
nc -vz <printer-ip> 990
```

Confirm LAN mode is enabled and that the Pi and printer are on the same trusted network. Do not fall back to unpinned MQTT or FTPS.

## MQTT stays offline

Check:

1. printer IP and serial correspond to the same physical device;
2. the rotated access code in `.env` is current;
3. `P1S_MQTT_CERT_PATH` or `A1_MINI_MQTT_CERT_PATH` is readable by the service user;
4. the certificate is the one captured from port 8883 on that device;
5. no VLAN/firewall blocks TCP 8883.

Useful commands:

```bash
sudo -u <service-user> test -r /var/lib/bambubabu/certs/<printer>-mqtt.pem
openssl x509 -in /var/lib/bambubabu/certs/<printer>-mqtt.pem -noout -subject -issuer -fingerprint -sha256
sudo journalctl -u bambubabu -f
```

A certificate verification failure is a security stop. Re-capture only after physically confirming the printer and network.

If the service repeatedly reports `CERTIFICATE_VERIFY_FAILED` with `self-signed certificate in chain`, the MQTT file was captured by an older script that kept only the leaf certificate. Update the checkout, move the old certificate aside, and recapture so the file contains the complete chain. Do not disable TLS verification.

## FTPS upload fails

The service retries three times, then fails the job if no start command may have been sent. Check TCP 990, free space on the printer, and the FTPS pin.

Recompute the observed pin without writing credentials:

```bash
openssl s_client -connect <printer-ip>:990 -showcerts </dev/null 2>/dev/null \
  | openssl x509 -pubkey -noout \
  | openssl pkey -pubin -outform DER 2>/dev/null \
  | openssl dgst -sha256 -binary \
  | openssl base64 -A
```

Prefix the result with `sha256//` only after confirming it belongs to the intended physical printer. Never remove the pin or pass the access code on a command line.

## Job is stuck in `starting` or moves to `attention`

`starting` is deliberately not equivalent to printing. BambuBabu waits for a newer MQTT report containing `PREPARE` or `RUNNING`. A timeout, service restart during handoff, or unexplained state requires physical inspection.

A missing QoS 1 acknowledgement after the start publish is also ambiguous: the printer may already have accepted the command. The job must remain in `attention`; do not submit another job or clear the plate until the printer is physically inspected.

Do not resubmit immediately. Check the printer screen and plate:

- if the printer is running, leave the job/slot blocked and restore MQTT reporting;
- if it failed or never started, inspect the device and remove any model/material safely;
- only then use Plate Cleared. An `attention` job is resolved to `failed`, not retried automatically.

This behavior prevents duplicate prints after an ambiguous command.

## Printer reports `IDLE` without `FINISH`

The job moves from `printing` to `attention`, printer state becomes error, and `plate_cleared` remains false. This can indicate a firmware/report interruption, manual stop, or lost completion message. Inspect the physical printer before resolving it. BambuBabu will not infer success from `IDLE`.

If the physical screen is idle with no error but MQTT retains `FAILED`, use the Printers tab acknowledgement only after confirming the printer is motionless, has no active job, is cool, and the plate is clear. The action is unavailable while a job owns the printer or the plate is blocked. It does not weaken the next start: any new `FAILED` report rejects that handoff.

## Plate Cleared returns HTTP 409

Plate clearance is rejected while durable printer/job state is `printing`, `paused`, or `starting`. Restore MQTT connectivity and reconcile the real printer state first. The button is an assertion that a human removed the model and the plate is safe; it is not a generic queue-unblock button.

After `FINISH`, the API intentionally reports the live printer as `idle`, but `plate_cleared=false` and `current_job_id` still block dispatch. Once the model is physically removed and Plate Cleared succeeds, the retained firmware `FINISH` event does not prevent the next job from starting.

## OrcaSlicer is missing or fails startup validation

Expected production paths are:

```text
ORCA_SLICER_PATH=/opt/bambubabu/orca/appimage-root/AppRun
SLICER_PROFILES_DIR=/opt/bambubabu/orca/appimage-root/resources/profiles/BBL
```

Check them:

```bash
test -x /opt/bambubabu/orca/appimage-root/AppRun
test -d /opt/bambubabu/orca/appimage-root/resources/profiles/BBL
command -v xvfb-run
command -v xauth
ldconfig -p | grep -E 'libOpenGL\.so\.0|libGLU\.so\.1'
```

Re-run `scripts/install_pi.sh` if the versioned installation is incomplete. The installer verifies the official ARM64 artifact checksum before extraction.

On minimal Ubuntu images, install both headless display helpers explicitly:

```bash
sudo apt-get install -y xvfb xauth libopengl0 libglu1-mesa
sudo systemctl restart bambubabu
```

If Orca reports `XDG_RUNTIME_DIR is invalid or not set`, the unit is from an older checkout. Update and reinstall the generated unit, or add a systemd override that supplies a private directory; do not point `XDG_RUNTIME_DIR` at a shared `/tmp` path.

If Orca reports `Failed to open file for writing` under the application checkout, update the service to a revision that runs Orca from its writable runtime log directory. Do not make the checkout writable to the service.

If P1S slicing reports `return -17`, verify the configured process is `0.20mm Standard @BBL X1C`; Orca's P1S machine preset declares that default, while the P1P process explicitly excludes the P1S. Stop the service before changing profiles or attempting another job. Repeated fallback attempts are suppressed and the job remains assigned to its original printer.

To discard a queued/upload-free job before restarting after an operator stop, use the offline recovery tool with the service stopped. It refuses `uploading`, `starting`, `printing`, and `attention` jobs because those may have affected a physical printer:

```bash
./venv/bin/python scripts/cancel_pending_job.py <job-uuid>
```

## Orca reports missing preset/inheritance errors

Do not copy only the machine/process/filament JSON files. Orca presets inherit from base files. `SLICER_PROFILES_DIR` must point to the complete extracted `BBL` tree.

Verify the selected files exist:

```bash
profiles=/opt/bambubabu/orca/appimage-root/resources/profiles/BBL
test -f "$profiles/machine/Bambu Lab P1S 0.4 nozzle.json"
test -f "$profiles/machine/Bambu Lab A1 mini 0.4 nozzle.json"
test -f "$profiles/process/0.20mm Standard @BBL P1P.json"
test -f "$profiles/process/0.20mm Standard @BBL A1M.json"
```

Fallback routing always re-slices with the target profile. Never rename or reuse another printer's `.3mf`.

Before upload, BambuBabu requires a printable `.gcode.3mf` archive containing `Metadata/plate_1.gcode`. If this validation fails, inspect the Orca CLI output and preset tree; do not upload the archive manually or attempt an MQTT start command.

## Upload errors

| HTTP status | Cause | Action |
|---|---|---|
| `400` | wrong suffix, truncated/invalid STL envelope | export a valid binary or ASCII STL |
| `413` | stream exceeded `MAX_STL_SIZE_MB` | reduce the mesh or raise the reviewed limit |
| `422` | invalid form value/email | correct the input |
| `429` | active jobs reached `MAX_ACTIVE_JOBS` | wait for jobs to reach terminal states |
| `507` | uploads plus slices reached `MAX_STORAGE_MB` | inspect retention and disk capacity |

Check both configured quota and real filesystem capacity:

```bash
df -h /var/lib/bambubabu
du -sh /var/lib/bambubabu/uploads /var/lib/bambubabu/sliced
```

Do not manually remove files for active or plate-blocking jobs. Terminal retention and orphan cleanup run on the maintenance interval.

## Logs tab is empty or incompatible

The dashboard expects `GET /api/logs/all?limit=80` to return a JSON array of structured database events. It no longer parses the rotating text log.

```bash
curl -s 'http://127.0.0.1:8000/api/logs/all?limit=5'
```

An expected item has `timestamp`, `level`, `event`, and `message`. If the API returns data but the UI is stale, hard-refresh the browser. The rotating application log is separate at `/var/lib/bambubabu/logs/bambubabu.log`.

## SQLite, WAL, or backup problems

Inspect without copying a live `.db` file directly:

```bash
sqlite3 /var/lib/bambubabu/bambubabu.db 'PRAGMA journal_mode; PRAGMA integrity_check;'
ls -l /var/lib/bambubabu/backups
```

Expected journal mode is `wal`, integrity result is `ok`, and database/backup files are owner-only. The service uses SQLite's online backup API; a plain copy can miss WAL content.

To restore:

```bash
sudo systemctl stop bambubabu
install -m 0600 /var/lib/bambubabu/backups/<verified-backup>.db \
  /var/lib/bambubabu/bambubabu.db
sudo systemctl start bambubabu
```

Keep the old database as a protected backup until the restored service passes health and job-history checks.

## Installer or dependency failure

The supported deployment is Ubuntu 24.04 ARM64. Confirm:

```bash
uname -m
. /etc/os-release && printf '%s %s\n' "$ID" "$VERSION_ID"
```

Expected architecture is `aarch64`. `requirements.lock` contains exact versions and hashes; the installer uses pip `--require-hashes`. Do not replace it with an unpinned `pip install` during recovery. Regenerate the lock only as a reviewed dependency update, then run tests and `pip-audit`.

### `pydantic-core`, PyO3, or NumPy tries to compile on Python 3.14

This release supports Python 3.12. A message saying PyO3 supports at most Python 3.13 means the virtual environment was created with the wrong interpreter; `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1` is not an approved workaround.

From the repository root, replace only the disposable development environment and verify it before testing:

```bash
./scripts/bootstrap_dev.sh --recreate
.venv/bin/python --version
.venv/bin/python -m pytest -o addopts='' -W error
.venv/bin/python -m ruff check backend tests
```

The version must begin with `Python 3.12`. On Ubuntu 24.04, install it with:

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv
```

If that Ubuntu release does not package 3.12, install `uv` from its official documentation and rerun the bootstrap; it will install a managed 3.12 interpreter. Do not change the tested dependency pins merely to make a 3.14 environment install.

## Service cannot read a checkout under `/home`

The unit uses `ProtectHome=read-only`, not inaccessible. If the checkout was moved after installation, reinstall the generated unit from the new path by rerunning `scripts/install_pi.sh`. Confirm `WorkingDirectory` and `ExecStart`:

```bash
systemctl cat bambubabu
```

Runtime writes must remain under `/var/lib/bambubabu`; the application checkout is intentionally read-only to the service.

## Validation checklist after a repair

```bash
scripts/check_secrets.sh
bash -n scripts/*.sh
test "$(.venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" = 3.12
.venv/bin/python -m ruff check backend tests
.venv/bin/python -m pytest -o addopts='' -W error
curl -s http://127.0.0.1:8000/api/health
```

For a transport or lifecycle repair, also perform a controlled hardware exercise: real slice, upload, confirmed start, MQTT progress, completion, plate block, physical clearance, and service restart during a non-printing test job. Record firmware versions and observed MQTT states without recording credentials.
