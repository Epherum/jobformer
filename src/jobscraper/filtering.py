from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


# Scraper behavior:
# - Titles that match DELETE_PATTERNS are discarded even if they match TECH/AI/SALES.
# - Titles that match TOO_SENIOR_PATTERNS are kept, but the default decision in Sheets becomes OVERSENIOR.

DECISION_NEW = "NEW"
DECISION_TOO_SENIOR = "OVERSENIOR"


TOO_SENIOR_PATTERNS: list[str] = [
    r"\bexecutive\b",
    r"\bvp\b",
    r"\bvice\s+president\b",
    r"\bdirector\b",
    r"\bdirecteur\b",
    r"\bdirectrice\b",
    r"\bhead\s+of\b",
    r"\bc\-level\b",
    r"\bchief\b",
    r"\bprincipal\b",
    r"\bstaff\b",
    r"\blead\b",
    r"\bmanager\b",
    r"\bsenior\b",
    r"\bsr\b",
    r"\bconfirmé\b",
    r"\bconfirmée\b",
]


# Keep this list conservative; it is easy to over-filter.
DELETE_PATTERNS: list[str] = [
    # Sales-heavy pipeline roles
    r"sales\s+development\s+representative",
    r"business\s+development\s+representative",
    r"\bsdr\b",
    r"\bbdr\b",

    # Non-software engineering / trades you flagged (FR/EN)
    r"électricit",
    r"electri(c|que)",
    r"\bcfo\b",
    r"\bcfa\b",
    r"automatisme",
    r"maintenance\b",
    r"\bindustri(el|elle|els|elles)\b",
    r"manufactur",
    r"assemblage",
    r"contrôleur\s+qualité",
    r"controleur\s+qualite",
    r"\bqualité\b",
    r"\bqualite\b",
    r"génie\s+civil",
    r"genie\s+civil",
    r"revit",
    r"coffrage",
    r"ferraillage",

    # QA/testing
    r"\bqa\b",
    r"test(\b|eur|euse)",
    r"fonctionnel(le)?",

    # Accounting/HR/marketing/product/video
    r"comptab",
    r"finance\b",
    r"ressources\s+humaines",
    r"\brh\b",
    r"marketing\b",
    r"chef\s+de\s+produit",
    r"product\s+manager",
    r"video\s+editor",
    r"monteur\s+vid(é|e)o",

    # Retail / service / logistics
    r"\bcaissier\b",
    r"\bcaisse\b",
    r"\bcashier\b",
    r"\blivreur\b",
    r"\bcoursier\b",
    r"\bchauffeur\b",
    r"\bpréparateur\b",
    r"\bpreparateur\b",
    r"\bvendeur\b",
    r"\bvendeuse\b",
]

_RE_TOO_SENIOR = re.compile("|".join(f"(?:{p})" for p in TOO_SENIOR_PATTERNS), flags=re.IGNORECASE)
_RE_DELETE = re.compile("|".join(f"(?:{p})" for p in DELETE_PATTERNS), flags=re.IGNORECASE)


@dataclass(frozen=True)
class KeywordRule:
    label: str
    keywords: List[str]


# Broader rules (safe now because we are not using keywords to generate requests).
# These rules are used for local filtering/labeling only.
BROAD_RULES: List[KeywordRule] = [
    # TECH (broad)
    KeywordRule(
        label="TECH",
        keywords=[
            "full stack",
            "full-stack",
            "fullstack",
            "développeur",
            "developer",
            "ingénieur",
            "engineer",
            "frontend",
            "front-end",
            "backend",
            "back-end",
            "software",
            "web",
            "it",
            "informatique",
            # Common Tunisia/FR IT titles
            "chef de projet",
            "project manager",
            "analyste",
            "analyste fonctionnel",
            "fonctionnel",
            "consultant",
            "data center",
            "datacenter",
            "monétique",
            "react",
            "next",
            "node",
            "javascript",
            "typescript",
            "python",
            "sql",
            "devops",
            "docker",
            "postgres",
            "postgresql",
            "prisma",
            "supabase",
            # ERP / technico-functional terms
            "technico-fonctionnel",
            "techno-fonctionnel",
            "sage",
        ],
    ),

    # AI (broad, with special handling for short token IA)
    KeywordRule(
        label="AI",
        keywords=[
            "machine learning",
            "deep learning",
            "intelligence artificielle",
            "computer vision",
            "vision",
            "yolo",
            "rag",
            "llm",
        ],
    ),

    # SALES (broad)
    KeywordRule(
        label="SALES",
        keywords=[
            "sales",
            "commercial",
            "vente",
            "business development",
            "développement commercial",
            "account executive",
            "account manager",
            "chargé d'affaires",
            "chargé daffaires",
            "ingénieur commercial",
            "technico-commercial",
            "chef des ventes",
            # Tunisia/common variants
            "télévente",
            "télévendeur",
            "télévendeurs",
            "téléconseiller",
            "téléconseillère",
            "téléopérateur",
            "téléopérateurs",
            "centre d'appel",
            "centre d’appels",
            "call center",
            "centre de contact",
            "vendeur",
            "vendeuse",
            "conseiller commercial",
            "chargé clientèle",
            "chargé de clientèle",
            "chargée de clientèle",
            "prise de rdv",
            "prise de rendez",
            "rdv",
        ],
    ),
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


def is_too_senior(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(_RE_TOO_SENIOR.search(t))


def is_blocked(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(_RE_DELETE.search(t))


def decision_for_title(title: str) -> str:
    # Only apply OVERSENIOR if the job is relevant in the first place.
    # This avoids tagging random irrelevant jobs as OVERSENIOR.
    if not is_relevant(title):
        return DECISION_NEW
    return DECISION_TOO_SENIOR if is_too_senior(title) else DECISION_NEW


def is_relevant(text: str, rules: Iterable[KeywordRule] = BROAD_RULES) -> bool:
    if is_blocked(text):
        return False
    return len(match_labels(text, rules=rules)) > 0
