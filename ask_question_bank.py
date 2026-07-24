"""
Runs the full question bank (data/question_bank.py, ~100 questions,
basic -> advanced, across all 15 KB topics) through the instrumented
RAG pipeline. This exists to give the observability platform (traces,
metrics, evaluations) enough realistic volume and variety to actually
be interesting -- 3 test queries doesn't show much in a dashboard.

Also doubles as a large-scale retrieval quality check: each question
is tagged with its expected source doc, so this reports hit-rate
broken down by difficulty -- a good way to see whether retrieval holds
up as well on advanced/scenario-style questions as it does on basic
definition questions.

Run:
    python ask_question_bank.py
    python ask_question_bank.py --limit 20        # quick smoke test
    python ask_question_bank.py --difficulty basic
"""

import argparse
import sys
import time
from collections import defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from sentinel.collector.instrument import instrument_sut
instrument_sut()

from rag.pipeline import run_pipeline
from data.question_bank import QUESTION_BANK, TOPIC_TO_SOURCE_FILE


def run(limit: int | None = None, difficulty: str | None = None, start_idx: int = 1, verbose: bool = True):
    questions = QUESTION_BANK
    if difficulty:
        questions = [q for q in questions if q["difficulty"] == difficulty]
    
    total_len = len(questions)
    
    if start_idx > 1:
        questions = questions[start_idx - 1:]
    if limit:
        questions = questions[:limit]

    print(f"Running {len(questions)} questions (starting from index {start_idx}) through the pipeline...\n")

    stats_by_difficulty = defaultdict(lambda: {"total": 0, "hits": 0, "errors": 0, "latency_ms": []})
    results = []
    start = time.time()

    for i, item in enumerate(questions, start=start_idx):
        query = item["question"]
        difficulty_label = item["difficulty"]
        expected_source = TOPIC_TO_SOURCE_FILE[item["topic"]]

        # Sleep between requests to respect rate limits
        if i > start_idx:
            time.sleep(1.5)

        t0 = time.time()
        try:
            result = run_pipeline(query, k=3)
            latency_ms = (time.time() - t0) * 1000
            hit = expected_source in result["sources"]

            stats_by_difficulty[difficulty_label]["total"] += 1
            stats_by_difficulty[difficulty_label]["hits"] += int(hit)
            stats_by_difficulty[difficulty_label]["latency_ms"].append(latency_ms)

            marker = "✓" if hit else "✗"
            if verbose:
                print(f"[{i:>3}/{total_len}] {marker} [{difficulty_label:<12}] {query[:60]}")
            results.append({**item, "hit": hit, "trace_id": result["trace_id"], "latency_ms": latency_ms})

        except ValueError as e:
            stats_by_difficulty[difficulty_label]["total"] += 1
            stats_by_difficulty[difficulty_label]["errors"] += 1
            if verbose:
                print(f"[{i:>3}/{total_len}] ⚠ ERROR [{difficulty_label:<12}] {query[:60]} -- {e}")

    elapsed = time.time() - start

    print(f"\n{'=' * 70}")
    print(f"Done in {elapsed:.1f}s. Retrieval hit-rate by difficulty:")
    print(f"{'=' * 70}")
    for level in ["basic", "intermediate", "advanced"]:
        s = stats_by_difficulty.get(level)
        if not s or s["total"] == 0:
            continue
        hit_rate = s["hits"] / s["total"] * 100
        avg_latency = sum(s["latency_ms"]) / len(s["latency_ms"]) if s["latency_ms"] else 0
        print(f"  {level:<12} hit_rate={hit_rate:5.1f}%  ({s['hits']}/{s['total']})  "
              f"avg_latency={avg_latency:.1f}ms  errors={s['errors']}")

    overall_total = sum(s["total"] for s in stats_by_difficulty.values())
    overall_hits = sum(s["hits"] for s in stats_by_difficulty.values())
    print(f"\n  OVERALL hit_rate: {overall_hits / overall_total * 100:.1f}% ({overall_hits}/{overall_total})")
    print(f"\n  Traces written to sentinel_events.jsonl -- check the Streamlit dashboard tabs,")
    print(f"  or run `python -m sentinel.trace.sync` then `python -m sentinel.metrics.run_metrics`.")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N questions")
    parser.add_argument("--difficulty", choices=["basic", "intermediate", "advanced"], default=None)
    parser.add_argument("--start", type=int, default=1, help="Start index of questions to run (1-indexed)")
    args = parser.parse_args()

    run(limit=args.limit, difficulty=args.difficulty, start_idx=args.start)
