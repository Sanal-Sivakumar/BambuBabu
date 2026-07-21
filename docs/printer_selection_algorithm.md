# 🖨️ Printer Selection Algorithm

> **System**: Bambu Lab P1S + A1 Mini — Automated Print Routing
> **Default Printer**: A1 Mini (lower power consumption)
> **Queue Strategy**: Smallest job first

---

## Overview

Every uploaded STL file goes through a 6-step decision pipeline before being assigned to a printer or queued. The algorithm balances three priorities:

1. **Physical fit** — does the object physically fit on the printer?
2. **Complexity** — is the job too complex for the A1 Mini?
3. **Efficiency** — shortest jobs go first in the queue

---

## Step 1 — Analyse the STL File

Using the `trimesh` Python library, extract 4 metrics from the uploaded STL:

| Metric | Description |
|---|---|
| `face_count` | Total number of triangles in the mesh |
| `volume_cm3` | Total volume of the object in cubic centimetres |
| `overhang_ratio` | Fraction of faces with a downward normal > 45° (require supports) |
| `bounding_box` | Physical dimensions of the object: X mm × Y mm × Z mm |

---

## Step 2 — Compute Complexity Score (0–100)

Each metric is normalised to a 0–100 scale, then combined with weights:

```
face_score     = min(face_count / 500,000 , 1.0) × 100
overhang_score = overhang_ratio × 100
volume_score   = min(volume_cm3 / 300     , 1.0) × 100

complexity_score = (face_score     × 0.40)
                 + (overhang_score  × 0.40)
                 + (volume_score    × 0.20)
```

### Example Scores

| Object | Face Count | Overhangs | Volume | Score | Label |
|---|---|---|---|---|---|
| Simple box | 12 | 0% | 10 cm³ | **2** | Simple |
| Phone stand | 80,000 | 20% | 50 cm³ | **14** | Simple |
| Mechanical part | 300,000 | 55% | 120 cm³ | **46** | Simple |
| Complex sculpture | 700,000 | 70% | 280 cm³ | **76** | Complex ✦ |
| Lattice structure | 1,000,000+ | 80% | 400 cm³ | **98** | Complex ✦ |

> **Threshold**: Score > 50 → P1S | Score ≤ 50 → A1 Mini

---

## Step 3 — Hard Physical Constraint (Size Check)

Build volumes:

| Printer | Max X | Max Y | Max Z |
|---|---|---|---|
| **A1 Mini** | 180 mm | 180 mm | 180 mm |
| **P1S** | 256 mm | 256 mm | 256 mm |

```
IF bounding_box exceeds P1S max in ANY dimension:
    → REJECT job
    → Email user: "Object too large for both printers"

ELSE IF bounding_box exceeds A1_MINI max in ANY dimension:
    → FORCE assign to P1S
    → Skip complexity score (no choice)
```

---

## Step 4 — Complexity-Based Printer Assignment

```
COMPLEXITY_THRESHOLD = 50  (configurable)

IF complexity_score > COMPLEXITY_THRESHOLD:
    preferred_printer = P1S        ← complex/detailed job

ELSE:
    preferred_printer = A1 Mini    ← default, lower power consumption
```

---

## Step 5 — Availability Check & Fallback

```
IF preferred_printer is IDLE:
    → Assign directly ✓

ELSE IF preferred_printer is BUSY:

    other_printer = the other one

    IF other_printer is IDLE AND job fits on other_printer:
        → Assign to other_printer ✓
        → Re-slice with other printer's profile

    ELSE:
        → Add to preferred_printer's QUEUE
```

---

## Step 6 — Queue Ordering (Smallest Job First)

When a printer becomes free, the next job is picked from the queue using this sort order:

```
PRIMARY   → estimated_print_time_minutes   (ASCENDING — shortest first)
SECONDARY → submitted_at                   (ASCENDING — FIFO for equal sizes)
```

> Short jobs clear the queue faster, keeping the printer productive.
> For two jobs of identical print time, the earlier submission wins.

---

## Complete Decision Flow

```
STL Uploaded
      │
      ▼
 [ANALYSE STL]
  face_count, volume, overhang_ratio, bounding_box
      │
      ▼
 [SIZE CHECK] ─── Exceeds P1S max? ──────────────────► REJECT ❌
      │                                                 Email user
      │
      ├── Exceeds A1 Mini max? ──────────────────────► FORCE P1S
      │                                                 (skip scoring)
      │
      ▼
 [SCORE 0–100]
      │
      ├── score ≤ 50 ──► preferred = A1 Mini  (default)
      └── score > 50 ──► preferred = P1S      (complex)
      │
      ▼
 [AVAILABILITY CHECK]
      │
      ├── preferred IDLE ─────────────────────────────► ASSIGN ✓
      │
      └── preferred BUSY
               │
               ├── other printer IDLE + job fits? ────► ASSIGN other ✓
               │                                        Re-slice for new profile
               │
               └── both BUSY or doesn't fit
                           │
                           ▼
                    [ADD TO QUEUE]
                    Sort: shortest print time first
                          then: earliest submission time
```

---

## Decision Summary Table

| Condition | Result |
|---|---|
| Object too big for P1S | ❌ Rejected — email user |
| Object too big for A1 Mini only | ✅ Forced to P1S |
| Score ≤ 50, A1 Mini free | ✅ A1 Mini (default) |
| Score ≤ 50, A1 Mini busy → P1S free + fits | ✅ Fallback to P1S |
| Score > 50, P1S free | ✅ P1S |
| Score > 50, P1S busy → A1 Mini free + fits | ✅ Fallback to A1 Mini |
| Both printers busy | ⏳ Queued (shortest job first) |

---

## Configurable Parameters

All thresholds are set in the `.env` file and can be tuned without code changes:

```env
# Complexity
COMPLEXITY_THRESHOLD=50        # 0–100. Above this value → P1S

# File limits
MAX_STL_SIZE_MB=100            # Reject files larger than this

# A1 Mini build volume (mm)
A1_MINI_MAX_X_MM=180
A1_MINI_MAX_Y_MM=180
A1_MINI_MAX_Z_MM=180

# P1S build volume (mm)
P1S_MAX_X_MM=256
P1S_MAX_Y_MM=256
P1S_MAX_Z_MM=256

# Queue
MAX_CONCURRENT_JOBS=2          # MVP limit
```

---

## Complexity Score Weights — Rationale

| Metric | Weight | Reason |
|---|---|---|
| **Face count** | 40% | Directly reflects geometric detail and mesh density |
| **Overhang ratio** | 40% | Overhangs require supports → longer print, higher failure risk → P1S is more reliable |
| **Volume** | 20% | Large objects consume more material but are not always complex |

> These weights are tunable based on real-world print data over time.

---

*Last updated: July 2026*
*Part of the 3D Print Automation System — Raspberry Pi 5 + Bambu Lab*
