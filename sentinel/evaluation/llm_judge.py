"""
LLM-as-Judge evaluators.

Uses a separate client from rag/generator.py on purpose -- the judge
model evaluating an answer shouldn't be indistinguishable from the
model that wrote it in the code path, even if it happens to be the
same underlying model today. Swapping the judge to a different
provider (Groq) later is a one-file change.

Same fail-soft contract as the rest of this codebase: no API key means
a clearly tagged skipped result, never a crash.
"""

import json
import os

from google import genai
from groq import Groq

USE_GROQ = os.getenv("USE_GROQ", "false").lower() in ("true", "1", "yes")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if USE_GROQ:
    JUDGE_MODEL = os.getenv("SENTINEL_JUDGE_MODEL", "llama-3.3-70b-versatile")
else:
    JUDGE_MODEL = os.getenv("SENTINEL_JUDGE_MODEL", "gemini-2.5-flash")

_gemini_client = None
_groq_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


FAITHFULNESS_PROMPT = """You are a strict evaluator judging whether an AI \
answer is fully supported by the given context.

Context:
{context}

Answer:
{answer}

Score the answer's faithfulness to the context from 0.0 (unsupported or \
contradicts the context) to 1.0 (fully supported, no unsupported claims).
Respond ONLY with JSON, no other text: {{"score": <float 0-1>, "reasoning": "<one sentence>"}}"""

RELEVANCE_PROMPT = """You are a strict evaluator judging whether an AI \
answer actually addresses the user's question.

Question: {query}
Answer: {answer}

Score relevance from 0.0 (does not address the question) to 1.0 (fully \
addresses it).
Respond ONLY with JSON, no other text: {{"score": <float 0-1>, "reasoning": "<one sentence>"}}"""


def _call_gemini_judge(prompt: str) -> dict:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")
    client = _get_gemini_client()
    model = JUDGE_MODEL if "gemini" in JUDGE_MODEL else "gemini-2.5-flash"
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    parsed = json.loads(response.text)
    return {
        "score": float(parsed["score"]),
        "reasoning": parsed.get("reasoning", ""),
        "skipped": False,
    }


def _call_groq_judge(prompt: str) -> dict:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not configured")
    client = _get_groq_client()
    model = JUDGE_MODEL if ("llama" in JUDGE_MODEL or "mixtral" in JUDGE_MODEL or "gemma" in JUDGE_MODEL) else "llama-3.3-70b-versatile"
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    parsed = json.loads(response.choices[0].message.content)
    return {
        "score": float(parsed["score"]),
        "reasoning": parsed.get("reasoning", ""),
        "skipped": False,
    }


def _run_judge(prompt: str) -> dict:
    attempts = []
    if USE_GROQ:
        attempts = [("Groq", _call_groq_judge), ("Gemini", _call_gemini_judge)]
    else:
        attempts = [("Gemini", _call_gemini_judge), ("Groq", _call_groq_judge)]

    errors = []
    for provider, func in attempts:
        try:
            return func(prompt)
        except Exception as e:
            errors.append(f"{provider}: {e}")

    return {
        "score": None,
        "reasoning": f"[JUDGE ERROR] All providers failed. Errors: {'; '.join(errors)}",
        "skipped": True,
    }


def judge_faithfulness(context: str, answer: str) -> dict:
    # Truncate defensively -- a judge prompt shouldn't silently balloon
    # in cost because one retrieval returned an unusually large context.
    return _run_judge(FAITHFULNESS_PROMPT.format(context=context[:4000], answer=answer))


def judge_relevance(query: str, answer: str) -> dict:
    return _run_judge(RELEVANCE_PROMPT.format(query=query, answer=answer))
