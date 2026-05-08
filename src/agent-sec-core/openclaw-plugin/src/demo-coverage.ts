/**
 * Demo module for testing incremental coverage gate.
 *
 * Contains pure utility functions to verify diff-cover behavior.
 */

/** Add two numbers. */
export function add(a: number, b: number): number {
  return a + b;
}

/** Subtract b from a. */
export function subtract(a: number, b: number): number {
  return a - b;
}

/** Multiply two numbers. */
export function multiply(a: number, b: number): number {
  return a * b;
}

/** Safe division — returns null for zero divisor. */
export function divide(a: number, b: number): number | null {
  if (b === 0) {
    return null;
  }
  return a / b;
}

/** Check if number is positive. */
export function isPositive(n: number): boolean {
  if (n > 0) {
    return true;
  }
  return false;
}

/** Check if number is even. */
export function isEven(n: number): boolean {
  if (n % 2 === 0) {
    return true;
  }
  return false;
}

/** Clamp value between low and high. */
export function clamp(value: number, low: number, high: number): number {
  if (value < low) {
    return low;
  }
  if (value > high) {
    return high;
  }
  return value;
}

/** Return letter grade for a score. */
export function grade(score: number): string {
  if (score >= 90) {
    return "A";
  } else if (score >= 80) {
    return "B";
  } else if (score >= 70) {
    return "C";
  } else if (score >= 60) {
    return "D";
  } else {
    return "F";
  }
}

/** Classic fizzbuzz. */
export function fizzbuzz(n: number): string {
  if (n % 15 === 0) {
    return "FizzBuzz";
  } else if (n % 3 === 0) {
    return "Fizz";
  } else if (n % 5 === 0) {
    return "Buzz";
  } else {
    return String(n);
  }
}

/** Calculate factorial (throws for negative). */
export function factorial(n: number): number {
  if (n < 0) {
    throw new Error("Negative numbers not allowed");
  }
  if (n <= 1) {
    return 1;
  }
  return n * factorial(n - 1);
}
