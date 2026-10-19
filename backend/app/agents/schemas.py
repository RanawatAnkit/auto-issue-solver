"""Input/output contracts for the LLM agents.

Every agent response is validated against one of these models before it is
used downstream. Fields the pipeline cannot work without stay required so a
truncated or incomplete model response is rejected instead of being filled with
invented values; list fields that may legitimately be empty default to [].
"""
from pydantic import AliasChoices, BaseModel, Field, create_model, field_validator
from typing import List, Literal, Optional

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

class FileChangePlan(BaseModel):
    path: str = Field(description="Repository-relative path, copied exactly from the Available Files list unless the file is being created.")
    operation: Literal["modify", "create", "delete"] = Field(description="'modify' an existing file, 'create' a new file, or 'delete'.")
    reason: str = Field(description="Why this file needs to change.")
    symbols: List[str] = Field(default_factory=list, description="Functions, classes or other symbols to add or change.")
    planned_changes: List[str] = Field(default_factory=list, description="Specific changes to make in this file.")

class SolutionPlan(BaseModel):
    summary: str = Field(description="Short description of the overall approach.")
    affected_files: List[FileChangePlan] = Field(description="Every file that must be created or modified.")
    tests: List[str] = Field(default_factory=list, description="Tests to add or run to verify the change.")
    dependencies: List[str] = Field(default_factory=list, description="New packages required, if any.")
    risks: List[str] = Field(default_factory=list, description="Possible regressions.")
    assumptions: List[str] = Field(default_factory=list, description="Assumptions that were not verified.")
    acceptance_criteria: List[str] = Field(description="Conditions that show the issue is resolved.")

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
    )
    return create_model("CodeChangePlan", changes=(List[step_change], Field(
        description=f"Changes to {target} only. Empty list if the file needs no change.")))

FAILURE_TYPES = (
    "ASSERTION_ERROR", "IMPORT_ERROR", "SYNTAX_ERROR", "TYPE_ERROR", "RUNTIME_ERROR", "TIMEOUT",
    "DEPENDENCY_ERROR", "CONFIGURATION_ERROR", "TEST_COLLECTION_ERROR", "UNKNOWN",
)

def _clamp_confidence(v):
    try:
        return min(max(float(v), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0

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
