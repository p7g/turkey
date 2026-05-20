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
signature Show[T] {
    fn show(T) -> string
}

fn print[T](obj: T)(S: Show[T]) {
    print_to_console_somehow(S.show(obj))
}

type Option[T] = Some(T) | None

implicit module OptionShow[T](ShowT: Show[T]) : Show[Option[T]] {
    fn show(self: Option[T]) -> string = match self {
        Some(v) => "Some(" + ShowT.show(v) + ")"
        None => "None"
    }
}

implicit module StringShow : Show[string] {
    fn show(self: string) -> string = self
}

// print[Option[string]] needs Show[Option[string]] -> finds OptionShow[T]
// OptionShow[string] needs Show[string] -> finds StringShow
// implicitly print(Some("hello!"))(OptionShow(StringShow))
print(Some("hello!")) // prints: Some("hello!")
```
