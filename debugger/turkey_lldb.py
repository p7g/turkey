"""lldb formatters for programs `boot build -g` compiled.

Load with `command script import debugger/turkey_lldb.py`, or run
`scripts/turkey-lldb`, which does.

They read the types `Turkey.DebugTypes` writes, by their names, which name
the module that declares each type, and by their shapes:

* `Data.String.String`, `Data.Array.Array a`, `Prim.Array a` and
  `Data.Vec.Vec a`: a pointer to an array object, its length at 8 and its
  elements at 16, or to a vector's storage and its length.
* `Data.Map.BaseMap h k v`, which a `Map k v` is: a map's buckets, in the
  order they were added, from `head` along each bucket's `next`.
* A compact sum, such as `Data.Option.Option Data.String.String`: a union of
  the one word as the enumeration of the nullaries, `nullary`, and as the
  payload. A word below `immediates` is a nullary; any other is the
  payload, or a wrap of it, an object tagged `wrapTag` whose one field is
  the payload. These constants, and where an object's tag and fields are,
  are read from the `Turkey.Runtime` enumeration the program carries.
* A sum held in slots or as an object: a structure with a `tag` member, an
  enumeration of the constructors, and a union of one structure per
  constructor; the constructor the tag names is the value.
* Any other object: a structure whose first member is the `header`, which
  is shown as its fields alone.

Each formatter reads only memory the program wrote; one whose value is out
of the range a debugger can read shows the error rather than failing.
"""

from __future__ import annotations

import lldb

# The most elements or entries a summary lists, and children a collection
# shows.
SHOWN = 100
CATEGORY = "turkey"


def _guarded(summary):
    """`summary`, showing what went wrong in place of the value rather than
    a traceback: a variable a debugger shows may hold anything before the
    program writes it."""
    def run(value: lldb.SBValue, internal: dict) -> str:
        try:
            return summary(value, internal)
        except Exception as e:  # noqa: BLE001 -- any failure is shown, not raised
            return f"<unreadable: {e}>"
    run.__name__ = summary.__name__
    return run


def _pointer(value: lldb.SBValue) -> int:
    return value.GetValueAsUnsigned(0)


def _object(value: lldb.SBValue) -> lldb.SBValue:
    """The structure a pointer points at, or the value itself, as the
    program holds it rather than as a formatter shows it."""
    value = value.GetNonSyntheticValue()
    if value.GetType().GetCanonicalType().IsPointerType():
        return value.Dereference().GetNonSyntheticValue()
    return value


def _read(value: lldb.SBValue, address: int, size: int) -> bytes:
    error = lldb.SBError()
    data = value.GetProcess().ReadMemory(address, size, error)
    if not error.Success():
        raise ValueError(error.GetCString())
    return data


def _members(t: lldb.SBType) -> list[lldb.SBTypeMember]:
    t = t.GetCanonicalType()
    return [t.GetFieldAtIndex(k) for k in range(t.GetNumberOfFields())]


def _named(value: lldb.SBValue, name: str) -> lldb.SBValue | None:
    found = value.GetChildMemberWithName(name)
    return found if found.IsValid() else None


# -- arrays, strings and vectors -----------------------------------------------

def _elements(array: lldb.SBValue) -> tuple[int, int, lldb.SBType]:
    """An array object's length, the address of its first element, and the
    element type: a structure wrapping one whose `value` is the element."""
    obj = _object(array)
    length = obj.GetChildMemberWithName("length").GetValueAsSigned(0)
    elements = obj.GetChildMemberWithName("elements")
    return length, elements.GetLoadAddress(), elements.GetType().GetArrayElementType()


def _element(array: lldb.SBValue, address: int, element: lldb.SBType,
             name: str) -> lldb.SBValue:
    made = array.CreateValueFromAddress(name, address, element)
    inner = _named(made, "value")
    if inner is not None and len(_members(element)) == 1:
        return array.CreateValueFromAddress(name, address, inner.GetType())
    return made


