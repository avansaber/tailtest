// Fixture test file for JSRunner tests.

import { describe, it, expect } from "vitest";
import { add, subtract } from "../src/math.js";

describe("math", () => {
  it("adds two numbers", () => {
    expect(add(2, 3)).toBe(5);
  });

  it("subtracts two numbers (intentional bug)", () => {
    // subtract() returns a + b, not a - b, so this assertion fails.
    // The JSRunner test suite asserts that this failure is surfaced
    // as a Finding with severity HIGH.
    expect(subtract(5, 3)).toBe(2);
  });

  it.skip("multiplies two numbers (skipped)", () => {
    // Intentionally skipped to exercise JSRunner's skipped-test parsing.
    expect(true).toBe(true);
  });
});
