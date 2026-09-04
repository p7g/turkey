import os
import re
import subprocess
import sys

import pytest
from pathlib import Path

from turkey.driver import check
from turkey.cli import main as cli_main
from turkey.errors import TurkeyPanic
from turkey import backend_ir as bir
from turkey import core
from turkey.backend_lower import lower
from turkey.llvmgen import compile, execute, generate, _root_slots

REPO_ROOT = Path(__file__).resolve().parent.parent

PROGRAMS_DIR = Path(__file__).parent / "programs"
# Every runtime entry point generated code is allowed to call. Allocation,
# panics, root and frame bookkeeping, and the string and float operations that
# genuinely need C. Reading a field, an element, a tag, an array length, a cell
# or a closure slot is not here and must not be: each is a `getelementptr` and
# a `load`, because the offset and the layout are compile-time facts in a
# monomorphized program. Anything new appearing here is a decision, not an
# accident, which is the point of listing what is allowed rather than what is
# forbidden.
def _runtime_entry_points() -> set[str]:
    """Every function the runtime exports, read from its header.

    Read rather than listed so that deleting one cannot leave this test
    quietly asserting something about a name that no longer exists.
    """
    header = (Path(__file__).parent.parent / "runtime" / "turkey_runtime.h")
    return set(re.findall(r"\b(turkey_\w+)\s*\(", header.read_text()))


ALLOWED_RUNTIME_CALLS = {
    "turkey_object_new", "turkey_array_new", "turkey_cell_new",
    "turkey_closure_new", "turkey_closure_capture",
    "turkey_box", "turkey_unbox",
    "turkey_string_new", "turkey_string_concat", "turkey_string_concat_all",
    "turkey_string_eq", "turkey_string_lt", "turkey_string_byte_length",
    "turkey_string_byte_at", "turkey_string_decode_at",
    "turkey_string_next_index", "turkey_string_slice", "turkey_string_find",
    "turkey_string_rfind", "turkey_string_to_byte_storage",
    "turkey_string_from_bytes", "turkey_string_is_valid_utf8",
    "turkey_int_to_string", "turkey_float_to_string", "turkey_char_to_string",
    "turkey_float_parse", "turkey_float_can_parse", "turkey_float_fmod",
    "turkey_float_remainder", "turkey_float_floor", "turkey_float_ceil",
    "turkey_float_round", "turkey_float_trunc",
    "turkey_print", "turkey_write",
    # The outside world: arguments, the two file doors, the error stream and
    # `exit`. Each is one call by construction -- there is no inline form of
    # opening a file -- so they belong here rather than being made into loads.
    "turkey_args_storage", "turkey_file_can_read", "turkey_read_file_bytes",
    "turkey_write_file_bytes", "turkey_stderr_write", "turkey_exit",
    "turkey_panic", "turkey_panic_string", "turkey_panicked",
    "turkey_root_enter", "turkey_root_leave",
    "turkey_frame_enter", "turkey_frame_leave",
}
NATIVE_PROGRAMS = sorted(
    path.stem for path in PROGRAMS_DIR.glob("*.tl")
    if not path.stem.startswith("err_") and path.with_suffix(".expected").exists()
)
ERROR_PROGRAMS = sorted(
    path.stem for path in PROGRAMS_DIR.glob("err_*.tl")
    if path.with_suffix(".expected").exists()
)


def native(src: str) -> None:
    checked = check(src)
    execute(checked.opt, checked.decls, checked.main)


def test_generated_llvm_is_verified_and_contains_native_arithmetic():
    checked = check("fun main() { print(1 + 2) }")
    text = generate(checked.opt, checked.decls, checked.main)
    assert "llvm.sadd.with.overflow.i64" in text
    assert "turkey_int_to_string" in text


def test_shadow_roots_use_stack_storage_and_direct_stores():
    checked = check('fun main() { let value = Some("rooted"); print(value) }')
    text = generate(checked.opt, checked.decls, checked.main)
    assert "alloca [" in text
    assert "@turkey_root_enter" in text
    assert "@turkey_root_set" not in text
    assert "@turkey_root_push" not in text


def test_a_nullary_constructor_is_built_once_for_the_whole_run():
    checked = check(
        'fun pick(n) = if n > 0 { Some(n) } else { None }\n'
        'fun main() { print(pick(1)); print(pick(-1)) }')
    text = generate(checked.opt, checked.decls, checked.main)
    # `None` carries no fields, so every evaluation of it may share one
    # object. Only the entry function still builds one; everywhere else the
    # use is a load of the global holding it.
    for part in text.split("\ndefine ")[1:]:
        if part.startswith("i8 @turkeyfn_run("):
            continue
        assert "i64 0, i64 0)" not in part, part.splitlines()[0]
    assert text.count("@.turkey.nullary.value.") >= 2


