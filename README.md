# 🐼 BambuBabu — Automated 3D Print Management

> A self-hosted web platform for makerspace 3D print queuing, slicing, and automation for Bambu Lab printers.

---

## What Is BambuBabu?

BambuBabu is a Raspberry Pi-hosted web application that lets makerspace members submit STL files through a browser, automatically analyses the model complexity, selects the right printer, slices it, uploads it, and starts the print — all without any manual intervention.

Members visit the web portal, fill in their name and email, upload their STL, and walk away. BambuBabu handles everything else.

---

## Hardware Setup

| Device | Role | IP Address |
|---|---|---|
| Raspberry Pi 5 (4GB) | Runs BambuBabu server | `192.168.10.241` |
| Bambu Lab P1S | Complex / large prints | `192.168.10.116` |
| Bambu Lab A1 Mini | Simple / small prints | `192.168.10.115` |

> **Requirement:** Both printers must have **LAN Mode** enabled (Settings → Network → LAN Mode on the printer touchscreen).

---

## Features

- 📁 **STL Upload Portal** — Members upload files from any browser on the local network
- 🧠 **Automatic Complexity Analysis** — Scores each model using geometry analysis (face count, volume, bounding box)
- 🖨️ **Smart Printer Routing** — Simple models → A1 Mini, complex/large → P1S
- ⚙️ **OrcaSlicer Integration** — Real slicing via OrcaSlicer 2.4.2 running headlessly on Pi
- 📤 **Automatic FTP Upload** — Sends sliced `.3mf` files to the printer's SD card via FTPS
- 🎬 **One-Click Print Start** — Issues the print command via MQTT after upload
- 📊 **Live Dashboard** — Job queue, printer status, and logs all update every 5 seconds
- 📧 **Email Notifications** — Members get notified when their print starts and completes

---

## Quick Start (Fresh Pi Setup)

### 1. Clone the repo
```bash
git clone https://github.com/Sanal-Sivakumar/BambuBabu.git
cd BambuBabu
```

### 2. Create virtual environment and install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Create `.env` file
```bash
cp .env.example .env
nano .env   # fill in your printer IPs, serial numbers, access codes
```

### 4. Install OrcaSlicer (for real slicing)
```bash
sudo apt install -y xvfb libwebkit2gtk-4.1-0
sudo mkdir -p /opt/OrcaSlicer
sudo wget -O /opt/OrcaSlicer/OrcaSlicer.AppImage \
  "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage"
sudo chmod +x /opt/OrcaSlicer/OrcaSlicer.AppImage
```

### 5. Run the server
```bash
python -m backend.main
```

Visit `http://<pi-ip>:8000` in your browser.

---

## Environment Variables (`.env`)

```env
# Printers
P1S_IP=192.168.10.116
P1S_SERIAL=01P09C552500636
P1S_ACCESS_CODE=dd4b4e51

A1_MINI_IP=192.168.10.115
A1_MINI_SERIAL=0300DA610705389
A1_MINI_ACCESS_CODE=c9fd869a

# Slicer
MOCK_SLICER=false
ORCA_SLICER_PATH=/opt/OrcaSlicer/OrcaSlicer.AppImage

# Email (optional)
SMTP_PASSWORD=your_gmail_app_password
ADMIN_EMAIL=your@email.com
```

---

## Project Structure

```
BambuBabu/
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Settings from .env
│   ├── api/
│   │   ├── jobs.py          # Job queue API endpoints
│   │   ├── printers.py      # Printer status API endpoints
│   │   └── logs.py          # Application log API endpoints
│   ├── core/
│   │   ├── printer.py       # Bambu MQTT + FTPS client
│   │   ├── printer_manager.py  # Manages both printers
│   │   ├── queue_processor.py  # Background automation loop
│   │   ├── slicer.py        # OrcaSlicer CLI wrapper
│   │   ├── complexity.py    # STL analysis engine
│   │   └── logger.py        # Structured logging
│   ├── db/
│   │   ├── models.py        # SQLAlchemy ORM models
│   │   ├── crud.py          # Database operations
│   │   └── session.py       # DB connection
│   └── email/
│       └── mailer.py        # Email notifications
├── frontend/                # Static HTML/CSS/JS dashboard
├── config/
│   └── slicer_profiles/     # OrcaSlicer JSON profiles
├── requirements.txt
└── .env.example
```

---

## Daily Operations

### Start the server
```bash
cd ~/Documents/BambuBabu
source venv/bin/activate
python -m backend.main
```

### Kill if port 8000 is busy
```bash
sudo fuser -k 8000/tcp
```

### Clear the job queue (all non-printing jobs)
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from backend.db.session import SessionLocal
from backend.db.models import Job, LogEntry, PrinterState
with SessionLocal() as db:
    db.query(LogEntry).delete()
    db.query(Job).delete()
    for s in db.query(PrinterState).all():
        s.plate_cleared = True
        s.current_job_id = None
    db.commit()
    print('Queue cleared')
"
```

---

## Complexity Scoring

BambuBabu scores each STL from 0–100:

| Score Range | Printer | Reason |
|---|---|---|
| 0–49 | A1 Mini | Simple geometry, fits in 180×180×180mm |
| 50–100 | P1S | Complex/large, needs 256×256×256mm build volume |

Score factors: triangle count, volume, bounding box dimensions, aspect ratio.
If a model is too large for both printers, the job is automatically rejected with an explanation.
