from __future__ import annotations

from strategies.validate_utils import _is_int_value, _is_number_value


class TestIsIntValue:
    def test_plain_int(self):
        assert _is_int_value(5) is True

    def test_zero(self):
        assert _is_int_value(0) is True

    def test_negative(self):
        assert _is_int_value(-10) is True

    def test_bool_true_rejected(self):
        # bool is a subclass of int; must be excluded
        assert _is_int_value(True) is False

    def test_bool_false_rejected(self):
        assert _is_int_value(False) is False

    def test_float_rejected(self):
        assert _is_int_value(1.0) is False

    def test_string_rejected(self):
        assert _is_int_value("5") is False

    def test_none_rejected(self):
        assert _is_int_value(None) is False


class TestIsNumberValue:
    def test_int(self):
        assert _is_number_value(5) is True

    def test_float(self):
        assert _is_number_value(1.5) is True

    def test_zero_float(self):
        assert _is_number_value(0.0) is True

    def test_negative_float(self):
        assert _is_number_value(-2.5) is True

    def test_bool_true_rejected(self):
        assert _is_number_value(True) is False

    def test_bool_false_rejected(self):
        assert _is_number_value(False) is False

    def test_string_rejected(self):
        assert _is_number_value("1.5") is False

    def test_none_rejected(self):
        assert _is_number_value(None) is False
