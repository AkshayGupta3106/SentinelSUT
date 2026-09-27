# 🛡️ Sentinel AI

**An AI observability, evaluation, and root-cause-analysis platform — built around a RAG chatbot that exists purely to be watched.**

> The chatbot is 20% of this project. The platform watching it is 80%.

Most portfolio RAG projects stop at "I built a chatbot." This one asks a different question: **how would you actually know if an AI system quietly got worse in production, and why?** Everything here — 8-stage distributed tracing, LLM-as-judge evaluation, statistical regression detection, and a rule-based root-cause-analysis engine — exists to answer that.

---

## Table of Contents

- [The pitch](#the-pitch)
- [Architecture](#architecture)
- [What's actually built](#whats-actually-built-11-sections)
- [Quickstart](#quickstart)
- [Walkthrough by dashboard tab](#walkthrough-by-dashboard-tab)
- [Real engineering findings](#real-engineering-findings-not-a-highlight-reel---actual-bugs)
- [Honest design tradeoffs](#honest-design-tradeoffs)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)
- [What's next](#whats-next)

---

## The pitch

> Designed and built an internal AI observability platform for a RAG system — non-invasive distributed tracing via runtime instrumentation, LLM-as-judge + heuristic evaluation with judge calibration, statistical regression detection (threshold/z-score/trend), and a rule-based root-cause-analysis engine that correctly attributed a real injected latency regression to its exact source stage with high confidence.

Every claim in that sentence is backed by something you can actually run — not a diagram, a running system.

---

## Architecture

```
                              User
                                │
                                ▼
                      Existing RAG Chatbot  (rag/)  ← the System Under Test
─────────────────────────────────────────────────────────────────────────
 Query → Validation → Embedding → ChromaDB Retrieval → Top-k Ranking
   → Context Builder → Prompt Builder → Gemini → Response Parser → Answer
─────────────────────────────────────────────────────────────────────────

                    Sentinel AI Platform  (sentinel/)
                    watches the SUT from OUTSIDE — zero
                    lines inside rag/ import sentinel/

              Event Collector  (runtime monkey-patching,
                    same technique real APM agents use)
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
         Trace Collector   Evaluation Engine  Metrics Collector
         (waterfalls,      (LLM-judge +       (p50/p95/p99,
          DB persistence)   heuristics,        per-stage latency,
                             calibrated)        error rate)
                │               │                │
                └───────────────┼────────────────┘
                                ▼
                   Regression Detection Engine
                (threshold + z-score + trend methods)
                                │
                                ▼
                    Root Cause Analysis Engine
              (rule-based attribution + optional LLM polish)
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                ▼
          HTML Report    SQLite/Postgres    Discord Alerts
                                │
                                ▼
                     Power BI Export Layer
              (CSV/Parquet + bridge tables + build guide)
```

---

## What's actually built (11 sections)

| # | Section | Status | Proof |
|---|---|---|---|
| 1–2 | RAG chatbot (System Under Test) | ✅ | 8 independently-traceable stages, works with or without a live API key |
| 3 | Event Collector | ✅ | Async, non-blocking, `trace_id`-threaded via `contextvars`, zero lines changed inside `rag/` |
| 4 | Trace Collector | ✅ | SQLAlchemy-backed trace assembly, idempotent DB sync |
| 5 | Metrics Collector | ✅ | p50/p95/p99 rollups over 5-min/1-hour/1-day windows |
| 6 | Evaluation Engine | ✅ | Heuristic + real LLM-as-judge scoring, calibrated against known good/bad answers, caching by `(trace_id, evaluator)` |
| 7 | Regression Detection | ✅ | Threshold, z-score, and trend-based methods — proven against a real injected regression |
| 8 | Root Cause Analysis | ✅ | Rule-based attribution correctly diagnosed the injected regression's exact source stage, `high` confidence |
| 9 | Persistence Layer | ✅ | Auto-generated PostgreSQL DDL + ER docs, centralized read repository |
| 10 | Reporting & Alerting | ✅ | Self-contained HTML report, Discord webhook alerts with real dedup logic |
| 11 | Power BI Export | ✅ | Real CSV/Parquet export incl. many-to-many bridge table + full build guide |
| 12 | Productionization | 🔜 | Tests, CI, Docker, case study — not yet built |

Every ✅ above was actually run and its output inspected — not just written and assumed correct. See [Real engineering findings](#real-engineering-findings-not-a-highlight-reel---actual-bugs) for the bugs that surfaced along the way.

---

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env                 # optional: paste a real GEMINI_API_KEY

python data/ingest.py                # build the 15-topic knowledge base (88 chunks)
python ask_question_bank.py --limit 30   # generate realistic traffic (99 Qs available)
python -m sentinel.trace.sync        # assemble traces into the DB
python -m sentinel.metrics.run_metrics
python -m sentinel.regression.seed_demo_metrics   # synthetic history for regression demo
python -m sentinel.regression.run_regression
python -m sentinel.rca.run_rca

streamlit run streamlit_app.py       # the full platform, 7 tabs
```

Works **fully offline, no API key required** — every external call (Gemini generation, LLM judges, Discord alerts) fails soft with a clearly tagged fallback instead of crashing. Add a real `GEMINI_API_KEY` any time and everything upgrades automatically, no code changes needed.

---

## Walkthrough by dashboard tab

**💬 Chat** — the actual RAG chatbot. Answers ML/DS interview questions from a 15-topic, 88-chunk knowledge base. Every answer shows sources, chunk counts, `trace_id`, and — live — its evaluation scores.

**📊 Sentinel Dashboard** — KPIs (total traces, avg latency, error rate), stage-latency bar chart, recent traces table, and a per-trace waterfall chart.

**📈 Metrics** — p50/p95/p99 trace latency, error rate, and per-stage average latency as real time series, computed by the Metrics Collector.

**🧪 Evaluation** — judge calibration report (proves the evaluators actually separate good answers from bad), retrieval hit-rate against a 10-question golden set, and a live table of every evaluation run so far.

**🚨 Regression** — seed synthetic history (7 stable days + 1 injected regression) and watch threshold/z-score/trend detection catch it, with flagged points highlighted directly on the metric charts.

**🔍 RCA** — root cause reports as confidence-color-coded cards, each citing the exact regressions used as evidence.

**📄 Reports** — generate a self-contained offline HTML report, trigger Discord alerts, inspect the alert log, and export everything for Power BI.

---

## Real engineering findings (not a highlight reel — actual bugs)

This section exists because "I built X" is a weaker claim than "I built X, broke it, found out why, and fixed it." Every item below actually happened during development of this project, not written in hindsight:

- **Daemon thread silently dropped events on exit.** The async event writer used a daemon thread; on process exit it could get killed mid-drain. Caught with a real test: 17 events emitted, only 10 landed on disk. Fixed with a synchronous drain-on-`stop()` + `atexit` hook. Re-verified: all 17 landed.
- **SQLite silently drops timezone-awareness on round-trip.** A timestamp written as tz-aware came back naive on read, crashing the metrics window-bucketing math with a `TypeError`. Normalized all timestamps to naive UTC consistently as the fix — and documented *why* Postgres wouldn't have this problem.
- **LLM prompt bug caught from real usage, not my own testing.** The RCA polish prompt said "rewrite as 2-3 sentences" but never said "give me exactly one version" — so Gemini, being genuinely helpful, returned multiple labeled options instead of a final answer. Fixed by explicitly forbidding multiple options/preamble in the prompt. Also added a `force=True` regeneration path since the buggy outputs were already persisted (regeneration is idempotent by design, which meant a naive re-run wouldn't have fixed anything).
- **Stale model name caught before it could bite.** Defaulted to `gemini-2.5-flash`; a web search confirmed the current model as of this build is `gemini-3.5-flash`. Fixed in all 3 call sites before the user ever tested with a real key.
- **Deprecated SDK swapped proactively.** `google-generativeai` threw a `FutureWarning` mid-development; swapped to the current `google-genai` SDK across the whole codebase before shipping.
- **A genuine retrieval failure, root-caused to the token level.** The query *"Why does dropout help prevent overfitting?"* missed its expected document entirely in the top-6 results. Investigation traced it to exactly one shared word (`dropout`) between the query and the correct chunk after stopword removal — the outcome-language framing ("prevent overfitting") shares almost no vocabulary with the mechanism-language source text ("randomly zeroes out neurons"). Confirmed this is a direct, expected consequence of using a lexical `HashingVectorizer` instead of a real semantic embedding model — a documented tradeoff from day one, now backed by a concrete reproducible example.
- **Discord alerting tested against a genuine network failure, not a mock.** A fake webhook URL actually reached Discord's real servers and returned a real `403 Forbidden`. Confirmed the code caught it, logged the exact error to the DB, and didn't crash the report pipeline — then separately verified that *failed* sends correctly retry while *successful* sends are the only thing deduplicated.

---

## Honest design tradeoffs

Every non-obvious decision below was a deliberate tradeoff, not an oversight — documented here and in the code itself:

| Decision | Why | Tradeoff |
|---|---|---|
| `HashingVectorizer` instead of a downloaded embedding model | Zero network calls, zero model downloads, fully offline-capable | Lexical overlap only, not real semantic similarity (see the dropout case study above) |
| SQLite instead of live Postgres | No Postgres server available in the dev sandbox | Schema is generated as real PostgreSQL DDL and dialect-agnostic via SQLAlchemy — swapping `DATABASE_URL` should work, but has only been runtime-tested against SQLite |
| Runtime monkey-patching instead of decorators inside `rag/` | Proves the SUT has zero dependency on the observability layer — delete `sentinel/` and the chatbot still works, unmodified | Slightly less obvious than a `@trace_stage` decorator directly in the pipeline code |
| No `.pbix` file | Power BI Desktop isn't available in a code sandbox — faking one would just be lying | Real CSV/Parquet export + a step-by-step build guide instead (`docs/POWERBI_GUIDE.md`) |
| Separate LLM client for judging vs. generation | The model grading an answer shouldn't be code-path-identical to the model that wrote it | Marginal extra complexity for a real self-preference-bias mitigation |

---

## Project structure

```
sentinel_sut_chatbot/
├── rag/                        # System Under Test — the RAG chatbot itself
│   ├── query_validator.py      # Stage 1
│   ├── embeddings.py           # Stage 2
│   ├── retriever.py            # Stage 3
│   ├── ranker.py                # Stage 4
│   ├── context_builder.py      # Stage 5
│   ├── prompt_builder.py       # Stage 6
│   ├── generator.py            # Stage 7 (Gemini, fails soft)
│   ├── response_parser.py      # Stage 8
│   └── pipeline.py             # Chains all 8 stages
│
├── sentinel/                   # The observability platform — watches rag/ from outside
│   ├── collector/               # Section 3: event collection via monkey-patching
│   ├── trace/                   # Section 4: trace assembly + DB models
│   ├── metrics/                 # Section 5: time-windowed rollups
│   ├── evaluation/              # Section 6: heuristic + LLM-judge evaluators
│   ├── regression/              # Section 7: threshold/z-score/trend detection
│   ├── rca/                     # Section 8: rule-based root cause analysis
│   ├── alerting/                # Section 10: Discord webhooks
│   ├── reporting/               # Section 10: HTML reports
│   ├── export/                  # Section 11: Power BI CSV/Parquet export
│   ├── repository.py            # Section 9: centralized read layer
│   └── export_schema.py         # Section 9: auto-generated schema docs
│
├── data/
│   ├── docs/                    # 15-topic knowledge base (88 chunks)
│   └── question_bank.py         # 99 questions, basic → advanced, all 15 topics
│
├── docs/
│   ├── schema.sql                # Auto-generated PostgreSQL DDL
│   ├── er_overview.md            # Auto-generated schema docs
│   └── POWERBI_GUIDE.md          # Step-by-step Power BI build guide
│
├── streamlit_app.py             # 7-tab dashboard: Chat/Dashboard/Metrics/Eval/Regression/RCA/Reports
├── main.py                      # FastAPI alternative to Streamlit
├── ask_question_bank.py         # Bulk traffic generator + retrieval hit-rate report
└── verify_gemini_setup.py       # Standalone real-API-key sanity check
```

---

## Known limitations

- Retrieval uses lexical hashing, not real semantic embeddings (see the dropout case study) — swappable, not fixed
- Regression/RCA demos rely on seeded synthetic history since one dev session doesn't produce enough real time-series spread — this is intentional, matching how the original design doc called for "a synthetic test: intentionally degrade a param, prove the engine flags it"
- Runtime-tested against SQLite only; Postgres compatibility is dialect-correct but unverified
- No `.pbix` file — export layer + guide only
- Discord alerting verified against real HTTP errors, not a confirmed successful send (no real webhook was available during development)

