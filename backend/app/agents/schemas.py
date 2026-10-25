"""Input/output contracts for the LLM agents.

Every agent response is validated against one of these models before it is
used downstream. Fields the pipeline cannot work without stay required so a
truncated or incomplete model response is rejected instead of being filled with
invented values; list fields that may legitimately be empty default to [].
"""
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, create_model, field_validator
from typing import Annotated, List, Literal, Optional

def _clamp_confidence(v):
    try:
        return min(max(float(v), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0

class IssueAnalysis(BaseModel):
    problem_summary: str = Field(description="One or two sentences describing the problem or requested feature.")
    expected_behavior: str = Field(description="What should happen once the issue is resolved.")
    current_behavior: str = Field(description="What happens today (or what is missing).")
    affected_areas: List[str] = Field(default_factory=list, description="Files, modules or components likely involved. Use real paths from the repository when known.")
    constraints: List[str] = Field(default_factory=list, description="Requirements the change must respect, e.g. backwards compatibility.")
    acceptance_criteria: List[str] = Field(default_factory=list, description="Concrete, testable conditions that show the issue is resolved.")

class RetrievedContextChunk(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    content: str
    score: float
    symbol: str = ""
    source: str = ""

class CodeLocation(BaseModel):
    """A code region verified against the checkout (never taken from the model unchecked)."""
    file_path: str
    symbol: str = ""
    kind: str = ""  # "class", "function", "method", "lines" or "file"
    start_line: int
    end_line: int
    snippet: str = ""
    reason: str = ""
    intended_behavior: str = ""
    evidence: List[str] = Field(default_factory=list)

class SearchCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["find_class", "find_function", "find_method_in_class", "symbols_in_file", "find_text", "read_lines"] = Field(
        description="find_class(name) | find_function(name) | find_method_in_class(name, class_name) | "
                    "symbols_in_file(file_path) | find_text(text) | read_lines(file_path, start_line, end_line)")
    name: str = Field(default="", description="Class, function or method name.")
    class_name: str = Field(default="", description="Class name for find_method_in_class.")
    file_path: str = Field(default="", description="Repository-relative path for symbols_in_file / read_lines.")
    text: str = Field(default="", description="Exact code text for find_text.")
    start_line: int = Field(default=0, description="First line for read_lines.")
    end_line: int = Field(default=0, description="Last line for read_lines.")

class ProposedLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str = Field(description="Repository-relative path exactly as shown in search results.")
    symbol: str = Field(default="", description="Qualified name as shown in search results, e.g. 'Class.method'. Empty for a line range.")
    start_line: int = Field(default=0, description="Only for a line range without a symbol.")
    end_line: int = Field(default=0, description="Only for a line range without a symbol.")
    reason: str = Field(description="Why this code is responsible for the issue, based on the code you saw.")
    intended_behavior: str = Field(default="", description="What this code should do once the issue is resolved.")

class LocalizationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis: str = Field(description="What the code seen so far shows about the issue.")
    searches: List[SearchCall] = Field(default_factory=list, description="Searches to run next. Empty when done.")
    locations: List[ProposedLocation] = Field(default_factory=list, description="Code that must change to resolve the issue. Only code you have seen.")
    done: bool = Field(default=False, description="True when the locations are final.")

class LocalizationResult(BaseModel):
    # "seeded": the model did not converge; the verified deterministic seed locations are used.
    status: Literal["localized", "seeded", "fallback", "disabled"]
    reason: str = ""
    locations: List[CodeLocation] = Field(default_factory=list)
    rounds: int = 0
    searches: List[dict] = Field(default_factory=list)
    rejected: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    # Last LLM failure, kept so a "seeded" result says why the model's localization was not used.
    llm_error: str = ""

class Evidence(BaseModel):
    location_id: str = Field(default="", description="Id of a localized location (e.g. 'L1') when citing one.")
    file_path: str = Field(default="", description="Repository-relative path when not citing a location id.")
    symbol: str = Field(default="", description="Qualified symbol name, if any.")
    start_line: int = Field(default=0, description="First cited line (when not citing a location id).")
    end_line: int = Field(default=0, description="Last cited line (when not citing a location id).")
    claim: str = Field(description="What this code shows about the root cause.")

class RootCauseHypothesis(BaseModel):
    description: str = Field(description="The probable root cause.")
    evidence: List[Evidence] = Field(default_factory=list, description="Code shown above that supports this hypothesis.")
    confidence: float = Field(default=0.5, description="0.0 to 1.0.")

    _confidence = field_validator("confidence", mode="before")(classmethod(lambda cls, v: _clamp_confidence(v)))

class FileChangePlan(BaseModel):
    path: str = Field(description="Repository-relative path, copied exactly from the Available Files list unless the file is being created.")
    operation: Literal["modify", "create", "delete"] = Field(description="'modify' an existing file, 'create' a new file, or 'delete'.")
    reason: str = Field(description="Why this file needs to change.")
    symbols: List[str] = Field(default_factory=list, description="Existing functions, classes or methods to change, by qualified name (e.g. 'Class.method').")
    new_symbols: List[str] = Field(default_factory=list, description="Functions, classes or methods to add.")
    planned_changes: List[str] = Field(default_factory=list, description="Specific changes to make in this file.")

class SolutionPlan(BaseModel):
    summary: str = Field(description="Short description of the overall approach.")
    root_cause_hypotheses: List[RootCauseHypothesis] = Field(default_factory=list, description="Probable root causes, each backed by evidence from the code shown.")
    affected_files: List[FileChangePlan] = Field(description="Every file that must be created or modified.")
    tests: List[str] = Field(default_factory=list, description="Tests to add or run to verify the change.")
    dependencies: List[str] = Field(default_factory=list, description="New packages required, if any.")
    risks: List[str] = Field(default_factory=list, description="Possible regressions.")
    assumptions: List[str] = Field(default_factory=list, description="Assumptions that were not verified.")
    acceptance_criteria: List[str] = Field(description="Conditions that show the issue is resolved.")
    # How each explicit requirement from the issue text is met: {"requirement", "addressed_by", "how"}.
    requirements: List[dict] = Field(default_factory=list)

# ---- Strict planner output -------------------------------------------------
# The schema the planner model must fill when grounding is available. Every
# list the pipeline relies on is required and non-empty, so constrained
# decoding cannot return an "empty but valid" plan; length and count limits
# (enforced by Ollama during decoding) keep the answer within what a slow
# local model can produce before OLLAMA_TIMEOUT. It is converted into the
# SolutionPlan above (plan_from_draft), which stays the stored contract.

def _text(max_chars: int, description: str):
    return Field(min_length=1, max_length=max_chars, description=description)

ShortText = Annotated[str, Field(min_length=1, max_length=200)]
# Bare distribution names: the pattern is enforced while decoding, so prose cannot be emitted here.
# The length bound must live inside the pattern: Ollama ignores maxLength on a string that has a pattern
# (measured: 125-character output with maxLength 60), and every such answer then failed validation.
PackageName = Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,59}$")]

class EvidenceDraft(BaseModel):
    """Evidence as the model writes it. The field is named for what it must contain: small models
    follow field names more reliably than descriptions (a field named 'claim' drew proposals)."""
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(default="", max_length=8, description="Id of a localized location (e.g. 'L1') when citing one.")
    file_path: str = Field(default="", max_length=200, description="Repository-relative path when not citing a location id.")
    symbol: str = Field(default="", max_length=120, description="Qualified symbol name, if any.")
    start_line: int = Field(default=0, description="First cited line (when not citing a location id).")
    end_line: int = Field(default=0, description="Last cited line (when not citing a location id).")
    current_code_behavior: str = _text(200, "What the cited code does TODAY, as visible in it (e.g. 'filters only by "
                                       "status and priority'). Never what should change.")

    def to_evidence(self) -> Evidence:
        return Evidence(location_id=self.location_id, file_path=self.file_path, symbol=self.symbol,
                        start_line=self.start_line, end_line=self.end_line, claim=self.current_code_behavior)

class PlannedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=120, description="For 'modify': qualified name of an EXISTING symbol "
                        "shown above (e.g. 'TaskService.list_tasks'). For 'add': the new function, class, field or "
                        "parameter (e.g. 'Task.due_date').")
    action: Literal["modify", "add"] = Field(description="'modify' an existing symbol or 'add' a new one.")
    change: str = _text(160, "One sentence: the concrete behavior that must change.")
    details: str = _text(200, "One sentence: new fields, parameters, validation rules or return values. Do not repeat 'change'.")
    preserves: str = _text(160, "One sentence: how existing behavior and callers keep working.")

class FileChangeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=200, description="Repository-relative path, copied exactly from the Available Files list unless the file is being created.")
    operation: Literal["modify", "create"] = Field(description="'modify' an existing file or 'create' a new one.")
    reason: str = _text(160, "One sentence: why this file needs to change.")
    changes: List[PlannedChange] = Field(min_length=1, max_length=6, description="Every symbol-level change in this file.")

class RootCauseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = _text(240, "What the code shown does today and why that does not meet the issue.")
    evidence: List[EvidenceDraft] = Field(min_length=1, max_length=3, description="Code shown above that supports this.")
    confidence: float = Field(description="0.0 to 1.0: how strongly the evidence supports this.")

    _confidence = field_validator("confidence", mode="before")(classmethod(lambda cls, v: _clamp_confidence(v)))

class PlannedTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str = Field(max_length=200, description="Test file the test belongs in (one of the affected files).")
    name: str = Field(min_length=1, max_length=80, description="Test function name.")
    scenario: str = _text(200, "One sentence: setup, action and expected result.")

class SolutionPlanDraft(BaseModel):
    """Base of the per-call planner schema; plan_draft_model adds one required test list per criterion."""
    model_config = ConfigDict(extra="forbid")
    summary: str = _text(300, "Short description of the overall approach.")
    root_cause_hypotheses: List[RootCauseDraft] = Field(min_length=1, max_length=3)
    affected_files: List[FileChangeDraft] = Field(min_length=1, max_length=10, description="Every file that must be created or modified, including tests.")
    risks: List[ShortText] = Field(min_length=1, max_length=5, description="Concrete ways the change could break existing behavior.")
    assumptions: List[ShortText] = Field(max_length=5, description="Anything not confirmed by the code shown; empty only if nothing is uncertain.")
    dependencies: List[PackageName] = Field(max_length=5, description="New third-party package names only (e.g. 'python-dateutil'); usually empty.")