def test_a_for_loop_allocates_no_cursor():
    # `Iterator.iter` builds a one-field mutable record and `next` reads and
    # writes it. Once `next` is inlined the record is born and dies inside
    # one function without being handed to anything, so it is its field and
    # nothing else -- and a loop over an array allocates nothing at all.
    checked = check(
        'fun total(xs : Array Int) -> Int {\n'
        '    var sum = 0\n'
        '    for x in xs { sum = sum + x }\n'
        '    sum\n'
        '}\n'
        'fun main() { print(total([1, 2, 3])) }')
    source = lower(checked.opt, checked.decls, checked.main)
    total = next(f for f in source.functions if "23_total" in f.name)
    built = [i.op for b in total.blocks for i in b.instructions
             if i.op in ("object_new", "array_new", "cell_new", "closure_new")]
    assert not built, f"the loop still allocates {built}"


def test_roots_are_the_slots_live_across_a_collection():
    # `bump` allocates nothing and the only thing in it that can collect is
    # the bounds check it calls. `backend_lower` gives every ANF temporary a
    # slot, and slots used to be rooted whether or not a collection could
    # happen while they held anything, so a two-line function paid a root
    # store per temporary.
    # Recursive so that it survives to be looked at: `loop_breakers` never
    # inlines a self-recursive binding, and since `_size` started discounting
    # the names and single-constructor unpacks that make up most of a function
    # like this one, the straight-line version folds into its caller.
    checked = check("""
type Tape = Tape { data : Array Int, pos : Int }
fun bump(t : Tape, n : Int) {
    if n <= 0 { return }
    t.data[t.pos] = t.data[t.pos] + 1
    bump(t, n - 1)
}
fun main() { let t = Tape { data = [0], pos = 0 }
             bump(t, 3); print(t.data[0]) }
""")
    source = lower(checked.opt, checked.decls, checked.main)
    bump = next(f for f in source.functions if "23_bump" in f.name)
    pointers = [s for s in bump.slots
                if s.layout in (bir.Layout.PTR, bir.Layout.BOXED)]
    assert len(pointers) >= 8, "the lowering should still be slot-heavy"
    index, roots, live = _root_slots(bump)
    assert roots < len(pointers)
    # And each safepoint names its own live set rather than the union: the
    # frame is registered once but the collector is told, per call, exactly
    # which of those slots holds something.
    assert live, "the bounds check it calls is a safepoint"
    assert any(names < set(index) for names in live.values()), (
        "no safepoint should have to claim every root the function has")


def test_a_function_whose_only_safepoints_are_cold_registers_nothing_hot():
    """`inc` is the brainfuck benchmark's hot loop body, and it has two calls.

    Both are the out-of-bounds message, so the frame that describes them --
    and the array it describes, and the zeroing that array used to need --
    belongs on the paths that panic, not on the one that stores a byte.

    Recursive so that there is still a function to look at: a self-recursive
    binding is a loop breaker and is never inlined, which the straight-line
    version now would be.
    """
    checked = check("""
type Tape = Tape { data : Array Int, pos : Int }
fun inc(t : Tape, amount : Int) {
    if amount <= 0 { return }
    t.data[t.pos] = t.data[t.pos] + amount
    inc(t, amount - 1)
}
fun main() { let t = Tape { data = [0], pos = 0 }
             inc(t, 1); print(t.data[0]) }
""")
    text = generate(checked.opt, checked.decls, checked.main)
    body = _body(text, "23_inc")
    entry = body.split("\n\n")[0]
    assert "@turkey_root_enter" in body, "the cold paths still need a frame"
    assert "@turkey_root_enter" not in entry, (
        "the hot path should register no root frame")
    assert "@turkey_frame_enter" not in entry, (
        "the hot path should register no panic frame")


def _body(text: str, name: str) -> str:
    """The text of one generated function, by the fragment naming it."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("define ") and name in line)
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end])


def test_language_string_literals_are_allocated_once_at_module_entry():
    checked = check('fun main() { print("same"); print("same") }')
    text = generate(checked.opt, checked.decls, checked.main)
    # Matched without the pointer's spelling: `generate` returns LLVM's own
    # rendering of the module, so a typed-pointer LLVM writes `i8*` where an
    # opaque-pointer one writes `ptr`. What this test is about is how many
    # times the literal is built and read, which neither spelling changes.
    calls = [line for line in text.splitlines()
             if "@turkey_string_new(" in line and " call " in f" {line} "]
    assert len(calls) == 1
    assert text.count("@.turkey.literal.bytes.0") >= 1
    loads = [line for line in text.splitlines()
             if line.lstrip().startswith("%")
             and " load " in line and "@.turkey.literal.value.0" in line]
    assert len(loads) == 2


def test_native_scalar_program_prints(capfd):
    native("fun main() { print(40 + 2) }")
    assert capfd.readouterr().out == "42\n"


def test_native_recursive_join_is_stack_safe(capfd):
    native("""
fun main() {
    var i = 0
    while i < 100000 { i = i + 1 }
    print(i)
}
""")
    assert capfd.readouterr().out == "100000\n"


def test_native_checked_integer_overflow_panics():
    with pytest.raises(TurkeyPanic, match="integer overflow in \\+"):
        native("fun main() { print(9223372036854775807 + 1) }")


def test_native_integer_division_remainder_and_shifts(capfd):
    native("""
