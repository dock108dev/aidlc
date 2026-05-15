"""Claude CLI configuration and command construction."""

from __future__ import annotations


def apply_config(cli, config: dict) -> None:
    """Populate ClaudeCLI attributes from config."""
    cli.config = config
    providers_cfg = config.get("providers", {})
    if not isinstance(providers_cfg, dict):
        providers_cfg = {}
    claude_cfg = providers_cfg.get("claude", {})
    if not isinstance(claude_cfg, dict):
        claude_cfg = {}
    cli.cli_command = str(claude_cfg.get("cli_command", "claude"))
    cli.model = str(claude_cfg.get("default_model", "opus"))
    cli.max_retries = config.get("retry_max_attempts", 2)
    cli.retry_base_delay = config.get("retry_base_delay_seconds", 30)
    cli.retry_max_delay = config.get("retry_max_delay_seconds", 300)
    cli.retry_backoff_factor = config.get("retry_backoff_factor", 2.0)
    cli.dry_run = config.get("dry_run", False)


def dry_run_result(model: str) -> dict:
    return {
        "success": True,
        "output": "[DRY RUN] No execution",
        "error": None,
        "failure_type": None,
        "duration_seconds": 0.0,
        "retries": 0,
        "usage": {},
        "total_cost_usd": None,
        "model_used": model,
        "usage_source": "dry_run",
    }


def build_command(
    cli_command: str,
    model: str,
    *,
    effective_resume_id: str | None,
    effective_session_id: str | None,
    allow_edits: bool,
) -> list[str]:
    cmd = [cli_command]
    if effective_resume_id:
        cmd.extend(["--resume", effective_resume_id])
    elif effective_session_id:
        cmd.extend(["--session-id", effective_session_id])
    cmd.extend(
        [
            "--print",
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
    )
    if allow_edits:
        cmd.append("--dangerously-skip-permissions")
    return cmd
