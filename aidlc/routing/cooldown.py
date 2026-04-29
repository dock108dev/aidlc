"""Cooldown / rate-limit machinery for the routing engine.

Extracted from `engine.py` so the engine stays focused on the
resolve+execute loop. This module exposes pure functions that operate on
mutable cooldown dicts owned by `ProviderRouter`:

  * ``is_on_cooldown`` — check + sweep an expired entry.
  * ``compute_rate_limit_cooldown_until`` — exponential buffer applied
    on top of the provider-reported restore time, escalating per
    consecutive (provider, model) rate limit.
  * ``next_model_restore_time`` — earliest future expiry across the
    model-cooldown dict, with sweep of stale entries.

The router keeps the dicts on itself because tests mutate them
directly (e.g. ``router._model_cooldowns[(p, m)] = expiry``).
"""

from __future__ import annotations

import time
from typing import Hashable

from . import result_signals


def is_on_cooldown(
    cooldowns: dict[Hashable, float],
    key: Hashable,
    now: float | None = None,
) -> bool:
    """Return True when ``key`` has a future expiry; pop expired entries.

    Used both for provider-level (``provider_id``) and model-level
    (``(provider_id, model)``) cooldown maps.
    """
    expiry = cooldowns.get(key)
    if not expiry:
        return False
    t = now if now is not None else time.time()
    if t < expiry:
        return True
    cooldowns.pop(key, None)
    return False


def compute_rate_limit_cooldown_until(
    *,
    provider_id: str,
    model: str,
    result: dict,
    now: float,
    backoff_step: dict[tuple[str, str], int],
    buffer_base_seconds: int,
    fallback_cooldown_seconds: int,
) -> float | None:
    """Next epoch when *model* may be tried again after a rate limit.

    Adds an exponential buffer on top of the provider-reported window:
    base·1×, base·2×, base·4×, … capped at base·8× (multiplier
    ``min(2^step, 8)``) per consecutive rate limit on this
    ``(provider, model)``. Resets when a call succeeds (caller pops the
    key from ``backoff_step``).

    If buffer base is 0 and the response includes no parseable restore
    time, returns None (legacy test / opt-out: no cooldown row).
    """
    key = (provider_id, model)
    step = backoff_step.get(key, 0)
    mult = min(2**step, 8)
    buf_seconds = float(buffer_base_seconds) * float(mult)

    reported = result_signals.extract_restore_time_epoch(result)
    if reported is None and buf_seconds <= 0:
        return None

    if reported is not None:
        restore_at = reported + buf_seconds
    else:
        restore_at = now + max(buf_seconds, float(fallback_cooldown_seconds))

    if restore_at <= now:
        restore_at = now + 1.0

    backoff_step[key] = step + 1
    return restore_at


def record_rate_limit(
    *,
    router,
    decision,
    result: dict,
    now: float,
    effective_phase: str,
    provider_scope: bool,
) -> None:
    """Apply cooldown bookkeeping + emit logs for a rate-limited result.

    Mutates ``router._provider_cooldowns``/``_model_cooldowns`` in place.
    Caller is responsible for adding ``decision`` to its own
    ``excluded_providers`` / ``excluded_models`` / ``rate_limited_models``
    sets — this function only owns the cooldown-side state.
    """
    raw_reported = result_signals.extract_restore_time_epoch(result)
    restore_at = router._compute_rate_limit_cooldown_until(
        decision.provider_id, decision.model, result, now
    )
    buf_hint: float | None = None
    if restore_at is not None and raw_reported is not None:
        buf_hint = max(0.0, restore_at - raw_reported)
    elif restore_at is not None and raw_reported is None:
        buf_hint = max(0.0, restore_at - now)

    detail = result_signals.format_rate_limit_diagnostics(
        result,
        raw_restore_epoch=raw_reported,
        cooldown_until_epoch=restore_at,
        buffer_seconds=buf_hint,
    )
    for line in detail.splitlines():
        router.logger.warning(f"[routing] {effective_phase}: rate_limited_detail | {line}")

    if restore_at is not None:
        from datetime import datetime

        router._model_cooldowns[(decision.provider_id, decision.model)] = restore_at
        if provider_scope:
            router._provider_cooldowns[decision.provider_id] = restore_at
        restore_text = datetime.fromtimestamp(restore_at).isoformat(timespec="seconds")
        scope_text = "provider" if provider_scope else "model"
        router.logger.warning(
            f"[routing] {effective_phase}: rate limited on "
            f"{decision.provider_id}/{decision.model} ({scope_text} scope); cooldown_until={restore_text} "
            f"(try another provider if configured)"
        )
    else:
        scope_text = "provider" if provider_scope else "model"
        router.logger.warning(
            f"[routing] {effective_phase}: rate limited on "
            f"{decision.provider_id}/{decision.model} ({scope_text} scope); no cooldown_until "
            f"(try another provider if configured)"
        )


def next_model_restore_time(
    model_cooldowns: dict[tuple[str, str], float],
    now: float | None = None,
) -> float | None:
    """Return the earliest future expiry across ``model_cooldowns``.

    Sweeps stale entries (expiry <= now) as a side-effect. Returns None
    when no future expiry remains.
    """
    t = now if now is not None else time.time()
    next_restore: float | None = None
    for (provider_id, model), expiry in list(model_cooldowns.items()):
        if expiry <= t:
            model_cooldowns.pop((provider_id, model), None)
            continue
        if next_restore is None or expiry < next_restore:
            next_restore = expiry
    return next_restore