fun main() {
    print(-7 / 2)
    print(-7 % 3)
    print(Int.shl(1, 10))
    print(Int.shr(-8, 1))
}
""")
    assert capfd.readouterr().out == "-3\n-1\n1024\n-4\n"


def test_native_invalid_shift_panics():
    with pytest.raises(TurkeyPanic, match="shift amount"):
        native("fun main() { print(Int.shl(1, 64)) }")


def test_native_panic_frames_match_the_optimized_backend():
    source = """fun descend(n : Int) -> Int {
    if n == 0 { return error("boom") }
    descend(n - 1)
}
fun main() { print(descend(2)) }
"""
    checked = check(source, "trace.tl")
    with pytest.raises(TurkeyPanic) as raised:
        execute(checked.opt, checked.decls, checked.main, "trace.tl")
    assert raised.value.render("trace.tl") == """panic: boom
  at descend (trace.tl:2:24)
  at descend (trace.tl:3:5)
  at descend (trace.tl:3:5)
  at main (trace.tl:5:20)"""


def test_native_float_division_is_ieee(capfd):
    native("fun main() { print(1.0 / 0.0); print(0.0 / 0.0); print(-0.0) }")
    assert capfd.readouterr().out == "Infinity\nNaN\n-0.0\n"


def test_native_float_text_conversion_and_rounding_match_primitives(capfd):
    native("""
fun main() {
    print(0.1); print(1.0e16); print(1000000000.0)
    print(Float.parse("1.0e+16")); print(Float.parse("Infinity"))
    print(Float.parse("nan")); print(Float.parse("oops"))
    print(Float.round(0.5)); print(Float.round(-2.5)); print(Float.floor(-1.5))
}
""")
    assert capfd.readouterr().out == (
        "0.1\n1.0e+16\n1000000000.0\nSome(1.0e+16)\nSome(Infinity)\n"
        "None\nNone\n1.0\n-3.0\n-2.0\n"
    )


def test_native_utf8_views_and_checked_byte_conversion(capfd):
    native("""
fun main() {
    for b in String.bytes("hé") { print(b) }
    for c in String.codePoints("hé") { print(c) }
    print(String.toBytes("hé"))
    print(String.fromBytes(String.toBytes("hé")))
    let invalid = Array.filled(
        1, Option.unwrapOr(Byte.fromInt(195), Byte.minValue()))
    print(String.fromBytes(invalid))
}
""")
    assert capfd.readouterr().out == \
        "104\n195\n169\nh\né\n[104, 195, 169]\nSome(hé)\nNone\n"


def test_native_string_search_slices_and_builder(capfd):
    native("""
fun main() {
    print(String.split("a,b,c", ","))
    print(String.replace("a-b-c", "-", "+"))
    print(String.startsWith("é", "a"))
    print(String.endsWith("hé", "é"))
    let b = String.builder()
    String.push(b, "a"); String.push(b, "é"); String.push(b, "c")
    print(String.build(b))
}
""")
    assert capfd.readouterr().out == \
        "[a, b, c]\na+b+c\nFalse\nTrue\naéc\n"


def test_native_closures_snapshot_values_and_share_cells(capfd):
    native("""
fun main() {
    let fs = [] : Array (fun() -> Int)
    var i = 0
    while i < 3 {
        let n = i
        Array.push(fs, fun() -> Int = n)
        i = i + 1
    }
    var total = 0
    let bump = fun() -> Int = { total = total + 1; total }
    print(fs[0]()); print(fs[1]()); print(fs[2]())
    print(bump()); print(bump())
}
""")
    assert capfd.readouterr().out == "0\n1\n2\n1\n2\n"


def test_a_var_no_closure_sees_is_a_slot_rather_than_a_cell():
    """Both directions of `backend_lower._flat_refs`.

    `lower.py` makes every `var` a `CRef` because a `var` is captured by
    reference. A `var` no closure mentions owes nothing to that rule, and the
    cell costs an allocation, an indirection per access, a GC root, and a value
    `mem2reg` can never put in a register.
    """
    def allocates_a_cell(source: str) -> bool:
        # Call sites only: the declaration is in the module either way.
        checked = check(source)
        return any(
            "@turkey_cell_new(" in line and " call " in f" {line} "
            for line in generate(
                checked.opt, checked.decls, checked.main).splitlines())

    assert not allocates_a_cell("""
fun main() {
    var total = 0
    for var i = 0; i < 10; i = i + 1 { total = total + i }
    print(total)
}
""")

    # The same loop, with a closure that writes `total`. The cell is what
    # makes that write visible outside the closure, so it has to stay.
    assert allocates_a_cell("""
fun main() {
    var total = 0
    let bump = fun() -> Int = { total = total + 1; total }
    for var i = 0; i < 10; i = i + 1 { total = total + i }
    print(bump())
}
""")


def test_native_recursive_local_closure_uses_two_phase_environment(capfd):
    native("""
