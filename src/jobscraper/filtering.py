from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class KeywordRule:
    label: str
    keywords: List[str]


# Broader rules (safe now because we are not using keywords to generate requests).
# These rules are used for local filtering/labeling only.
BROAD_RULES: List[KeywordRule] = [
    # TECH (broad)
    KeywordRule(label="TECH", keywords=[
        "full stack", "full-stack", "fullstack", "développeur", "developer", "ingénieur", "engineer",
        "frontend", "front-end", "backend", "back-end", "software", "web", "it", "informatique",
        "react", "next", "node", "javascript", "typescript", "python", "sql",
        "devops", "docker", "postgres", "postgresql", "prisma", "supabase",
        # ERP / technico-functional terms
        "technico-fonctionnel", "techno-fonctionnel", "sage",
    ]),

    # AI (broad, with special handling for short token IA)
    KeywordRule(label="AI", keywords=[
        "machine learning", "deep learning", "intelligence artificielle", "computer vision", "vision", "yolo", "rag", "llm",
    ]),

    # SALES (broad)
    KeywordRule(label="SALES", keywords=[
        "sales", "commercial", "vente", "business development", "développement commercial",
        "account executive", "account manager", "chargé d'affaires", "chargé daffaires", "ingénieur commercial", "technico-commercial",
        "chef des ventes",
        # Tunisia/common variants
        "télévente", "télévendeur", "télévendeurs", "vendeur", "conseiller commercial", "chargé clientèle",
    ]),
]


def match_labels(text: str, rules: Iterable[KeywordRule] = BROAD_RULES) -> List[str]:
    t = (text or "").lower()
    labels: List[str] = []

    for rule in rules:
        if any(k.lower() in t for k in rule.keywords):
            labels.append(rule.label)

    # Important: avoid false positives on "IA" from substrings like "Industria...".
    # Match IA as a whole word only.
    if re.search(r"\bia\b", text or "", flags=re.IGNORECASE):
        labels.append("AI")

    # de-dupe while keeping order
    out: List[str] = []
    seen = set()
    for l in labels:
        if l in seen:
            continue
        seen.add(l)
        out.append(l)
    return out


def is_relevant(text: str, rules: Iterable[KeywordRule] = BROAD_RULES) -> bool:
    return len(match_labels(text, rules=rules)) > 0
