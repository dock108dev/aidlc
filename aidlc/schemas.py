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
    "create_issue",       # Create a new issue for implementation
    "update_issue",       # Refine an existing issue
    "create_doc",         # Create a design/planning document
    "update_doc",         # Update an existing document
    "research",           # Investigate a topic before creating issues
}


@dataclass
class PlanningAction:
    action_type: str
    rationale: str

    # For issue operations
    issue_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    labels: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    acceptance_criteria: list = field(default_factory=list)

    # For doc operations
    file_path: Optional[str] = None
    content: Optional[str] = None

    # For research operations
    research_topic: Optional[str] = None
    research_question: Optional[str] = None
    research_scope: list = field(default_factory=list)  # file paths to examine

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
        if not self.rationale or not self.rationale.strip():
            errors.append("rationale must not be empty")

        if is_finalization and self.action_type == "create_issue":
            errors.append("create_issue prohibited during finalization")

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

        if self.action_type in ("create_doc", "update_doc"):
            if not self.file_path:
                errors.append(f"{self.action_type} requires file_path")
            if not self.content:
                errors.append(f"{self.action_type} requires content")

        if self.action_type == "research":
            if not self.research_topic:
                errors.append("research requires research_topic")
            if not self.research_question:
                errors.append("research requires research_question")

        return errors

    @classmethod
    def from_dict(cls, data: dict) -> "PlanningAction":
        return cls(
            action_type=data.get("action_type", ""),
            rationale=data.get("rationale", ""),
            issue_id=data.get("issue_id"),
            title=data.get("title"),
            description=data.get("description"),
            priority=data.get("priority"),
            labels=data.get("labels", []),
            dependencies=data.get("dependencies", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            file_path=data.get("file_path"),
            content=data.get("content"),
            research_topic=data.get("research_topic"),
            research_question=data.get("research_question"),
            research_scope=data.get("research_scope", []),
        )


@dataclass
class PlanningOutput:
    frontier_assessment: str
    actions: list[PlanningAction]
    cycle_notes: str = ""
    planning_complete: bool = False
    completion_reason: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PlanningOutput":
        actions = [PlanningAction.from_dict(a) for a in data.get("actions", [])]
        return cls(
            frontier_assessment=data.get("frontier_assessment", ""),
            actions=actions,
            cycle_notes=data.get("cycle_notes", ""),
            planning_complete=data.get("planning_complete", False),
            completion_reason=data.get("completion_reason", ""),
        )

    def validate(self, is_finalization: bool = False, known_issue_ids: set | None = None) -> list[str]:
        errors = []
        new_ids = [a.issue_id for a in self.actions if a.action_type == "create_issue" and a.issue_id]
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

    @classmethod
    def from_dict(cls, data: dict) -> "ImplementationResult":
        return cls(
            issue_id=data.get("issue_id", ""),
            success=data.get("success", False),
            summary=data.get("summary", ""),
            files_changed=data.get("files_changed", []),
            tests_passed=data.get("tests_passed", False),
            notes=data.get("notes", ""),
        )


# --- PARSING ---

def parse_json_output(raw_text: str) -> dict:
    """Extract JSON from Claude's response. Handles ```json blocks and raw JSON."""
    # Try ```json block first
    json_match = re.search(r"```json\s*\n(.*?)\n\s*```", raw_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try raw JSON object
        brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if brace_match:
            json_str = brace_match.group(0)
        else:
            raise ValueError(
                f"No JSON found in response. Starts with: {raw_text[:200]}"
            )

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}")


def parse_planning_output(raw_text: str) -> PlanningOutput:
    data = parse_json_output(raw_text)
    return PlanningOutput.from_dict(data)


def parse_implementation_result(raw_text: str) -> ImplementationResult:
    data = parse_json_output(raw_text)
    return ImplementationResult.from_dict(data)


# --- SCHEMA DESCRIPTIONS FOR PROMPTS ---

PLANNING_SCHEMA_DESCRIPTION = """\
You MUST output your planning actions as a single JSON block wrapped in ```json``` markers.

```
{
  "frontier_assessment": "Summary of what you assessed and why you chose these actions",
  "actions": [
    {
      "action_type": "create_issue | update_issue | create_doc | update_doc | research",
      "rationale": "Why this action is needed",

      // For create_issue:
      "issue_id": "ISSUE-001",
      "title": "Short descriptive title",
      "description": "Full description of what needs to be built/changed",
      "priority": "high | medium | low",
      "labels": ["feature", "backend"],
      "dependencies": ["ISSUE-000"],  // IDs of issues that must be done first
      "acceptance_criteria": [
        "Criterion 1 — specific and testable",
        "Criterion 2"
      ],

      // For update_issue:
      "issue_id": "ISSUE-001",
      "description": "Updated description",
      "acceptance_criteria": ["Updated criteria"],

      // For create_doc / update_doc:
      "file_path": "docs/design/feature-x.md",
      "content": "Full document content",

      // For research (investigate before creating issues):
      "research_topic": "scoring-algorithm",
      "research_question": "What formula should we use for score calculation?",
      "research_scope": ["src/scoring.py", "docs/requirements.md"]
    }
  ],
  "cycle_notes": "Observations about planning state or suggestions for next cycle"
}
```

Rules:
- Issue IDs must use the format ISSUE-NNN (e.g., ISSUE-001, ISSUE-042)
- Each issue MUST have acceptance_criteria with specific, testable requirements
- Dependencies must reference existing issue IDs
- Produce 1-15 high-quality actions per cycle. Quality over quantity.
- For create_doc, file_path must be relative to the project root
- Every action must have a rationale explaining why it's needed
- Use "research" when you need to investigate source code, derive formulas, explore
  design options, or resolve knowledge gaps BEFORE creating issues. Research results
  are written to docs/research/ and available in subsequent planning cycles.
"""

IMPLEMENTATION_SCHEMA_DESCRIPTION = """\
After implementing the issue, output a JSON result block wrapped in ```json``` markers:

```
{
  "issue_id": "ISSUE-001",
  "success": true,
  "summary": "What was implemented and how",
  "files_changed": ["src/auth.py", "tests/test_auth.py"],
  "tests_passed": true,
  "notes": "Any caveats or follow-up items"
}
```
"""
