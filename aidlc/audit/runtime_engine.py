"""Runtime audit engine for build/test/e2e execution checks."""

import os
import re
import subprocess
import time

from ..test_profiles import detect_test_profile


class RuntimeAuditEngine:
    """Runs executable health checks during full audit."""

    def __init__(self, auditor):
        self.auditor = auditor

    @property
    def project_root(self):
        return self.auditor.project_root

    @property
    def config(self):
        return self.auditor.config

    @property
    def logger(self):
        return self.auditor.logger

    def run_runtime_checks(self, project_type: str) -> dict:
        """Execute build/unit/integration/e2e checks and summarize results."""
        profile = detect_test_profile(self.project_root, project_type or "unknown", self.config)
        timeout = int(self.config.get("audit_runtime_timeout_seconds", 600))

        tier_results = []
        coverage_values = []
        playwright_present = False
        playwright_passed = None

        for tier in ("build", "unit", "integration", "e2e"):
            command = profile.get(tier)
            if not command:
                continue

            command = self._normalize_command(tier, command)
            is_playwright = tier == "e2e" and "playwright" in command.lower()
            if is_playwright:
                playwright_present = True

            passed, output, duration = self._run_command(command, timeout=timeout)
            coverage_percent = self._extract_coverage_percent(output)
            if coverage_percent is not None:
                coverage_values.append(coverage_percent)

            tier_results.append(
                {
                    "tier": tier,
                    "command": command,
                    "passed": passed,
                    "duration_seconds": round(duration, 2),
                    "coverage_percent": coverage_percent,
                    "output_excerpt": self._excerpt(output),
                }
            )

            if is_playwright:
                playwright_passed = passed

        overall_passed = all(item["passed"] for item in tier_results) if tier_results else True
        build_result = next((item for item in tier_results if item["tier"] == "build"), None)
        build_health = "healthy" if (build_result is None or build_result["passed"]) else "unhealthy"

        coverage_percent = max(coverage_values) if coverage_values else None
        return {
            "profile": profile,
            "tier_results": tier_results,
            "overall_passed": overall_passed,
            "build_health": build_health,
            "playwright_present": playwright_present,
            "playwright_passed": playwright_passed,
            "coverage_percent": coverage_percent,
            "coverage_threshold_percent": float(
                self.config.get("audit_coverage_threshold_percent", 85)
            ),
        }

    def _normalize_command(self, tier: str, command: str) -> str:
        """Apply audit-specific command overrides and headless behavior."""
        if tier == "e2e" and "playwright" in command.lower():
            override = self.config.get("audit_playwright_command_override")
            if override:
                return str(override)
            if self.config.get("audit_playwright_headless", True):
                command = command.replace("--headed", "").strip()
                if "--headless" not in command:
                    command = f"{command} --headless"
        return command

    def _run_command(self, command: str, timeout: int) -> tuple[bool, str, float]:
        """Run one command and return pass status, output text, and duration."""
        start = time.time()
        env = os.environ.copy()
        env.setdefault("CI", "1")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.project_root),
                timeout=timeout,
                env=env,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            return result.returncode == 0, output, time.time() - start
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Runtime audit command timed out after {timeout}s: {command}")
            return False, "Command timed out", time.time() - start
        except FileNotFoundError:
            self.logger.warning(f"Runtime audit command not found: {command}")
            return False, "Command not found", time.time() - start

    @staticmethod
    def _extract_coverage_percent(output: str) -> float | None:
        """Parse a percent-like coverage value from command output."""
        if not output:
            return None
        patterns = [
            r"coverage[^0-9]{0,20}(\d{1,3}(?:\.\d+)?)\s*%",
            r"all files[^0-9]{0,20}(\d{1,3}(?:\.\d+)?)\s*%",
            r"statements[^0-9]{0,20}(\d{1,3}(?:\.\d+)?)\s*%",
        ]
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                try:
                    value = float(match.group(1))
                except ValueError:
                    continue
                if 0 <= value <= 100:
                    return value
        return None

    @staticmethod
    def _excerpt(text: str, max_chars: int = 600) -> str:
        """Truncate command output for audit summaries."""
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]
