from pydantic import BaseModel, Field
from typing import List, Optional

class IssueAnalysis(BaseModel):
    problem_summary: str = Field(description="Summary of the issue.")
    expected_behavior: str = Field(description="What should occur typically.")
    current_behavior: str = Field(description="What is broken or occurring currently.")
    affected_areas: List[str] = Field(default_factory=list, description="Likely files/modules affected.")
    constraints: List[str] = Field(description="Technical boundaries.")

class RetrievedContextChunk(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    content: str
    score: float

class FileChangePlan(BaseModel):
    path: str = Field(description="Relative path to modify or create.")
    operation: str = Field(description="Must be exactly: 'modify', 'create', or 'delete'.")
    reason: str = Field(description="Why this file needs modification.")
    symbols: List[str] = Field(description="Specific classes or functions targeted.")
    planned_changes: List[str] = Field(description="Exact logical changes.")

class SolutionPlan(BaseModel):
    summary: str = Field(description="High-level evaluation metric map.")
    affected_files: List[FileChangePlan]
    tests: List[str] = Field(description="Tests to run confirming functionality.")
    dependencies: List[str] = Field(description="Packages implicitly reliant on modification.")
    risks: List[str] = Field(description="Risks of regressions.")
    assumptions: List[str] = Field(description="Assumptions constructed unverified.")
    acceptance_criteria: List[str] = Field(description="Final validations.")

class CodeChange(BaseModel):
    file_path: str
    operation: str = Field(description="modify, create, or delete")
    description: str
    patch: str = Field(description="The exact new content organically patched")

class CodeChangePlan(BaseModel):
    changes: List[CodeChange]

from pydantic import BaseModel, Field
from typing import List, Optional

class FailureAnalysis(BaseModel):
    failure_type: str = Field(description="One of: ASSERTION_ERROR, IMPORT_ERROR, SYNTAX_ERROR, TYPE_ERROR, RUNTIME_ERROR, TIMEOUT, DEPENDENCY_ERROR, CONFIGURATION_ERROR, TEST_COLLECTION_ERROR, UNKNOWN")
    root_cause: str = Field(description="Detailed explanation of why the test failed based on actual evidence")
    affected_files: List[str] = Field(description="Files implicated in the failure traces")
    affected_symbols: List[str] = Field(description="Functions or classes involved in the failure")
    evidence: List[str] = Field(description="Exact snippets from the test output proving the root cause")
    suggested_fix: str = Field(description="High-level approach suggested to resolve the failure natively limiting scope safely")
    confidence: float = Field(description="0.0 to 1.0 confidence score classifying the root_cause securely")
    unresolved_questions: List[str] = Field(description="Missing context questions preventing absolute resolution")

class RepairPlan(BaseModel):
    root_cause: str = Field(description="Reiterated root cause guiding the repair sequence")
    affected_files: List[str] = Field(description="Strict list of files to actively patch")
    changes: List[str] = Field(description="Logical descriptions mapping how each file is repaired tracking bounds strictly")
    tests_to_run: List[str] = Field(description="Test suites executing natively to ensure patch limits succeed optimally")
    reasoning: str = Field(description="Justification connecting the code changes natively linking the root_cause resolving logic")
    confidence: float = Field(description="Likelihood of full repair success limiting regression scopes natively")