@_guarded
def string_summary(value: lldb.SBValue, internal: dict) -> str:
    if _pointer(value) == 0:
        return "<null>"
    obj = _object(value)
    length = obj.GetChildMemberWithName("length").GetValueAsSigned(0)
    if length < 0:
        return "<invalid>"
    start = obj.GetChildMemberWithName("elements").GetLoadAddress()
    text = _read(value, start, min(length, 4096)).decode("utf-8", "replace")
    more = "..." if length > 4096 else ""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + more + '"'


class ArrayChildren:
    """An array's elements, as children `[0]`, `[1]`, ..."""

    def __init__(self, value: lldb.SBValue, internal: dict):
        self.value = value
        self.update()

    def update(self) -> bool:
        self.length, self.start, self.element = 0, 0, None
        if _pointer(self.value) != 0:
            self.length, self.start, self.element = _elements(self.storage())
        return False

    def storage(self) -> lldb.SBValue:
        return self.value

    def count(self) -> int:
        return max(0, self.length)

    def num_children(self) -> int:
        return min(self.count(), SHOWN)

    def get_child_index(self, name: str) -> int:
        try:
            return int(name.lstrip("[").rstrip("]"))
        except ValueError:
            return -1

    def get_child_at_index(self, k: int) -> lldb.SBValue | None:
        if self.element is None or k >= self.num_children():
            return None
        stride = self.element.GetByteSize()
        return _element(self.value, self.start + k * stride, self.element, f"[{k}]")

    def has_children(self) -> bool:
        return True


class VecChildren(ArrayChildren):
    """A vector's first `length` elements of its storage."""

    def update(self) -> bool:
        self.length, self.start, self.element = 0, 0, None
        if _pointer(self.value) != 0:
            fields = _record(_object(self.value))
            _, self.start, self.element = _elements(fields.GetChildMemberWithName("storage"))
            self.length = fields.GetChildMemberWithName("length").GetValueAsSigned(0)
        return False


@_guarded
def collection_summary(value: lldb.SBValue, internal: dict) -> str:
    shown = value.GetNumChildren()
    parts = [_shown(value.GetChildAtIndex(k)) for k in range(min(shown, 8))]
    if shown > 8:
        parts.append("...")
    return "[" + ", ".join(parts) + "]"


@_guarded
def map_summary(value: lldb.SBValue, internal: dict) -> str:
    shown = value.GetNumChildren()
    parts = []
    for k in range(min(shown, 8)):
        child = value.GetChildAtIndex(k)
        parts.append(f"{child.GetName()} = {_shown(child)}")
    if shown > 8:
        parts.append("...")
    return "{" + ", ".join(parts) + "}"


def _shown(value: lldb.SBValue) -> str:
    summary = value.GetSummary()
    if summary:
        return summary
    shown = value.GetValue()
    return shown if shown is not None else "{...}"


# -- maps ----------------------------------------------------------------------

class MapChildren:
    """A map's buckets, `key -> value`, in the order they were added."""

    def __init__(self, value: lldb.SBValue, internal: dict):
        self.value = value
        self.update()

    def update(self) -> bool:
        self.buckets = []
        if _pointer(self.value) == 0:
            return False
        bucket = _payload(_record(_object(self.value)).GetChildMemberWithName("head"))
        while bucket is not None and len(self.buckets) < SHOWN:
            fields = _record(_object(bucket))
            self.buckets.append(fields)
            bucket = _payload(fields.GetChildMemberWithName("next"))
        return False

    def num_children(self) -> int:
        return len(self.buckets)

    def get_child_index(self, name: str) -> int:
        return -1

    def get_child_at_index(self, k: int) -> lldb.SBValue | None:
        if k >= len(self.buckets):
            return None
        fields = self.buckets[k]
        key = _shown(fields.GetChildMemberWithName("key"))
        value = fields.GetChildMemberWithName("value")
        return value.CreateValueFromData(f"[{key}]", value.GetData(), value.GetType())

    def has_children(self) -> bool:
        return True


# -- sums and objects ----------------------------------------------------------

def _record(obj: lldb.SBValue) -> lldb.SBValue:
    """An object's fields: its one constructor's structure, after the
    header."""
    for member in _members(obj.GetType()):
        if member.GetName() not in ("header", "tag"):
            return obj.GetChildMemberWithName(member.GetName())
    return obj


