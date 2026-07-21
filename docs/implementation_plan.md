# 3D Print Automation System — MVP Implementation Plan

> **Goal**: Automate the end-to-end print workflow for max 2 concurrent jobs,
> replacing the current manual slice-and-print process.

---

## Architecture Overview

```
                     INTERNET
┌──────────────────────────────────────────────────────────┐
│                                                          │
│   ┌─────────────────┐       ┌──────────────────┐        │
│   │  Vercel (Next.js│──────►│  Cloudflare      │        │
│   │  User Portal)   │◄──────│  Tunnel (HTTPS)  │        │
│   └─────────────────┘       └────────┬─────────┘        │
│                                      │                   │
└──────────────────────────────────────│───────────────────┘
                                       │ encrypted tunnel
                      COMPANY WiFi     │
┌──────────────────────────────────────│───────────────────┐
│                                      ▼                   │
│               ┌──────────────────────────────┐           │
│               │       Raspberry Pi 5          │           │
│               │                              │           │
│               │  ┌──────────┐  ┌──────────┐  │           │
│               │  │ FastAPI  │  │ SQLite   │  │           │
│               │  │  (API)   │  │   (DB)   │  │           │
│               │  └──────────┘  └──────────┘  │           │
│               │  ┌──────────┐  ┌──────────┐  │           │
│               │  │OrcaSlicer│  │  Email   │  │           │
│               │  │  (CLI)   │  │  (SMTP)  │  │           │
│               │  └──────────┘  └──────────┘  │           │
│               └──────────────────────────────┘           │
│                        │ WiFi                             │
│              ┌──────────┴──────────┐                     │
│              ▼                     ▼                     │
│         ┌─────────┐           ┌──────────┐               │
│         │  P1S    │           │ A1 Mini  │               │
│         │MQTT+FTP │           │MQTT+FTP  │               │
│         └─────────┘           └──────────┘               │
└──────────────────────────────────────────────────────────┘
```

---

## How Cloudflare Tunnel Works

```
Pi runs a tiny daemon (cloudflared)
          │
          └──► Connects OUT to Cloudflare (no inbound ports needed)
                        │
                        └──► Pi gets a stable public HTTPS URL:
                             https://3dprint-api.yourcompany.com
                                        │
                                        └──► Vercel calls this URL
```

- **Free** on Cloudflare's free plan
- **No port forwarding** on the company router needed
- **TLS encrypted** end-to-end
- URL stays the same even after Pi reboots
- ~10 minutes to set up

---

## MVP Job Workflow

```
1. User visits Vercel site → uploads STL + name / email / description
2. Next.js → POST to Pi API (STL file + metadata)
3. Pi stores STL → analyses complexity → assigns printer → saves to SQLite
4. Pi sends approval email to Admin (STL attached, complexity score, suggested printer)
5. Admin clicks Approve → Pi starts slicing (OrcaSlicer CLI)
                        → Pi emails user: "Approved & queued"
6. Pi checks printer availability → uploads .3mf via FTPS
                                  → sends MQTT print command
                                  → emails user: "Printing started"
7. Pi monitors via MQTT → print % on status page
                        → on complete: emails admin + user
8. Admin clears plate → clicks "Plate Cleared" in portal
                      → unlocks next queued job
```

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Frontend** | Next.js 14 (App Router) | User portal + Admin dashboard |
| **Hosting** | Vercel (free tier) | Frontend deployment |
| **Tunnel** | Cloudflare Tunnel (free) | Expose Pi API to internet |
| **Backend** | FastAPI + Uvicorn | REST API on Raspberry Pi |
| **Database** | SQLite + SQLAlchemy | Job & printer state storage |
| **Migrations** | Alembic | DB schema versioning |
| **Slicer** | OrcaSlicer CLI (headless) | STL → .3mf per printer profile |
| **STL Analysis** | trimesh + numpy | Complexity scoring |
| **Printer Control** | paho-mqtt (MQTT) | Real-time printer commands & status |
| **File Transfer** | ftplib with TLS (FTPS) | Upload .3mf to printer |
| **Email** | SMTP (Gmail / company mail) | All notification emails |
| **Language** | Python 3.11+ | Core backend |

---

## Project File Structure

