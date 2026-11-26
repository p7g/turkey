// Filters the compiler's diagnostics for the page. No DOM, so node loads it.

// The wasm32 backend refuses some functions and compiles each to a trap. It
// reports how many on every compile, user functions included, as
// "main.gob: warning: N functions use operations the wasm32 backend does not
// compile yet ...".
const REFUSED = /functions use operations the wasm32 backend does not compile yet/;
const COUNT = /warning: (\d+) functions? use/;

// Returns the number the refused-functions warning in `diagnostics` states,
// or 0 if there is none.
export function refusedCount(diagnostics) {
  for (const line of diagnostics.split("\n")) {
    if (REFUSED.test(line)) {
      const match = COUNT.exec(line);
      return match === null ? 0 : Number(match[1]);
    }
  }
  return 0;
}

// Returns `diagnostics` trimmed, without the refused-functions warning when
// its count is `baseline`. The baseline is the count for a program that only
// prints: the runtime's own refused functions, which say nothing about the
// user's program. A larger count means the program uses something the
// backend cannot compile yet, and the warning is the clue to the trap it will
// hit, so we keep it.
export function withoutBaseline(diagnostics, baseline) {
  return diagnostics
    .split("\n")
    .filter((line) => !(REFUSED.test(line) && refusedCount(line) === baseline))
    .join("\n")
    .trim();
}