fun main() {
    fun fib(n : Int) -> Int =
        if n < 2 { n } else { fib(n - 1) + fib(n - 2) }
    print(fib(10))
}
""")
    assert capfd.readouterr().out == "55\n"


def test_exact_roots_survive_collection_on_every_allocation(monkeypatch, capfd):
    monkeypatch.setenv("TURKEY_GC_STRESS", "1")
    checked = check("""
fun main() {
    let prefix = "value="
    let values = [40, 41, 42]
    let show = fun(i : Int) -> String = prefix + Int.toString(values[i])
    print(show(2))
}
""")
    module = compile(checked.opt, checked.decls, checked.main)
    module.execute()
    assert capfd.readouterr().out == "value=42\n"
    assert module.runtime.turkey_heap_objects() == 0


def test_normal_execution_collects_before_the_entry_function_returns():
    checked = check("""
fun main() {
    var text = ""
    var i = 0
    while i < 5000 {
        text = text + "x"
        i = i + 1
    }
}
""")
    module = compile(checked.opt, checked.decls, checked.main)
    before = module.runtime.turkey_collection_count()
    module.execute()
    # NativeModule.execute performs one final collection. More than one proves
    # the allocation threshold also collected while generated code was active.
    assert module.runtime.turkey_collection_count() - before > 1
    assert module.runtime.turkey_heap_objects() == 0


def test_pointer_arrays_are_traced_under_gc_stress(monkeypatch, capfd):
    monkeypatch.setenv("TURKEY_GC_STRESS", "1")
    native("""
fun main() {
    let values = [] : Array (Option Int)
    Array.push(values, Some(1))
    Array.push(values, None)
    print(values)
}
""")
    assert capfd.readouterr().out == "[Some(1), None]\n"


def test_generic_filled_arrays_box_initial_scalars_under_gc_stress(
        monkeypatch, capfd):
    monkeypatch.setenv("TURKEY_GC_STRESS", "1")
    native("""
fun main() {
    let values = Array.filled(3, 4)
    values[1] = 9
    print(values)
}
""")
    assert capfd.readouterr().out == "[4, 9, 4]\n"


def test_array_byte_uses_one_byte_elements(capfd):
    checked = check("""
fun main() {
    let values = [Byte.maxValue(), Byte.truncate(300)]
    print(values)
}
""")
    text = generate(checked.opt, checked.decls, checked.main)
    # The element width and layout code are the point; the return type's
    # spelling is not, and differs between a typed- and opaque-pointer LLVM.
    calls = [line for line in text.splitlines()
             if "@turkey_array_new(" in line and " call " in f" {line} "]
    assert any("i32 1, i32 2" in line for line in calls)
    execute(checked.opt, checked.decls, checked.main)
    assert capfd.readouterr().out == "[255, 44]\n"


def test_llvm_command_prints_verified_ir(tmp_path, capsys):
    program = tmp_path / "program.tl"
    program.write_text("fun main() { print(3) }", encoding="utf-8")
    assert cli_main(["llvm", str(program)]) == 0
    assert "define i8 @turkeyfn_Main_23_main" in capsys.readouterr().out


def test_run_accepts_opt_in_llvm_backend(tmp_path, capfd):
    program = tmp_path / "program.tl"
    program.write_text("fun main() { print(7) }", encoding="utf-8")
    assert cli_main(["run", "--backend", "llvm", str(program)]) == 0
    assert capfd.readouterr().out == "7\n"


@pytest.mark.parametrize("name", NATIVE_PROGRAMS)
def test_native_programs_match_conformance_output(name, monkeypatch, capfd):
    program = PROGRAMS_DIR / f"{name}.tl"
    monkeypatch.chdir(PROGRAMS_DIR)
    assert cli_main(["run", "--backend", "llvm", program.name]) == 0
    expected = (program.with_suffix(".expected")
                .read_text(encoding="utf-8"))
    captured = capfd.readouterr()
    assert captured.out + captured.err == expected


@pytest.mark.parametrize("name", ERROR_PROGRAMS)
def test_native_error_programs_match_conformance_output(name, monkeypatch, capfd):
    program = PROGRAMS_DIR / f"{name}.tl"
    monkeypatch.chdir(PROGRAMS_DIR)
    assert cli_main(["run", "--backend", "llvm", program.name]) != 0
    expected = program.with_suffix(".expected").read_text(encoding="utf-8")
    captured = capfd.readouterr()
    assert captured.out + captured.err == expected


@pytest.mark.parametrize("name", NATIVE_PROGRAMS)
def test_field_and_element_access_is_emitted_inline(name, monkeypatch):
    """No conformance program reaches a field or element through a call.

    A field's offset and a field's scalar layout are both compile-time
    constants in a monomorphized program, so reading one is a
    `getelementptr` and a `load`. The runtime accessors these replace did the
    work at run time instead: `turkey_object_get_as` re-derived the stored
    layout from a 3-bit code in the header, compared it against the requested
    one, and silently boxed or unboxed on a mismatch.

    That mismatch is what made the call load-bearing rather than merely slow,
    and it is why the cutover was gated on measurement rather than argument:
    the stored layout is chosen by the *construction site* from its operand
    layouts (`_layout_metadata`) and the requested one is computed
    independently by the consumer (`layout_of`, or `_pattern_layout` at a
    pattern), and nothing makes the two agree by construction. Before the
    cutover a counter in the runtime recorded every bridge, and it was zero
    across all of these programs; that measurement is the licence, and it is
    recorded in the commit that took it.

    What is checkable *now* is the property that replaced it, so this asserts
    that rather than re-asserting a counter nothing can increment any more.

    The accessors this used to name are gone from the runtime, so naming them
    would assert nothing. What replaces the list is its complement: the
    runtime entry points a program may call at all. A reader of a field or an
    element is not on it, so a lowering that reached for one -- or that
    reintroduced an accessor to reach for -- fails here rather than passing a
    check that has quietly stopped being able to fail.
    """
    program = PROGRAMS_DIR / f"{name}.tl"
    monkeypatch.chdir(PROGRAMS_DIR)
    checked = check(program.read_text(encoding="utf-8"), str(program),
                    [program.parent.resolve()])
    text = generate(checked.opt, checked.decls, checked.main)
    called = {match.group(1) for line in text.splitlines()
              if " call " in f" {line} "
              for match in [re.search(r"@(turkey_\w+)\(", line)] if match}
    # A compiled Turkey function is mangled with the same prefix, so the
    # runtime's own entry points are the ones the header declares.
    unexpected = (called & _runtime_entry_points()) - ALLOWED_RUNTIME_CALLS
    assert not unexpected, (
        f"{name} calls {sorted(unexpected)}; if that is deliberate, add it to "
        f"ALLOWED_RUNTIME_CALLS, and if it is a field or element read it "
        f"should be a getelementptr and a load instead")


@pytest.mark.skipif(
    "TURKEY_GC_STRESS" in os.environ,
    reason="half a million allocations, each collecting the whole live heap")
def test_a_deep_heap_chain_is_traced_without_the_c_stack(capfd):
    """Tracing is a worklist, so its depth is not the C stack's depth.

    It used to recurse, one frame per heap pointer followed, so the longest
    chain of pointers a program could build was bounded by the C stack: half
    a million links is an unremarkable thing to allocate and this program
    died with SIGSEGV inside the collector rather than answering.
    """
    native("""