```
3d_auto/
│
├── docs/
│   ├── implementation_plan.md          ← this file
│   └── printer_selection_algorithm.md  ← routing logic
│
├── frontend/                           ← deployed to Vercel
│   ├── app/
│   │   ├── page.tsx                    # Upload portal
│   │   ├── status/[jobId]/page.tsx     # Job status page
│   │   └── admin/page.tsx              # Admin dashboard
│   ├── components/
│   ├── lib/
│   │   └── api.ts                      # Pi API client
│   ├── package.json
│   └── next.config.js
│
└── backend/                            ← runs on Raspberry Pi
    ├── main.py                         # FastAPI app entry
    ├── config.py                       # Settings (.env loader)
    ├── requirements.txt
    ├── .env                            # Secrets — NEVER commit
    │
    ├── api/
    │   ├── jobs.py                     # Upload, status, list
    │   ├── admin.py                    # Approve, reject, plate-cleared
    │   └── printers.py                 # Live printer status
    │
    ├── core/
    │   ├── printer.py                  # BambuPrinter class (MQTT + FTP)
    │   ├── printer_manager.py          # P1S + A1 Mini state management
    │   ├── complexity.py               # STL analyser (trimesh)
    │   ├── slicer.py                   # OrcaSlicer CLI wrapper
    │   └── job_runner.py               # Full flow orchestrator
    │
    ├── db/
    │   ├── models.py                   # SQLAlchemy models
    │   ├── crud.py                     # DB operations
    │   └── session.py                  # DB connection
    │
    ├── email/
    │   ├── mailer.py                   # SMTP sender
    │   └── templates/
    │       ├── admin_review.html
    │       ├── user_approved.html
    │       ├── user_printing.html
    │       └── print_complete.html
    │
    └── storage/
        ├── uploads/                    # Raw .stl files
        └── sliced/                     # .3mf files ready to print
```

---

## Database Schema

### `jobs` table

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `user_name` | TEXT | Submitter name |
| `user_email` | TEXT | Submitter email |
| `original_filename` | TEXT | Original STL filename |
| `stl_path` | TEXT | Internal storage path |
| `sliced_path` | TEXT | Set after slicing |
| `status` | ENUM | See job states below |
| `complexity_score` | FLOAT | 0.0 – 100.0 |
| `assigned_printer` | ENUM | P1S or A1_MINI |
| `estimated_print_minutes` | INT | From OrcaSlicer output |
| `approval_token` | TEXT | One-time UUID for email links |
| `token_expires_at` | DATETIME | Token expiry (24h) |
| `reject_reason` | TEXT | Set on rejection |
| `print_progress` | INT | 0–100% from MQTT |
| `submitted_at` | DATETIME | |
| `approved_at` | DATETIME | |
| `print_started_at` | DATETIME | |
| `print_ended_at` | DATETIME | |
| `plate_cleared_at` | DATETIME | |

### Job States (Status Enum)

```
PENDING_REVIEW → SLICING → QUEUED → UPLOADING → PRINTING → COMPLETED
                                                               ↓
                                                        PLATE_CLEARED
     └──────────────────────────────────────────────► REJECTED
     └──────────────────────────────────────────────► FAILED
```

### `printer_state` table

| Column | Type | Description |
|---|---|---|
| `printer_id` | ENUM | P1S or A1_MINI |
| `status` | TEXT | IDLE / PRINTING / PAUSED / ERROR / OFFLINE |
| `current_job_id` | UUID | FK to jobs table |
| `plate_cleared` | BOOL | Set by admin after print |
| `last_seen` | DATETIME | Last MQTT heartbeat |

---

## API Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/jobs` | User | Upload STL, create job |
| GET | `/api/jobs/{id}` | Public | Get job status (for status page) |
| GET | `/api/jobs` | Admin | List all jobs |
| POST | `/api/jobs/{id}/approve` | Token | Approve via email link |
| POST | `/api/jobs/{id}/reject` | Token | Reject via email link |
| POST | `/api/jobs/{id}/plate-cleared` | Admin | Mark plate as cleared |
| GET | `/api/printers` | Public | Live printer status |
| GET | `/api/health` | Public | Health check |

---

## Email Notifications

### 1 — Admin Review Email (on upload)
- **To**: Admin
- **Subject**: `[3D Print Request] Job #42 — bracket.stl — Suggested: A1 Mini`
- **Contains**: User info, STL attached, complexity score, suggested printer, estimated time, Approve / Reject buttons (signed links)

### 2 — User Approved Email (on approval)
- **To**: User
- **Subject**: `Your 3D Print has been approved — Job #42`
- **Contains**: Assigned printer, queue position, estimated wait time

### 3 — User Print Started Email
- **To**: User
- **Subject**: `Your 3D Print has started — Job #42`
- **Contains**: Printer name, start time, estimated completion time

### 4 — Print Complete Email
- **To**: Admin + User
- **Subject**: `Print Complete — Job #42 — Please clear the plate`
- **Admin body**: Reminder to clear plate + link to portal
- **User body**: Print is ready for collection

---

## Security Measures (MVP Scope)

