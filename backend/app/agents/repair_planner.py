from app.services.llm_service import LLMService
from app.agents.schemas import RepairPlan

class RepairPlanner:
    def __init__(self):
        self.llm = LLMService()
        
    def generate_plan(self, analysis_json: str, previous_plan: str, context: str) -> RepairPlan:
        prompt = f'''
        Construct a targeted limited scope Repair Plan rectifying the provided test execution failure directly.
        Failure Analysis: {analysis_json}
        Original Context: {context[:2000]}
        Previous Attempt Plan: {previous_plan[:1000]}
        
        Draft a safe, minimal patch strategy exclusively rectifying the root cause without escalating file scope structurally!
        Identify tests natively mapping resolution validation securely limits.
        '''
        
        plan = self.llm.generate_structured(prompt, RepairPlan)
        return plan