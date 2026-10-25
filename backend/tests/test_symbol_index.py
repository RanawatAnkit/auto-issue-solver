"""SymbolIndex: Python structure lookups over a real (temporary) checkout."""
import os
import shutil
import tempfile
import pytest
from app.services.symbol_index import SymbolIndex

FILES = {
    "pkg/models.py": (
        "import os\n"                                   # 1
        "\n"                                            # 2
        "class Base:\n"                                 # 3
        "    def save(self):\n"                         # 4
        "        return 'base'\n"                       # 5
        "\n"                                            # 6
        "    def delete(self):\n"                       # 7
        "        return 'delete'\n"                     # 8
        "\n"                                            # 9
        "class Middle(Base):\n"                         # 10
        "    pass\n"                                    # 11
        "\n"                                            # 12
        "class Order(Middle):\n"                        # 13
        "    \"\"\"An order.\"\"\"\n"                   # 14
        "    def save(self, force=False):\n"            # 15
        "        return 'order'\n"                      # 16
        "\n"                                            # 17
        "    class Meta:\n"                             # 18
        "        ordering = ['id']\n"                   # 19
        "        def describe(self):\n"                 # 20
        "            return 'meta'\n"                   # 21
        "\n"                                            # 22
        "    async def refresh(self):\n"                # 23
        "        def helper():\n"                       # 24
        "            return 1\n"                        # 25
        "        return helper()\n"                     # 26
        "\n"                                            # 27
        "@staticmethod\n"                               # 28
        "@other\n"                                      # 29
        "def make_order(x):\n"                          # 30
        "    total = x * 2  # compute total\n"          # 31
        "    return total\n"                            # 32
    ),
    "pkg/views.py": "from pkg import models\n\nclass OrderView(models.Base):\n    def get(self):\n        return models.make_order(1)\n",
    "pkg/broken.py": "def ok():\n    return 1\n\ndef broken(:\n    pass\n",
    "README.md": "# readme\nline two\n",
    "venv/lib/ignored.py": "def ignored():\n    pass\n",
}


@pytest.fixture
def checkout():
    root = tempfile.mkdtemp(prefix="symidx-")
    for rel, text in FILES.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def index(checkout):
    return SymbolIndex.build(checkout, list(FILES))


def _q(symbols):
    return sorted(s.qualname for s in symbols)


def test_indexes_classes_methods_nested_and_async(index):
    names = {s.qualname: s.kind for s in index.symbols_in_file("pkg/models.py")}
    assert names["Order"] == "class"
    assert names["Order.save"] == "method"
    assert names["Order.Meta"] == "class"              # nested class
    assert names["Order.Meta.describe"] == "method"    # method of a nested class
    assert names["Order.refresh"] == "method"          # async method
    assert names["make_order"] == "function"
    assert "Order.refresh.helper" not in names and "helper" not in names  # local functions are not addressable


def test_line_ranges_include_decorators(index):
    (fn,) = index.find_function("make_order")
    assert (fn.start_line, fn.end_line) == (28, 32)
    (cls,) = index.find_class("Order")
    assert (cls.start_line, cls.end_line) == (13, 26)
    assert cls.bases == ("Middle",)


def test_attribute_bases_are_recorded(index):
    (view,) = index.find_class("OrderView")
    assert view.bases == ("Base",)


def test_find_method_prefers_own_definition(index):
    found = index.find_method_in_class("save", "Order")
    assert [(s.qualname, s.start_line) for s in found] == [("Order.save", 15)]


def test_find_method_walks_inheritance(index):
    # Order -> Middle -> Base: 'delete' is only defined two levels up.
    found = index.find_method_in_class("delete", "Order")
    assert _q(found) == ["Base.delete"]
    assert _q(index.find_method_in_class("save", "OrderView")) == ["Base.save"]
    assert index.find_method_in_class("missing", "Order") == []
    assert index.find_method_in_class("save", "NoSuchClass") == []


def test_resolve_handles_module_prefixes_calls_and_files(index):
    assert _q(index.resolve("pkg.models.Order.save")) == ["Order.save"]
    assert _q(index.resolve("make_order()")) == ["make_order"]
    assert _q(index.resolve("save", "pkg/models.py")) == ["Base.save", "Order.save"]
    assert index.resolve("Order.save", "pkg/views.py") == []
    assert index.resolve("made_up_function") == []
    assert index.resolve("") == []


def test_enclosing_symbol_is_innermost(index):
    assert index.enclosing_symbol("pkg/models.py", 21).qualname == "Order.Meta.describe"
    assert index.enclosing_symbol("pkg/models.py", 25).qualname == "Order.refresh"
    assert index.enclosing_symbol("pkg/models.py", 1) is None


def test_syntax_error_file_is_recorded_but_lines_stay_readable(index):
    assert "pkg/broken.py" in index.parse_errors
    assert index.symbols_in_file("pkg/broken.py") == []
    assert "pkg/broken.py" in index.python_files
    assert index.line_count("pkg/broken.py") == 6
    assert "def broken(:" in index.read_lines("pkg/broken.py", 4, 4)


def test_non_python_files_are_known_by_path_only(index):
    assert index.has_file("README.md")
    assert "README.md" not in index.python_files
    assert index.line_count("README.md") == 3
    assert not index.has_file("pkg/missing.py")
    assert index.line_count("pkg/missing.py") is None


def test_ignored_directories_are_not_indexed(index):
    assert index.find_function("ignored") == []


def test_outline_shows_signatures_not_bodies(index):
    (cls,) = index.find_class("Order")
    outline = index.outline(cls)
    assert "class Order(Middle):" in outline
    assert "def save(self, force=False):" in outline
    assert "async def refresh(self):" in outline
    assert "return 'order'" not in outline


def test_find_text_reports_enclosing_symbol(index):
    hits = index.find_text("compute total")
    assert [(p, n, s.qualname) for p, n, s in hits] == [("pkg/models.py", 31, "make_order")]
    assert index.find_text("   ") == []


def test_read_lines_is_numbered_and_bounded(index):
    text = index.read_lines("pkg/models.py", 3, 100, max_lines=2)
    assert text.splitlines()[0].strip().startswith("3 | class Base")
    assert "more lines" in text


def test_oversized_files_are_skipped(checkout, monkeypatch):
    monkeypatch.setattr("app.config.settings.MAX_FILE_SIZE_MB", 0)
    index = SymbolIndex.build(checkout, list(FILES))
    assert index.symbols == []
    assert index.parse_errors["pkg/models.py"] == "file too large to index"


def test_building_the_index_does_not_modify_the_checkout(checkout):
    def snapshot():
        out = {}
        for d, _, names in os.walk(checkout):
            for n in names:
                p = os.path.join(d, n)
                out[p] = (os.path.getsize(p), os.path.getmtime(p))
        return out
    before = snapshot()
    SymbolIndex.build(checkout, list(FILES))
    assert snapshot() == before
