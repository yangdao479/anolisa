"""Demo module for testing incremental coverage gate.

This module contains functions with clear coverage boundaries.
Use with test_demo_coverage_high.py (pass) or test_demo_coverage_low.py (fail).
"""


def add(a: int, b: int) -> int:
    """Simple addition."""
    return a + b


def subtract(a: int, b: int) -> int:
    """Simple subtraction."""
    return a - b


def multiply(a: int, b: int) -> int:
    """Simple multiplication."""
    return a * b


def divide(a: int, b: int) -> float:
    """Division with zero check."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b


def is_positive(n: int) -> bool:
    """Check if number is positive."""
    if n > 0:
        return True
    return False


def is_even(n: int) -> bool:
    """Check if number is even."""
    if n % 2 == 0:
        return True
    return False


def clamp(value: int, low: int, high: int) -> int:
    """Clamp value between low and high."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def grade(score: int) -> str:
    """Return letter grade for a score."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"


def fizzbuzz(n: int) -> str:
    """Classic fizzbuzz."""
    if n % 15 == 0:
        return "FizzBuzz"
    elif n % 3 == 0:
        return "Fizz"
    elif n % 5 == 0:
        return "Buzz"
    else:
        return str(n)


def factorial(n: int) -> int:
    """Calculate factorial."""
    if n < 0:
        raise ValueError("Negative numbers not allowed")
    if n <= 1:
        return 1
    return n * factorial(n - 1)
