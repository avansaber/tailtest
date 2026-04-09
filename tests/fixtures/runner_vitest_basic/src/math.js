// Fixture source file for JSRunner tests. Keep plain JS (no TS) so the
// fixture doesn't need a compile step. JSRunner targets both.

export function add(a, b) {
  return a + b;
}

export function subtract(a, b) {
  // Intentional bug: returns a + b instead of a - b, so the matching
  // test in runner_vitest_basic/tests/math.test.js fails. JSRunner's
  // tests assert that this failure surfaces as a Finding.
  return a + b;
}
