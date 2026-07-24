"""
Root Cause Analysis Engine.

Takes whatever regressions Section 7 flagged for a given window and
narrows down WHERE the problem likely originates using a small
rule-based diagnosis layer. The rules ARE the reasoning -- the
optional Gemini call afterward only rewrites the rule-based finding
into plainer English; it's not asked to do the diagnosis itself. If
Gemini is unavailable, the rule-based diagnosis stands on its own,
unpolished but still correct, exactly like the rest of this codebase's
fail-soft pattern.
"""

import os
from datetime import datetime

from sqlalchemy import select
from google import genai
from groq import Groq

from ..trace.db import get_session, init_db
from ..regression.models import Regression
from .models import RCAReport

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

_client = None
_groq_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def _polish_with_llm(rule_based_summary: str) -> str:
    prompt = (
        "Rewrite the following root-cause-analysis finding as exactly 2-3 "
        "clear, plain-English sentences for an on-call engineer. Do not "
        "change its meaning or add any new claims.\n\n"
        "Output ONLY the rewritten sentences as plain text. Do not offer "
        "multiple options, do not write 'Option 1' / 'Option 2', do not "
        "add headers, markdown, or any explanation of what you did -- "
        "just the final rewritten text itself, nothing else.\n\n"
        f"Finding:\n{rule_based_summary}"
    )

    gemini_err = None
    if GEMINI_API_KEY:
        try:
            client = _get_client()
            model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
            response = client.models.generate_content(model=model_name, contents=prompt)
            return response.text.strip()
        except Exception as e:
            gemini_err = e
            print(f"[RCA LLM Fallback] Gemini failed: {e}. Trying Groq...")

    if GROQ_API_KEY:
        try:
            groq_client = _get_groq_client()
            model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            response = groq_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()
        except Exception as groq_err:
            if gemini_err:
                return rule_based_summary + f" [LLM polish failed: Gemini error: {gemini_err}; Groq error: {groq_err}]"
            return rule_based_summary + f" [LLM polish failed: Groq error: {groq_err}]"

    if gemini_err:
        return rule_based_summary + f" [LLM polish failed: Gemini error: {gemini_err}]"
    return rule_based_summary + " [LLM polish skipped: no API keys configured]"


class RCAEngine:
    def diagnose_window(self, window_start: datetime, force: bool = False) -> dict | None:
        session = get_session()
        try:
            regressions = session.execute(
                select(Regression).where(Regression.window_start == window_start)
            ).scalars().all()
        finally:
            session.close()

        if not regressions:
            return None

        by_metric = {r.metric_name: r for r in regressions}
        has_overall_latency = "latency_p95_ms" in by_metric
        has_error_rate = "error_rate_pct" in by_metric
        stage_latency_regressions = {
            name: r for name, r in by_metric.items() if name.startswith("stage_latency_avg_ms:")
        }

        # Rule 1: a stage-level latency regression alongside an overall
        # latency regression -- the stage is almost certainly the driver.
        if stage_latency_regressions and has_overall_latency:
            stage_name, stage_reg = max(
                stage_latency_regressions.items(), key=lambda kv: kv[1].delta_pct or 0
            )
            stage = stage_name.split(":", 1)[1]
            category = f"stage_latency:{stage}"
            confidence = "high"
            rule_summary = (
                f"Overall trace latency (p95) regressed by {by_metric['latency_p95_ms'].delta_pct:+.1f}%. "
                f"The '{stage}' stage's average latency regressed by {stage_reg.delta_pct:+.1f}% in the "
                f"same window, making it the dominant contributor. Root cause: {stage} stage slowdown "
                f"(e.g. cold index, upstream API degradation, or a config change affecting that stage "
                f"specifically)."
            )

        # Rule 2: latency AND error rate regress together -- likely a
        # cascading failure (slow stage causing timeouts downstream).
        elif has_error_rate and has_overall_latency:
            category = "cascading_failure"
            confidence = "medium"
            rule_summary = (
                f"Both latency (p95 {by_metric['latency_p95_ms'].delta_pct:+.1f}%) and error rate "
                f"({by_metric['error_rate_pct'].current_value:.1f}%) regressed in the same window. "
                f"Likely a cascading failure: elevated latency is causing timeouts or downstream "
                f"errors. Treat the latency regression as primary; re-check error rate after "
                f"latency is addressed."
            )

        # Rule 3: error rate alone, no latency movement -- points to a
        # functional bug, not a performance problem.
        elif has_error_rate:
            category = "error_rate_isolated"
            confidence = "medium"
            rule_summary = (
                f"Error rate spiked to {by_metric['error_rate_pct'].current_value:.1f}% without an "
                f"accompanying latency regression. This points to a functional bug (e.g. input "
                f"validation, parsing, or an upstream schema change) rather than a performance "
                f"issue. Check recent non-timing-related code or config changes."
            )

        # Rule 4: overall latency regressed but no single stage stands out.
        elif has_overall_latency:
            category = "latency_isolated"
            confidence = "medium"
            rule_summary = (
                f"Overall latency regressed by {by_metric['latency_p95_ms'].delta_pct:+.1f}% without "
                f"a clearly dominant single stage. Check for a broad infrastructure issue (host "
                f"contention, network) rather than a single pipeline stage."
            )

        # Fallback: doesn't match a known pattern.
        else:
            category = "unmatched_pattern"
            confidence = "low"
            rule_summary = (
                f"Regression(s) detected on {', '.join(by_metric.keys())} but the pattern doesn't "
                f"match a known rule. Manual investigation needed."
            )

        summary = _polish_with_llm(rule_summary)

        report = {
            "window_start": window_start,
            "regression_ids": [r.id for r in regressions],
            "root_cause_category": category,
            "confidence": confidence,
            "rule_based_summary": rule_summary,
            "summary": summary,
        }
        self._persist(report, force=force)
        return report

    def _persist(self, report: dict, force: bool = False) -> None:
        init_db()
        session = get_session()
        try:
            existing = session.execute(
                select(RCAReport).where(RCAReport.window_start == report["window_start"])
            ).scalar_one_or_none()
            if existing and not force:
                return
            if existing and force:
                session.delete(existing)
                session.flush()
            session.add(RCAReport(
                window_start=report["window_start"],
                regression_ids=report["regression_ids"],
                root_cause_category=report["root_cause_category"],
                confidence=report["confidence"],
                summary=report["summary"],
            ))
            session.commit()
        finally:
            session.close()

    def diagnose_all(self, force: bool = False) -> list[dict]:
        init_db()
        session = get_session()
        try:
            window_starts = session.execute(select(Regression.window_start).distinct()).scalars().all()
        finally:
            session.close()

        reports = []
        for ws in window_starts:
            r = self.diagnose_window(ws, force=force)
            if r:
                reports.append(r)
        return reports