# Each program's runtime constants, by its module's UUID.
_constants: dict[str, dict[str, int]] = {}


def _runtime(value: lldb.SBValue) -> dict[str, int]:
    """The runtime constants of the program `value` is in, from the
    `Turkey.Runtime` enumeration `boot build -g` writes."""
    target = value.GetTarget()
    key = target.GetModuleAtIndex(0).GetUUIDString() or str(target.GetExecutable())
    if key not in _constants:
        t = target.FindFirstType("Turkey.Runtime")
        if not t.IsValid():
            raise ValueError("the program has no Turkey.Runtime type")
        members = t.GetEnumMembers()
        _constants[key] = {members.GetTypeEnumMemberAtIndex(k).GetName():
                           members.GetTypeEnumMemberAtIndex(k).GetValueAsSigned()
                           for k in range(members.GetSize())}
    return _constants[key]


def _is_compact(t: lldb.SBType) -> bool:
    t = t.GetCanonicalType()
    members = _members(t)
    return (t.GetTypeClass() == lldb.eTypeClassUnion and len(members) == 2
            and members[0].GetName() == "nullary")


def _payload(compact: lldb.SBValue) -> lldb.SBValue | None:
    """A compact sum's payload, or None for a nullary."""
    runtime = _runtime(compact)
    word = compact.GetChildMemberWithName("nullary").GetValueAsUnsigned(0)
    if word < runtime["immediates"]:
        return None
    payload = compact.GetChildAtIndex(1)
    tag = int.from_bytes(_read(compact, word + runtime["tagOffset"], 4), "little", signed=True)
    if tag == runtime["wrapTag"]:
        return compact.CreateValueFromAddress(payload.GetName(), word + runtime["fieldsOffset"],
                                              payload.GetType())
    return payload


def _sum(value: lldb.SBValue) -> lldb.SBValue | None:
    """The structure of a sum value's constructor, which its `tag` names."""
    obj = _object(value)
    tag = _named(obj, "tag")
    if tag is None:
        return None
    name = tag.GetValue()
    for member in _members(obj.GetType()):
        if member.GetName() is None or member.GetName() == "":
            union = obj.GetChildMemberWithName(member.GetName() or "")
            if not union.IsValid():
                union = obj.GetChildAtIndex(len(_members(obj.GetType())) - 1)
            found = union.GetChildMemberWithName(name)
            if found.IsValid():
                return found
    return None


def _is_object(t: lldb.SBType) -> bool:
    """Whether `t` is a sum, in its slots or as an object, or a pointer to an
    object of a record's fields: a structure starting with its `tag`, or with
    the header and then the tag or the one constructor's structure. An array
    or a closure has other members after its header."""
    t = t.GetCanonicalType()
    if t.IsPointerType():
        t = t.GetPointeeType().GetCanonicalType()
    if t.GetTypeClass() != lldb.eTypeClassStruct:
        return False
    members = _members(t)
    if not members:
        return False
    if members[0].GetName() == "tag":
        return True
    if members[0].GetName() != "header" or len(members) < 2:
        return False
    second = members[1]
    return (second.GetName() == "tag"
            or second.GetType().GetCanonicalType().GetTypeClass() == lldb.eTypeClassStruct)


@_guarded
def compact_summary(value: lldb.SBValue, internal: dict) -> str:
    raw = value.GetNonSyntheticValue()
    word = raw.GetChildMemberWithName("nullary")
    if word.GetValueAsUnsigned(0) < _runtime(raw)["immediates"]:
        return word.GetValue() or str(word.GetValueAsUnsigned(0))
    payload = _payload(raw)
    return f"{raw.GetChildAtIndex(1).GetName()}({_shown(payload)})"


