# 🐼 BambuBabu — Automated 3D Print Management

> Automates the full print pipeline for **Bambu Lab P1S** + **A1 Mini** via a Raspberry Pi 5.
> Upload STL → auto-analyse → auto-slice → smart printer routing → auto-print → email notifications.

---

## ✨ Features

- **Drag-drop STL upload** via web dashboard
- **Automatic complexity scoring** — P1S for complex jobs, A1 Mini for everything else (less power)
- **Smallest job first** queue ordering
- **OrcaSlicer headless slicing** with per-printer profiles
- **MQTT + FTPS** printer control (Bambu LAN mode)
- **Email notifications** — print started, completed, failed
- **Plate Cleared gate** — next job only starts after admin confirms plate is clear
- **Structured logging** — rotating log files + DB log entries
- **Live web dashboard** — printer temps, progress, queue, logs

---

## 🏗️ Architecture

```
Browser (http://pi-ip:8000)
    └── FastAPI (backend/main.py)
         ├── SQLite DB (bambububu.db)
         ├── Queue Processor (background thread)
         │    ├── trimesh STL analyser
         │    └── OrcaSlicer CLI (slicing)
         └── MQTT + FTPS per printer
              ├── Bambu Lab P1S    (192.168.10.116)
              └── Bambu Lab A1 Mini (192.168.10.115)
```

---

## 🚀 Quick Start (Raspberry Pi)

### 1. Clone from GitHub
```bash
git clone https://github.com/yourusername/bambububu.git
cd bambububu
```

### 2. Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
nano .env   # fill in SMTP_PASSWORD (Gmail app password)
```

### 4. Enable LAN mode on both printers
On each printer touchscreen:
> Settings → Network → LAN Mode Locking → ON

### 5. Run
```bash
python -m backend.main
```

Open `http://localhost:8000` in your browser.

---

## 🧪 Testing Without Printers / OrcaSlicer

Set in `.env`:
```env
MOCK_SLICER=true
```

This skips real slicing and uses a fake 30-minute estimate.
MQTT connections will fail gracefully if printers are offline.

---

## 📦 Install OrcaSlicer on Raspberry Pi 5

```bash
# Download the ARM64 AppImage from OrcaSlicer releases
wget https://github.com/SoftFever/OrcaSlicer/releases/latest/download/OrcaSlicer_Linux_ARM64.AppImage
chmod +x OrcaSlicer_Linux_ARM64.AppImage

# Run once to extract
./OrcaSlicer_Linux_ARM64.AppImage --appimage-extract

# Move to /usr/local/bin
sudo mv squashfs-root /opt/orca-slicer
sudo ln -s /opt/orca-slicer/OrcaSlicer /usr/local/bin/OrcaSlicer

# Update .env
# ORCA_SLICER_PATH=/usr/local/bin/OrcaSlicer
```

---

## 🔁 Auto-start on Boot (systemd)

```bash
sudo nano /etc/systemd/system/bambububu.service
```

```ini
[Unit]
Description=BambuBabu 3D Print Automation
After=network.target

[Service]
WorkingDirectory=/home/pi/bambububu
ExecStart=/home/pi/bambububu/venv/bin/python -m backend.main
Restart=always
User=pi
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable bambububu
sudo systemctl start bambububu
sudo systemctl status bambububu
```

---

## 🌿 Git Workflow

```bash
# On your dev machine — push changes
git add .
git commit -m "feat: your changes"
git push origin main

# On Pi — pull and restart
git pull origin main
sudo systemctl restart bambububu
```

---

## 📡 Printer Details

| Printer | IP | Serial |
|---|---|---|
| Bambu Lab P1S | 192.168.10.116 | 01P09C552500636 |
| Bambu Lab A1 Mini | 192.168.10.115 | 0300DA610705389 |

> Access codes and credentials are in `.env` — never committed to git.

---

## 📂 Project Structure

```
bambububu/
├── backend/
│   ├── main.py             FastAPI app + lifespan
│   ├── config.py           Settings (from .env)
│   ├── api/                REST API routes
│   ├── core/               Business logic
│   │   ├── complexity.py   STL analyser
│   │   ├── printer.py      Bambu MQTT + FTPS client
│   │   ├── printer_manager.py  Manages both printers
│   │   ├── slicer.py       OrcaSlicer CLI wrapper
│   │   └── queue_processor.py  Background automation engine
│   ├── db/                 SQLAlchemy models + CRUD
│   ├── email/              Gmail SMTP notifications
│   ├── storage/            STL uploads + sliced .3mf files
│   └── logs/               Rotating log files
├── frontend/               Web dashboard (served by FastAPI)
├── config/slicer_profiles/ Per-printer OrcaSlicer profiles
├── docs/                   Documentation
├── .env.example            Config template
├── requirements.txt
└── README.md
```

---

## 🔒 Security Notes

- Printer access codes and email passwords are in `.env` only — never committed
- `.gitignore` excludes `.env`, log files, and uploaded STL files
- No external internet access required — fully local LAN operation

---

*BambuBabu v1.0 — Raspberry Pi 5 + Bambu Lab P1S + A1 Mini*
