"""Shared helpers for parsing scanner/project context strings."""


def parse_project_type(project_context: str) -> str:
    """Extract project type from scanner context text.

    Returns an empty string when the context does not include a
    recognizable "project type: ..." line.
    """
    if (
        "project_type" not in project_context.lower()
        and "project type" not in project_context.lower()
    ):
        return ""
    for line in project_context.split("\n"):
        if "project type" in line.lower() and ":" in line:
            return line.split(":")[-1].strip()
    return ""
