"""The scheduling model is a config knob, and its price constants match it.

Two failures this pins, both of which shipped to production undetected:

  * `claude-sonnet-4-20250514` was hardcoded at scheduling/nodes.py:226. When
    Anthropic retired it, every AI generation got a 404 from the SDK, which the
    pipeline caught and turned into status="PARSE_ERROR" — the graph never
    raises, by design, so a totally dead LLM path looked like a parsing quirk
    and nothing alerted. A knob means the next retirement is an env var, not a
    deploy.

  * LLM_INPUT/OUTPUT_COST_PER_M stayed at Sonnet 4's $3/$15 rates. Those feed
    calculate_llm_cost, which is what customers are BILLED at 1.3x markup, so a
    stale pair silently overcharges every tenant on every generation.

The price table is asserted per-model rather than as a lone magic number so
that changing SCHEDULING_MODEL without repricing fails here instead of on an
invoice.
"""

import re
from pathlib import Path

from backend.config import settings

# $ per 1M tokens (input, output), from Anthropic's published pricing.
MODEL_PRICES = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_NODES = Path(__file__).resolve().parent.parent / "backend" / "scheduling" / "nodes.py"


def test_model_is_not_hardcoded_in_the_pipeline():
    """nodes.py must read the knob, never a literal model ID."""
    source = _NODES.read_text()
    hardcoded = re.findall(r'model\s*=\s*["\']([^"\']+)["\']', source)
    assert not hardcoded, (
        f"hardcoded model ID(s) {hardcoded} in {_NODES.name}; "
        "use settings.SCHEDULING_MODEL so a retired model is an env-var fix"
    )
    assert "model=settings.SCHEDULING_MODEL" in source


def test_model_id_carries_no_date_suffix():
    """Current Claude IDs are bare; a date suffix is the retired-model shape."""
    assert not re.search(r"-\d{8}$", settings.SCHEDULING_MODEL), (
        f"{settings.SCHEDULING_MODEL!r} looks like a dated snapshot ID. Those get "
        "retired and then 404 — use the bare ID (e.g. 'claude-sonnet-5')."
    )


def test_price_constants_match_the_configured_model():
    """Billing rates must track the model actually being called."""
    model = settings.SCHEDULING_MODEL
    assert model in MODEL_PRICES, (
        f"no price entry for {model!r}. Add its published $/1M rates to "
        "MODEL_PRICES and update LLM_*_COST_PER_M together."
    )
    expected_in, expected_out = MODEL_PRICES[model]
    assert settings.LLM_INPUT_COST_PER_M == expected_in, (
        f"{model} input is ${expected_in}/1M but LLM_INPUT_COST_PER_M is "
        f"${settings.LLM_INPUT_COST_PER_M} — customers are billed this."
    )
    assert settings.LLM_OUTPUT_COST_PER_M == expected_out, (
        f"{model} output is ${expected_out}/1M but LLM_OUTPUT_COST_PER_M is "
        f"${settings.LLM_OUTPUT_COST_PER_M} — customers are billed this."
    )
