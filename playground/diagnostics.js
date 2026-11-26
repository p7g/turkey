// Filters the compiler's diagnostics for the page. No DOM, so node loads it.

// The wasm32 backend refuses some functions and compiles each to a trap. It
// warns once per compile that has any, "main.gob: warning: N functions use
// operations the wasm32 backend does not compile yet ...", and counts the
// library's refused functions along with the user's, whether the program
// calls them or not.
const REFUSED = /functions? use operations the wasm32 backend does not compile yet/;

// Returns the diagnostics to show for a program, trimmed. The refused-functions
// warning is dropped unless the run `trapped`: a program that compiles and
// exits never reached a refused function, so the warning is noise, and one
// that traps may have reached one, so the warning is the clue to why.
export function diagnosticsFor(diagnostics, trapped) {
  return diagnostics
    .split("\n")
    .filter((line) => trapped || !REFUSED.test(line))
    .join("\n")
    .trim();
}
