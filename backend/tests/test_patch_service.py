import os
import tempfile
import pytest
from app.agents.schemas import CodeChangePlan, CodeChange, TextEdit
from app.services.patch_service import PatchService, PatchError


@pytest.fixture
def ws():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "app.py"), "w", newline="") as f:
            f.write("def a():\n    return 1\n\n\ndef b():\n    return 1\n")
        yield td


def _read(ws, rel):
    with open(os.path.join(ws, rel), newline="") as f:
        return f.read()


def _modify(path, *edits):
    return CodeChangePlan(changes=[CodeChange(file_path=path, operation="modify",
                                              edits=[TextEdit(search=s, replace=r) for s, r in edits])])


def test_search_replace_edit_applies_and_reports_real_diff(ws):
    res = PatchService(ws).apply_plan(_modify("app.py", ("def a():\n    return 1", "def a():\n    return 2")))
    assert _read(ws, "app.py").startswith("def a():\n    return 2\n")
    assert "def b():\n    return 1" in _read(ws, "app.py")  # rest of the file preserved
    assert "-    return 1" in res["summary"] and "+    return 2" in res["summary"]
    assert res["changed_files"] == ["app.py"]


def test_ambiguous_search_is_rejected(ws):
    with pytest.raises(PatchError, match="occurs 2 times"):
        PatchService(ws).apply_plan(_modify("app.py", ("    return 1", "    return 2")))


def test_search_text_not_in_file_is_rejected(ws):
    with pytest.raises(PatchError, match="not found"):
        PatchService(ws).apply_plan(_modify("app.py", ("def missing():", "def x():")))


def test_trailing_whitespace_differences_are_tolerated(ws):
    PatchService(ws).apply_plan(_modify("app.py", ("def b():   \n    return 1  ", "def b():\n    return 3")))
    assert "def b():\n    return 3" in _read(ws, "app.py")


def test_reflowed_multiline_search_matches_unique_span():
    # Small models often collapse a multi-line signature onto one line in 'search'.
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "svc.py"), "w", newline="") as f:
            f.write("def list_tasks(\n    self,\n    status=None,\n    page=1,\n) -> list:\n    return []\n")
        PatchService(td).apply_plan(_modify(
            "svc.py", ("def list_tasks(self, status=None, page=1,) -> list:",
                       "def list_tasks(self, status=None, page=1, overdue_only=False) -> list:")))
        assert _read(td, "svc.py") == "def list_tasks(self, status=None, page=1, overdue_only=False) -> list:\n    return []\n"


def test_whitespace_insensitive_match_must_be_unique():
    # Both lines are identical once whitespace is ignored; an ambiguous match must be rejected.
    with tempfile.TemporaryDirectory() as td:
        content = "result = compute(alpha, beta, gamma)\nresult = compute(alpha,  beta,  gamma)\n"
        with open(os.path.join(td, "m.py"), "w", newline="") as f:
            f.write(content)
        with pytest.raises(PatchError, match="not found"):
            PatchService(td).apply_plan(_modify("m.py", ("result = compute(alpha,beta,gamma)", "result = 0")))
        assert _read(td, "m.py") == content


def test_not_found_feedback_quotes_the_closest_real_text():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "svc.py"), "w", newline="") as f:
            f.write("class S:\n    def list_tasks(\n        self,\n        page: int = 1,\n        page_size: int = 20,\n    ) -> list:\n        return []\n")
        # Model dropped the trailing comma while reflowing the signature.
        with pytest.raises(PatchError) as exc:
            PatchService(td).apply_plan(_modify("svc.py", ("def list_tasks(self, page: int = 1, page_size: int = 20) -> list:", "x")))
        assert "page_size: int = 20,\n    ) -> list:" in str(exc.value)


def test_edit_targeting_text_changed_by_earlier_edit_is_explained(ws):
    plan = _modify("app.py", ("def a():\n    return 1", "def a():\n    return 2"), ("def a():\n    return 1", "def a():\n    return 3"))
    with pytest.raises(PatchError, match="already changed by an earlier edit"):
        PatchService(ws).apply_plan(plan)


def test_edit_producing_invalid_python_is_rejected_with_location(ws):
    with pytest.raises(PatchError, match=r"not valid Python .* line 1"):
        PatchService(ws).apply_plan(_modify("app.py", ("def a():\n    return 1", "def a(:\n    return 1")))
    assert "def a():" in _read(ws, "app.py")


def test_crlf_line_endings_are_preserved():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "w.py"), "w", newline="") as f:
            f.write("x = 1\r\ny = 2\r\n")
        PatchService(td).apply_plan(_modify("w.py", ("y = 2", "y = 3")))
        assert _read(td, "w.py") == "x = 1\r\ny = 3\r\n"


def test_truncated_full_file_rewrite_is_rejected():
    # Regression: small models returned partial files that silently replaced whole modules/tests.
    with tempfile.TemporaryDirectory() as td:
        original = "".join(f"def f{i}():\n    return {i}\n" for i in range(30))
        with open(os.path.join(td, "big.py"), "w", newline="") as f:
            f.write(original)
        plan = CodeChangePlan(changes=[CodeChange(file_path="big.py", operation="modify", content="def f0():\n    return 0\n")])
        with pytest.raises(PatchError, match="shrink"):
            PatchService(td).apply_plan(plan)
        assert _read(td, "big.py") == original


def test_create_refuses_to_overwrite_existing_file(ws):
    plan = CodeChangePlan(changes=[CodeChange(file_path="app.py", operation="create", content="x = 1\n")])
    with pytest.raises(PatchError, match="already exists"):
        PatchService(ws).apply_plan(plan)


def test_modify_of_missing_file_is_rejected(ws):
    with pytest.raises(PatchError, match="does not exist"):
        PatchService(ws).apply_plan(_modify("nope.py", ("a", "b")))


def test_noop_change_is_rejected(ws):
    with pytest.raises(PatchError, match="does not alter"):
        PatchService(ws).apply_plan(_modify("app.py", ("def a():", "def a():")))


def test_failed_change_leaves_workspace_untouched(ws):
    plan = CodeChangePlan(changes=[
        CodeChange(file_path="new.py", operation="create", content="print('hi')\n"),
        CodeChange(file_path="app.py", operation="modify", edits=[TextEdit(search="not there", replace="x")]),
    ])
    with pytest.raises(PatchError):
        PatchService(ws).apply_plan(plan)
    assert not os.path.exists(os.path.join(ws, "new.py"))


def test_check_plan_never_writes(ws):
    PatchService(ws).check_plan(_modify("app.py", ("def a():\n    return 1", "def a():\n    return 9")))
    assert "return 9" not in _read(ws, "app.py")


def test_dotdot_inside_a_filename_is_not_traversal(ws):
    plan = CodeChangePlan(changes=[CodeChange(file_path="notes..v2.py", operation="create", content="x = 1\n")])
    PatchService(ws).apply_plan(plan)
    assert os.path.exists(os.path.join(ws, "notes..v2.py"))


def test_binary_content_is_rejected(ws):
    plan = CodeChangePlan(changes=[CodeChange(file_path="blob.py", operation="create", content="a\x00b")])
    with pytest.raises(PatchError, match="binary"):
        PatchService(ws).apply_plan(plan)


def test_legacy_patch_field_still_accepted():
    change = CodeChange(file_path="x.py", operation="create", description="", patch="print(1)")
    assert change.content == "print(1)"
