"""
Section 4 deliverable: assemble events from sentinel_events.jsonl into
the traces/trace_events DB tables, and prove it worked.

Run:
    python -m sentinel.trace.sync
"""

import sys
from sqlalchemy import select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from .db import get_session, init_db
from .models import Trace, TraceEvent
from .trace_assembler import sync_events_to_db


def main():
    written = sync_events_to_db()
    print(f"Synced {written} new trace(s) to the database.\n")

    init_db()
    session = get_session()
    try:
        traces = session.execute(select(Trace).order_by(Trace.started_at.desc())).scalars().all()
        print(f"Total traces in DB: {len(traces)}\n")

        for t in traces:
            status = "❌ error" if t.has_error else "✅ success"
            print(f"trace_id={t.trace_id[:8]}...  stages={t.num_stages}  "
                  f"total={t.total_latency_ms:.2f}ms  slowest={t.slowest_stage} "
                  f"({t.slowest_stage_latency_ms:.2f}ms)  {status}")

            events = session.execute(
                select(TraceEvent)
                .where(TraceEvent.trace_id == t.trace_id)
                .order_by(TraceEvent.stage_order)
            ).scalars().all()

            for e in events:
                marker = "  ✓" if e.status == "success" else "  ✗"
                print(f"{marker} {e.stage_name:<22} {e.latency_ms:>8.2f} ms")
            print()
    finally:
        session.close()


if __name__ == "__main__":
    main()
