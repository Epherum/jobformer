from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List

import requests


DEFAULT_LLAMA_CPP_URL = "http://127.0.0.1:8080"
DEFAULT_MODEL = "/home/wassim/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf"


@dataclass(frozen=True)
class LLMScore:
    score: float
    decision: str
    reasons: List[str]
    model: str


_SALES_RE = re.compile(r"\b(sales|account executive|account manager|business development|bdr|sdr|pre[- ]?sales|solutions? engineer|sales engineer|solution consultant|technical account manager|customer success manager|commercial|vente|technico-commercial|ing[ée]nieur commercial|avant[- ]vente)\b", re.I)
_ENGLISH_SPEAKER_RE = re.compile(r"\benglish\s+speaker\b|\banglophone\b|\bexcellent\s+english\b|\benglish\b", re.I)
_SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head of|director|directeur|directrice|vp|vice president|chief|manager)\b", re.I)
_CALL_CENTER_RE = re.compile(r"\b(call center|centre d[' ]appel|t[ée]l[ée]conseiller|t[ée]l[ée]vente|t[ée]l[ée]op[ée]rateur|customer support|service client|r[ée]ceptionniste|charg[ée] client[èe]le)\b", re.I)
_B2B_RE = re.compile(r"\b(b2b|saas|enterprise|account executive|business development|prospecting|pipeline|crm|quota|closing|demos?|solution)\b", re.I)
_TECH_RE = re.compile(r"\b(frontend|full.?stack|backend|software|engineer|developer|react|next\.js|typescript|node\.js|postgres|prisma|supabase|docker|data|analytics|ai|ml|machine learning|computer vision|rag|llm|api)\b", re.I)
_JUNIOR_RE = re.compile(r"\b(junior|jr\.?|entry level|débutant|0-2 years|1-3 years)\b", re.I)


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object found in LLM response")
    return json.loads(m.group(0))


def _guess_is_sales(title: str, page_text: str) -> bool:
    hay = f"{title}\n{page_text[:1200]}"
    return bool(_SALES_RE.search(hay or ""))




def score_job_with_local_llm(
    *,
    title: str,
    company: str,
    location: str,
    url: str,
    page_text: str,
    model: str = DEFAULT_MODEL,
    timeout_s: int = 90,
    retries: int = 1,
    learned_bias: str = "",
    learned_feedback: str = "",
) -> LLMScore:
    snippet = (page_text or "")[:1800]
    is_sales = _guess_is_sales(title, page_text)

    if is_sales:
        prompt = f"""
You are scoring ONLY a SALES job for this candidate.

Current rule set learned from prior manual reviews:
- Sales-only mode. If the job is clearly sales, it is generally a good fit.
- Strong positives: B2B sales, account management, business development, commercial roles, prospecting, portfolio management, solution selling, CRM, and strong spoken English.
- French-speaking requirement is a SOFT negative only. Do not reject for French alone. Usually score around 65-75 unless the rest of the role is unusually strong.
- Strong negatives: German-speaking requirement, terrain or field sales, door-to-door sales, low-value call-center style sales, cashier/caisse style work, and clearly oversenior roles.
- Also score lower when the role requires much more experience than the candidate likely has.
- Use prior human feedback when provided as background signal only, not as a hard override.

Scoring philosophy:
- Good sales fit by default: 80-95.
- Very strong B2B / SaaS / account executive / business development / sales engineer / pre-sales role: 90-98.
- French-speaking sales role: usually around 70.
- Borderline or lower-value sales role: 55-79.
- Terrain / cashier / obvious low-fit role: 0-35.

Also predict the recommended sheet action as suggested_decision using one of:
- APPLIED
- NOT FIT
- FRENCH
- OVERSENIOR

Return ONLY strict JSON with keys:
- score: number 0-100
- decision: "yes" | "maybe" | "no"
- suggested_decision: one of APPLIED | NOT FIT | FRENCH | OVERSENIOR
- reasons: array with exactly 1 short sentence (max ~160 chars)

Title: {title}
Company: {company}
Location: {location}
URL: {url}
Job text:
{snippet}

Prior human feedback bias: {learned_bias or "none"}
Prior human feedback details: {learned_feedback or "none"}
""".strip()
    else:
        return LLMScore(
            score=0,
            decision="no",
            reasons=["Skipped: non-sales job in sales-only mode."],
            model="rule:skip-non-sales",
        )

    backend = (os.getenv("LLM_BACKEND") or "llama_cpp").strip().lower()
    if backend != "llama_cpp":
        raise RuntimeError(f"Unsupported LLM_BACKEND={backend}. Use llama_cpp.")

    llama_cpp_url = (os.getenv("LLAMA_CPP_URL") or DEFAULT_LLAMA_CPP_URL).strip()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 120,
    }

    last_err: Exception | None = None
    data: Dict[str, Any] | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            resp = requests.post(f"{llama_cpp_url}/v1/chat/completions", json=payload, timeout=timeout_s)
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.ReadTimeout as e:
            last_err = e
            if attempt >= retries:
                raise
        except Exception as e:
            last_err = e
            if attempt >= retries:
                raise
    else:
        raise last_err or RuntimeError("llm request failed")

    raw = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content", "")
    obj = _extract_json(raw)

    score = float(obj.get("score", 0))
    decision = str(obj.get("decision", "")).strip().lower()
    suggested_decision = str(obj.get("suggested_decision", "") or "").strip().upper()
    reasons = obj.get("reasons", [])
    if isinstance(reasons, str):
        reasons = [reasons.strip()]
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    reasons = [" ".join(str(r).split()) for r in reasons if str(r).strip()]
    if reasons:
        reasons = [reasons[0][:180]]

    if score < 0:
        score = 0
    if score > 100:
        score = 100
    if decision not in {"yes", "no", "maybe"}:
        decision = "maybe"
    if suggested_decision not in {"APPLIED", "NOT FIT", "FRENCH", "OVERSENIOR"}:
        suggested_decision = "NOT FIT"
    model_name = (data.get("model", model) if data else model)
    return LLMScore(score=score, decision=suggested_decision, reasons=[str(r) for r in reasons], model=model_name)
