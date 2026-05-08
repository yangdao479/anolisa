//! Demo module for testing incremental coverage gate.
//!
//! Contains pure functions (no Linux-specific deps) to verify diff-cover behavior.

/// Add two numbers.
pub fn add(a: i64, b: i64) -> i64 {
    a + b
}

/// Subtract b from a.
pub fn subtract(a: i64, b: i64) -> i64 {
    a - b
}

/// Multiply two numbers.
pub fn multiply(a: i64, b: i64) -> i64 {
    a * b
}

/// Safe division returning None for zero divisor.
pub fn divide(a: i64, b: i64) -> Option<f64> {
    if b == 0 {
        None
    } else {
        Some(a as f64 / b as f64)
    }
}

/// Check if number is positive.
pub fn is_positive(n: i64) -> bool {
    n > 0
}

/// Clamp value between low and high (inclusive).
pub fn clamp(value: i64, low: i64, high: i64) -> i64 {
    if value < low {
        low
    } else if value > high {
        high
    } else {
        value
    }
}

/// Return letter grade for a score.
pub fn grade(score: u32) -> &'static str {
    if score >= 90 {
        "A"
    } else if score >= 80 {
        "B"
    } else if score >= 70 {
        "C"
    } else if score >= 60 {
        "D"
    } else {
        "F"
    }
}

/// Classic fizzbuzz.
pub fn fizzbuzz(n: u32) -> String {
    if n % 15 == 0 {
        "FizzBuzz".to_string()
    } else if n % 3 == 0 {
        "Fizz".to_string()
    } else if n % 5 == 0 {
        "Buzz".to_string()
    } else {
        n.to_string()
    }
}

/// Calculate factorial. Returns None for negative conceptual input (handled via u64).
pub fn factorial(n: u64) -> u64 {
    if n <= 1 {
        1
    } else {
        n * factorial(n - 1)
    }
}
