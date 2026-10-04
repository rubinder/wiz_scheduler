"""Tests for signal weight resolution hierarchy (request > location > company > defaults)."""

from backend.services.signal_weights import resolve_signal_weights


class TestSignalWeightResolution:
    """Test signal weight resolution following the hierarchy."""

    def test_all_defaults_when_none_provided(self):
        """When no weights provided, should return all zeros."""
        result = resolve_signal_weights()

        assert result == {
            "seniority_weight": 0.0,
            "pay_weight": 0.0,
            "overtime_weight": 0.0,
            "affinity_weight": 0.0,
        }

    def test_company_defaults_applied(self):
        """Company defaults should be used when provided."""
        company_config = {
            "seniority_weight": 0.3,
            "pay_weight": 0.5,
            "overtime_weight": 0.1,
            "affinity_weight": 0.0,
        }

        result = resolve_signal_weights(company_config=company_config)

        assert result == company_config

    def test_location_overrides_company(self):
        """Location defaults should override company defaults."""
        company_config = {
            "seniority_weight": 0.3,
            "pay_weight": 0.5,
            "overtime_weight": 0.1,
            "affinity_weight": 0.0,
        }
        location_config = {
            "seniority_weight": 0.7,  # Override
            "pay_weight": 0.2,  # Override
            # No overtime or affinity set, should inherit from company
        }

        result = resolve_signal_weights(
            company_config=company_config,
            location_config=location_config
        )

        assert result["seniority_weight"] == 0.7  # From location
        assert result["pay_weight"] == 0.2  # From location
        assert result["overtime_weight"] == 0.1  # From company
        assert result["affinity_weight"] == 0.0  # From company

    def test_request_overrides_all(self):
        """Request weights should override both location and company defaults."""
        company_config = {"seniority_weight": 0.3, "pay_weight": 0.5}
        location_config = {"seniority_weight": 0.7, "pay_weight": 0.2}
        request_weights = {
            "seniority_weight": 1.0,
            "pay_weight": 0.0,
        }

        result = resolve_signal_weights(
            request_weights=request_weights,
            location_config=location_config,
            company_config=company_config
        )

        assert result["seniority_weight"] == 1.0  # From request
        assert result["pay_weight"] == 0.0  # From request
        assert result["overtime_weight"] == 0.0  # Default (not in any config)
        assert result["affinity_weight"] == 0.0  # Default (not in any config)

    def test_partial_location_config(self):
        """Location config can partially override company defaults."""
        company_config = {
            "seniority_weight": 0.1,
            "pay_weight": 0.2,
            "overtime_weight": 0.3,
            "affinity_weight": 0.4,
        }
        location_config = {
            "seniority_weight": 0.9,  # Override just this
        }

        result = resolve_signal_weights(
            company_config=company_config,
            location_config=location_config
        )

        assert result["seniority_weight"] == 0.9  # From location override
        assert result["pay_weight"] == 0.2  # From company
        assert result["overtime_weight"] == 0.3  # From company
        assert result["affinity_weight"] == 0.4  # From company

    def test_null_location_config_skipped(self):
        """Null location config should not break resolution."""
        company_config = {"seniority_weight": 0.3}

        result = resolve_signal_weights(
            company_config=company_config,
            location_config=None
        )

        assert result["seniority_weight"] == 0.3  # From company

    def test_empty_configs_use_defaults(self):
        """Empty dicts should not override defaults."""
        result = resolve_signal_weights(
            company_config={},
            location_config={}
        )

        assert result == {
            "seniority_weight": 0.0,
            "pay_weight": 0.0,
            "overtime_weight": 0.0,
            "affinity_weight": 0.0,
        }
