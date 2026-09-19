"""Type boundaries of the eager Regex builder; runtime cases are in the corpus."""

import pytest

from tests.lang import check, types
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
    assert signatures['repeated'] == 'fun(String) -> Option (Array String)'
    assert signatures['optional'] == 'fun(String) -> Option (Option String)'
    assert signatures['pairs'] == 'fun(String) -> Option (Array (String, Option String))'


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
