"""Turn a pytest log into a SWE-bench verdict.

The FAIL_TO_PASS / PASS_TO_PASS lists in the dataset were produced by SWE-bench's
own log parser, so test IDs have to be extracted exactly the same way (including
its quirks, e.g. parametrized IDs containing spaces are cut at the first space).
"""
import re
from dataclasses import dataclass, field

PASSED, FAILED, SKIPPED, ERROR, XFAIL = "PASSED", "FAILED", "SKIPPED", "ERROR", "XFAIL"
_STATUSES = (PASSED, FAILED, SKIPPED, ERROR, XFAIL)


def parse_pytest_log(log: str) -> dict[str, str]:
    """Map test ID -> status from `pytest -rA` output (same rules as SWE-bench's parse_log_pytest)."""
    status_map: dict[str, str] = {}
    for line in log.split("\n"):
        if not line.startswith(_STATUSES):
            continue
        if line.startswith(FAILED):
            line = line.replace(" - ", " ")
        parts = line.split()
        if len(parts) <= 1:
            continue
        status_map[parts[1]] = parts[0]
    return status_map


def test_files_from_patch(patch: str) -> list[str]:
    """Files a diff touches, in order (the post-image path for renames)."""
    files = []
    for m in re.finditer(r"^diff --git a/(\S+) b/(\S+)$", patch or "", re.M):
        if m.group(2) not in files:
            files.append(m.group(2))
    return files


# Data files a test patch may add; passing them to pytest is an error (same list as SWE-bench).
_NON_TEST_EXTS = (".json", ".png", "csv", ".txt", ".md", ".jpg", ".jpeg", ".pkl", ".yml", ".yaml", ".toml")


def test_directives(test_patch: str) -> list[str]:
    """The arguments to pass to pytest: the test files the test patch touches."""
    return [f for f in test_files_from_patch(test_patch) if not f.endswith(_NON_TEST_EXTS)]


@dataclass
class TestReport:
    __test__ = False

    f2p_passed: list[str] = field(default_factory=list)
    f2p_failed: list[str] = field(default_factory=list)
    p2p_passed: list[str] = field(default_factory=list)
    p2p_failed: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return not self.f2p_failed and not self.p2p_failed

    def summary(self) -> str:
        f2p = len(self.f2p_passed) + len(self.f2p_failed)
        p2p = len(self.p2p_passed) + len(self.p2p_failed)
        return f"FAIL_TO_PASS {len(self.f2p_passed)}/{f2p} passing, PASS_TO_PASS {len(self.p2p_passed)}/{p2p} passing"

    def to_dict(self) -> dict:
        return {
            "f2p_passed": len(self.f2p_passed), "f2p_total": len(self.f2p_passed) + len(self.f2p_failed),
            "p2p_passed": len(self.p2p_passed), "p2p_total": len(self.p2p_passed) + len(self.p2p_failed),
            # The failing IDs are what you need to debug a run; passing ones are implied.
            "f2p_failed": self.f2p_failed, "p2p_failed": self.p2p_failed,
        }


def grade(status_map: dict[str, str], fail_to_pass: list[str], pass_to_pass: list[str]) -> TestReport:
    """SWE-bench rule: a test counts as passed if PASSED/XFAIL, as failed if missing or FAILED/ERROR.

    SKIPPED tests count as neither. The task is resolved only if no FAIL_TO_PASS and
    no PASS_TO_PASS test failed.
    """
    report = TestReport()
    for tests, passed, failed in ((fail_to_pass, report.f2p_passed, report.f2p_failed),
                                  (pass_to_pass, report.p2p_passed, report.p2p_failed)):
        for test in tests:
            status = status_map.get(test)
            if status in (PASSED, XFAIL):
                passed.append(test)
            elif status is None or status in (FAILED, ERROR):
                failed.append(test)
    return report
