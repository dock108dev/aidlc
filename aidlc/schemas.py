"""Structured output schemas for AIDLC.

Defines the JSON contracts between Claude (producer) and the runner (consumer)
for both planning and implementation phases.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

# --- PLANNING SCHEMAS ---

PLANNING_ACTION_TYPES = {
    "create_issue",  # Create a new issue for implementation
    "update_issue",  # Refine an existing issue
}


@dataclass
class PlanningAction:
    action_type: str

    # For issue operations
    issue_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    # critical_gap: intent that won't be deliverable from the current issue set
    # — either an uncovered BRAINDUMP ask, or a discovered prereq/infra issue
    # without which a covered ask can't ship. Required to file create_issue
    # during finalization.
    critical_gap: bool = False
    labels: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    acceptance_criteria: list = field(default_factory=list)

    def validate(
        self,
        is_finalization: bool = False,
        known_issue_ids: set | None = None,
        batch_issue_ids: set | None = None,
    ) -> list[str]:
        """Validate this action.

        Args:
            known_issue_ids: IDs of issues already in state (for duplicate/update checks)
            batch_issue_ids: IDs being created in the same batch (for dependency resolution)
        """
        errors = []
        if self.action_type not in PLANNING_ACTION_TYPES:
            errors.append(f"Unknown action_type: {self.action_type}")

        if is_finalization and self.action_type == "create_issue":
            if not self.critical_gap:
                errors.append(
                    "create_issue prohibited during finalization unless critical_gap=true"
                )
            elif self.priority != "high":
                errors.append(
                    "finalization create_issue requires priority='high' when critical_gap=true"
                )

        # For dependency checks, include both state IDs and batch IDs
        all_valid_ids = (known_issue_ids or set()) | (batch_issue_ids or set())

        if self.action_type == "create_issue":
            if not self.issue_id:
                errors.append("create_issue requires issue_id")
            if not self.title:
                errors.append("create_issue requires title")
            if not self.description:
                errors.append("create_issue requires description")
            if not self.acceptance_criteria:
                errors.append("create_issue requires acceptance_criteria")
            # Duplicate check — only against state, not batch
            if known_issue_ids and self.issue_id in known_issue_ids:
                errors.append(f"issue {self.issue_id} already exists")
            # Dependency check — against state + batch (within-batch deps are valid)
            if all_valid_ids and self.dependencies:
                for dep in self.dependencies:
                    if dep not in all_valid_ids:
                        errors.append(f"dependency '{dep}' is not a known issue")

        if self.action_type == "update_issue":
            if not self.issue_id:
                errors.append("update_issue requires issue_id")
            if all_valid_ids and self.issue_id and self.issue_id not in all_valid_ids:
                errors.append(f"cannot update unknown issue: {self.issue_id}")

        return errors

    @classmethod
    def from_dict(cls, data: dict) -> "PlanningAction":
        return cls(
            action_type=data.get("action_type", ""),
            issue_id=data.get("issue_id"),
            title=data.get("title"),
            description=data.get("description"),
            priority=data.get("priority"),
            critical_gap=bool(data.get("critical_gap", False)),
            labels=data.get("labels", []),
            dependencies=data.get("dependencies", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
        )


@dataclass
class PlanningOutput:
    frontier_assessment: str
    actions: list[PlanningAction]
    cycle_notes: str = ""
    planning_complete: bool = False
    completion_reason: str = ""

    @staticmethod
    def _normalize_completion_signals(data: dict) -> tuple[list[dict], bool, str]:
        """Normalize completion signals from either top-level fields or legacy action forms."""
        planning_complete = bool(data.get("planning_complete", False))
        completion_reason = data.get("completion_reason", "")
        normalized_actions = []

        for raw_action in data.get("actions", []):
            if not isinstance(raw_action, dict):
                continue

            action_type = raw_action.get("action_type")
            if action_type in {"set_planning_complete", "planning_complete"}:
                planning_complete = True
                if not completion_reason:
                    completion_reason = (
                        raw_action.get("completion_reason") or raw_action.get("reason") or ""
                    )
                continue

            normalized_actions.append(raw_action)

        return normalized_actions, planning_complete, completion_reason

    @classmethod
    def from_dict(cls, data: dict) -> "PlanningOutput":
        normalized_actions, planning_complete, completion_reason = (
            cls._normalize_completion_signals(data)
        )
        actions = [PlanningAction.from_dict(a) for a in normalized_actions]
        return cls(
            frontier_assessment=data.get("frontier_assessment", ""),
            actions=actions,
            cycle_notes=data.get("cycle_notes", ""),
            planning_complete=planning_complete,
            completion_reason=completion_reason,
        )

    def validate(
        self, is_finalization: bool = False, known_issue_ids: set | None = None
    ) -> list[str]:
        errors = []
        new_ids = [
            a.issue_id for a in self.actions if a.action_type == "create_issue" and a.issue_id
        ]
        batch_ids = set(new_ids)
        seen = set()
        for iid in new_ids:
            if iid in seen:
                errors.append(f"Duplicate issue_id in batch: {iid}")
            seen.add(iid)
        if known_issue_ids:
            for iid in new_ids:
                if iid in known_issue_ids:
                    errors.append(f"Issue {iid} already exists")

        for i, action in enumerate(self.actions):
            for err in action.validate(
                is_finalization=is_finalization,
                known_issue_ids=known_issue_ids,
                batch_issue_ids=batch_ids,
            ):
                errors.append(f"Action [{i}] ({action.action_type}): {err}")
        return errors


# --- IMPLEMENTATION SCHEMAS ---


@dataclass
class ImplementationResult:
    """Result from Claude implementing a single issue."""

    issue_id: str
    success: bool
    summary: str = ""
    files_changed: list = field(default_factory=list)
    tests_passed: bool = False
    notes: str = ""
    # ISSUE-007: optional list of `<file:line>` refs the agent inspected when
    # editing a system that already has callers. Empty list = checked, none
    # found. Absent = not declared (typically net-new code).
    existing_callers_checked: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "ImplementationResult":
        return cls(
            issue_id=data.get("issue_id", ""),
            success=data.get("success", False),
            summary=data.get("summary", ""),
            files_changed=data.get("files_changed", []),
            tests_passed=data.get("tests_passed", False),
            notes=data.get("notes", ""),
            existing_callers_checked=data.get("existing_callers_checked", []) or [],
        )


# --- PARSING ---


def parse_json_output(raw_text: str) -> dict:
    """Extract the first JSON object from Claude's response.

    Accepts either a ```json fenced block or raw text containing a JSON
    object. When the response includes content **after** the JSON object
    (a second JSON block, a trailing prose explanation, log lines emitted
    after a graceful timeout-stop, etc.), the trailing content is
    ignored — only the first balanced JSON object is returned.

    Raises ``ValueError`` when no JSON object is found or the first
    candidate cannot be parsed.
    """
    # Try ```json fenced block first — when present it's the model's
    # explicit "this is the structured result" marker, so honor it.
    json_match = re.search(r"```json\s*\n(.*?)\n\s*```", raw_text, re.DOTALL)
    if json_match:
        candidate = json_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON: {e}")

    # Otherwise scan the raw text for the earliest top-level JSON marker
    # ('{' or '[') and let raw_decode consume exactly one balanced value.
    # raw_decode returns (parsed_value, end_index) and silently leaves any
    # trailing data alone — the historical greedy regex used to glue
    # multiple JSON blocks together and produce "Extra data" failures.
    candidates = [i for i in (raw_text.find("{"), raw_text.find("[")) if i != -1]
    if not candidates:
        raise ValueError(f"No JSON found in response. Starts with: {raw_text[:200]}")
    start = min(candidates)
    try:
        data, _end = json.JSONDecoder().raw_decode(raw_text[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}")
    if not isinstance(data, dict):
        # Every caller of parse_json_output expects an object; reject
        # arrays/scalars explicitly rather than passing them through.
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


def parse_planning_output(raw_text: str) -> PlanningOutput:
    data = parse_json_output(raw_text)
    return PlanningOutput.from_dict(data)


def parse_implementation_result(raw_text: str) -> ImplementationResult:
    data = parse_json_output(raw_text)
    return ImplementationResult.from_dict(data)


def parse_test_fix_outcome(raw_text: str) -> dict | None:
    """Parse test-fix response JSON (tests_now_passing, failures, follow-up).

    Returns None if no valid outcome object is present.
    """
    if not (raw_text or "").strip():
        return None
    try:
        data = parse_json_output(raw_text)
    except ValueError:
        return None
    if not isinstance(data, dict) or "tests_now_passing" not in data:
        return None
    return data


# --- SCHEMA DESCRIPTIONS FOR PROMPTS ---

PLANNING_SCHEMA_DESCRIPTION = """\
Output **one** ```json``` block only (no extra prose outside JSON).

Top-level:
- `frontier_assessment`: ≤400 chars — what you checked and why these actions.
- `cycle_notes`: ≤300 chars — notes for the next cycle.
- `actions[]`: 1–15 items; one of the shapes below. **Use the exact field names shown — no aliases.**
- `planning_complete` / `completion_reason`: top-level completion signal. Do NOT emit `action_type: "set_planning_complete"`.

Action shapes (canonical keys only — unknown keys are ignored, missing required keys fail validation):

```
{"action_type": "create_issue", "issue_id": "ISSUE-001",
 "title": "...", "description": "...", "priority": "high",
 "labels": [], "dependencies": [], "acceptance_criteria": ["..."],
 "critical_gap": false}
```
```
{"action_type": "update_issue", "issue_id": "ISSUE-001",
 "description": "...", "priority": "medium",
 "labels": [], "dependencies": [], "acceptance_criteria": ["..."]}
```

Rules:
- ISSUE-NNN format; deps must already exist (in backlog or same batch).
- Finalization cycle: `create_issue` only if `critical_gap: true` and `priority: "high"`.
- Discovery and research are pre-planning phases. `.aidlc/discovery/findings.md`
  and `.aidlc/research/*.md` are already on disk — read them to shape issues. Do
  not propose `research` as a planning action; that action type is removed.
- Doc authoring is not a planning action. Source code and BRAINDUMP.md are the
  inputs alongside discovery/research artifacts; do not file
  `create_doc`/`update_doc` actions — they are removed.
"""

IMPLEMENTATION_SCHEMA_DESCRIPTION = """\
End with **only** one ```json``` block (minimal prose outside it).

- `summary`: ≤500 chars — what changed + where.
- `notes`: ≤400 chars — caveats/follow-ups (empty if none).
- `existing_callers_checked` (ISSUE-007): `<file:line>` refs you inspected when
  modifying a system with callers. Empty list = checked and none exist. Omit
  only for net-new code with no callers possible.

```
{
  "issue_id": "ISSUE-001",
  "success": true,
  "summary": "...",
  "files_changed": ["src/auth.py", "tests/test_auth.py"],
  "tests_passed": true,
  "notes": "",
  "existing_callers_checked": ["src/api.py:42", "src/cli.py:117"]
}
```
"""

TEST_FIX_OUTCOME_SCHEMA_DESCRIPTION = """\
After attempting fixes, end with **only** one ```json``` block (minimal prose outside it).

Use this **only** in the "fix failing tests" follow-up prompt — not for normal implementation.

Fields:
- `tests_now_passing`: boolean — `true` **only** if the project's configured test command would exit 0 **after your edits**.
- `failures_are_pre_existing_unrelated`: boolean — `true` **only** if the test command still fails, but the failures are clearly **unrelated** to this issue's scope (e.g. other suites, parse errors in other tests, pre-existing integration breakage). Do **not** set `true` to avoid work you could fix in scope.
- `follow_up_documentation`: string — If `failures_are_pre_existing_unrelated` is `true`, **required**: concrete description of what still fails and why it should become separate follow-up work. Empty if not applicable.

```
{
  "tests_now_passing": false,
  "failures_are_pre_existing_unrelated": true,
  "follow_up_documentation": "Broader GUT gate fails due to ... (unrelated to ISSUE-NNN); file X has parse error ..."
}
```
"""
