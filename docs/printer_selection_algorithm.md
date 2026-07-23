# Printer selection and fallback specification

Last reconciled with `backend/core/complexity.py` and `backend/core/queue_processor.py`: 2026-07-23.

## Goal

Select a safe preferred printer from measured STL geometry, then use availability-aware fallback without ever sending printer-specific sliced output to the wrong machine.

The selection result is a preference, not a license to dispatch. Printer slot, live state, plate clearance, and confirmed start invariants still apply.

## Inputs

`analyse_stl()` loads the STL through trimesh and returns:

| Metric | Type | Use |
|---|---|---|
| `face_count` | integer | geometry/triangulation complexity |
| `volume_cm3` | positive float | model scale contribution |
| `overhang_ratio` | 0-1 float | downward-facing face contribution |
| `bbox.x/y/z` | millimetres | hard build-volume fit |
| `complexity_score` | 0-100 float | preferred-printer threshold |

STL units are assumed to be millimetres. Volume is absolute mesh volume divided by 1000. Bounding dimensions are the peak-to-peak values of the axis-aligned mesh bounds.

Overhang faces have a Z normal below `-0.707`, corresponding to a downward angle greater than approximately 45 degrees. The ratio counts faces, not their surface areas.

## Complexity formula

```text
face_score     = min(face_count / 500000, 1.0) * 100
overhang_score = overhang_ratio * 100
volume_score   = min(volume_cm3 / 300, 1.0) * 100

complexity_score =
    0.40 * face_score
  + 0.40 * overhang_score
  + 0.20 * volume_score
```

Default threshold:

```text
COMPLEXITY_THRESHOLD=50
```

A score strictly greater than the threshold prefers P1S. A score equal to or below the threshold prefers A1 Mini when it fits.

## Build-volume rules

Configured defaults:

| Printer | X | Y | Z |
|---|---:|---:|---:|
| A1 Mini | 180 mm | 180 mm | 180 mm |
| P1S | 256 mm | 256 mm | 256 mm |

Environment names are `A1_MINI_MAX_X`, `A1_MINI_MAX_Y`, `A1_MINI_MAX_Z`, `P1S_MAX_X`, `P1S_MAX_Y`, and `P1S_MAX_Z`.

The hard routing decision is:

```text
if bbox exceeds P1S on any axis:
    reject
else if bbox exceeds A1 Mini on any axis:
    prefer P1S
else if complexity_score > COMPLEXITY_THRESHOLD:
    prefer P1S
else:
    prefer A1 Mini
```

No automatic rotation search occurs. A model that would fit only after reorientation can be routed differently or rejected based on its uploaded orientation.

## Preferred-printer queue order

Each printer selects only `queued` jobs currently assigned to it. Ordering is:

1. smallest non-null `estimated_minutes`;
2. submission time.

Jobs without an estimate sort after estimated jobs in SQLite. This is shortest-job-first within a printer, with age as the tie-breaker. It is not global FIFO.

## Dispatch prerequisites

A preferred job does not dispatch unless durable printer state satisfies all of:

```text
status == idle
plate_cleared == true
current_job_id is null
```

The queue then atomically changes the job from `queued` to `uploading` and assigns `current_job_id` in the same transaction. If another worker changed the job first, dispatch loses the compare-and-swap and does nothing.

The live printer client separately requires MQTT connectivity and current status `idle` before publishing a start command.

## Cross-printer fallback

Fallback is evaluated when a target printer has no directly assigned job to dispatch.

A queued job assigned to the other printer is eligible only if:

1. the target is idle, cleared, and unowned;
2. the preferred/source printer is not idle, cleared, and unowned;
3. all stored bounding-box axes are present;
4. `can_fit_on_printer(bbox, target)` is true;
5. no slicing worker has already reserved that job;
6. the job atomically transitions from `queued` to `slicing`.

Eligible fallback candidates use the same estimated-time/submission ordering.

### Mandatory re-slice

Fallback always starts from the original STL and calls `slice_stl(stl_path, target, ...)`. Output contains the target printer suffix:

```text
<job-uuid>-p1s.3mf
<job-uuid>-a1_mini.3mf
```

Only after target slicing succeeds does the transaction update `assigned_printer`, store the new path/estimate, and return the job to `queued`. The next queue pass performs normal target dispatch.

If fallback slicing fails, the job returns to `queued` with its original assignment and logs `FALLBACK_ABORTED`. It does not become failed merely because opportunistic fallback was unavailable.

### No-steal rule

If the preferred printer is currently idle, cleared, and unowned, fallback is not allowed even if the other printer is also idle. This avoids needless re-slicing and respects the geometry/complexity preference.

## Examples

### Small, simple model

```text
bbox: 70 x 40 x 25 mm
faces: 40000
volume: 18 cm3
overhang ratio: 0.05
score: below 50
```

Preferred printer: A1 Mini.

### Small but complex model

```text
bbox: 120 x 100 x 90 mm
score: 63
```

Preferred printer: P1S because the score is strictly above the threshold.

### Too large for A1 Mini

```text
bbox: 210 x 150 x 90 mm
score: 15
```

Preferred printer: P1S because X exceeds 180 mm.

### Too large for both

```text
bbox: 270 x 100 x 80 mm
```

Result: rejected before slicing because X exceeds the P1S limit.

### Safe fallback

```text
preferred: A1 Mini
A1 Mini: offline or occupied
P1S: idle, plate clear, no current job
model: fits P1S
```

Result: reserve the job, re-slice with P1S presets, assign P1S, queue, then dispatch normally.

### Fallback prohibited

```text
preferred: P1S
P1S: idle and clear
A1 Mini: idle and clear
```

Result: no fallback. P1S keeps the job.

## Safety interaction with start confirmation

Routing completion only means a target-specific file is ready. The job still passes through:

```text
queued -> uploading -> starting -> printing
```

`printing` requires an authoritative newer MQTT report showing `PREPARE` or `RUNNING`. A publish failure fails closed. An acknowledged command without physical confirmation becomes `attention` and blocks the selected printer. Fallback never retries an ambiguous start on the other printer.

## Tested properties

Automated tests assert that:

- a busy preferred printer permits fit-safe fallback;
- an available preferred printer prevents fallback stealing;
- fallback calls the slicer with the target printer;
- successful fallback updates assignment and returns to `queued`;
- printer handoff failure cannot become `printing`;
- ambiguous start retains printer ownership and plate blocking;
- atomic transitions prevent cancellation or competing workers from reviving/stealing a job.

## Known limitations and future tuning

- Axis-aligned bounds do not optimize model orientation.
- Face-count overhang ratio is affected by tessellation density; surface-area weighting would be more stable.
- The formula does not include material, supports generated by Orca, nozzle choice, filament availability, maintenance state, or energy cost.
- Shortest-job-first can delay a long job under sustained small-job load; an aging/fairness term may be needed with production traffic.
- Build-volume maxima do not include configurable safety margins or exclusion zones.
- Real duration is parsed from Orca output when available; a missing estimate sorts after estimated jobs.

Any formula or threshold change must update this document and add tests for boundary values, especially exact threshold equality and exact build-volume fits.
