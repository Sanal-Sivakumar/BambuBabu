# 🐛 BambuBabu — Troubleshooting & Bug History

This document records every significant bug, error, and difficulty encountered during development and deployment of BambuBabu, along with the root cause analysis and solution. Updated continuously.

---

## Table of Contents

1. [pydantic-core build failure on Raspberry Pi ARM64](#1-pydantic-core-build-failure-on-raspberry-pi-arm64)
2. [scipy Fortran compiler error on Pi](#2-scipy-fortran-compiler-error-on-pi)
3. [ModuleNotFoundError: No module named 'fastapi'](#3-modulenotfounderror-no-module-named-fastapi)
4. [sqlalchemy greenlet_spawn error](#4-sqlalchemy-greenlet_spawn-error)
5. [Port 8000 already in use](#5-port-8000-already-in-use)
6. [GET /api/logs/all returns 404](#6-get-apilogsall-returns-404)
7. [FTP upload timeout — "The read operation timed out"](#7-ftp-upload-timeout--the-read-operation-timed-out)
8. [FTP PASV host mismatch](#8-ftp-pasv-host-mismatch)
9. [OrcaSlicer invalid CLI flag -g](#9-orcaslicer-invalid-cli-flag--g)
10. [OrcaSlicer --output flag not found](#10-orcaslicer---output-flag-not-found)
11. [OrcaSlicer: missing WebKitGTK 4.1 runtime](#11-orcaslicer-missing-webkitgtk-41-runtime)
12. [OrcaSlicer download 404 — wrong URL](#12-orcaslicer-download-404--wrong-url)
13. [Database model import names wrong](#13-database-model-import-names-wrong)
14. [GitHub clone fails with password authentication](#14-github-clone-fails-with-password-authentication)
15. [numpy.ndarray has no attribute 'ptp'](#15-numpyndarray-has-no-attribute-ptp)

---

## 1. pydantic-core build failure on Raspberry Pi ARM64

**Error:**
```
Building wheel for pydantic-core (pyproject.toml) ... error
Python reports SOABI: cpython-313-aarch64-linux-gnu
Computed rustc target triple: aarch64-unknown-linux-gnu
error: could not compile `pydantic-core`
```

**Root Cause:**
`pydantic-core` is written in Rust. Pip could not find a pre-built wheel for Python 3.13 on ARM64, so it attempted to compile from source. The Rust compiler was either not installed or failed during compilation.

**Fix:**
Install from piwheels (pre-compiled ARM wheels for Raspberry Pi):
```bash
pip install pydantic-core --index-url https://www.piwheels.org/simple
```
Or pin the version that has an available piwheels wheel:
```bash
pip install pydantic==2.7.1 pydantic-core==2.18.4
```

**Lesson:** Always check piwheels.org before trying to compile Rust/C extensions on the Pi.

---

## 2. scipy Fortran compiler error on Pi

**Error:**
```
error: metadata-generation-failed
scipy==1.13.1 requires Fortran compiler (gfortran)
```

**Root Cause:**
`scipy` was listed in `requirements.txt` but no pre-built wheel existed for Python 3.13 ARM64. Building from source requires `gfortran`, which was not installed.

**Fix:**
Removed `scipy` from `requirements.txt`. BambuBabu's complexity analysis uses `trimesh` + `numpy` for geometry operations, which don't require scipy.

```diff
- scipy==1.13.1
```

**Lesson:** Always audit dependencies. Only include packages that are directly used in code.

---

## 3. ModuleNotFoundError: No module named 'fastapi'

**Error:**
```
File "backend/main.py", line 6, in <module>
    from fastapi import FastAPI
ModuleNotFoundError: No module named 'fastapi'
```

**Root Cause:**
Virtual environment was not activated before running the server.

**Fix:**
```bash
source venv/bin/activate
python -m backend.main
```

**Lesson:** Always activate the venv first. The prompt changes to `(venv)` when active.

---

## 4. sqlalchemy greenlet_spawn error

**Error:**
```
from sqlalchemy.engine.events import Connection
ImportError: cannot import name 'AdaptedConnection'
```

**Root Cause:**
SQLAlchemy 2.0.30 wheel from piwheels was corrupted or incompatible with Python 3.13. The package installed but was missing internal modules.

**Fix:**
Reinstall SQLAlchemy forcing a clean download:
```bash
pip uninstall sqlalchemy -y
pip install sqlalchemy==2.0.30 --no-cache-dir
```

---

## 5. Port 8000 already in use

**Error:**
```
ERROR: [Errno 98] error while attempting to bind on address ('0.0.0.0', 8000): address already in use
```

**Root Cause:**
A previous BambuBabu process did not shut down cleanly (killed with Ctrl+Z instead of Ctrl+C, or server crashed mid-shutdown).

**Fix:**
```bash
sudo fuser -k 8000/tcp
sleep 1
python -m backend.main
```

**Prevention:** Always use Ctrl+C to stop the server. If using systemd, use `sudo systemctl stop bambububu`.

---

## 6. GET /api/logs/all returns 404

**Symptom:**
Browser console shows repeated:
```
GET /api/logs/all?limit=80 HTTP/1.1" 404 Not Found
```
Logs tab in UI shows error or is blank.

**Root Cause:**
The `logs.py` API router was never created. The frontend expected this endpoint but it didn't exist in the backend.

**Fix:**
Created `backend/api/logs.py` with a `GET /api/logs/all` endpoint that reads `logs/bambububu.log` and returns the last N lines.

Registered the router in `backend/main.py`:
```python
from backend.api import jobs, printers, logs
app.include_router(logs.router)
```

---

## 7. FTP upload timeout — "The read operation timed out"

**Symptom:**
Jobs go from `UPLOADING` → `FAILED` with error:
```
Print start error: The read operation timed out
```

**Root Cause (discovered after investigation):**
The FTP upload itself was **succeeding** — files were physically present on the printer's SD card (confirmed by listing via FTP). However, Bambu Lab printers sometimes do not send the "226 Transfer complete" FTP response after receiving the file. Python's `ftplib.storbinary()` waits indefinitely for this response and eventually hits the socket timeout.

**Evidence:** Files matching job UUIDs (15MB, 12MB) were visible on the printer's SD card even for "failed" jobs.

**Fix:**
Replaced `storbinary()` with `transfercmd()` to manually control the data socket. After sending all bytes and closing the data connection, we wait only 5 seconds for the "226" response, then proceed regardless:

```python
conn = ftp.transfercmd(f"STOR /{remote_filename}")
with open(local_path, "rb") as fp:
    while True:
        block = fp.read(32768)
        if not block:
            break
        conn.sendall(block)
conn.close()

# Wait max 5s for "226 Transfer complete" — don't fail if it doesn't come
ftp.sock.settimeout(5)
try:
    ftp.voidresp()
except Exception:
    pass  # Printer didn't send 226 — that's OK, file is there
```

**Status:** ✅ Fixed

---

## 8. FTP PASV host mismatch

**Symptom:**
FTP connects on port 990, logs in, but data channel hangs.

**Root Cause:**
In FTP passive mode (PASV), the server sends back its own IP address for the data connection. Bambu printers sometimes return a different internal IP than the one the client used to connect, causing the data channel to connect to an unreachable address.

**Fix:**
Created `BambuFTP` class that overrides `makepasv()` to always return the printer's known IP regardless of what the PASV response says:

```python
class BambuFTP(ftplib.FTP_TLS):
    def makepasv(self):
        _, port = super().makepasv()
        return self._force_host, port  # Always use the printer's real IP
```

---

## 9. OrcaSlicer invalid CLI flag -g

**Error:**
```
Invalid option --g
```

**Root Cause:**
The `-g` flag was inherited from PrusaSlicer's CLI (which embeds G-code in the 3MF). OrcaSlicer 2.4.x removed or never supported this flag.

**Fix:**
Removed `-g` from the command. OrcaSlicer 2.4.x automatically embeds G-code when using `--slice 0 --export-3mf`.

```diff
- "-g",
```

---

## 10. OrcaSlicer --output flag not found

**Error:**
```
setup params error
```
And no output file produced.

**Root Cause:**
Used `--output filename.3mf` but OrcaSlicer 2.4.x uses `--export-3mf filename.3mf` to export the sliced project as a 3MF file. The `--outputdir` flag only specifies a directory, not a full path.

**Fix:**
```diff
- "--output", str(output_file),
+ "--export-3mf", str(output_file),
```

---

## 11. OrcaSlicer: missing WebKitGTK 4.1 runtime

**Error:**
```
Error: missing host WebKitGTK 4.1 runtime libraries.
Install the distro package providing libwebkit2gtk-4.1.so.0
```

**Root Cause:**
OrcaSlicer's AppImage bundles most libraries but NOT WebKitGTK (it's too large). It requires the system to provide `libwebkit2gtk-4.1.so.0`. This was missing on Raspberry Pi OS Trixie.

**Fix:**
```bash
sudo apt install -y libwebkit2gtk-4.1-0
```
This installed 28.3MB of WebKitGTK and its dependencies (libharfbuzz-icu0, libhyphen0, libjavascriptcoregtk-4.1-0, libmanette-0.2-0, xdg-dbus-proxy).

---

## 12. OrcaSlicer download 404 — wrong URL

**Error:**
```
HTTP request sent, awaiting response... 404 Not Found
```

**Root Cause:**
The OrcaSlicer GitHub repository moved from `SoftFever/OrcaSlicer` to `OrcaSlicer/OrcaSlicer`. Also, the filename format changed between versions — v2.2.0 was used but the naming convention changed for v2.4.2 to include the Ubuntu version: `OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage`.

**Fix:**
Use the correct URL:
```bash
sudo wget -O /opt/OrcaSlicer/OrcaSlicer.AppImage \
  "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage"
```

**Lesson:** Always verify download URLs from the actual GitHub releases page rather than guessing filenames.

---

## 13. Database model import names wrong

**Error:**
```
ImportError: cannot import name 'PrintJob' from 'backend.db.models'
```

**Root Cause:**
The SQLAlchemy ORM models were assumed to be named `PrintJob`, `PrintJobLog`, `PrinterState` based on common naming conventions, but the actual model class names in `models.py` are `Job`, `LogEntry`, `PrinterState`.

**Correct imports:**
```python
from backend.db.models import Job, LogEntry, PrinterState
```

**Lesson:** Always check `models.py` for actual class names before writing scripts.

---

## 14. GitHub clone fails with password authentication

**Error:**
```
remote: Invalid username or token. Password authentication is not supported for Git operations.
```

**Root Cause:**
GitHub deprecated password authentication for Git operations in August 2021. HTTPS cloning now requires a Personal Access Token (PAT).

**Fix:**
Generate a PAT at GitHub → Settings → Developer settings → Personal access tokens, then use it as the password when cloning:
```bash
git clone https://github.com/Sanal-Sivakumar/BambuBabu.git
# Username: Sanal-Sivakumar
# Password: <paste PAT here>
```

**Alternative (permanent fix):** Use SSH keys:
```bash
ssh-keygen -t ed25519 -C "pi@bambubabu"
cat ~/.ssh/id_ed25519.pub  # Add this to GitHub → Settings → SSH Keys
git clone git@github.com:Sanal-Sivakumar/BambuBabu.git
```

---

## 15. numpy.ndarray has no attribute 'ptp'

**Error:**
```
STL analysis error: 'numpy.ndarray' has no attribute 'ptp'
```

**Root Cause:**
`numpy.ptp()` (peak-to-peak) was deprecated in NumPy 1.24 and removed in NumPy 2.0. The complexity analysis code used `mesh.bounds.ptp(axis=0)` to get bounding box dimensions.

**Fix:**
Replace `ptp()` with equivalent expression:
```python
# Old (broken with numpy 2.0+)
extents = mesh.bounds.ptp(axis=0)

# Fixed
extents = mesh.bounds[1] - mesh.bounds[0]
```

**Lesson:** When upgrading NumPy, check for deprecated API usage in any code using `.ptp()`, `.in1d()`, or `.cumproduct()`.

---

## Quick Reference — Common Commands

```bash
# Start server
source venv/bin/activate && python -m backend.main

# Kill stuck port
sudo fuser -k 8000/tcp

# Clear queue
python3 -c "
import sys; sys.path.insert(0, '.')
from backend.db.session import SessionLocal
from backend.db.models import Job, LogEntry, PrinterState
with SessionLocal() as db:
    db.query(LogEntry).delete(); db.query(Job).delete()
    [setattr(s, 'plate_cleared', True) or setattr(s, 'current_job_id', None)
     for s in db.query(PrinterState).all()]
    db.commit(); print('Done')
"

# Test FTP connection to A1 Mini
python3 -c "
import ftplib, ssl, socket
class T(ftplib.FTP_TLS):
    def __init__(self,h,c):
        super().__init__(); self._h=h; self.context=c; self._s=None
    @property
    def sock(self): return self._s
    @sock.setter
    def sock(self,v):
        if v and not isinstance(v,ssl.SSLSocket): v=self.context.wrap_socket(v,server_hostname=None)
        self._s=v
    def connect(self,host,port=990,timeout=15,source_address=None):
        self.host=host; self.port=port; self.timeout=timeout
        r=socket.create_connection((host,port),timeout)
        self.sock=r; self.af=r.family
        self.file=self.sock.makefile('r',encoding='utf-8')
        self.welcome=self.getresp(); return self.welcome
    def makepasv(self):
        _,p=super().makepasv(); return self._h,p
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
try: ctx.set_ciphers('DEFAULT@SECLEVEL=1')
except: pass
f=T('192.168.10.115',ctx); f.connect('192.168.10.115'); f.login('bblp','c9fd869a'); f.prot_p(); f.set_pasv(True); f.dir(); print('OK'); f.quit()
"

# Test OrcaSlicer slicing
xvfb-run --auto-servernum /opt/OrcaSlicer/OrcaSlicer.AppImage \
  --slice 0 --export-3mf /tmp/test.3mf /path/to/test.stl && echo "Slice OK"
```
