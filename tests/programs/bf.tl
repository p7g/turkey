type Tape = Tape { data : Array Int, pos : Int }

fun newTape() = Tape { data = [0], pos = 0 }

fun inc(t, amount) {
    let val = t.data[t.pos] + amount
    t.data[t.pos] = val % 256
}

fun move(t, amount) {
    t.pos = t.pos + amount
    while t.pos >= len(t.data) {
        Array.push(t.data, 0)
    }
}

fun get(t) = t.data[t.pos]

type Op = Inc(Int)
        | Move(Int)
        | Print
        | Loop(Array Op)

fun parse(chars, start) {
    var i = start
    let ops = []
    while i < len(chars) {
        Array.push(ops, match chars[i] {
            '+' -> Inc(1)
            '-' -> Inc(-1)
            '>' -> Move(1)
            '<' -> Move(-1)
            '.' -> Print
            '[' -> {
                let (loopOps, newI) = parse(chars, i + 1)
                i = newI
                Loop(loopOps)
            }
            ']' -> break
            _ -> continue
        })
        i = i + 1
    }
    (ops, i)
}

-- The general type, and it takes both halves of M10 to write it down. `ops` is
-- any `Iterator` whose element is an `Op`, which is what `run` actually needs
-- -- not an `Array Op`, which is merely what the recursive call would have
-- pinned it to. `Item s ~ Op` is the equality (delta 39); stating it in a
-- signature is what makes it a *given*, so `Item s` reduces to `Op` and the
-- `match` below can find its constructors. And because the signature is known
-- before the body is solved (delta 38), `run(tape, loopOps)` instantiates it at
-- `Array Op` rather than forcing `s` to be one.
fun run[Iterator s, Item s ~ Op](tape : Tape, ops : s) -> Unit {
    for op in ops {
        match op {
            Inc(n) -> inc(tape, n)
            Move(n) -> move(tape, n)
            Print -> write(Option.unwrapOr(Char.fromInt(get(tape)), '?'))
            Loop(loopOps) -> {
                while get(tape) != 0 {
                    run(tape, loopOps)
                }
            }
        }
    }
}

let hello = "++++++++[>++++[>++>+++>+++>+<<<<-]>+>->+>>+[<]<-]>>.>>---.+++++++..+++.>.<<-.>.+++.------.--------.>+.>++."
let bench = ">++[<+++++++++++++>-]<[[>+>+<<-]>[<+>-]++++++++[>++++++++<-]>.[-]<<>++++++++++[>++++++++++[>++++++++++[>++++++++++[>++++++++++[>++++++++++[>++++++++++[-]<-]<-]<-]<-]<-]<-]<-]++++++++++."

-- `String.codePoints` is a lazy view; the parser indexes, so it wants an
-- array and says so.
fun collect(s : String) -> Array Char {
    let out = Array.new(String.byteLength(s))
    for c in String.codePoints(s) { Array.push(out, c) }
    out
}

fun main() {
    let source = collect(hello)
    let (ops, _) = parse(source, 0)
    let tape = newTape()
    run(tape, ops)
}
