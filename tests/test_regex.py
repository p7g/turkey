"""Type boundaries of the eager Regex builder; runtime cases are in the corpus."""

import pytest

from tests.lang import check, run, types
from tests.lang import CompileError


def test_regex_result_types_are_inferred_through_associated_families():
    signatures = types('''
import Regex as R
fun either(text) = R.fullMatch(R.choice(R.literal("a"), R.satisfy(fun(c) = True)), text)
fun repeated(text) = R.fullMatch(R.some(R.literal("a")), text)
fun optional(text) = R.fullMatch(R.optional(R.literal("a")), text)
fun pairs(text) = R.fullMatch(R.many(do {
    let a = R.literal("a")?
    let b = R.optional(R.literal("b"))?
    pure((a, b))
}), text)
''')
    assert signatures['either'] == 'fun(String) -> Option (Either String Char)'
    assert signatures['repeated'] == 'fun(String) -> Option (Vec String)'
    assert signatures['optional'] == 'fun(String) -> Option (Option String)'
    assert signatures['pairs'] == 'fun(String) -> Option (Vec (String, Option String))'


@pytest.mark.parametrize('source', [
    # Construction never gives the continuation the matched String.
    '''fun bad() = do {
        let handle = R.literal("a")?
        let text : String = handle
        pure(text)
    }''',
    # A selected branch preserves its result type.
    '''fun bad() -> Option (Either String String) =
        R.fullMatch(R.choice(R.literal("a"), R.satisfy(fun(c) = True)), "a")''',
    # Repetition is an array, not a last-capture String.
    '''fun bad() -> Option String = R.fullMatch(R.many(R.literal("a")), "aaa")''',
    # Constructors and VM internals are not part of the exported API.
    '''fun bad() = R.Capture(fun(captures) = "forged")''',
    '''fun bad() = R.Consume(fun(c) = True)''',
])
def test_regex_rejects_incorrect_capture_and_result_types(source):
    with pytest.raises(CompileError):
        check('import Regex as R\n' + source)


# Every task builds its own pattern while the others build theirs, so that
# capture tags are drawn from the supply at the same time; a tag handed out
# twice would make one pattern's capture resolve to another fragment's text.
PARALLEL_CONSTRUCTION = '''
import Regex as R
import System.Parallel as Parallel

fun digit(c : Char) -> Bool = c >= '0' && c <= '9'

fun pattern() = do {
    let left = R.capture(R.some(R.satisfy(digit)))?
    R.literal("-")?
    let middle = R.optional(R.capture(R.some(R.satisfy(Char.isAsciiAlpha))))?
    R.literal("-")?
    let right = R.capture(R.some(R.satisfy(digit)))?
    pure((left, middle, right))
}

fun main() {
    let xs = []
    for var i = 0; i < 400; i = i + 1 { Vec.push(xs, i) }
    let results = Parallel.map(xs, fun(i) {
        var ok = True
        for var j = 0; j < 20; j = j + 1 {
            let expected = Int.toString(i) + "-" + Int.toString(j)
            match R.fullMatch(pattern(), Int.toString(i) + "-ab-" + Int.toString(j)) {
                Some((l, Some(m), r)) -> {
                    if l + "-" + r != expected || m != "ab" { ok = False }
                }
                _ -> { ok = False }
            }
            -- A digit in the middle group: no match.
            if let Some(_) = R.fullMatch(pattern(), Int.toString(i) + "-a1-" + Int.toString(j)) {
                ok = False
            }
        }
        ok
    })
    var failures = 0
    for ok in results { if !ok { failures = failures + 1 } }
    print(failures)
}
'''


@pytest.mark.parametrize('workers', ['1', '8'])
def test_regex_patterns_built_by_parallel_tasks_match_independently(workers):
    result = run(PARALLEL_CONSTRUCTION, env={'TURKEY_WORKERS': workers})
    assert result.code == 0, result.stderr
    assert result.stdout == '0\n'
