"""
Stage 7: Generation (Gemini)

Calls the Gemini API with the final prompt via the current `google-genai`
SDK (the older `google-generativeai` package is deprecated as of 2025).

Fails soft (returns a clearly tagged fallback string) rather than
crashing when GEMINI_API_KEY is not set, so the rest of the pipeline
stays runnable/demoable without a live key -- and so Sentinel AI has a
real "degraded" status to detect, not just "success" or "hard crash".
"""

import os
from google import genai
from groq import Groq

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

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


def generate_answer(prompt: str) -> str:
    """
    Generate an answer given the final prompt.
    Tries Gemini first, and falls back to Groq if Gemini fails or is unconfigured.
    """
    if GEMINI_API_KEY:
        try:
            client = _get_gemini_client()
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            print(f"[FALLBACK] Gemini generation failed: {e}. Trying Groq...")
            if GROQ_API_KEY:
                try:
                    groq_client = _get_groq_client()
                    response = groq_client.chat.completions.create(
                        model=GROQ_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return response.choices[0].message.content
                except Exception as groq_err:
                    raise RuntimeError(f"Gemini failed ({e}) and Groq fallback failed: {groq_err}") from groq_err
            raise RuntimeError(f"Gemini generation failed: {e}") from e
    else:
        # No Gemini key, try Groq directly if available
        if GROQ_API_KEY:
            try:
                groq_client = _get_groq_client()
                response = groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content
            except Exception as groq_err:
                return f"[FALLBACK: GEMINI NOT SET & GROQ FAILED] {groq_err}"
        return (
            "[FALLBACK: NO GEMINI_API_KEY or GROQ_API_KEY SET] "
            "Set GEMINI_API_KEY or GROQ_API_KEY in your .env to get real answers. "
            "Pipeline structure is working correctly up to this point."
        )
