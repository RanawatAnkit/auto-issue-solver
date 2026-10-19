import pytest
res = pytest.main(["-q"])
with open("FINAL_TEST_OUT.txt", "w", encoding="utf-8") as f:
    f.write("ALL TESTS PASSED!" if res == 0 else f"TESTS FAILED (code {res})")
