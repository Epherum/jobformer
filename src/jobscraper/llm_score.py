from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List

import requests


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct"


@dataclass(frozen=True)
class LLMScore:
    score: float
    decision: str
    reasons: List[str]
    model: str


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    # Try to extract the first JSON object.
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object found in LLM response")
    return json.loads(m.group(0))


def score_job_with_ollama(
    *,
    title: str,
    company: str,
    location: str,
    url: str,
    page_text: str,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 90,
    retries: int = 1,
    ollama_url: str = DEFAULT_OLLAMA_URL,
) -> LLMScore:
    """Score a job using local Ollama and return strict JSON parsed output.

    retries: number of additional attempts for transient timeouts.
    """

    # Keep prompt short; job pages can be long.
    snippet = (page_text or "")[:4000]

    prompt = f"""
You are a strict job screening and ranking assistant.

Candidate profile (the person we are scoring for):
- Education: Master’s degree in Artificial Intelligence.
- Target pivots: wants to pivot into higher-value B2B sales while leveraging technical background.
- Also relevant: software/data/AI engineering roles.
- Strong interests/keywords (tech track): React, Next.js, TypeScript, Node.js, APIs, PostgreSQL, Prisma, Supabase, Docker/DevOps basics; AI/ML/Computer Vision/RAG/LLMs.
- Languages: English + French job posts. Handle both.

We have TWO separate tracks. You must pick ONE:
1) track="sales": high-quality B2B roles where the AI/tech background is an advantage.
   Target roles: Sales Engineer, Solutions Engineer, Pre-sales, Solution Consultant, Technical Account Manager, Enterprise/SMB Account Executive (B2B SaaS), Business Development in B2B tech.
   Non-target sales (usually reject): call center / centre d'appel, téléconseiller, télévente, téléopérateur, customer support disguised as sales, retail counter sales.

2) track="tech": hands-on software/data/AI roles.
   Target roles: Frontend/Fullstack/Backend Engineer, Data Analyst/BI/Analytics Engineer, Data Engineer, ML Engineer/Applied AI/Computer Vision.

Hard rules:
- Do NOT penalize track="sales" for missing a coding/tech stack.
- Do NOT reward track="tech" for generic business/sales language.
- Treat call-center style roles as a strong negative for track="sales". Only consider them if the posting is clearly B2B SaaS with real prospecting/pipeline ownership and a credible path to AE/SE.
- Prefer roles that are not purely senior leadership unless responsibilities are clearly hands-on.
- Return ONE short reason line only.

Output format: return ONLY strict JSON (no markdown, no extra text) with keys:
- track: "sales" | "tech"
- score: number 0-100
- decision: "yes" | "maybe" | "no"
- reasons: array with exactly 1 short sentence (max ~160 chars)

Scoring guidelines (calibrate to be useful):
- decision="yes" (score >= 75): strong fit for the candidate’s goals.
- decision="maybe" (score 50-74): potentially useful but needs human review.
- decision="no" (score < 50): low-value, off-target, or dead-end.

How to score track="sales" (0-100):
+ Strong positives:
  - Explicit Sales Engineer / Solutions Engineer / Pre-sales / TAM / Solution Consultant.
  - B2B SaaS / technical product / enterprise customers.
  - Responsibilities include discovery, demos, solution design, stakeholder management, pipeline/forecast, closing.
  - Commission/OTE structure, quota-carrying clarity, or strong enablement.
+ Negatives:
  - Call-center language (scripts, inbound customer service, telemarketing, high-volume calls).
  - Pure administrative sales ops ("administration des ventes" / order processing) unless it clearly leads toward AM/AE.
  - B2C retail.

How to score track="tech" (0-100):
+ Positives:
  - JS/TS + React/Next.js + Node/APIs.
  - PostgreSQL/Prisma/Supabase.
  - Docker/DevOps.
  - AI/ML/CV/RAG/LLMs with real responsibilities.
+ Negatives:
  - Trades/industrial maintenance/civil engineering/healthcare/etc.
  - Pure IT support without engineering.

Title: {title}
Company: {company}
Location: {location}
URL: {url}

Job page text (may be long; focus on responsibilities and role type):
{snippet}
""".strip()

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }

    last_err: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            resp = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=timeout_s)
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.ReadTimeout as e:
            last_err = e
            if attempt >= retries:
                raise
            continue
        except Exception as e:
            last_err = e
            if attempt >= retries:
                raise
            continue
    else:
        # Should be unreachable
        raise last_err or RuntimeError("ollama request failed")

    raw = data.get("response", "")
    obj = _extract_json(raw)

    score = float(obj.get("score", 0))
    decision = str(obj.get("decision", "")).strip().lower()
    reasons = obj.get("reasons", [])
    if isinstance(reasons, str):
        reasons = [reasons.strip()]
    if not isinstance(reasons, list):
        reasons = [str(reasons)]

    # Force a single short line for downstream sheets.
    reasons = [" ".join(str(r).split()) for r in reasons if str(r).strip()]
    if reasons:
        reasons = [reasons[0][:180]]

    # Clamp score
    if score < 0:
        score = 0
    if score > 100:
        score = 100

    if decision not in {"yes", "no", "maybe"}:
        decision = "maybe"

    return LLMScore(score=score, decision=decision, reasons=[str(r) for r in reasons], model=data.get("model", model))
