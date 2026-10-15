"""`System.IO`'s streams: `Buffer`, the `Read` and `Write` classes,
`Buffered` and its line operations, the whole-transfer functions, and the
existential `AnyRead`/`AnyWrite`.

Most sources here are an in-memory `Chunks` whose `readInto` hands out at most
a few bytes per call, so that every path that has to cope with a short read is
made to: lines split across refills, a buffer moved to the front, a line
longer than the buffer.
"""

from __future__ import annotations


from tests.lang import run

HEADER = """\
import System.IO as IO

-- A source over `text` that answers at most `step` bytes per read.
type Chunks = Chunks { bytes : Array Byte, step : Int, var at : Int }

fun chunks(text : String, step : Int) -> Chunks =
    Chunks { bytes = String.toBytes(text), step = step, at = 0 }

instance IO.Read Chunks {
    type ReadError = String
    fun readInto(c, into) {
        var n = len(c.bytes) - c.at
        if n > c.step { n = c.step }
        if n > IO.room(into) { n = IO.room(into) }
        IO.append(into, c.bytes, c.at, c.at + n)
        c.at = c.at + n
        Right(n)
    }
}

-- A source that fails on its first read.
type Broken = Broken

instance IO.Read Broken {
    type ReadError = String
    fun readInto(b, into) = Left("broken")
}

-- A sink that collects what it is given, and counts the writes.
type Sink = Sink { var got : String, var writes : Int }

fun sink() -> Sink = Sink { got = "", writes = 0 }

instance IO.Write Sink {
    type WriteError = String
    fun writeFrom(s, from) {
        s.got = s.got + Option.unwrapOr(IO.toText(from), "?")
        s.writes = s.writes + 1
        Right(())
    }
    fun flush(s) = Right(())
}

fun showLine(l : IO.Line String) -> String = match l {
    IO.Got -> "Got"
    IO.Partial -> "Partial"
    IO.End -> "End"
    IO.Failed(e) -> "Failed(" + e + ")"
}
"""


def output(src: str) -> list[str]:
    result = run(HEADER + src)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_a_buffer_is_a_window_over_its_storage():
    out = output("""
fun main() {
    let b = IO.buffer(8)
    print((IO.capacity(b), IO.filled(b), IO.room(b)))
    IO.append(b, String.toBytes("hello"), 0, 5)
    IO.push(b, Byte.truncate(33))
    print((IO.filled(b), IO.room(b), len(b)))
    print((Byte.toInt(b[0]), Byte.toInt(b[5])))
    IO.consume(b, 2)
    print(IO.toText(b))
    var sum = 0
    for x in b { sum = sum + Byte.toInt(x) }
    print(sum)
    -- Consuming the rest starts the window again at the front.
    IO.consume(b, 4)
    print((IO.filled(b), IO.room(b)))
    print(IO.toText(IO.bytesOf("text")))
    print(IO.bytes(IO.bytesOf("ab")))
}
""")
    assert out == ["(8, 0, 8)", "(6, 2, 6)", "(104, 33)", "Some(llo!)",
                   str(sum(b"llo!")), "(0, 8)", "Some(text)", "[97, 98]"]


def test_toText_refuses_bytes_that_are_not_utf8():
    out = output("""
fun main() {
    let b = IO.buffer(4)
    IO.push(b, Byte.truncate(0xC3))
    print(IO.toText(b))
    IO.push(b, Byte.truncate(0xA9))
    print(IO.toText(b))
}
""")
    assert out == ["None", "Some(é)"]


def test_lines_across_refills_and_a_carriage_return():
    out = output("""
fun main() {
    -- A buffer of 8, refilled 3 bytes at a time: lines straddle every refill,
    -- and the 20-byte line is longer than the buffer.
    let b = IO.buffered(chunks("one\\ntwo\\r\\n\\nthe-twenty-byte-line\\nlast", 3), 8)
    for line in IO.lines(b) { print(line) }
}
""")
    assert out == ["Right(one)", "Right(two)", "Right()", "Right(the-twenty-byte-line)",
                   "Right(last)"]


def test_readLine_reports_invalid_utf8_and_a_failed_source():
    out = output("""
fun main() {
    let bytes = [Byte.truncate(111), Byte.truncate(107), Byte.truncate(10),
                 Byte.truncate(0xff), Byte.truncate(10)]
    let b = IO.buffered(Chunks { bytes = bytes, step = 2, at = 0 }, 16)
    print(IO.readLine(b))
    print(IO.readLine(b))
    print(IO.readLine(IO.buffered(Broken, 16)))
    -- The end, after the last line.
    let one = IO.buffered(chunks("a\\n", 5), 16)
    print(IO.readLine(one))
    print(IO.readLine(one))
}
""")
    assert out == ["Right(Some(ok))", "Left(InvalidUTF8)", "Left(Underlying(broken))",
                   "Right(Some(a))", "Right(None)"]


def test_lines_stops_after_its_first_error():
    out = output("""
fun main() {
    var n = 0
    for line in IO.lines(IO.buffered(Broken, 16)) {
        print(line)
        n = n + 1
    }
    print(n)
}
""")
    assert out == ["Left(Underlying(broken))", "1"]


def test_readLineInto_appends_each_line_to_the_callers_buffer():
    out = output("""
fun main() {
    let b = IO.buffered(chunks("alpha\\nbe\\r\\n\\nlong-long-line\\nend", 3), 4)
    let out = IO.buffer(64)
    while True {
        let l = IO.readLineInto(b, out)
        print(showLine(l) + " " + Option.unwrapOr(IO.toText(out), "?"))
        IO.clear(out)
        match l {
            IO.End -> break
            _ -> {}
        }
    }
}
""")
    assert out == ["Got alpha", "Got be\r", "Got ", "Got long-long-line", "Got end", "End "]