_CRITERION_FIELD = "tests_for_criterion_"
_REQUIREMENT_FIELD = "requirement_"


class RequirementCoverage(BaseModel):
    """How one explicit requirement from the issue is met by the plan."""
    model_config = ConfigDict(extra="forbid")
    addressed_by: List[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=4,
        description="Planned file paths, symbols (e.g. 'TaskCreate.due_date') or test names that implement it.")
    how: str = _text(200, "One sentence: how those planned changes meet the requirement.")


def plan_draft_model(criteria: List[str], testable: List[int],
                     requirements: List[str] = ()) -> type[SolutionPlanDraft]:
    """The planner schema for one call: a required, non-empty test list for each testable criterion
    (1-based numbers), and a required coverage entry for each explicit issue requirement, so neither
    can be skipped: decoding enforces it rather than hoping for it."""
    fields = {
        f"{_CRITERION_FIELD}{i}": (List[PlannedTest], Field(
            min_length=1, max_length=3, description=f"Tests verifying acceptance criterion {i}: {criteria[i - 1][:150]}"))
        for i in testable
    }
    fields.update({
        f"{_REQUIREMENT_FIELD}{i}": (RequirementCoverage, Field(description=f"Issue requirement {i}: {req[:150]}"))
        for i, req in enumerate(requirements, 1)
    })
    return create_model("SolutionPlanDraft", __base__=SolutionPlanDraft, **fields)


def draft_requirements(draft: SolutionPlanDraft) -> List[tuple]:
    """(requirement number, RequirementCoverage) in order."""
    names = sorted((n for n in type(draft).model_fields if n.startswith(_REQUIREMENT_FIELD)),
                   key=lambda n: int(n[len(_REQUIREMENT_FIELD):]))
    return [(int(n[len(_REQUIREMENT_FIELD):]), getattr(draft, n)) for n in names]