type Chain = Link(Chain) | End

fun build(n : Int) -> Chain {
    var out = End
    for var i = 0; i < n; i = i + 1 { out = Link(out) }
    out
}

fun depth(c : Chain) -> Int {
    var n = 0
    var at = c
    loop {
        match at {
            End -> break
            Link(next) -> { n = n + 1; at = next }
        }
    }
    n
}

fun main() { print(show(depth(build(500000)))) }
""")
    assert capfd.readouterr().out == "500000\n"


def test_top_level_dictionaries_survive_gc_stress(monkeypatch, capfd):
    """A pointer-typed module global is a root in its own right.

    `dicts.tl` keeps seven of them, each an instance dictionary built once by
    the module initializer and read for the rest of the run. Nothing registered
    them: what kept them alive was that the root frame held every pointer in
    the function that built them and never cleared a slot, so they were rooted
    by accident. Rooting by liveness ends the accident, and under GC stress
    this program collected its own dictionaries and then read them.

    They now live in a module-level array that is registered once from the
    entry function, so the store that updates a global is the store that roots
    it.
    """
    monkeypatch.setenv("TURKEY_GC_STRESS", "1")
    program = PROGRAMS_DIR / "dicts.tl"
    checked = check(program.read_text(encoding="utf-8"), str(program),
                    [program.parent.resolve()])
    execute(checked.opt, checked.decls, checked.main, str(program))
    captured = capfd.readouterr()
    assert captured.out + captured.err == program.with_suffix(".expected").read_text()


def test_generic_layout_bridges_survive_gc_stress(monkeypatch, capfd):
    monkeypatch.setenv("TURKEY_GC_STRESS", "1")
    program = PROGRAMS_DIR / "question_control.tl"
    checked = check(program.read_text(encoding="utf-8"), str(program),
                    [program.parent.resolve()])
    execute(checked.opt, checked.decls, checked.main, str(program))
    captured = capfd.readouterr()
    assert captured.out + captured.err == program.with_suffix(".expected").read_text()


def test_no_runtime_symbol_can_be_named_by_a_turkey_program():
    """The two symbol namespaces cannot meet.

    `mangle` is the only way a Turkey name becomes a native symbol and it
    always prepends `COMPILED_PREFIX`, so the compiled program owns that
    prefix entirely; this asserts the runtime stays off it, which is the other
    half. Together they make a collision impossible rather than merely
    unobserved.

    It is worth having as a rule rather than as a habit. `mangle("panic")` is
    `turkey_panic` under the old prefix, and the only thing that stopped a
    Turkey function reaching it was that top-level binding names happen to
    carry a module qualifier -- except `%bound11757`, which does not, so even
    that was not true. The failure it prevents is not a compile error either:
    `turkey_collect` and `turkey_heap_objects` are called from Python by name
    through `get_function_address`, so a compiled function landing on one
    would be handed back to Python and called as the collector.
    """
    from turkey.backend_lower import COMPILED_PREFIX

    entry_points = _runtime_entry_points()
    assert entry_points, "the header should declare something"
    clashing = {name for name in entry_points
                if name.startswith(COMPILED_PREFIX)}
    assert not clashing, (
        f"{sorted(clashing)} could be named by a Turkey program; the runtime "
        f"must keep off {COMPILED_PREFIX!r}")


def test_every_compiled_symbol_carries_the_compiled_prefix():
    """The half of the guarantee that lives in the backend.

    Not only the mangled names: the two symbols the backend invents for
    itself, module setup and the entry thunk, are in the same namespace and
    were spelled `turkey_module_initialize` and `turkey_run` before this.
    """
    from turkey.backend_lower import COMPILED_PREFIX, lower

    checked = check('fun main() { print(len([1, 2])) }')
    source = lower(checked.opt, checked.decls, checked.main)
    stray = [function.name for function in source.functions
             if not function.name.startswith(COMPILED_PREFIX)]
    assert not stray, f"{stray} would share the runtime's namespace"


# -- values a statement throws away, and values a name stands for -------------
#
# Three shapes `boot` reached that no test program had. Each was well-typed,
# ran correctly under `pygen`, and could not be lowered to LLVM -- which is the
# signature of a fact the front end knows and the backend was never told.
# FINDINGS 48, 49 and 50.


def _core_children(node):
    """One level of a Core node, for the small structural checks below."""
    import dataclasses

    if not dataclasses.is_dataclass(node):
        return []
    out = []
    for f in dataclasses.fields(node):
        value = getattr(node, f.name)
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if dataclasses.is_dataclass(item):
                out.append(item)
    return out


# --- FINDINGS 54: four ways for a layout to be forgotten -------------------


def test_the_uniform_representation_is_only_offered_to_a_variable():
    """`held_at` answers `BOXED` for `a` and refuses `Index.Value (Array a)`.

    A body that knows nothing about a type still has to keep a value of it
    somewhere, and the uniform representation is that somewhere -- but the
    type that says a body can only *hold* a value is a bare variable. A stuck
    family application is not that: `Index.Value (Array Bool)` is the `Bool`
    the instance says it is, and its writer stores it as one. Answering
    `BOXED` there is one side guessing rather than a convention two sides
    keep, and the reader then unboxes a raw value (FINDINGS 54).
    """
    from turkey.backend_lower import held_at
    from turkey.types import STAR, TFam, TVar
    from turkey.errors import Unsupported

    variable = TVar(1)
    assert held_at(variable) is bir.Layout.BOXED
    with pytest.raises(Unsupported):
        held_at(TFam("Index.Value", variable, STAR))


def test_a_dictionarys_methods_count_as_its_abstractions_parameters():
    """An instance dictionary is a record of lambdas, not a lambda.

    `layout.transparent` asks what a binding's abstraction takes, and reading
    the lambda *spine* finds nothing in `instance Index (Array a)` -- while
    the `get` inside it does `Prim.arrayGet` on an `Array a`. The argument
    that a lambda deeper in the body is a closure the body makes for itself
    does not survive a body that hands the closure out, and a dictionary is
    nothing but that.
    """
    from turkey import layout
    from turkey.core import CBind, CLam, CParam, CRecord, CTyLam, CUnit, CVar
    from turkey.types import (STAR, TApp, TCon, TFun, TVar, UNIT,
                              kind_arrow)

    a = TVar(1)
    array = TApp(TCon("Data.Array#Array", kind_arrow(1)), a, STAR)
    dict_ty = TApp(TCon("%Dict.Std.Classes#Index", kind_arrow(1)),
                   array, STAR)
    get_ty = TFun([array], a)
    # The shape `lower` builds for an instance: the class's binder outside, a
    # record of methods inside, and the method that destructures an `Array a`
    # inside that.
    value = CTyLam(dict_ty, None, [a], CRecord(
        dict_ty, None, "%Dict.Std.Classes#Index",
        [("get", CLam(get_ty, None, [CParam("xs", array)],
                      CVar(a, None, "xs"), "#get"))]))
    bind = CBind("%inst.Std.Classes#Index.Data.Array#Array", dict_ty, [a],
                 value)
    assert [p.name for p in core.abstraction_parameters(value)] == ["xs"]
    assert layout.transparent(bind)


def test_a_specialized_method_states_the_forall_it_kept():
    """`mono` consumes the dictionary lambda; the `CTyLam` under it survives.

    A method quantifies over its own variables as well as its class's, and
    `lower.method_abstraction` states those beneath the dictionary lambda.
    Supplying the evidence leaves that abstraction outermost, and a copy that
    left it there had empty `binders` and a still-generic body -- which
    `mono.instantiate` cannot match type arguments against and
    `layout.share` cannot key a copy on.
    """
    from turkey.core import CTyLam
    source = (PROGRAMS_DIR / "dicts.tl").read_text(encoding="utf-8")
    checked = check(source, "dicts.tl", [PROGRAMS_DIR])
    for bind in checked.opt.dicts + checked.opt.binds:
        if isinstance(bind.value, CTyLam):
            assert bind.binders, bind.name


def test_the_backend_is_handed_no_stuck_type_family():
    """`Index.Value (Array Bool)` is the `Bool` the instance says it is.

    `layout_of` cannot answer a family application, so one reaching the
    backend is a value whose representation is decided by guesswork on one
    side and by the instance on the other. `_Rewriter.ty` reduces as it
    substitutes and is right that with no substitution there is nothing to
    do, which leaves every binding nothing specialized carrying the types the
    lowering wrote -- and a dictionary's methods are typed in the class's
    families there.
    """
    import dataclasses
    from turkey.core import CAlt, CExpr, CParam
    from turkey.types import TFam, prune

    source = (PROGRAMS_DIR / "dicts.tl").read_text(encoding="utf-8")
    checked = check(source, "dicts.tl", [PROGRAMS_DIR])
    found: list[str] = []

    def walk(node, owner: str) -> None:
        if isinstance(node, TFam):
            found.append(f"{owner}: {node!r}")
            return
        if isinstance(node, (CExpr, CAlt, CParam)):
            for f in dataclasses.fields(node):
                if f.name == "binders":
                    continue
                walk(getattr(node, f.name), owner)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, owner)

    for bind in checked.opt.dicts + checked.opt.binds:
        walk(prune(bind.ty), bind.name)
        walk(bind.value, bind.name)
    assert not found, found[:5]


# --- a newtype is its payload ---------------------------------------------


def test_a_single_field_wrapper_is_not_allocated():
    """`type Wrapped = W(Int)` has no run-time existence.

    One variant, one field, no way to tell the wrapper from what it wraps --
    and with no function identity and no reflection in the language, a value
    of it *is* its payload. So it is held at the payload's layout, which for
    an `Int` is `i64` and not a pointer to a heap object holding one.
    """
    checked = check("""
