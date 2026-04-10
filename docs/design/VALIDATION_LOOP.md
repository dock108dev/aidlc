# Validation Loop — Iterative Test-and-Fix Until Stable

## Problem

AIDLC implements all issues then moves to finalization, but never checks if the project
actually **works**. The implementer runs unit tests per-issue, but doesn't do E2E/integration
testing or loop back to fix systemic problems. Result: code exists but may not function.

## Solution

Add a **validation loop** between implementation and finalization that:
1. Runs stack-specific test suites (unit → integration → E2E)
2. Parses failures into new fix issues
3. Implements fixes
4. Re-tests
5. Repeats until stable or max iterations reached

## Architecture

```
IMPLEMENTING → VALIDATING (new) → FINALIZING → DONE
                   ↑       |
                   └───────┘  (loop if tests fail)
```

## Implementation Prompts (8 tasks, estimated order)

### Prompt 1: Test Profiles Registry (~150 lines)
**File:** `aidlc/test_profiles.py`

Create a registry of stack-specific test commands organized by tier:
- Unit tests (fast, isolated)
- Integration tests (module interactions)
- E2E tests (full system — Playwright, Godot headless, XCUITest, etc.)
- Build validation (does it compile/launch)

Per-stack profiles for: Python, JavaScript/TypeScript, Rust, Go, Ruby, Java,
Godot (GDScript), Unity (C#), Swift/iOS, C++.

Each profile: detection logic, commands per tier, output format, failure parser hint.

Users can override via `.aidlc/config.json` `test_profiles` key.

### Prompt 2: Test Failure Parser (~200 lines)
**File:** `aidlc/test_parser.py`

Parse test output from various frameworks into structured `TestFailure` objects:
- test_name, file, line, assertion, stack_trace, severity
- Framework-specific parsers: pytest, jest, cargo test, go test, GUT (Godot),
  NUnit (Unity), XCTest (Swift), Playwright
- Fallback: regex-based generic parser for unknown frameworks
- Cap parsed failures at `test_failure_parse_limit` (default 20)

### Prompt 3: Validation Issue Generator (~100 lines)
**File:** `aidlc/validation_issues.py`

Convert `TestFailure` objects into AIDLC `Issue` objects:
- Title: "Fix: {test_name} — {short_assertion}"
- Description: full error context, stack trace, file location
- Priority: high
- Labels: ["validation", "auto-generated"]
- Acceptance criteria: "Test {name} passes", "No new failures introduced"
- Dependency on the parent issue that likely caused the failure
- Dedup: don't create issues for the same test failure twice

### Prompt 4: Add VALIDATING Phase to Models (~30 lines)
**File:** `aidlc/models.py` (modify)

- Add `VALIDATING = "validating"` to RunPhase (between VERIFYING and FINALIZING)
- Add state fields:
  - `validation_cycles: int = 0`
  - `validation_issues_created: int = 0`
  - `validation_test_results: list = []` (per-cycle pass/fail summaries)
- Update `to_dict()` / `from_dict()`

### Prompt 5: Validation Engine (~250 lines)
**File:** `aidlc/validator.py`

Core validation loop engine:
```python
class Validator:
    def run(self) -> bool:
        """Run validation loop. Returns True if project is stable."""
        for cycle in range(max_validation_cycles):
            # 1. Run test profile tiers (unit → integration → e2e)
            results = self._run_test_tiers()
            
            # 2. If all pass, we're done
            if results.all_passed:
                return True
            
            # 3. Parse failures
            failures = self._parse_failures(results)
            
            # 4. Generate fix issues
            new_issues = self._create_fix_issues(failures)
            
            # 5. Run implementer on just the new issues
            self._implement_fixes(new_issues)
            
            # 6. Check if we're making progress
            if not self._making_progress(results):
                break
        
        return False
```

### Prompt 6: Wire Validation into Runner (~40 lines)
**File:** `aidlc/runner.py` (modify)

Insert VALIDATING phase between VERIFYING and FINALIZING:
```python
# After implementer.run():
if not plan_only and config.get("validation_enabled", True):
    validator = Validator(state, run_dir, config, cli, project_context, logger)
    is_stable = validator.run()
    if is_stable:
        logger.info("Validation complete: project is stable")
    else:
        logger.warning(f"Validation incomplete after {state.validation_cycles} cycles")
```

Add `--skip-validation` flag to CLI.

### Prompt 7: Config Defaults + CLI Flags (~30 lines)
**Files:** `aidlc/config.py`, `aidlc/__main__.py` (modify)

Config defaults:
```python
"validation_enabled": True,
"validation_max_cycles": 3,
"validation_batch_size": 10,        # max fix issues per cycle
"test_profile_mode": "progressive", # unit → integration → e2e
"e2e_test_command": None,           # override for E2E specifically
"build_validation_command": None,   # "godot --headless --script" etc
```

CLI: `--skip-validation` on `run`, validation info in `status`

### Prompt 8: Tests (~150 lines)
**File:** `tests/test_validator.py`, `tests/test_test_parser.py`, `tests/test_test_profiles.py`

- Test profile detection for Python, JS, Godot projects
- Test failure parsing for pytest, jest output formats
- Validation issue generation from failures
- Validation loop with mock CLI (passes after 1 fix cycle)
- Validation loop exit on max cycles
- Config override behavior

## Stack-Specific E2E Approaches

| Stack | E2E Tool | Detection | Command |
|-------|----------|-----------|---------|
| Web (JS/TS) | Playwright | `playwright.config.*` | `npx playwright test` |
| Web (JS/TS) | Cypress | `cypress.config.*` | `npx cypress run` |
| Python web | Playwright | `conftest.py` + playwright | `pytest --playwright` |
| Godot | Headless scene | `project.godot` | `godot --headless --script res://tests/run_tests.gd` |
| Unity | Play mode | `*.asmdef` in Tests/ | `unity -runTests -testPlatform PlayMode` |
| Swift/iOS | XCUITest | `*.xcodeproj` | `xcodebuild test -scheme X` |
| Rust | cargo test | `Cargo.toml` | `cargo test --test '*'` |
| Go | go test | `go.mod` | `go test ./... -tags=e2e` |

## Iteration Control

- Max 3 validation cycles (configurable)
- Max 10 fix issues per cycle (prevents explosion)
- Exit if pass rate drops between cycles (getting worse, not better)
- Exit if same tests keep failing across cycles (design problem, not implementation)
- All validation issues tracked separately in state for reporting

## Config Example

```json
{
  "validation_enabled": true,
  "validation_max_cycles": 3,
  "test_profile_mode": "progressive",
  "e2e_test_command": "npx playwright test",
  "build_validation_command": "npm run build"
}
```

## Estimated Effort

| Prompt | Lines | Complexity | Dependencies |
|--------|-------|------------|--------------|
| 1. Test Profiles | ~150 | Medium | None |
| 2. Test Parser | ~200 | Medium | None |
| 3. Issue Generator | ~100 | Low | Prompt 2 |
| 4. Models Update | ~30 | Low | None |
| 5. Validator Engine | ~250 | High | Prompts 1-4 |
| 6. Runner Wiring | ~40 | Low | Prompt 5 |
| 7. Config + CLI | ~30 | Low | Prompt 6 |
| 8. Tests | ~150 | Medium | All above |
| **Total** | **~950** | | |