class CompactChildren:
    """A compact sum's payload as its one child, or none for a nullary."""

    def __init__(self, value: lldb.SBValue, internal: dict):
        self.value = value
        self.update()

    def update(self) -> bool:
        self.payload = _payload(self.value)
        return False

    def num_children(self) -> int:
        return 0 if self.payload is None else 1

    def get_child_index(self, name: str) -> int:
        return 0

    def get_child_at_index(self, k: int) -> lldb.SBValue | None:
        return self.payload if k == 0 else None

    def has_children(self) -> bool:
        return self.payload is not None


@_guarded
def object_summary(value: lldb.SBValue, internal: dict) -> str:
    raw = value.GetNonSyntheticValue()
    if raw.GetType().GetCanonicalType().IsPointerType() and _pointer(raw) == 0:
        return "<null>"
    obj = _object(raw)
    fields = _sum(raw)
    name = _named(obj, "tag").GetValue() if fields is not None else ""
    if fields is None:
        fields = _record(obj)
    return _constructed(name, fields)


def _constructed(name: str, fields: lldb.SBValue) -> str:
    """A constructor applied, as the source writes it: `Name(a, b)` for
    positional fields, `Name { x = a }` for named ones, `Name` for none."""
    children = [fields.GetChildAtIndex(k) for k in range(min(fields.GetNumChildren(), 8))]
    if not children:
        return name or "{}"
    more = ", ..." if fields.GetNumChildren() > 8 else ""
    if all((c.GetName() or "").isdigit() for c in children):
        return f"{name}({', '.join(_shown(c) for c in children)}{more})"
    inner = ", ".join(f"{c.GetName()} = {_shown(c)}" for c in children)
    return (f"{name} {{ {inner}{more} }}" if name else f"{{ {inner}{more} }}")


class ObjectChildren:
    """An object's or a sum's fields, without its header and tag."""

    def __init__(self, value: lldb.SBValue, internal: dict):
        self.value = value
        self.update()

    def update(self) -> bool:
        self.fields = None
        if self.value.GetType().GetCanonicalType().IsPointerType() and _pointer(self.value) == 0:
            return False
        self.fields = _sum(self.value)
        if self.fields is None:
            self.fields = _record(_object(self.value))
        return False

    def num_children(self) -> int:
        return 0 if self.fields is None else self.fields.GetNumChildren()

    def get_child_index(self, name: str) -> int:
        return -1 if self.fields is None else self.fields.GetIndexOfChildWithName(name)

    def get_child_at_index(self, k: int) -> lldb.SBValue | None:
        return None if self.fields is None else self.fields.GetChildAtIndex(k)

    def has_children(self) -> bool:
        return self.num_children() > 0


def is_compact(t: lldb.SBType, internal: dict) -> bool:
    return _is_compact(t)


def is_object(t: lldb.SBType, internal: dict) -> bool:
    return _is_object(t) and not _is_compact(t)


def __lldb_init_module(debugger: lldb.SBDebugger, internal: dict) -> None:
    m = __name__
    add = lambda command: debugger.HandleCommand(f"{command} -w {CATEGORY}")
    add(f'type summary add -F {m}.string_summary "Data.String.String"')
    for pattern in (r"^Data\.Array\.Array ", r"^Prim\.Array "):
        add(f'type synthetic add -l {m}.ArrayChildren -x "{pattern}"')
        add(f'type summary add -e -F {m}.collection_summary -x "{pattern}"')
    add(f'type synthetic add -l {m}.VecChildren -x "^Data\\.Vec\\.Vec "')
    add(f'type summary add -e -F {m}.collection_summary -x "^Data\\.Vec\\.Vec "')
    add(f'type synthetic add -l {m}.MapChildren -x "^Data\\.Map\\.BaseMap "')
    add(f'type summary add -e -F {m}.map_summary -x "^Data\\.Map\\.BaseMap "')
    add(f"type synthetic add -l {m}.CompactChildren --recognizer-function {m}.is_compact")
    add(f"type summary add -e -F {m}.compact_summary --recognizer-function {m}.is_compact")
    add(f"type synthetic add -l {m}.ObjectChildren --recognizer-function {m}.is_object")
    add(f"type summary add -e -F {m}.object_summary --recognizer-function {m}.is_object")
    debugger.HandleCommand(f"type category enable {CATEGORY}")
