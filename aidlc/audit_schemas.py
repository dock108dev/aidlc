"""Structured output schemas for code audit Claude prompts."""

from .schemas import parse_json_output


AUDIT_MODULE_ANALYSIS_PROMPT = """\
Analyze the following source code module and return a JSON assessment.

Module: {module_name}
Path: {module_path}

Source files:
{source_content}

Return your analysis as a single JSON block wrapped in ```json``` markers:

```json
{{
  "module_name": "{module_name}",
  "description": "What this module does (1-2 sentences)",
  "capabilities": ["list of features/capabilities this module provides"],
  "dependencies": ["list of other modules/packages this module imports from within the project"],
  "external_dependencies": ["list of third-party packages used"],
  "quality_signals": {{
    "has_tests": true/false,
    "has_docstrings": true/false,
    "complexity": "low | medium | high",
    "notes": "Any quality observations"
  }}
}}
```
"""


AUDIT_FEATURE_INVENTORY_PROMPT = """\
Given the following project structure and module summaries, create a feature inventory.

Project type: {project_type}
Frameworks: {frameworks}

Module summaries:
{module_summaries}

Return a JSON block listing the features/capabilities that exist in this codebase:

```json
{{
  "features": [
    {{
      "name": "Feature name",
      "status": "complete | partial | stub",
      "modules": ["modules involved"],
      "description": "What it does"
    }}
  ],
  "summary": "Overall assessment of the project's current state"
}}
```
"""


def parse_audit_module_output(raw_text: str) -> dict:
    """Parse Claude's module analysis response."""
    return parse_json_output(raw_text)


def parse_audit_feature_output(raw_text: str) -> dict:
    """Parse Claude's feature inventory response."""
    return parse_json_output(raw_text)
