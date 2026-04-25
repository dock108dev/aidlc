"""Test failure parser for AIDLC validation loop.

Parses output from various test frameworks into structured TestFailure objects
that can be converted into fix issues.
"""

import re
from dataclasses import dataclass


@dataclass
class FailureReport:
    """A single test failure extracted from test output."""

    test_name: str
    file: str = ""
    line: int = 0
    assertion: str = ""
    stack_trace: str = ""
    framework: str = "unknown"

    def short_description(self) -> str:
        """One-line summary for issue titles."""
        if self.assertion:
            short = self.assertion[:80]
            return f"{self.test_name} — {short}"
        return self.test_name


def parse_test_failures(
    output: str, framework: str = "auto", max_failures: int = 20
) -> list[FailureReport]:
    """Parse test output into structured failures.

    Args:
        output: Raw test command output (stdout + stderr)
        framework: Hint for which parser to use ("pytest", "jest", "go", "auto")
        max_failures: Cap on failures to parse

    Returns:
        List of TestFailure objects
    """
    if not output or not output.strip():
        return []

    if framework == "auto":
        framework = _detect_framework(output)

    parsers = {
        "pytest": _parse_pytest,
        "jest": _parse_jest,
        "go": _parse_go,
        "cargo": _parse_cargo,
        "gut": _parse_gut,
        "rspec": _parse_rspec,
        "generic": _parse_generic,
    }

    parser = parsers.get(framework, _parse_generic)
    failures = parser(output)
    return failures[:max_failures]


def _detect_framework(output: str) -> str:
    """Auto-detect test framework from output patterns."""
    if "FAILED" in output and ("pytest" in output or "test session starts" in output):
        return "pytest"
    if "FAIL" in output and ("Tests:" in output or "Test Suites:" in output):
        return "jest"
    if "--- FAIL:" in output and "go test" in output.lower():
        return "go"
    if "failures:" in output and "test result:" in output:
        return "cargo"
    if "GUT" in output or "gut_cmdln" in output:
        return "gut"
    if "rspec" in output.lower() or "Failure/Error:" in output:
        return "rspec"
    return "generic"