type Wrapped = W(Int)
fun main() { let w = W(7); match w { W(n) -> print(n) } }
""")
    assert "Main#Wrapped" in checked.decls.newtypes()
    text = generate(checked.opt, checked.decls, checked.main)
    assert "call ptr @turkey_object_new" not in text


def test_an_erased_wrapper_still_answers_its_payload(capfd):
    """Built, matched, projected and passed through a generic function."""
    native("""
type Wrapped = W(Int)
type Boxed a = Boxed(a)

fun unwrap(w : Wrapped) -> Int = match w { W(n) -> n }

fun main() {
    let wrapped = Array.map([1, 2, 3], W)
    print(Array.map(wrapped, unwrap))
    let b = Boxed("inside")
    print(b.0)
    print(W(4).0 + unwrap(W(5)))
}
""")
    assert capfd.readouterr().out == "[1, 2, 3]\ninside\n9\n"


def test_a_wrapper_over_a_type_variable_is_erased_too():
    """`Boxed a` adds no edge to the recursion graph, so nothing stops it.

    A bare variable payload is not another candidate -- it is whatever the
    caller's type argument is, and that has a layout of its own or is held
    uniformly like any other `a`.
    """
    checked = check("type Boxed a = Boxed(a)\nfun main() { print(Boxed(1).0) }")
    assert "Main#Boxed" in checked.decls.newtypes()


def test_a_cycle_of_wrappers_keeps_one_box():
    """Erasure has to terminate, and a cycle is where it would not.

    `A(B)`, `B(C)`, `C(A)` is one wrapper spread over three declarations and
    has no representation if all three are erased. One member of the cycle
    keeps its box -- the first by name -- which is enough for the rest.
    """
    checked = check("""
