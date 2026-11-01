from ttr_live import supervisor_interval_seconds


def test_supervisor_interval_defaults_to_ten_seconds():
    assert supervisor_interval_seconds(None) == 10


def test_supervisor_interval_is_clamped_to_safe_bounds():
    assert supervisor_interval_seconds('1') == 5
    assert supervisor_interval_seconds('120') == 60


def test_supervisor_interval_accepts_custom_value():
    assert supervisor_interval_seconds('15') == 15


def test_supervisor_interval_handles_invalid_value():
    assert supervisor_interval_seconds('invalid') == 10