def test_readLineInto_answers_partial_when_the_output_fills():
    out = output("""
fun main() {
    let b = IO.buffered(chunks("abcdefghij\\nxy\\n", 4), 6)
    let out = IO.buffer(4)
    for var i = 0; i < 6; i = i + 1 {
        let l = IO.readLineInto(b, out)
        print(showLine(l) + " " + Option.unwrapOr(IO.toText(out), "?"))
        IO.clear(out)
    }
    print(showLine(IO.readLineInto(IO.buffered(Broken, 4), out)))
}
""")
    assert out == ["Partial abcd", "Partial efgh", "Got ij", "Got xy", "End ", "End ",
                   "Failed(broken)"]


def test_a_buffered_read_bypasses_the_buffer_only_when_empty_and_large():
    out = output("""
fun main() {
    let source = chunks("0123456789abcdefghij", 100)
    let b = IO.buffered(source, 4)
    let small = IO.buffer(2)
    print(IO.readInto(b, small))
    -- The buffer holds "23" now; a large read takes those two first.
    let large = IO.buffer(10)
    print(IO.readInto(b, large))
    print(IO.toText(large))
    -- Empty again, and the read is as large as the buffer: straight through.
    IO.clear(large)
    print(IO.readInto(b, large))
    print(IO.toText(large))
    print(source.at)
}
""")
    assert out == ["Right(2)", "Right(2)", "Some(23)", "Right(10)", "Some(456789abcd)", "14"]


def test_a_buffered_write_holds_small_writes_and_passes_large_ones():
    out = output("""
fun main() {
    let s = sink()
    let b = IO.buffered(s, 8)
    print(IO.writeText(b, "abc"))
    print(IO.writeText(b, "defgh"))
    print((s.got, s.writes))
    -- Fills the buffer, which goes out, then the rest is held.
    print(IO.writeText(b, "ijklm"))
    print((s.got, s.writes))
    print(IO.flush(b))
    print((s.got, s.writes))
    -- Empty, and as large as the buffer: one write, straight through.
    print(IO.writeFrom(b, IO.bytesOf("0123456789")))
    print((s.got, s.writes))
}
""".replace("print(IO.writeText(b, ", "check(IO.writeText(b, ")
       .replace("print(IO.flush(b))", "check(IO.flush(b))")
       .replace("print(IO.writeFrom(", "check(IO.writeFrom(")
       + """
fun check(r : Either String Unit) { print(Either.isRight(r)) }
""")
    assert out == ["True", "True", "(, 0)", "True", "(abcdefgh, 1)", "True",
                   "(abcdefghijklm, 2)", "True", "(abcdefghijklm0123456789, 3)"]


def test_readExact_takes_no_more_than_asked():
    out = output("""
fun main() {
    let b = IO.buffered(chunks("headbody!", 2), 64)
    let into = IO.buffer(16)
    print(IO.readExact(b, into, 4))
    print(IO.toText(into))
    IO.clear(into)
    print(IO.readExact(b, into, 4))
    print(IO.toText(into))
    IO.clear(into)
    print(IO.readExact(b, into, 4))
    print(IO.readExact(Broken, into, 1))
}
""".replace("print(IO.readExact(", "check(IO.readExact(") + """
fun check(r : Either (IO.ExactError String) Unit) = match r {
    Right(_) -> print("Right")
    Left(e) -> print(e)
}
""")
    assert out == ["Right", "Some(head)", "Right", "Some(body)", "EndedEarly",
                   "ReadFailed(broken)"]


def test_readAll_copy_and_writeText():
    out = output("""
fun main() {
    let text = String.repeat("0123456789", 50000)
    match IO.readAll(chunks(text, 70000)) {
        Right(b) -> print((IO.filled(b), IO.toText(b) == Some(text)))
        Left(e) -> print(e)
    }
    let s = sink()
    match IO.copy(chunks("copied", 4), s, IO.buffer(3)) {
        Right(n) -> print((n, s.got, s.writes))
        Left(_) -> print("failed")
    }
    match IO.copy(Broken, s, IO.buffer(3)) {
        Left(IO.Reading(e)) -> print(e)
        _ -> print("?")
    }
}
""")
    assert out == ["(500000, True)", "(6, copied, 2)", "broken"]


def test_any_read_and_any_write_choose_at_run_time():
    out = output("""
fun pick(failing : Bool) -> IO.AnyRead String =
    if failing { IO.anyRead(Broken, fun(e) = "lifted " + e) }
    else { IO.anyRead(chunks("x\\ny\\n", 1), fun(e) = e) }

fun main() {
    for line in IO.lines(IO.buffered(pick(False), 4)) { print(line) }
    for line in IO.lines(IO.buffered(pick(True), 4)) { print(line) }
    let s = sink()
    let w = IO.anyWrite(s, fun(e) = e)
    let _ = IO.writeText(w, "via any")
    let _ = IO.flush(w)
    print(s.got)
}
""")
    assert out == ["Right(x)", "Right(y)", "Left(Underlying(lifted broken))", "via any"]


def test_reading_into_a_full_buffer_is_a_bug():
    result = run(HEADER + """
fun main() {
    let _ = IO.readInto(IO.buffered(chunks("x", 1), 4), IO.buffer(0))
}
""")
    assert result.code != 0
    assert "readInto: the buffer has no room" in result.stderr
