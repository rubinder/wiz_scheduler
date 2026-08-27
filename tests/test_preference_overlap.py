"""The 50% overlap rule, shared by the hour-range preference and the cap.

Fraction thresholds are where off-by-one bugs live, so the boundary is
asserted from both sides. The fraction is of the SHIFT's duration, not the
range's — a 1-hour shift fully inside an 8-hour range is 100% matched, not
12.5%.
"""

from backend.config import settings
from backend.scheduling.preferences import matches_range, overlap_fraction


def test_fully_inside_is_one():
    assert overlap_fraction("13:00", "17:00", "13:00", "17:00") == 1.0


def test_short_shift_inside_a_long_range_is_one():
    """The denominator is the shift, not the range."""
    assert overlap_fraction("14:00", "15:00", "13:00", "21:00") == 1.0


def test_no_overlap_is_zero():
    assert overlap_fraction("09:00", "12:00", "16:00", "22:00") == 0.0


def test_touching_edges_is_zero():
    assert overlap_fraction("09:00", "16:00", "16:00", "22:00") == 0.0


def test_half_overlap_is_one_half():
    # 14:00-18:00 is 4h; 16:00-22:00 covers 16:00-18:00 = 2h.
    assert overlap_fraction("14:00", "18:00", "16:00", "22:00") == 0.5


def test_zero_length_shift_is_zero_not_a_crash():
    assert overlap_fraction("13:00", "13:00", "13:00", "17:00") == 0.0


def test_threshold_is_one_half():
    assert settings.SCHEDULING_RANGE_MATCH_THRESHOLD == 0.5


def test_exactly_at_the_threshold_matches():
    assert matches_range("14:00", "18:00", "16:00", "22:00") is True


def test_just_below_the_threshold_does_not_match():
    # 14:00-18:10 is 250 min; overlap 16:00-18:10 = 130 min -> 0.52 ... so use
    # a shift where the overlap is unambiguously under half:
    # 13:00-18:00 is 5h; overlap with 16:00-22:00 is 2h -> 0.4
    assert matches_range("13:00", "18:00", "16:00", "22:00") is False


def test_overnight_shift_fully_matches_same_overnight_range():
    # 22:00-06:00 is 8h; the identical overnight range covers all 8h.
    assert overlap_fraction("22:00", "06:00", "22:00", "06:00") == 1.0


def test_overnight_shift_does_not_match_a_day_range():
    # 22:00-06:00 (wraps to next day) never touches 09:00-17:00.
    assert overlap_fraction("22:00", "06:00", "09:00", "17:00") == 0.0


def test_day_shift_does_not_match_an_overnight_range():
    # 09:00-17:00 never touches an overnight 22:00-06:00 range.
    assert overlap_fraction("09:00", "17:00", "22:00", "06:00") == 0.0


def test_overnight_shift_partially_matches_a_range_on_the_next_page():
    # 22:00-02:00 is 4h (crosses midnight). The 00:00-06:00 range overlaps
    # it from 00:00-02:00 = 2h -> 2/4 = 0.5.
    assert overlap_fraction("22:00", "02:00", "00:00", "06:00") == 0.5


def test_zero_length_shift_is_still_zero_even_overnight_adjacent():
    # Confirms zero-length shifts are handled before wraparound normalisation
    # turns them into a spurious 24h shift.
    assert overlap_fraction("13:00", "13:00", "13:00", "17:00") == 0.0


def test_matches_range_true_for_matching_overnight_pair():
    assert matches_range("22:00", "06:00", "22:00", "06:00") is True


def test_matches_range_false_for_overnight_shift_against_day_range():
    assert matches_range("22:00", "06:00", "09:00", "17:00") is False


def test_matches_range_false_for_day_shift_against_overnight_range():
    assert matches_range("09:00", "17:00", "22:00", "06:00") is False
