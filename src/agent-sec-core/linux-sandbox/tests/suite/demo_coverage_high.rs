//! HIGH coverage test for demo_coverage module (~95% line coverage).
//! When included in a PR, diff-cover should report gate PASS.

use linux_sandbox::demo_coverage::*;

#[test]
fn test_add() {
    assert_eq!(add(2, 3), 5);
    assert_eq!(add(-1, -2), -3);
}

#[test]
fn test_subtract() {
    assert_eq!(subtract(5, 3), 2);
}

#[test]
fn test_multiply() {
    assert_eq!(multiply(3, 4), 12);
}

#[test]
fn test_divide_normal() {
    assert_eq!(divide(10, 2), Some(5.0));
}

#[test]
fn test_divide_by_zero() {
    assert_eq!(divide(1, 0), None);
}

#[test]
fn test_is_positive() {
    assert!(is_positive(5));
    assert!(!is_positive(0));
    assert!(!is_positive(-3));
}

#[test]
fn test_clamp_below() {
    assert_eq!(clamp(-5, 0, 10), 0);
}

#[test]
fn test_clamp_above() {
    assert_eq!(clamp(15, 0, 10), 10);
}

#[test]
fn test_clamp_within() {
    assert_eq!(clamp(5, 0, 10), 5);
}

#[test]
fn test_grade_all() {
    assert_eq!(grade(95), "A");
    assert_eq!(grade(85), "B");
    assert_eq!(grade(75), "C");
    assert_eq!(grade(65), "D");
    assert_eq!(grade(50), "F");
}

#[test]
fn test_fizzbuzz_all() {
    assert_eq!(fizzbuzz(15), "FizzBuzz");
    assert_eq!(fizzbuzz(9), "Fizz");
    assert_eq!(fizzbuzz(10), "Buzz");
    assert_eq!(fizzbuzz(7), "7");
}

#[test]
fn test_factorial() {
    assert_eq!(factorial(0), 1);
    assert_eq!(factorial(1), 1);
    assert_eq!(factorial(5), 120);
}
