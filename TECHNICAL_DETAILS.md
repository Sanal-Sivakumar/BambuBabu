# 📚 BambuBabu — Technical Documentation

> Complete technical reference from zero knowledge to advanced internals.
> Written so a beginner can understand, and an expert can go deep.

---

## Table of Contents

1. [The Big Picture — What Problem Does This Solve?](#1-the-big-picture)
2. [System Architecture](#2-system-architecture)
3. [Hardware](#3-hardware)
4. [Networking & Protocols](#4-networking--protocols)
5. [Backend — Python FastAPI](#5-backend--python-fastapi)
6. [Database — SQLite + SQLAlchemy](#6-database--sqlite--sqlalchemy)
7. [Bambu Printer Communication](#7-bambu-printer-communication)
8. [STL Analysis & Printer Routing](#8-stl-analysis--printer-routing)
9. [Slicing — OrcaSlicer CLI](#9-slicing--orcaslicer-cli)
10. [Job Queue Automation](#10-job-queue-automation)
11. [Frontend Dashboard](#11-frontend-dashboard)
12. [Email Notifications](#12-email-notifications)
13. [Deployment — Raspberry Pi Setup](#13-deployment--raspberry-pi-setup)
14. [Systemd Service (Auto-start)](#14-systemd-service-auto-start)
15. [Configuration Reference](#15-configuration-reference)
16. [API Reference](#16-api-reference)
17. [Data Flow — Full Job Lifecycle](#17-data-flow--full-job-lifecycle)

---

## 1. The Big Picture

### The Problem
A university makerspace has two 3D printers. Members queue up manually, printers sometimes sit idle while waiting for someone to notice, and there's no system for tracking who printed what or when. Every print requires a staff member to physically slice the file and start the job.

### The Solution
BambuBabu completely automates the pipeline:

```
Member uploads STL file via browser
        ↓
BambuBabu analyses the geometry
        ↓
Decides: A1 Mini or P1S?
        ↓
Slices the STL automatically
        ↓
Uploads the sliced file to the printer via WiFi
        ↓
Issues the print command
        ↓
Monitors progress via MQTT
        ↓
Emails member when done
```

No human intervention required from submission to print start.

---

## 2. System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────┐
│                  Raspberry Pi 5 (4GB)                   │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │          FastAPI Web Server (port 8000)          │    │
│  │                                                  │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │    │
│  │  │ /api/jobs│ │/api/print│ │   /api/logs/all  │ │    │
│  │  └──────────┘ └──────────┘ └──────────────────┘ │    │
│  │                                                  │    │
│  │  ┌────────────────────────────────────────────┐  │    │
│  │  │          Static Frontend (HTML/JS)         │  │    │
│  │  └────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────┘    │
│                          │                               │
│  ┌───────────────────────▼─────────────────────────┐    │
│  │           Queue Processor (background thread)    │    │
│  │                                                  │    │
│  │  PENDING → ANALYSING → SLICING → QUEUED →       │    │
│  │  UPLOADING → PRINTING → COMPLETED                │    │
│  └──────┬──────────────────────────┬───────────────┘    │
│         │                          │                     │
│  ┌──────▼──────┐          ┌────────▼──────────┐         │
│  │ OrcaSlicer  │          │  PrinterManager   │         │
│  │  (xvfb-run) │          │                   │         │
│  └─────────────┘          │ ┌───────────────┐ │         │
│                            │ │ BambuPrinter  │ │         │
│  ┌─────────────┐           │ │  (P1S)        │ │         │
│  │  SQLite DB  │           │ └───────────────┘ │         │
│  │             │           │ ┌───────────────┐ │         │
│  │  Jobs       │           │ │ BambuPrinter  │ │         │
│  │  Printers   │           │ │  (A1 Mini)    │ │         │
│  │  Logs       │           │ └───────────────┘ │         │
│  └─────────────┘           └──────────┬────────┘         │
└─────────────────────────────────────── │ ────────────────┘
                                         │ WiFi
                    ┌────────────────────┼────────────────┐
                    │                    │                │
             ┌──────▼──────┐    ┌────────▼──────┐        │
             │  Bambu P1S  │    │ Bambu A1 Mini │        │
             │192.168.10.116│   │192.168.10.115 │        │
             └─────────────┘    └───────────────┘        │
                    WiFi LAN (192.168.10.0/24)            │
```

### Component Responsibilities

| Component | File | Purpose |
|---|---|---|
| FastAPI app | `backend/main.py` | HTTP server, routing, lifespan management |
| Job API | `backend/api/jobs.py` | CRUD for print jobs |
| Printer API | `backend/api/printers.py` | Live printer status |
| Logs API | `backend/api/logs.py` | Stream log file to UI |
| Queue Processor | `backend/core/queue_processor.py` | The automation brain |
| Printer Manager | `backend/core/printer_manager.py` | Owns both printer instances |
| Bambu Printer | `backend/core/printer.py` | MQTT + FTPS per printer |
| Complexity | `backend/core/complexity.py` | STL geometry analysis |
| Slicer | `backend/core/slicer.py` | OrcaSlicer CLI wrapper |
| Database | `backend/db/` | SQLite via SQLAlchemy |
| Frontend | `frontend/` | Browser UI |

---

## 3. Hardware

### Raspberry Pi 5 (4GB)
- **OS:** Raspberry Pi OS Trixie (Debian 13, 64-bit ARM)
- **Architecture:** aarch64 (ARM Cortex-A76)
- **Role:** Application server, runs Python + OrcaSlicer
- **Network:** WiFi or Ethernet on local network
- **IP:** `192.168.10.241:8000` (accessed by all browsers)

### Bambu Lab P1S
- **Type:** Enclosed FDM printer
- **Build Volume:** 256 × 256 × 256 mm
- **Nozzle:** 0.4mm
- **Use Case:** Complex models, large prints, high complexity score (≥50)
- **IP:** `192.168.10.116`
- **Serial:** `01P09C552500636`
- **Protocol:** MQTT over TLS (port 8883) + Implicit FTPS (port 990)

### Bambu Lab A1 Mini
- **Type:** Open-frame FDM printer
- **Build Volume:** 180 × 180 × 180 mm
- **Nozzle:** 0.4mm
- **Use Case:** Simple models, small prints, low complexity score (<50)
- **IP:** `192.168.10.115`
- **Serial:** `0300DA610705389`
- **Protocol:** MQTT over TLS (port 8883) + Implicit FTPS (port 990)

---

## 4. Networking & Protocols

### Network Layout
All devices are on the same local WiFi: `192.168.10.0/24`

```
Router (192.168.10.1)
├── Raspberry Pi (192.168.10.241)
├── Bambu P1S   (192.168.10.116)
└── Bambu A1 Mini (192.168.10.115)
```

### MQTT Protocol (Printer Status & Control)
MQTT is a lightweight publish/subscribe protocol used for IoT devices.

**How Bambu Lab uses it:**
- Printers publish status updates every few seconds to: `device/<serial>/report`
- BambuBabu subscribes to this topic to receive live status
- BambuBabu publishes print commands to: `device/<serial>/request`

**Connection details:**
- Port: `8883` (MQTT over TLS)
- Username: `bblp`
- Password: Access code shown on printer screen (e.g., `dd4b4e51`)
- TLS: Self-signed certificate, verification disabled
- Library: `paho-mqtt`

**Status message fields:**
```json
{
  "print": {
    "gcode_state": "IDLE",
    "mc_percent": 0,
    "nozzle_temper": 25.0,
    "bed_temper": 25.0,
    "stg_cur": 255
  }
}
```

**Print command (MQTT publish):**
```json
{
  "print": {
    "sequence_id": "0",
    "command": "project_file",
    "param": "Metadata/plate_1.gcode",
    "url": "ftp://filename.3mf",
    "bed_type": "auto",
    "timelapse": false,
    "bed_leveling": true,
    "flow_cali": false,
    "vibration_cali": true,
    "layer_inspect": false,
    "use_ams": false
  }
}
```

### Implicit FTPS Protocol (File Upload)
FTP (File Transfer Protocol) over TLS, on port 990.

**What "implicit" means:**
- Regular FTP: starts unencrypted, optionally upgrades to TLS with STARTTLS command
- **Implicit FTPS:** the entire connection is wrapped in TLS from the very first byte
- Python's built-in `ftplib.FTP_TLS` only supports explicit mode
- BambuBabu uses a custom `BambuFTP` subclass that wraps the socket in TLS on connect

**Passive mode (PASV):**
- After connecting, the client asks the server to open a data port for file transfer
- The server responds with an IP address and port number
- The client connects to that IP:port for the actual data transfer
- Bug: Bambu printers sometimes respond with the wrong IP in PASV
- Fix: `BambuFTP.makepasv()` always replaces the IP with the known printer IP

**226 Transfer Complete issue:**
- After all file bytes are sent, FTP protocol requires the server to send "226 Transfer complete"
- Bambu printers sometimes don't send this response, or send it very slowly
- Python waits forever for this response → timeout
- Fix: Use `transfercmd()` to manually control the data socket, then wait only 5s for "226"

---

## 5. Backend — Python FastAPI

### What Is FastAPI?
FastAPI is a modern Python web framework for building APIs. It's fast (uses async I/O), automatically generates API documentation, and validates request/response data using Python type hints.

### Application Startup (`backend/main.py`)
FastAPI uses a "lifespan" context manager for startup/shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # === STARTUP ===
    init_db()                    # Create SQLite tables if not exist
    printer_manager.init()       # Connect to both printers via MQTT
    queue_processor.start()      # Start background automation loop
    
    yield  # App runs here
    
    # === SHUTDOWN ===
    queue_processor.stop()
    printer_manager.shutdown()
```

### Static File Serving
The frontend HTML/JS/CSS is served directly by FastAPI:
```python
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
```

### CORS
All origins are allowed (it's a local network app):
```python
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

---

## 6. Database — SQLite + SQLAlchemy

### Why SQLite?
SQLite is a file-based database (no server needed). Perfect for a single-Pi deployment. The database is stored at `backend/bambububu.db`.

### ORM Models

**`Job` — a print job:**
```python
class Job(Base):
    id: str               # UUID (primary key)
    original_filename: str
    submitter_name: str
    submitter_email: str
    status: JobStatus     # PENDING/ANALYSING/SLICING/QUEUED/UPLOADING/PRINTING/COMPLETED/FAILED
    stl_path: str         # Where the STL is stored on disk
    sliced_path: str      # Where the 3MF is stored after slicing
    assigned_printer: str # "p1s" or "a1_mini"
    complexity_score: float
    estimated_minutes: int
    error_message: str
    created_at: datetime
```

**`PrinterState` — live printer status:**
```python
class PrinterState(Base):
    printer_id: str       # "p1s" or "a1_mini"
    status: PrinterStatus # IDLE/PRINTING/PAUSED/ERROR/OFFLINE
    plate_cleared: bool   # True = ready for next job
    current_job_id: str   # Which job is printing now
    print_progress: float # 0-100%
    nozzle_temp: float
    bed_temp: float
```

**`LogEntry` — application events:**
```python
class LogEntry(Base):
    id: int
    event: str            # e.g., "PRINT_STARTED", "JOB_FAILED"
    message: str
    job_id: str           # Optional, links to a Job
    printer_id: str       # Optional, links to a printer
    level: LogLevel       # INFO/WARNING/ERROR
    timestamp: datetime
```

### Job Status Flow
```
PENDING → ANALYSING → SLICING → QUEUED → UPLOADING → PRINTING → COMPLETED
                                                    ↘ FAILED (at any point)
```

---

## 7. Bambu Printer Communication

### BambuFTP Class (`backend/core/printer.py`)

Handles the implicit FTPS upload to the printer's SD card:

```python
class BambuFTP(ftplib.FTP_TLS):
    def __init__(self, host_ip, context):
        self._force_host = host_ip  # Always use this IP for data channel
        
    def sock_setter(self, value):
        # Wrap every socket in TLS immediately (implicit mode)
        if not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        
    def connect(self, host, port=990):
        # Create raw TCP socket, then immediately wrap in TLS
        raw = socket.create_connection((host, port))
        self.sock = raw  # triggers TLS wrap via setter
        
    def makepasv(self):
        # Get port from PASV response, but use known IP
        _, port = super().makepasv()
        return self._force_host, port  # Fixes wrong IP bug
```

**Upload flow:**
```python
def upload_file(self, local_path, remote_filename):
    ftp = BambuFTP(host_ip=self.ip, context=ctx)
    ftp.connect(self.ip, 990, timeout=60)
    ftp.login("bblp", self.access_code)
    ftp.prot_p()      # Enable TLS on data channel too
    ftp.set_pasv(True)
    
    # transfercmd() gives us direct control of the data socket
    conn = ftp.transfercmd(f"STOR /{remote_filename}")
    with open(local_path, "rb") as f:
        while block := f.read(32768):
            conn.sendall(block)
    conn.close()
    
    # Wait max 5s for "226" response — move on regardless
    ftp.sock.settimeout(5)
    try:
        ftp.voidresp()
    except:
        pass  # File is there even if server didn't confirm
```

### BambuPrinter Class — MQTT

```python
class BambuPrinter:
    def connect(self):
        self.mqtt = mqtt.Client(transport="tcp")
        self.mqtt.tls_set(cert_reqs=ssl.CERT_NONE)
        self.mqtt.username_pw_set("bblp", self.access_code)
        self.mqtt.on_message = self._on_message
        self.mqtt.connect(self.ip, 8883)
        self.mqtt.loop_start()  # Background thread
        self.mqtt.subscribe(f"device/{self.serial}/report")
    
    def _on_message(self, client, userdata, msg):
        # Parse JSON status update from printer
        data = json.loads(msg.payload)["print"]
        self.status = data.get("gcode_state", "")
        self.progress = data.get("mc_percent", 0)
        self.nozzle_temp = data.get("nozzle_temper", 0)
        self.on_status_update(self.printer_id, snapshot)
    
    def start_print(self, filename, job_name):
        command = {
            "print": {
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "url": f"ftp://{filename}",
                "bed_leveling": True,
                "vibration_cali": True,
            }
        }
        self.mqtt.publish(f"device/{self.serial}/request", json.dumps(command))
```

---

## 8. STL Analysis & Printer Routing

### What Is an STL File?
STL (Stereolithography) is a file format for 3D models. It describes a solid object as a mesh of triangular faces. Each triangle has 3 vertices (corners) and a normal vector (direction it faces).

### Complexity Analysis (`backend/core/complexity.py`)

BambuBabu uses `trimesh` to load and analyse the STL:

```python
import trimesh

mesh = trimesh.load(stl_path, force="mesh")

analysis = {
    "face_count": len(mesh.faces),        # Number of triangles
    "vertex_count": len(mesh.vertices),   # Number of corner points
    "volume_cm3": mesh.volume / 1000,     # Model volume in cm³
    "surface_area_cm2": mesh.area / 100,  # Surface area in cm²
    "bounding_box": {
        "x": extents[0],  # Width in mm
        "y": extents[1],  # Depth in mm
        "z": extents[2],  # Height in mm
    },
    "complexity_score": score,  # 0-100
}
```

### Complexity Score Formula

Score is calculated on a 0–100 scale based on:

| Factor | Weight | Rationale |
|---|---|---|
| Face count | 30% | More triangles = more complex geometry |
| Volume | 20% | Larger volume = more material = more time |
| Bounding box (max dim) | 30% | Size determines which printer can fit it |
| Surface area | 20% | More surface = more detail = more time |

Each factor is normalised against thresholds:
- Face count threshold: 100,000 faces = score 100
- Volume threshold: 500 cm³ = score 100
- Max dimension threshold: 200mm = score 100

### Printer Selection

```python
def select_printer(analysis):
    score = analysis["complexity_score"]
    bbox = analysis["bounding_box"]
    max_dim = max(bbox["x"], bbox["y"], bbox["z"])
    
    # Check if it fits at all
    if max_dim > 256:
        return None, "Model too large for any printer"
    
    # Fits only in P1S (too big for A1 Mini)
    if max_dim > 180:
        return PrinterID.P1S, None
    
    # Simple models → A1 Mini (energy efficient)
    if score < 50:
        return PrinterID.A1_MINI, None
    
    # Complex models → P1S (better for complex prints)
    return PrinterID.P1S, None
```

---

## 9. Slicing — OrcaSlicer CLI

### What Is Slicing?
A 3D printer doesn't understand a 3D model. It needs G-code: a sequence of movement commands (G1 X10 Y20 Z0.2 E5.0...). The slicer converts the 3D model into these commands by:
1. Slicing the model into horizontal layers (0.2mm each)
2. Generating the path the nozzle should follow for each layer
3. Adding supports, infill, perimeters, etc.
4. Outputting a `.3mf` file containing the G-code

### OrcaSlicer
OrcaSlicer is a free, open-source slicer specifically designed for Bambu Lab printers. It contains built-in profiles for all Bambu machines with optimal settings for each.

**Installation on Raspberry Pi:**
```bash
# Install virtual display (OrcaSlicer needs X11 even in CLI mode)
sudo apt install -y xvfb libwebkit2gtk-4.1-0

# Download ARM64 AppImage (self-contained executable)
sudo wget -O /opt/OrcaSlicer/OrcaSlicer.AppImage \
  "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage"
```

**CLI Command:**
```bash
xvfb-run --auto-servernum \
  /opt/OrcaSlicer/OrcaSlicer.AppImage \
  --slice 0 \
  --export-3mf output.3mf \
  --load-settings printer_profile.json \
  input.stl
```

- `xvfb-run --auto-servernum`: Creates a virtual display (Xvfb = X Virtual Framebuffer)
- `--slice 0`: Slice all plates
- `--export-3mf output.3mf`: Save result as 3MF with embedded G-code
- `--load-settings`: Load printer/process configuration

### AppImage Format
An AppImage is a self-contained Linux executable that bundles the application and most of its dependencies into a single file. It uses FUSE (filesystem in userspace) to mount itself and run. That's why `libfuse2` is needed.

---

## 10. Job Queue Automation

### QueueProcessor (`backend/core/queue_processor.py`)

The queue processor is a background thread that polls the database every 10 seconds and advances jobs through the pipeline.

**Poll cycle (`_tick`):**
```python
def _tick(self):
    # 1. Submit PENDING jobs for slicing (in thread pool)
    pending = get_jobs_by_status(PENDING)
    for job in pending:
        executor.submit(_slice_pipeline, job.id)
    
    # 2. Dispatch QUEUED jobs to idle printers
    for printer_id in [A1_MINI, P1S]:
        _try_dispatch(printer_id)
```

**Slice Pipeline (runs in thread pool worker):**
```
PENDING
  → ANALYSING: trimesh loads STL, calculates complexity score
  → select_printer(): decides A1_MINI or P1S
  → SLICING: calls OrcaSlicer, waits up to 10 minutes
  → QUEUED: ready for a printer
```

**Dispatch (when printer is idle and plate is cleared):**
```
QUEUED
  → UPLOADING: BambuFTP uploads .3mf to printer SD card
  → MQTT publish: send print command to printer
  → PRINTING: printer starts
  → (MQTT callback detects FINISH/FAILED)
  → COMPLETED or FAILED
```

### Thread Safety
The slicer runs in a `ThreadPoolExecutor` (max 1 worker) to avoid running two OrcaSlicer instances simultaneously (would exhaust Pi's RAM). The main queue loop uses a `threading.Lock` to prevent double-scheduling jobs.

### Plate Cleared Logic
After a print completes, `plate_cleared = False`. No new jobs are dispatched to that printer until an admin marks the plate as cleared (the finished print has been removed). This prevents a new print from starting on top of a finished one.

---

## 11. Frontend Dashboard

### Technology
- Pure HTML + CSS + Vanilla JavaScript
- No framework (no React, no Vue)
- Served as static files by FastAPI
- Updates every 5 seconds via `fetch()` calls to the API

### Pages / Tabs
1. **Upload** — Form to submit an STL file with name and email
2. **Queue** — Live list of all jobs with status badges and filter buttons
3. **Printers** — Live status cards showing temperature, progress, connection
4. **Logs** — Tail of the application log file

### Auto-Refresh
```javascript
setInterval(refreshQueue, 5000);    // Reload jobs every 5s
setInterval(refreshPrinters, 5000); // Reload printer status every 5s
setInterval(refreshLogs, 5000);     // Reload logs every 5s
```

### Job Status Colours
| Status | Colour | Meaning |
|---|---|---|
| PENDING | Grey | Waiting to be picked up |
| ANALYSING | Blue | Checking STL geometry |
| SLICING | Purple | OrcaSlicer is running |
| QUEUED | Yellow | Ready, waiting for idle printer |
| UPLOADING | Orange | Sending file to printer |
| PRINTING | Green | Currently printing |
| COMPLETED | Teal | Done ✅ |
| FAILED | Red | Error — see error message |

---

## 12. Email Notifications

BambuBabu sends emails at two points:
1. When a print **starts** (job transitions to PRINTING)
2. When a print **completes** (MQTT reports FINISH)
3. When a print **fails** on the printer

**SMTP Configuration:**
```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx  # Gmail App Password (not your login password)
```

**Gmail App Password:**
Gmail requires a special "App Password" for SMTP when 2FA is enabled:
Account → Security → 2-Step Verification → App passwords → Generate

**Email transport (STARTTLS):**
```python
with smtplib.SMTP(SMTP_HOST, 587) as server:
    server.starttls()  # Upgrade to TLS
    server.login(SMTP_USER, SMTP_PASSWORD)
    server.sendmail(FROM, TO, msg.as_string())
```

---

## 13. Deployment — Raspberry Pi Setup

### Complete Fresh Setup

```bash
# 1. Update system
sudo apt update && sudo apt upgrade -y

# 2. Clone repo
git clone https://github.com/Sanal-Sivakumar/BambuBabu.git
cd BambuBabu

# 3. Python virtual environment
python3 -m venv venv
source venv/bin/activate

# 4. Install Python dependencies
pip install -r requirements.txt

# 5. Configure
cp .env.example .env
nano .env

# 6. Install OrcaSlicer
sudo apt install -y xvfb libwebkit2gtk-4.1-0
sudo mkdir -p /opt/OrcaSlicer
sudo wget -O /opt/OrcaSlicer/OrcaSlicer.AppImage \
  "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage"
sudo chmod +x /opt/OrcaSlicer/OrcaSlicer.AppImage

# 7. Test
python -m backend.main
```

### Python Dependencies (`requirements.txt`)

```
fastapi==0.111.0          # Web framework
uvicorn[standard]==0.30.1 # ASGI server (uses uvloop on Linux)
sqlalchemy==2.0.30        # Database ORM
pydantic==2.7.1           # Data validation
pydantic-settings==2.3.0  # .env file loading
paho-mqtt==1.6.1          # MQTT client for printer communication
trimesh==4.3.2            # STL file loading and analysis
numpy>=1.24.0             # Numerical computing (used by trimesh)
python-multipart==0.0.9   # File upload support
python-dotenv==1.0.1      # Load .env file
```

**What was removed and why:**
- `scipy`: Required Fortran compiler (gfortran) to build from source on ARM64; not actually used
- `aiofiles`: Not used in the codebase

---

## 14. Systemd Service (Auto-start)

To make BambuBabu start automatically when the Pi boots:

**Create service file:**
```bash
sudo nano /etc/systemd/system/bambububu.service
```

```ini
[Unit]
Description=BambuBabu 3D Print Automation
After=network.target

[Service]
Type=simple
User=tinkerspace
WorkingDirectory=/home/tinkerspace/Documents/BambuBabu
Environment=PATH=/home/tinkerspace/Documents/BambuBabu/venv/bin
ExecStart=/home/tinkerspace/Documents/BambuBabu/venv/bin/python -m backend.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable bambububu
sudo systemctl start bambububu

# Check status
sudo systemctl status bambububu

# View logs
sudo journalctl -u bambububu -f
```

---

## 15. Configuration Reference

All settings loaded from `.env` file via `pydantic-settings`:

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `BambuBabu` | Application name |
| `DEBUG` | `false` | Enable debug logging |
| `HOST` | `0.0.0.0` | Listen on all interfaces |
| `PORT` | `8000` | Web server port |
| `P1S_IP` | — | P1S printer IP address |
| `P1S_SERIAL` | — | P1S serial number (on printer label) |
| `P1S_ACCESS_CODE` | — | P1S LAN access code (shown on screen) |
| `A1_MINI_IP` | — | A1 Mini IP address |
| `A1_MINI_SERIAL` | — | A1 Mini serial number |
| `A1_MINI_ACCESS_CODE` | — | A1 Mini access code |
| `MOCK_SLICER` | `false` | Skip real slicing (for testing) |
| `ORCA_SLICER_PATH` | `/usr/bin/OrcaSlicer` | Path to OrcaSlicer AppImage |
| `SMTP_HOST` | `smtp.gmail.com` | Email server |
| `SMTP_PORT` | `587` | Email server port (STARTTLS) |
| `SMTP_USER` | — | Gmail address |
| `SMTP_PASSWORD` | — | Gmail App Password |
| `ADMIN_EMAIL` | — | Admin notification email |
| `COMPLEXITY_THRESHOLD` | `50.0` | Score above which jobs go to P1S |
| `MAX_STL_SIZE_MB` | `100` | Reject STL files larger than this |
| `QUEUE_POLL_INTERVAL_SECONDS` | `10` | How often the queue processor checks for work |

---

## 16. API Reference

### Jobs

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/api/jobs?status=pending` | Filter by status |
| `POST` | `/api/jobs/upload` | Submit new STL (multipart form) |
| `GET` | `/api/jobs/{id}` | Get single job details |
| `DELETE` | `/api/jobs/{id}` | Cancel/delete a job |

### Printers

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/printers` | Get both printer states |
| `POST` | `/api/printers/{id}/plate-cleared` | Mark plate as cleared |

### Logs

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/logs/all?limit=80` | Get last N log lines |

### Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Server health check |

---

## 17. Data Flow — Full Job Lifecycle

```
1. UPLOAD
   User fills form → browser POST /api/jobs/upload
   → Server saves STL to backend/storage/uploads/<uuid>.stl
   → Creates Job record with status=PENDING
   → Returns job ID to browser

2. ANALYSIS (within 10s, in thread pool)
   QueueProcessor._tick() sees PENDING job
   → Calls _slice_pipeline(job_id) in executor
   → Job status → ANALYSING
   → trimesh.load(stl_path) loads the 3D mesh
   → Calculates: face_count, volume, bounding_box, complexity_score
   → select_printer() decides: A1_MINI or P1S
   → Saves analysis results to Job record

3. SLICING (1–10 minutes on Pi)
   Job status → SLICING
   → subprocess.run([xvfb-run, OrcaSlicer, --slice 0, --export-3mf, ...])
   → OrcaSlicer reads STL, generates layer-by-layer toolpaths
   → Outputs .3mf file to backend/storage/sliced/<uuid>.3mf
   → Parses estimated print time from OrcaSlicer stdout
   → Job status → QUEUED

4. UPLOAD TO PRINTER (when printer is idle + plate cleared)
   QueueProcessor._try_dispatch() checks printer state
   → Job status → UPLOADING
   → BambuFTP connects to printer:990 (implicit FTPS)
   → Sends all bytes via transfercmd()
   → Waits max 5s for "226" confirmation
   → File is now on printer SD card

5. PRINT START
   BambuPrinter.start_print() publishes MQTT message to printer
   → Printer receives command, shows confirmation on touchscreen
   → Printer starts: bed heating → levelling → printing
   → Job status → PRINTING

6. MONITORING
   Printer sends MQTT status every ~3 seconds
   → BambuPrinter._on_message() receives updates
   → Updates PrinterState in DB (progress %, temperatures)
   → Frontend polls /api/printers every 5s to show live data

7. COMPLETION
   MQTT message arrives with gcode_state="FINISH"
   → Job status → COMPLETED
   → plate_cleared set to False (must manually confirm plate removed)
   → Email sent to submitter
   → Admin email sent with print details

8. PLATE CLEARED (manual step)
   Admin removes finished print from bed
   → Clicks "Plate Cleared" in web UI
   → POST /api/printers/{id}/plate-cleared
   → plate_cleared = True
   → Next QUEUED job dispatched to this printer
```