| Concern | Solution |
|---|---|
| STL file validation | Magic bytes check, size limit (100MB), extension check |
| Admin approval links | One-time UUID token, expires after 24 hours |
| Admin dashboard | Password protected (env var), upgrade to JWT post-MVP |
| Pi API exposure | Cloudflare Tunnel — no open ports on company network |
| Secrets | `.env` file, `chmod 600`, never committed to git |
| File storage | Outside web root, UUID-based internal filenames |
| Email token replay | Token marked used on first click, ignored on subsequent |

---

## Printer Configuration

```yaml
# config/printers.yaml
printers:
  p1s:
    name: "Bambu Lab P1S"
    ip: "192.168.x.x"           # fill in
    serial: "XXXXXXXX"          # from printer screen
    access_code: "XXXXXXXX"     # from printer screen (LAN mode)
    mqtt_port: 8883
    ftp_port: 990
    build_volume: [256, 256, 256]
    slicer_profile: "config/slicer_profiles/p1s_standard.json"

  a1_mini:
    name: "Bambu Lab A1 Mini"
    ip: "192.168.x.x"           # fill in
    serial: "XXXXXXXX"
    access_code: "XXXXXXXX"
    mqtt_port: 8883
    ftp_port: 990
    build_volume: [180, 180, 180]
    slicer_profile: "config/slicer_profiles/a1mini_standard.json"
```

---

## Implementation Phases

### Phase 1 — Pi Backend Foundation
- [ ] FastAPI project setup
- [ ] SQLite DB + SQLAlchemy models + Alembic migrations
- [ ] STL upload endpoint with file validation
- [ ] STL complexity analyser (trimesh)
- [ ] Printer selection logic
- [ ] Health check endpoint

### Phase 2 — Email & Approval Flow
- [ ] SMTP email integration
- [ ] Admin review email with signed approval/reject links
- [ ] Approve / Reject endpoints (token validation)
- [ ] User notification emails (approved, started, complete)

### Phase 3 — Slicing
- [ ] Install OrcaSlicer on Pi 5
- [ ] Configure printer profiles (P1S + A1 Mini)
- [ ] OrcaSlicer CLI subprocess wrapper
- [ ] Background slicing trigger on approval
- [ ] Store estimated print time from slicer output

### Phase 4 — Printer Control
- [ ] MQTT client per printer (paho-mqtt + TLS)
- [ ] FTPS file upload to printer
- [ ] Print command via MQTT
- [ ] Live progress monitoring (%, temps, errors)
- [ ] Plate-cleared gate logic (blocks next job)

### Phase 5 — Cloudflare Tunnel
- [ ] Install cloudflared on Pi
- [ ] Create tunnel + assign public HTTPS URL
- [ ] Register as systemd service (auto-start on boot)

### Phase 6 — Vercel Frontend
- [ ] Next.js project setup
- [ ] Upload page (drag-drop STL + user form)
- [ ] Job status page (polling Pi API)
- [ ] Admin dashboard (queue view, approve/reject, plate-cleared, live printer tiles)
- [ ] Deploy to Vercel

### Phase 7 — Integration Testing
- [ ] End-to-end test with real STL on both printers
- [ ] Email flow verified (all 4 email types)
- [ ] Queue ordering test (multiple jobs)
- [ ] Edge cases: printer offline, slicer fail, oversized file, token expiry

---

## What's Needed Before Coding Starts

1. **Both Printer Details** (from printer touchscreen → Settings → Network):
   - IP address
   - Serial number
   - Access code
   - LAN mode must be enabled

2. **Email credentials** for notifications:
   - Gmail app password (recommended), OR
   - Company SMTP server + credentials

3. **Raspberry Pi**:
   - OS installed (Raspberry Pi OS recommended)
   - Connected to company WiFi
   - SSH access confirmed

4. **Accounts** (all free):
   - Cloudflare account (for tunnel)
   - Vercel account (for frontend hosting)

---

## Resource Usage on Pi 5 (4GB)

| Service | RAM Estimate |
|---|---|
| FastAPI (Uvicorn, 2 workers) | ~150 MB |
| SQLite | ~20 MB |
| MQTT clients (2×) | ~30 MB |
| OrcaSlicer CLI (during slicing) | ~1–2 GB peak |
| cloudflared | ~30 MB |
| OS + overhead | ~300 MB |
| **Total** | **~2–3 GB** (tight but manageable) |

> Keep OrcaSlicer slicing sequential — never run two slice jobs simultaneously.

---

*Last updated: July 2026*
*Stack: Next.js + Vercel + Cloudflare Tunnel + FastAPI + SQLite + OrcaSlicer + paho-mqtt*