def draft_tests(draft: SolutionPlanDraft) -> List[tuple]:
    """(PlannedTest, [criterion numbers]) with tests listed under several criteria merged."""
    merged: dict = {}
    for name in sorted((n for n in type(draft).model_fields if n.startswith(_CRITERION_FIELD)),
                       key=lambda n: int(n[len(_CRITERION_FIELD):])):
        number = int(name[len(_CRITERION_FIELD):])
        for t in getattr(draft, name):
            key = (t.file_path.strip(), t.name.strip())
            if key in merged:
                merged[key][1].append(number)
            else:
                merged[key] = (t, [number])
    return list(merged.values())


class RelatedClassDecision(BaseModel):
    """Targeted plan repair: the decision for one request/response class the plan left out."""
    model_config = ConfigDict(extra="forbid")
    needs_change: bool = Field(description="True if this class must change for the issue.")
    change: str = _text(160, "One sentence: what changes in this class (e.g. 'add an optional due_date field'), "
                             "or, if needs_change is false, why it needs no change.")
    details: str = _text(200, "One sentence: fields, types and validation involved (or 'none').")
    preserves: str = _text(160, "One sentence: how existing requests and responses keep working (or 'n/a').")


def related_class_field(qualname: str) -> str:
    return "class_" + "".join(ch if ch.isalnum() else "_" for ch in qualname)


def related_class_repair_model(class_names: List[str]) -> type[BaseModel]:
    """One required decision per missing class, so the answer cannot skip any of them."""
    fields = {related_class_field(name): (RelatedClassDecision, Field(description=f"Decision for class {name}."))
              for name in class_names}
    return create_model("RelatedClassRepair", __config__=ConfigDict(extra="forbid"), **fields)


def _shown(model: type[BaseModel], field: str, text: str) -> str:
    """Text as stored, visibly marked when decoding stopped it at the schema's length limit."""
    limit = next((m.max_length for m in model.model_fields[field].metadata if hasattr(m, "max_length")), None)
    text = text.strip()
    return f"{text} [cut at {limit} characters]" if limit and len(text) >= limit else text


def _format_change(c: PlannedChange) -> str:
    verb = "Modify" if c.action == "modify" else "Add"
    return (f"{verb} `{c.symbol}`: {_shown(PlannedChange, 'change', c.change)} "
            f"Details: {_shown(PlannedChange, 'details', c.details)} "
            f"Preserves: {_shown(PlannedChange, 'preserves', c.preserves)}")


def plan_from_draft(draft: SolutionPlanDraft, acceptance_criteria: List[str],
                    requirements: List[str] = ()) -> SolutionPlan:
    """Derive the stored SolutionPlan; symbols/new_symbols come from the same per-change entries.

    Acceptance criteria are the issue analysis's, not re-written by the planner, so that test
    'covers' numbers refer to a fixed list.
    """
    return SolutionPlan(
        summary=draft.summary,
        root_cause_hypotheses=[RootCauseHypothesis(description=h.description,
                                                   evidence=[e.to_evidence() for e in h.evidence],
                                                   confidence=h.confidence) for h in draft.root_cause_hypotheses],
        affected_files=[FileChangePlan(
            path=f.path, operation=f.operation, reason=f.reason,
            symbols=list(dict.fromkeys(c.symbol for c in f.changes if c.action == "modify")),
            new_symbols=list(dict.fromkeys(c.symbol for c in f.changes if c.action == "add")),
            planned_changes=[_format_change(c) for c in f.changes],
        ) for f in draft.affected_files],
        tests=[f"{t.file_path}::{t.name}: {_shown(PlannedTest, 'scenario', t.scenario)} "
               f"(covers criteria {', '.join(map(str, covers))})"
               for t, covers in draft_tests(draft)],
        dependencies=draft.dependencies,
        risks=draft.risks,
        assumptions=draft.assumptions,
        acceptance_criteria=list(acceptance_criteria),
        requirements=[{"requirement": requirements[i - 1], "addressed_by": list(cov.addressed_by), "how": cov.how}
                      for i, cov in draft_requirements(draft) if 0 < i <= len(requirements)],
    )

class TextEdit(BaseModel):
    symbol: str = Field(default="", description="Python only: qualified name of a function, method or class to replace entirely, e.g. 'TaskService.list_tasks'. When set, 'search' is ignored and 'replace' must be the complete new definition.")
    search: str = Field(default="", description="Exact, contiguous text copied from the current file, including indentation. Must occur exactly once; include surrounding lines if needed to make it unique. Leave empty when using 'symbol'.")
    replace: str = Field(description="Text that replaces 'search', or the complete new definition when 'symbol' is set.")

