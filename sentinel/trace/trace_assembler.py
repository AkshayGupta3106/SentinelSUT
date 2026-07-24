"""
Trace Assembler.

Takes the flat stream of StageEvents (currently living in
sentinel_events.jsonl) and stitches each trace_id's events into one
Trace record with derived fields: total latency, slowest stage, error
stage, stage count. This is the "distributed tracing" piece -- same
concept as Jaeger/Zipkin's trace assembly, just scoped to one process
instead of a real distributed system.
"""

import json
import os
from collections import defaultdict
from datetime import datetime

from .models import Trace, TraceEvent
from .db import get_session, init_db
from ..config import EVENTS_PATH


def _load_raw_events(path: str = EVENTS_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TraceAssembler:
    """
    Groups raw StageEvent dicts by trace_id and computes derived
    per-trace fields. Stateless -- call assemble() with whatever
    events you have (from the JSONL log, or eventually a Kafka/queue
    consumer in a real deployment).
    """

    def assemble(self, raw_events: list[dict]) -> list[dict]:
        by_trace: dict[str, list[dict]] = defaultdict(list)
        for e in raw_events:
            by_trace[e["trace_id"]].append(e)

        assembled = []
        for trace_id, events in by_trace.items():
            events.sort(key=lambda e: e["timestamp_start"])

            total_latency = sum(e["latency_ms"] for e in events)
            slowest = max(events, key=lambda e: e["latency_ms"])
            error_events = [e for e in events if e["status"] == "error"]

            assembled.append({
                "trace_id": trace_id,
                "query_id": events[0].get("query_id"),
                "started_at": _parse_ts(events[0]["timestamp_start"]),
                "ended_at": _parse_ts(events[-1]["timestamp_end"]),
                "total_latency_ms": round(total_latency, 3),
                "num_stages": len(events),
                "slowest_stage": slowest["stage_name"],
                "slowest_stage_latency_ms": slowest["latency_ms"],
                "has_error": len(error_events) > 0,
                "error_stage": error_events[0]["stage_name"] if error_events else None,
                "events": events,
            })

        return assembled

    def persist(self, assembled_traces: list[dict]) -> int:
        """
        Writes assembled traces to the DB. Idempotent: traces already
        present (by trace_id) are skipped, not duplicated or updated --
        safe to re-run against a growing JSONL file.
        """
        init_db()
        session = get_session()
        written = 0

        try:
            existing_ids = {row[0] for row in session.query(Trace.trace_id).all()}

            for t in assembled_traces:
                if t["trace_id"] in existing_ids:
                    continue

                trace_row = Trace(
                    trace_id=t["trace_id"],
                    query_id=t["query_id"],
                    started_at=t["started_at"],
                    ended_at=t["ended_at"],
                    total_latency_ms=t["total_latency_ms"],
                    num_stages=t["num_stages"],
                    slowest_stage=t["slowest_stage"],
                    slowest_stage_latency_ms=t["slowest_stage_latency_ms"],
                    has_error=t["has_error"],
                    error_stage=t["error_stage"],
                )
                session.add(trace_row)

                for i, e in enumerate(t["events"]):
                    session.add(TraceEvent(
                        event_id=e["event_id"],
                        trace_id=t["trace_id"],
                        stage_name=e["stage_name"],
                        stage_order=i,
                        timestamp_start=_parse_ts(e["timestamp_start"]),
                        timestamp_end=_parse_ts(e["timestamp_end"]),
                        latency_ms=e["latency_ms"],
                        status=e["status"],
                        error=e.get("error"),
                        input_summary=e.get("input_summary"),
                        output_summary=e.get("output_summary"),
                        event_metadata=e.get("metadata"),
                    ))

                written += 1

            session.commit()
        finally:
            session.close()

        return written


def sync_events_to_db(events_path: str = EVENTS_PATH) -> int:
    """
    Convenience entrypoint: read the JSONL log, assemble, persist.
    Returns the number of newly-written traces.
    """
    raw_events = _load_raw_events(events_path)
    if not raw_events:
        return 0

    assembler = TraceAssembler()
    assembled = assembler.assemble(raw_events)
    return assembler.persist(assembled)
