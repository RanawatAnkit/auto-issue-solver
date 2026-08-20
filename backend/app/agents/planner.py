import logging
from typing import List
from app.services.llm_service import LLMService
from app.agents.schemas import IssueAnalysis, RetrievedContextChunk, SolutionPlan

logger = logging.getLogger(__name__)

class PlannerAgent:
    def __init__(self):
        self.llm = LLMService()

    def create_plan(self, analysis: IssueAnalysis, context: List[RetrievedContextChunk], repo_name: str, available_files: List[str] = None) -> SolutionPlan:
        system = (
            "You are an architecture planner generating a clean code modification schema.\n"
            "You MUST only target files identified explicitly in the Retrieved Context provided OR the explicit Available Files list.\n"
            "Limit assumptions. Do not write the code natively. Just output the structural Plan json.\n"
            "Do NOT invent file paths. If a file does not exist, you must use operation='create'. If modifying, it MUST exist."
        )
        
        context_str = "\n---CONTEXT---\n".join([f"[{c.file_path}]\n{c.content}" for c in context])
        avail_str = "\n".join(available_files or [])
        
        user = (
            f"Repository: {repo_name}\n"
            f"Available Files:\n{avail_str}\n\n"
            f"Analysis:\n{analysis.model_dump_json(indent=2)}\n"
            f"Context: {context_str}"
        )
        plan = self.llm.generate_structured(system, user, SolutionPlan)
        
        self.validate_plan(plan, available_files or [])
        return plan

    def validate_plan(self, plan: SolutionPlan, available_files: List[str]):
        if not plan.affected_files:
            raise ValueError("Solution Plan validation failed: Empty affected files list.")
            
        for f in plan.affected_files:
            if ".." in f.path or f.path.startswith("/"):
                raise ValueError(f"Path Traversal security violation rejected: {f.path}")
                
            if ".git/" in f.path or f.path == ".git":
                raise ValueError(f"Git restriction active avoiding security bypass: {f.path}")
                
            if getattr(f, "operation", "modify") == "create":
                if f.path in available_files:
                    raise ValueError(f"Planner attempted to 'create' an already existing file: {f.path}")
            elif getattr(f, "operation", "modify") == "modify":
                if available_files and f.path not in available_files:
                    raise ValueError(f"Plan references out-of-context missing untracked file: {f.path}. Modifications require existing tracked files.")
            else:
                pass # deletions are not completely tracked right now natively.
        
        if not plan.acceptance_criteria:
            raise ValueError("Plan missing acceptance criteria metrics.")
