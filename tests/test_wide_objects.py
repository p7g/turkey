"""Objects of many fields: a closure's environment of any width keeps every
capture through collections, including a capture written after the closure
is made.
"""

from tests import lang

STRESS = {"TURKEY_GC_STRESS": "7"}


def closure(count: int) -> str:
    """A recursive local function capturing `count` locals, every other a
    string, and itself after them, called from a loop that allocates enough to
    collect under stress."""
    lets = "\n".join(f"    let v{i} = Int.toString({i})" if i % 2 == 0 else f"    let v{i} = {i}"
                     for i in range(count))
    total = " + ".join(f"String.byteLength(v{i})" if i % 2 == 0 else f"v{i}"
                       for i in range(count))
    return f"""
fun main() {{
{lets}
    fun sum(k : Int) -> Int = if k == 0 {{ {total} }} else {{ sum(k - 1) + 1 }}
    let kept = []
    for var i = 0; i < 300; i = i + 1 {{ Vec.push(kept, Int.toString(sum(i % 3))) }}
    print(kept[299] + " " + v{count - 2})
}}
"""


def test_a_closure_of_seventy_captures_keeps_them_through_collections():
    src = closure(70)
    result = lang.run(src, env=STRESS, flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == "1292 68\n"