class CodeChange(BaseModel):
    file_path: str = Field(description="Repository-relative path of the file.")
    operation: Literal["modify", "create", "delete"] = Field(description="'modify' with edits, 'create' with content, or 'delete'.")
    description: str = Field(default="", description="What this change does.")
    edits: List[TextEdit] = Field(default_factory=list, description="For 'modify': search/replace edits applied in order.")
    # 'patch' is accepted for backwards compatibility with older callers.
    content: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("content", "patch"),
        description="For 'create': the complete content of the new file. Leave empty for 'modify'.",
    )

    @property
    def patch(self) -> Optional[str]:
        return self.content

class CodeChangePlan(BaseModel):
    changes: List[CodeChange]


def single_file_change_plan(target: str, operation: str) -> type[BaseModel]:
    """A CodeChangePlan schema whose only permitted file is `target`.

    Used for per-file coding steps so that providers with schema-constrained
    decoding (Ollama, OpenAI/Gemini structured output) cannot emit another file.
    """
    step_change = create_model(
        "CodeChange",
        __base__=CodeChange,
        file_path=(Literal[target], Field(description=f"Must be exactly '{target}'.", json_schema_extra={"enum": [target]})),
        operation=(Literal[operation], Field(description=f"Must be '{operation}'.", json_schema_extra={"enum": [operation]})),
        # Bounded lists become bounded grammars under constrained decoding. Without
        # a bound, small models were seen repeating the same edit until the output
        # token limit, which never yields valid JSON.
        edits=(List[TextEdit], Field(default_factory=list, max_length=MAX_STEP_EDITS,
                                     description=f"For 'modify': at most {MAX_STEP_EDITS} search/replace edits, applied in order.")),
    )
    return create_model("CodeChangePlan", changes=(List[step_change], Field(
        max_length=1, description=f"At most one change, to {target} only. Empty list if the file needs no change.")))


MAX_STEP_EDITS = 8

FAILURE_TYPES = (
    "ASSERTION_ERROR", "IMPORT_ERROR", "SYNTAX_ERROR", "TYPE_ERROR", "RUNTIME_ERROR", "TIMEOUT",
    "DEPENDENCY_ERROR", "CONFIGURATION_ERROR", "TEST_COLLECTION_ERROR", "UNKNOWN",
)

class FailureAnalysis(BaseModel):
    failure_type: str = Field(description="One of: " + ", ".join(FAILURE_TYPES))
    root_cause: str = Field(description="Why the tests failed, based only on the test output.")
    affected_files: List[str] = Field(default_factory=list, description="Repository files implicated by the traceback.")
    affected_symbols: List[str] = Field(default_factory=list, description="Functions or classes involved in the failure.")
    evidence: List[str] = Field(default_factory=list, description="Exact lines from the test output that show the root cause.")
    suggested_fix: str = Field(description="How to fix the root cause, limited in scope.")
    confidence: float = Field(default=0.5, description="0.0 to 1.0.")
    unresolved_questions: List[str] = Field(default_factory=list, description="Information that was missing.")

    @field_validator("failure_type", mode="before")
    @classmethod
    def _normalize_type(cls, v):
        v = str(v or "").strip().upper().replace(" ", "_")
        return v if v in FAILURE_TYPES else "UNKNOWN"

    _confidence = field_validator("confidence", mode="before")(classmethod(lambda cls, v: _clamp_confidence(v)))

class RepairPlan(BaseModel):
    root_cause: str = Field(description="The root cause being repaired.")
    affected_files: List[str] = Field(description="Repository files that must change to fix it.")
    changes: List[str] = Field(description="The specific change to make in each file.")
    tests_to_run: List[str] = Field(default_factory=list, description="Tests that verify the repair.")
    reasoning: str = Field(default="", description="Why these changes fix the root cause.")
    confidence: float = Field(default=0.5, description="0.0 to 1.0.")

    _confidence = field_validator("confidence", mode="before")(classmethod(lambda cls, v: _clamp_confidence(v)))