type A = A(B)
type B = B(C)
type C = C(A)
fun main() { print("ok") }
""")
    erased = {n for n in checked.decls.newtypes() if n.startswith("Main#")}
    assert erased == {"Main#B", "Main#C"}, erased


def test_an_array_element_is_one_indirection_closer():
    """`Array a = Array(ArrayStorage a)` is the case that motivated this.

    The comment in `lower_pattern` says the wrapper's tag test is paid on
    every element access; erasing the wrapper removes the load beneath it too.
    """
    checked = check("fun main() { let xs = [1, 2, 3]; print(xs[1]) }")
    assert "Data.Array#Array" in checked.decls.newtypes()


def test_a_one_armed_if_discards_a_branch_that_answers_something(capfd):
    """`if c { e }` is `Unit` whatever `e` is (section 6.7, `infer._gen_EIf`).

    The branch here answers an `Option`, held at `PTR`, where the `if` answers
    `Unit`. Lowering the branch straight into the `if`'s destination made that
    a `ptr` arriving where a `unit` was expected; the value is simply not
    wanted, and nothing had said so.
    """
    native("""
fun main() {
    let xs = [1, 2, 3]
    if len(xs) > 0 { Array.pop(xs) }
    print(len(xs))
}
""")
    assert capfd.readouterr().out == "2\n"


def test_a_one_armed_if_whose_branch_diverges_is_still_lowered(capfd):
    """The other half: the discard must not make an unreachable block real.

    `return` leaves the branch, so the block that would take its value is
    never jumped to, and `finish` drops it for being unreachable.
    """
    native("""
