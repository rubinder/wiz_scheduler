"""Signal weight resolution for scheduling.

Signal weights follow a hierarchy:
1. Schedule generation request (highest priority - user can override for a single generation)
2. Location defaults (overrides company defaults)
3. Company defaults
4. All zeros (disabled by default)
"""

from typing import Any, Dict


def resolve_signal_weights(
    request_weights: Dict[str, float] | None = None,
    location_config: Dict[str, Any] | None = None,
    company_config: Dict[str, Any] | None = None,
) -> Dict[str, float]:
    """Resolve signal weights following the hierarchy: request > location > company > defaults.

    Args:
        request_weights: Signal weights from the schedule generation request
        location_config: Location default signal config (overrides company)
        company_config: Company default signal config

    Returns:
        Resolved signal weights {seniority_weight, pay_weight, overtime_weight}
        Note: Affinity is not tunable; it's always applied like preferences
    """
    # Start with defaults
    resolved = {
        "seniority_weight": 0.0,
        "pay_weight": 0.0,
        "overtime_weight": 0.0,
    }

    # Apply company defaults if set
    if company_config:
        resolved.update({
            "seniority_weight": company_config.get("seniority_weight", 0.0),
            "pay_weight": company_config.get("pay_weight", 0.0),
            "overtime_weight": company_config.get("overtime_weight", 0.0),
        })

    # Override with location defaults if set
    if location_config:
        if "seniority_weight" in location_config:
            resolved["seniority_weight"] = location_config["seniority_weight"]
        if "pay_weight" in location_config:
            resolved["pay_weight"] = location_config["pay_weight"]
        if "overtime_weight" in location_config:
            resolved["overtime_weight"] = location_config["overtime_weight"]

    # Override with request weights if set (highest priority)
    if request_weights:
        if "seniority_weight" in request_weights:
            resolved["seniority_weight"] = request_weights["seniority_weight"]
        if "pay_weight" in request_weights:
            resolved["pay_weight"] = request_weights["pay_weight"]
        if "overtime_weight" in request_weights:
            resolved["overtime_weight"] = request_weights["overtime_weight"]

    return resolved
