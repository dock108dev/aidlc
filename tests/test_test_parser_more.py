"""Extra tests for aidlc.test_parser framework detection and parsers."""

from aidlc.test_parser import parse_test_failures


def test_auto_detect_jest_from_fail_line():
    out = "FAIL src/a.test.js\nTests:       1 failed, 1 total\n"
    fs = parse_test_failures(out)
    assert fs and fs[0].framework == "jest"


def test_auto_detect_go_from_output():
    out = """go test ./pkg
--- FAIL: TestZot (0.01s)
    z.go:3: boom
FAIL
"""
    fs = parse_test_failures(out)
    assert fs and fs[0].framework == "go"


def test_auto_detect_cargo():
    out = """
---- my_test stdout ----
thread 'my_test' panicked at 'boom', src/lib.rs:42

failures:
    my_test
test result: FAILED. 1 failed
"""
    fs = parse_test_failures(out)
    assert fs and fs[0].framework == "cargo"
    assert fs[0].test_name == "my_test"


def test_auto_detect_gut():
    out = "GUT v1\n[FAILED] : test_player\nassertion failed\n[PASSED] other\n"
    fs = parse_test_failures(out)
    assert fs and fs[0].framework == "gut"


def test_auto_detect_rspec():
    out = """
1) MyClass does thing
     Failure/Error: expect(x).to eq(1)
     # ./spec/foo_spec.rb:12:in `block'
Finished in 0.1s
"""
    fs = parse_test_failures(out)
    assert fs and fs[0].framework == "rspec"


def test_pytest_short_format_and_error_block():
    out = """
tests/test_x.py::test_y FAILED

_____ test_y _____

tests/test_x.py:9: AssertionError: nope
"""
    fs = parse_test_failures(out, framework="pytest")
    assert any(f.test_name == "test_y" for f in fs)


def test_jest_stack_and_expect_assertion():
    out = """
● suite > case one
    expect(received).toBe(expected)
    at foo (/src/a.test.js:10:1)

Tests:       1 failed, 1 total
"""
    fs = parse_test_failures(out, framework="jest")
    assert fs
    assert "expect(" in fs[0].assertion or fs[0].file.endswith(".js")
