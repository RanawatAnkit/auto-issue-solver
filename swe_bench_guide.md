# SWE-bench Evaluation Guide for Auto Issue Solver

## 1. What is SWE-bench?

**SWE-bench** (Software Engineering Benchmark) is the **industry-standard benchmark** used by top AI research labs (OpenAI, Anthropic, Google DeepMind, Cognition/Devin) to evaluate autonomous coding agents on real-world software engineering tasks.

### Why was it created?
Traditional coding benchmarks (like HumanEval or MBPP) test simple isolated Python functions (e.g., *"write a function to reverse a string"*). Real software engineering isn't like that:
- Real engineering requires understanding an entire 100,000-line codebase.
- Reading an issue reported by a human user with informal language and tracebacks.
- Finding the exact files and functions to modify.
- Modifying the code without breaking existing tests.

### How does SWE-bench work?
SWE-bench collected **thousands of real GitHub issues and merged pull requests** from top open-source Python repositories (such as `django/django`, `sympy/sympy`, `pytest-dev/pytest`, `matplotlib/matplotlib`, `psf/requests`, `scikit-learn/scikit-learn`).

Each benchmark task instance contains:
1. `repo`: The repository (e.g., `psf/requests`).
2. `base_commit`: The commit in git history **before** the bug was fixed.
3. `problem_statement`: The exact text of the GitHub issue filed by a user.
4. `test_patch`: The secret unit test written by the maintainer in the real PR that tests the fix.
5. `patch`: The human reference fix (used for comparison).

---

## 2. Why Adding SWE-bench is a Game-Changer for Your Project

For a college final-year capstone project or engineering portfolio:
- **Instant Credibility**: Evaluators and professors often ask: *"How do you know your agent actually works on diverse real-world software, not just your own toy project?"*
- **Quantitative Metrics**: Instead of just saying *"it works on our demo"*, you can present a benchmark report:
  > *"Our Auto Issue Solver achieved an evaluation score on SWE-bench Lite instances, demonstrating end-to-end repository indexing, patch generation, and automated validation."*
- **Direct Alignment**: Your `auto-issue-solver` architecture (Issue Analyzer ➔ Context Builder ➔ Planner ➔ Coding Agent ➔ Test Runner) is **already designed exactly like a SWE-bench agent!**

---

## 3. The SWE-bench Variants: Which One to Choose?

| Dataset | Total Instances | Average Evaluation Time | Best For |
| :--- | :--- | :--- | :--- |
| **SWE-bench Full** | 2,294 tasks | ~100+ hours (thousands of Docker builds) | Production frontier models with huge compute clusters |
| **SWE-bench Lite** | 300 tasks | ~10-15 hours | Academic research and benchmark papers |
| **SWE-bench Mini (5-10 tasks)** *(Recommended)* | 5 to 10 curated tasks | **15 - 30 minutes** on your local machine | **College demos, presentation slides, and fast local validation** |

> [!TIP]
> For your project demo, evaluate on **5 to 10 curated tasks** from SWE-bench Lite (e.g. from `psf/requests`, `pallets/flask`, `pytest-dev/pytest`). You can show the real benchmark data, run the evaluation live or display the evaluation report!

---

## 4. How `auto-issue-solver` Fits into SWE-bench

Here is how each SWE-bench concept maps 1-to-1 to your existing codebase:

```mermaid
graph LR
    subgraph SWE-bench Instance
        A[problem_statement] --> D[auto-issue-solver Pipeline]
        B[base_commit & repo] --> D
    end
    subgraph Auto-Issue-Solver
        D --> E[Issue Analyzer & Context Builder]
        E --> F[Planner & Coding Agent]
        F --> G[Generated Patch / Git Diff]
    end
    subgraph Evaluation
        G --> H[Apply Patch to Repository]
        I[SWE-bench test_patch] --> H
        H --> J[Run pytest in Docker Sandbox]
        J --> K{All Tests Pass?}
        K -->|YES| L[RESOLVED (Success)]
        K -->|NO| M[FAILED]
    end
```

| SWE-bench Concept | Component in Your Project |
| :--- | :--- |
| **Task / Problem Statement** | `Issue.title` and `Issue.body` fed to `IssueAnalyzer` |
| **Codebase Context** | `RepositoryScanner` + `CodeRetriever` (SentenceTransformers vector search) |
| **Code Generation** | `PlannerAgent` + `CodingAgent` (Ollama `qwen2.5-coder` or Gemini) |
| **Patch Application** | `PatchService.apply_plan()` |
| **Verification Sandbox** | `TestRunner` in Docker (`test-sandbox:latest`) |
| **Result Metric** | Pass/Fail boolean (`RESOLVED` if `FAIL_TO_PASS` tests pass without regression) |

---

## 5. How We Will Implement It: 3 Clean Components

We don't need to rewrite any of your core system! We simply build a lightweight **Evaluation Harness** on top of your existing services:

### Component 1: `swe_bench_loader.py` (Dataset Loader)
- Downloads task instances from the official HuggingFace dataset (`princeton-nlp/SWE-bench_Lite`) or loads a local JSON file containing 5-10 curated tasks.
- For each task, clones the repository at the specified `base_commit`.

### Component 2: `swe_bench_runner.py` (Execution Adapter)
- Feeds `problem_statement` into your existing `IssueAnalyzer`, `ContextBuilder`, and `CodingAgent`.
- Obtains the agent's generated patch (`.patch` file).

### Component 3: `swe_bench_evaluator.py` (Test & Scoring Engine)
- Applies the agent's patch inside your Docker sandbox.
- Applies the ground-truth `test_patch`.
- Executes `pytest` to verify:
  1. **`FAIL_TO_PASS`**: Tests that previously failed now pass.
  2. **`PASS_TO_PASS`**: Existing tests did not break (no regressions).
- Outputs a clean JSON report and markdown summary table:
  ```text
  ================= SWE-bench Evaluation Results =================
  Task ID                       Repo              Status     Time
  ----------------------------------------------------------------
  psf__requests-2148            psf/requests      RESOLVED   45s
  pallets__flask-4045           pallets/flask     RESOLVED   52s
  pytest-dev__pytest-5221       pytest            RESOLVED   68s
  ...
  Total Resolved: 4/5 (80.0%)
  ================================================================
  ```

---

## 6. What Do You Need to Do?

Nothing complicated! Here is the plan:

1. **Step 1 (Ready to start now)**:
   We will create a curated dataset of **5 realistic, lightweight SWE-bench tasks** (stored in `backend/eval/swe_bench_tasks.json`) so you don't have to download 10GB of HuggingFace data.
2. **Step 2**:
   We will build `backend/eval/run_swe_bench.py` which plugs your existing `CodingAgent` and `TestRunner` into these tasks.
3. **Step 3**:
   You can run it with a single command:
   ```powershell
   python -m eval.run_swe_bench --provider ollama --max-tasks 3
   ```
4. **Step 4**:
   We can also add an **"Evaluation"** tab or summary card in the frontend dashboard so you can show the benchmark score on screen during your presentation!

---

## Summary
SWE-bench evaluates your agent on real GitHub bugs against hidden unit tests. Because your project already has the complete analyzer, planner, coder, and test runner, adding SWE-bench is simply connecting a benchmark runner to test your existing pipeline!