fun first(xs : Array Int) -> Int {
    if len(xs) == 0 { return -1 }
    xs[0]
}
fun main() { print(first([])); print(first([7])) }
""")
    assert capfd.readouterr().out == "-1\n7\n"


def test_a_constructor_can_be_passed_as_a_function(capfd):
    """`Array.map(xs, Some)` names a constructor without applying it.

    `CCon` is the allocation itself, so a bare one is not a value any backend
    can pass. It is eta-expanded into the function its type already says it is.
    """
    native("""
type Wrapped = W(Int)
fun unwrap(w : Wrapped) -> Int = match w { W(n) -> n }
fun main() {
    let wrapped = Array.map([1, 2, 3], W)
    print(Array.map(wrapped, unwrap))
}
""")
    assert capfd.readouterr().out == "[1, 2, 3]\n"


def test_a_saturated_constructor_call_builds_no_wrapper():
    """Named *and* applied stays `CApp(CCon, args)`, the shape it always was.

    Asserted on the Core rather than on the output, because the output would
    be right either way: a wrapper the optimizer inlines is invisible from
    outside and is exactly what this must not start emitting.
    """
    from turkey.core import CApp, CCon, CLam

    checked = check("""
type Pair = P(Int, Int)
fun left(p : Pair) -> Int = match p { P(a, _) -> a }
fun main() { print(left(P(4, 5))) }
""")
    seen = []

    def walk(node):
        if isinstance(node, CApp) and isinstance(node.fn, CCon):
            seen.append(node.fn.name)
        for child in _core_children(node):
            walk(child)

    for bind in checked.core.binds:
        walk(bind)
    assert any(name.endswith("#P") or name == "P" for name in seen), seen


def test_two_records_sharing_a_name_keep_their_own_scattered_slots(capfd):
    """A flattened record's slots are found through `env`, which has scope.

    `b` in `main` is a record every mention of which is a field of it, so it
    is scattered into slots and never allocated. Inlining `tally` then brings
    a second `b` into the same body -- bound by a `match`, over a different
    record, with a different field. Keyed by the bare name, as the slots once
    were, the inner `b.value` looked into the outer `b`'s slots and found
    `parts`; here that raises, and where the field names happen to agree it
    would instead read the wrong field at the wrong layout.

    The shape is `boot`'s: `Turkey.Classes#showClasses` builds a string in a
    `b` that inlining turns into slots, and `Map.entries` brought its own `b`.
    Reproducing it needs the inner binding *not* to be a record literal, or
    `_flat_records` refuses the name as repeated and the collision cannot
    arise -- hence the `Array.at`.
    """
    native("""
type Acc = Acc { parts : Array Int }
type Item = Item { value : Int }

fun tally(xs : Array Item) -> Int = match Array.at(xs, 0) {
    None -> 0
    Some(b) -> b.value
}

fun main() {
    let items = [Item { value = 42 }]
    let b = Acc { parts = Array.new(4) }
    Array.push(b.parts, tally(items))
    print(b.parts[0])
}
""")
    assert capfd.readouterr().out == "42\n"


# -- a standalone executable -------------------------------------------------


def test_build_produces_an_executable_that_runs_on_its_own(tmp_path):
    """`turkey build`, end to end: no Python at run time.

    The JIT reaches the entry through `ctypes` and reads the panic and exit
    flags back in Python. A compiled program has neither, so this checks the
    three things the C `main` took over: the arguments arrive, what the program
    prints is what it printed under the JIT, and the status it chose is the
    process's.
    """
    source = tmp_path / "prog.tl"
    source.write_text("""
import System.Env as Env

fun main() {
    let given = Env.args()
    print(Int.toString(len(given)))
    for a in given { print(a) }
    Env.exit(3)
}
""", encoding="utf-8")
    output = tmp_path / "prog"
    build = subprocess.run(
        [sys.executable, "-m", "turkey", "build", str(source), "-o", str(output)],
        cwd=REPO_ROOT, env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    assert output.is_file()

    ran = subprocess.run([str(output), "one", "two"], capture_output=True,
                         text=True)
    assert ran.stdout == "2\none\ntwo\n"
    assert ran.returncode == 3


def test_a_built_program_reports_a_panic_and_fails(tmp_path):
    """The other half of what `turkey_main` took over from the JIT boundary."""
    source = tmp_path / "boom.tl"
    source.write_text(
        "fun main() { let xs = [1]\n print(xs[4]) }\n", encoding="utf-8")
    output = tmp_path / "boom"
    build = subprocess.run(
        [sys.executable, "-m", "turkey", "build", str(source), "-o", str(output)],
        cwd=REPO_ROOT, env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    ran = subprocess.run([str(output)], capture_output=True, text=True)
    assert ran.returncode == 1
    assert "panic:" in ran.stderr
    # The frames come from the same shadow stack the JIT boundary reads.
    assert "boom.tl" in ran.stderr
