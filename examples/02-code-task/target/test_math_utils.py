"""Tests for math_utils."""
from math_utils import multiply


def test_multiply_integers():
    assert multiply(3, 4) == 12


def test_multiply_floats():
    assert abs(multiply(2.5, 2.0) - 5.0) < 1e-9


def test_multiply_by_zero():
    assert multiply(99, 0) == 0
