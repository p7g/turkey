type Tape = Tape { data : Array Int, pos : Int }

fun newTape() = Tape { data = [0], pos = 0 }

fun inc(t, amount) {
    let val = t.data[t.pos] + amount
    t.data[t.pos] = val % 256
}

fun move(t, amount) {
    t.pos = t.pos + amount
    while t.pos >= t.data.length {
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
    while i < chars.length {
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

fun run(tape, ops) {
    for op in ops {
        match op {
            Inc(n) -> inc(tape, n)
            Move(n) -> move(tape, n)
            Print -> write(Char.toString(Char.fromInt(get(tape))))
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

fun main() {
    let source = String.chars(hello)
    let (ops, _) = parse(source, 0)
    let tape = newTape()
    run(tape, ops)
}
