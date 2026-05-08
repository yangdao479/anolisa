"""HIGH coverage test — covers >80% of demo_coverage_gate.py.

This test file exercises nearly all functions and branches.
When submitted as a PR, diff-cover should report ~90%+ coverage → gate PASS.
"""

import pytest
from agent_sec_cli.demo_coverage_gate import (
    add,
    clamp,
    divide,
    factorial,
    fizzbuzz,
    grade,
    is_even,
    is_positive,
    multiply,
    subtract,
)


class TestAdd:
    def test_positive(self):
        assert add(2, 3) == 5

    def test_negative(self):
        assert add(-1, -2) == -3


class TestSubtract:
    def test_basic(self):
        assert subtract(5, 3) == 2


class TestMultiply:
    def test_basic(self):
        assert multiply(3, 4) == 12


class TestDivide:
    def test_basic(self):
        assert divide(10, 2) == 5.0

    def test_zero_division(self):
        with pytest.raises(ValueError, match="Cannot divide by zero"):
            divide(1, 0)


class TestIsPositive:
    def test_positive(self):
        assert is_positive(5) is True

    def test_zero(self):
        assert is_positive(0) is False

    def test_negative(self):
        assert is_positive(-3) is False


class TestIsEven:
    def test_even(self):
        assert is_even(4) is True

    def test_odd(self):
        assert is_even(3) is False


class TestClamp:
    def test_below_low(self):
        assert clamp(-5, 0, 10) == 0

    def test_above_high(self):
        assert clamp(15, 0, 10) == 10

    def test_within_range(self):
        assert clamp(5, 0, 10) == 5


class TestGrade:
    def test_a(self):
        assert grade(95) == "A"

    def test_b(self):
        assert grade(85) == "B"

    def test_c(self):
        assert grade(75) == "C"

    def test_d(self):
        assert grade(65) == "D"

    def test_f(self):
        assert grade(50) == "F"


class TestFizzbuzz:
    def test_fizzbuzz(self):
        assert fizzbuzz(15) == "FizzBuzz"

    def test_fizz(self):
        assert fizzbuzz(9) == "Fizz"

    def test_buzz(self):
        assert fizzbuzz(10) == "Buzz"

    def test_number(self):
        assert fizzbuzz(7) == "7"


class TestFactorial:
    def test_zero(self):
        assert factorial(0) == 1

    def test_positive(self):
        assert factorial(5) == 120

    def test_negative(self):
        with pytest.raises(ValueError, match="Negative numbers not allowed"):
            factorial(-1)
