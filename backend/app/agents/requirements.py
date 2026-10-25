"""Explicit requirements stated in an issue, extracted without an LLM.

Issue analysis summarizes an issue into a few acceptance criteria and can drop explicit asks (run 332 lost
"add a README example" and "choose one date representation, document it"). These requirements are taken
verbatim from the issue text so the planner must account for every one of them.
"""
import re
from typing import List

# Headings whose items are requirements. "Scope"/"Out of scope" items are constraints, not work to plan.
_REQUIREMENT_HEADINGS = re.compile(
    r"^(requirements?|acceptance criteria|expected behaviou?r|definition of done|tasks?|to ?do|deliverables?)$", re.I)
_HEADING = re.compile(r"^\s*(#{1,6}\s*)?(\*\*)?([A-Za-z][A-Za-z /()-]{1,40}?)(\*\*)?:?\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)]|\[[ xX]\]|- \[[ xX]\])\s+(.*\S)\s*$")
MAX_REQUIREMENTS = 15


def _clean(item: str) -> str:
    item = re.sub(r"^\[[ xX]\]\s*", "", item.strip())
    return re.sub(r"\s+", " ", item).strip()


def extract_requirements(text: str, limit: int = MAX_REQUIREMENTS) -> List[str]:
    """Items under requirement-like headings; otherwise the issue's list items. Order kept, duplicates dropped."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    in_section, sectioned, listed = False, [], []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        heading = _HEADING.match(stripped)
        if heading and not _LIST_ITEM.match(stripped):
            in_section = bool(_REQUIREMENT_HEADINGS.match(heading.group(3).strip()))
            continue
        item = _LIST_ITEM.match(line)
        if item:
            listed.append(_clean(item.group(1)))
        if in_section:
            sectioned.append(_clean(item.group(1) if item else stripped))
    found, seen = [], set()
    for req in (sectioned or listed):
        key = req.lower().rstrip(".")
        if len(req) >= 8 and key not in seen:
            seen.add(key)
            found.append(req)
    return found[:limit]