def _parse_pytest(output: str) -> list[FailureReport]:
    """Parse pytest output."""
    failures = []
    # Match: FAILED tests/test_foo.py::TestClass::test_method - AssertionError: ...
    pattern = re.compile(
        r"FAILED\s+([\w/\\.]+)::(\S+)\s*[-—]\s*(.*?)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(output):
        file_path = match.group(1)
        test_name = match.group(2)
        assertion = match.group(3).strip()
        failures.append(
            FailureReport(
                test_name=test_name,
                file=file_path,
                assertion=assertion,
                framework="pytest",
            )
        )

    # If no FAILED lines, try the short format
    if not failures:
        short_pattern = re.compile(r"([\w/\\.]+)::(\S+)\s+FAILED", re.MULTILINE)
        for match in short_pattern.finditer(output):
            failures.append(
                FailureReport(
                    test_name=match.group(2),
                    file=match.group(1),
                    framework="pytest",
                )
            )

    # Try to extract assertion details from the verbose output
    error_blocks = re.split(r"_{5,}\s+(\S+)\s+_{5,}", output)
    for i in range(1, len(error_blocks), 2):
        name = error_blocks[i]
        block = error_blocks[i + 1] if i + 1 < len(error_blocks) else ""
        for f in failures:
            if f.test_name in name and not f.stack_trace:
                f.stack_trace = block[:500]

    return failures


def _parse_jest(output: str) -> list[FailureReport]:
    """Parse Jest output."""
    failures = []
    # Match: ● TestSuite > test name
    pattern = re.compile(r"●\s+(.+?)\s*\n\s*(.*?)(?=\n\s*●|\n\s*Tests?:)", re.DOTALL)
    for match in pattern.finditer(output):
        test_path = match.group(1).strip()
        details = match.group(2).strip()

        # Extract file/line from stack
        loc_match = re.search(r"at\s+\S+\s+\((.+?):(\d+):\d+\)", details)
        file_path = loc_match.group(1) if loc_match else ""
        line = int(loc_match.group(2)) if loc_match else 0

        # Extract assertion
        assert_match = re.search(r"(expect\(.+?\)\.[\w.]+\(.+?\))", details)
        assertion = assert_match.group(1) if assert_match else details[:200]

        failures.append(
            FailureReport(
                test_name=test_path,
                file=file_path,
                line=line,
                assertion=assertion,
                stack_trace=details[:500],
                framework="jest",
            )
        )

    # Fallback: FAIL lines
    if not failures:
        fail_pattern = re.compile(r"FAIL\s+(\S+)", re.MULTILINE)
        for match in fail_pattern.finditer(output):
            failures.append(
                FailureReport(
                    test_name=match.group(1),
                    file=match.group(1),
                    framework="jest",
                )
            )

    return failures


def _parse_go(output: str) -> list[FailureReport]:
    """Parse Go test output."""
    failures = []
    pattern = re.compile(
        r"--- FAIL:\s+(\S+)\s+\(.*?\)\n(.*?)(?=--- |FAIL\s|ok\s)",
        re.DOTALL,
    )
    for match in pattern.finditer(output):
        test_name = match.group(1)
        details = match.group(2).strip()

        loc_match = re.search(r"(\S+\.go):(\d+):", details)
        file_path = loc_match.group(1) if loc_match else ""
        line = int(loc_match.group(2)) if loc_match else 0

        failures.append(
            FailureReport(
                test_name=test_name,
                file=file_path,
                line=line,
                assertion=details[:200],
                stack_trace=details[:500],
                framework="go",
            )
        )

    return failures


def _parse_cargo(output: str) -> list[FailureReport]:
    """Parse Rust cargo test output."""
    failures = []
    pattern = re.compile(
        r"---- (\S+) stdout ----\n(.*?)(?=---- |\nfailures:)",
        re.DOTALL,
    )
    for match in pattern.finditer(output):
        test_name = match.group(1)
        details = match.group(2).strip()

        loc_match = re.search(r"thread '.*?' panicked at '(.*?)',\s+(\S+):(\d+)", details)
        assertion = loc_match.group(1) if loc_match else details[:200]
        file_path = loc_match.group(2) if loc_match else ""
        line = int(loc_match.group(3)) if loc_match else 0

        failures.append(
            FailureReport(
                test_name=test_name,
                file=file_path,
                line=line,
                assertion=assertion,
                stack_trace=details[:500],
                framework="cargo",
            )
        )

    return failures


def _parse_gut(output: str) -> list[FailureReport]:
    """Parse Godot GUT test output."""
    failures = []
    pattern = re.compile(
        r"\[FAILED\]\s*:?\s*(.+?)(?:\n|$)(.*?)(?=\[FAILED\]|\[PASSED\]|$)",
        re.DOTALL,
    )
    for match in pattern.finditer(output):
        test_name = match.group(1).strip()
        details = match.group(2).strip()

        failures.append(
            FailureReport(
                test_name=test_name,
                assertion=details[:200],
                stack_trace=details[:500],
                framework="gut",
            )
        )

    return failures


def _parse_rspec(output: str) -> list[FailureReport]:
    """Parse Ruby RSpec output."""
    failures = []
    pattern = re.compile(
        r"\d+\)\s+(.+?)\n\s+Failure/Error:\s*(.*?)(?=\n\s+\d+\)|\nFinished)",
        re.DOTALL,
    )
    for match in pattern.finditer(output):
        test_name = match.group(1).strip()
        details = match.group(2).strip()

        loc_match = re.search(r"#\s+(\S+):(\d+)", details)
        file_path = loc_match.group(1) if loc_match else ""
        line = int(loc_match.group(2)) if loc_match else 0

        failures.append(
            FailureReport(
                test_name=test_name,
                file=file_path,
                line=line,
                assertion=details[:200],
                stack_trace=details[:500],
                framework="rspec",
            )
        )

    return failures


def _parse_generic(output: str) -> list[FailureReport]:
    """Fallback parser for unknown frameworks."""
    failures = []

    # Look for common failure patterns
    patterns = [
        re.compile(r"(?:FAIL|FAILED|ERROR|FAILURE)[\s:]+(.+?)(?:\n|$)", re.IGNORECASE),
        re.compile(r"(?:assert|expect).*?(?:fail|error).*?:?\s*(.+?)(?:\n|$)", re.IGNORECASE),
    ]

    seen = set()
    for pattern in patterns:
        for match in pattern.finditer(output):
            name = match.group(1).strip()[:120]
            if name and name not in seen:
                seen.add(name)
                failures.append(
                    FailureReport(
                        test_name=name,
                        framework="generic",
                    )
                )

    return failures
