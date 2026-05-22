# turkey 🦃

Roadmap:

- ML-style static type system
- Hindley-Milner type inference
  - global inference per module, exports need annotations
- lower to SSA
- lower to bytecode for low-level register-based VM
- modular implicits[1] with lexical coherence
- light optimizations on SSA
- fancy allocator
- lower to native code AoT


[1]: https://www.cl.cam.ac.uk/~jdy22/papers/modular-implicits.pdf

Sample (syntax subject to change):

```rust
signature Show[a] {
    fn show(a) -> String
}

fn print(obj: a)(S: Show[a]) {
    print_to_console_somehow(S.show(obj))
}

type Option[a] = Some(a) | None

implicit module OptionShow[a](ShowT: Show[a]) : Show[Option[a]] {
    fn show(self: Option[a]) -> String = match self {
        Some(v) => "Some(" + ShowT.show(v) + ")"
        None => "None"
    }
}

implicit module StringShow : Show[String] {
    fn show(self: String) -> String = self
}

// print[Option[string]] needs Show[Option[string]] -> finds OptionShow[a]
// OptionShow[string] needs Show[string] -> finds StringShow
// implicitly print(Some("hello!"))(OptionShow(StringShow))
print(Some("hello!")) // prints: Some("hello!")
```
