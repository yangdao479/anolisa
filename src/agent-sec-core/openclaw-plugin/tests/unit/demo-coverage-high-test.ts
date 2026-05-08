/**
 * HIGH coverage test for demo-coverage module (~95% line coverage).
 * When included in a PR, diff-cover should report gate PASS.
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  add,
  subtract,
  multiply,
  divide,
  isPositive,
  isEven,
  clamp,
  grade,
  fizzbuzz,
  factorial,
} from "../../src/demo-coverage.js";

describe("demo-coverage HIGH coverage", () => {
  describe("add", () => {
    it("adds positive numbers", () => {
      assert.equal(add(2, 3), 5);
    });
    it("adds negative numbers", () => {
      assert.equal(add(-1, -2), -3);
    });
  });

  describe("subtract", () => {
    it("basic subtraction", () => {
      assert.equal(subtract(5, 3), 2);
    });
  });

  describe("multiply", () => {
    it("basic multiplication", () => {
      assert.equal(multiply(3, 4), 12);
    });
  });

  describe("divide", () => {
    it("normal division", () => {
      assert.equal(divide(10, 2), 5);
    });
    it("returns null for zero divisor", () => {
      assert.equal(divide(1, 0), null);
    });
  });

  describe("isPositive", () => {
    it("returns true for positive", () => {
      assert.equal(isPositive(5), true);
    });
    it("returns false for zero", () => {
      assert.equal(isPositive(0), false);
    });
    it("returns false for negative", () => {
      assert.equal(isPositive(-3), false);
    });
  });

  describe("isEven", () => {
    it("returns true for even", () => {
      assert.equal(isEven(4), true);
    });
    it("returns false for odd", () => {
      assert.equal(isEven(3), false);
    });
  });

  describe("clamp", () => {
    it("clamps below low", () => {
      assert.equal(clamp(-5, 0, 10), 0);
    });
    it("clamps above high", () => {
      assert.equal(clamp(15, 0, 10), 10);
    });
    it("returns value within range", () => {
      assert.equal(clamp(5, 0, 10), 5);
    });
  });

  describe("grade", () => {
    it("returns A for 90+", () => {
      assert.equal(grade(95), "A");
    });
    it("returns B for 80+", () => {
      assert.equal(grade(85), "B");
    });
    it("returns C for 70+", () => {
      assert.equal(grade(75), "C");
    });
    it("returns D for 60+", () => {
      assert.equal(grade(65), "D");
    });
    it("returns F for below 60", () => {
      assert.equal(grade(50), "F");
    });
  });

  describe("fizzbuzz", () => {
    it("returns FizzBuzz for multiples of 15", () => {
      assert.equal(fizzbuzz(15), "FizzBuzz");
    });
    it("returns Fizz for multiples of 3", () => {
      assert.equal(fizzbuzz(9), "Fizz");
    });
    it("returns Buzz for multiples of 5", () => {
      assert.equal(fizzbuzz(10), "Buzz");
    });
    it("returns number string otherwise", () => {
      assert.equal(fizzbuzz(7), "7");
    });
  });

  describe("factorial", () => {
    it("returns 1 for 0", () => {
      assert.equal(factorial(0), 1);
    });
    it("returns 120 for 5", () => {
      assert.equal(factorial(5), 120);
    });
    it("throws for negative", () => {
      assert.throws(() => factorial(-1), /Negative numbers not allowed/);
    });
  });
});
