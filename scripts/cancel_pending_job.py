#!/usr/bin/env python3
"""Cancel a non-physical job while the BambuBabu service is stopped.

This is an operator recovery tool for jobs that have not begun upload. It never
contacts a printer and refuses statuses where a physical handoff may have begun.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.db import crud  # noqa: E402
from backend.db.models import JobStatus  # noqa: E402
from backend.db.session import SessionLocal  # noqa: E402


CANCELLABLE = {
    JobStatus.PENDING,
    JobStatus.ANALYSING,
    JobStatus.SLICING,
    JobStatus.QUEUED,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", help="UUID shown by the Jobs API or dashboard")
    args = parser.parse_args()
    try:
        job_id = str(uuid.UUID(args.job_id))
    except ValueError:
        parser.error("job_id must be a UUID")

    with SessionLocal.begin() as db:
        job = crud.get_job(db, job_id)
        if job is None:
            print("Job not found.", file=sys.stderr)
            return 2
        if job.status not in CANCELLABLE:
            print(
                f"Refusing to cancel job in '{job.status.value}' state: "
                "it may have reached a printer. Inspect the physical printer first.",
                file=sys.stderr,
            )
            return 3
        crud.transition_job_status(db, job_id, job.status, JobStatus.CANCELLED)
        crud.add_log(
            db,
            "JOB_CANCELLED",
            "Cancelled by offline operator recovery tool before printer handoff",
            job_id=job_id,
        )
    print(f"Cancelled non-physical job {job_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
