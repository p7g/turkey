"""gdb pretty-printers for programs `boot build -g` compiled.

A program built with `-g` for Linux names this file in its
`.debug_gdb_scripts` section. gdb looks for a file named there in the
current directory and then along the source path, so it loads this one once
the source path includes this directory and the safe path allows it:

    directory /path/to/turkey/debugger
    add-auto-load-safe-path /path/to/turkey/debugger

or by hand, with `source debugger/turkey_gdb.py`.

They read the types `Turkey.DebugTypes` writes, by their names and shapes,
as `turkey_lldb.py` does for lldb, and print `Data.String.String`,
`Data.Array.Array a`, `Prim.Array a`, `Data.Vec.Vec a`, `Data.Map.BaseMap h k
v`, compact sums such as `Data.Option.Option Data.String.String`, sums in
their slots and as objects, and records as objects.
"""

from __future__ import annotations

import gdb
import gdb.printing

# The most elements or entries a printer lists.
SHOWN = 100


def _fields(t: gdb.Type) -> list[gdb.Field]:
    """A structure's or a union's members, and nothing for any other type,
    whose `fields()` gdb either refuses or answers with something else."""
    t = t.strip_typedefs()
    if t.code not in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION):
        return []
    return list(t.fields())


def _target(value: gdb.Value) -> gdb.Value:
    """The object a pointer points at, or the value itself."""
    if value.type.strip_typedefs().code == gdb.TYPE_CODE_PTR:
        return value.dereference()
    return value


def _address(value: gdb.Value) -> int:
    return int(value.cast(gdb.lookup_type("long")))


def _elements(array: gdb.Value) -> tuple[int, int, gdb.Type]:
    """An array object's length, the address of its first element, and the
    element type."""
    obj = _target(array)
    elements = obj["elements"]
    return int(obj["length"]), int(elements.address), elements.type.strip_typedefs().target()


def _element(address: int, element: gdb.Type) -> gdb.Value:
    value = gdb.Value(address).cast(element.pointer()).dereference()
    fields = _fields(element)
    if len(fields) == 1 and fields[0].name == "value":
        return value["value"]
    return value


def _record(obj: gdb.Value) -> gdb.Value:
    """An object's fields: its one constructor's structure, after the
    header."""
    for field in _fields(obj.type):
        if field.name not in ("header", "tag"):
            return obj[field]
    return obj


def _runtime() -> dict[str, int]:
    """The program's runtime constants, from the `Turkey.Runtime`
    enumeration `boot build -g` writes."""
    return {field.name: int(field.enumval) for field in gdb.lookup_type("Turkey.Runtime").fields()}


def _payload(compact: gdb.Value) -> gdb.Value | None:
    """A compact sum's payload, or None for a nullary."""
    runtime = _runtime()
    word = int(compact["nullary"].cast(gdb.lookup_type("unsigned long")))
    if word < runtime["immediates"]:
        return None
    payload = compact[_fields(compact.type)[1]]
    tag = int(gdb.Value(word + runtime["tagOffset"]).cast(gdb.lookup_type("int").pointer())
              .dereference())
    if tag == runtime["wrapTag"]:
        return gdb.Value(word + runtime["fieldsOffset"]).cast(payload.type.pointer()).dereference()
    return payload


def _sum(value: gdb.Value) -> tuple[str, gdb.Value | None] | None:
    """The constructor a sum value's `tag` names, and its structure."""
    obj = _target(value)
    names = [f.name for f in _fields(obj.type)]
    if "tag" not in names:
        return None
    name = str(obj["tag"])
    for field in _fields(obj.type):
        if not field.name:
            union = obj[field]
            for variant in _fields(union.type):
                if variant.name == name:
                    return name, union[variant]
    return name, None


class StringPrinter:
    def __init__(self, value: gdb.Value):
        self.value = value

    def to_string(self) -> str:
        if _address(self.value) == 0:
            return "<null>"
        length, start, _ = _elements(self.value)
        if length < 0:
            return "<invalid>"
        data = gdb.selected_inferior().read_memory(start, min(length, 4096))
        return bytes(data).decode("utf-8", "replace")

    def display_hint(self) -> str:
        return "string"


class ArrayPrinter:
    def __init__(self, value: gdb.Value):
        self.value = value

    def storage(self) -> tuple[int, int, gdb.Type]:
        return _elements(self.value)

    def to_string(self) -> str:
        return "<null>" if _address(self.value) == 0 else f"length {self.storage()[0]}"

    def children(self):
        if _address(self.value) == 0:
            return
        length, start, element = self.storage()
        for k in range(min(max(length, 0), SHOWN)):
            yield f"[{k}]", _element(start + k * element.sizeof, element)

    def display_hint(self) -> str:
        return "array"


class VecPrinter(ArrayPrinter):
    def storage(self) -> tuple[int, int, gdb.Type]:
        fields = _record(_target(self.value))
        _, start, element = _elements(fields["storage"])
        return int(fields["length"]), start, element


class MapPrinter:
    def __init__(self, value: gdb.Value):
        self.value = value

    def to_string(self) -> str:
        return "<null>" if _address(self.value) == 0 else f"size {int(_record(_target(self.value))['size'])}"

    def children(self):
        if _address(self.value) == 0:
            return
        bucket = _payload(_record(_target(self.value))["head"])
        shown = 0
        while bucket is not None and shown < SHOWN:
            fields = _record(_target(bucket))
            yield f"[{shown}].key", fields["key"]
            yield f"[{shown}].value", fields["value"]
            shown += 1
            bucket = _payload(fields["next"])

    def display_hint(self) -> str:
        return "map"


class CompactPrinter:
    def __init__(self, value: gdb.Value):
        self.value = value

    def to_string(self) -> str | None:
        payload = _payload(self.value)
        if payload is None:
            return str(self.value["nullary"])
        return _fields(self.value.type)[1].name or None

    def children(self):
        payload = _payload(self.value)
        if payload is not None:
            yield "0", payload


class ObjectPrinter:
    def __init__(self, value: gdb.Value):
        self.value = value

    def found(self) -> tuple[str, gdb.Value | None]:
        if self.value.type.strip_typedefs().code == gdb.TYPE_CODE_PTR and _address(self.value) == 0:
            return "<null>", None
        found = _sum(self.value)
        if found is not None:
            return found
        return "", _record(_target(self.value))

    def to_string(self) -> str | None:
        return self.found()[0] or None

    def children(self):
        _, fields = self.found()
        if fields is None:
            return
        for field in _fields(fields.type):
            yield field.name, fields[field]


def _is_compact(t: gdb.Type) -> bool:
    t = t.strip_typedefs()
    fields = _fields(t)
    return t.code == gdb.TYPE_CODE_UNION and len(fields) == 2 and fields[0].name == "nullary"


def _is_object(t: gdb.Type) -> bool:
    """Whether `t` is a sum, in its slots or as an object, or a pointer to an
    object of a record's fields, as `turkey_lldb.py` decides it."""
    t = t.strip_typedefs()
    if t.code == gdb.TYPE_CODE_PTR:
        t = t.target().strip_typedefs()
    if t.code != gdb.TYPE_CODE_STRUCT:
        return False
    fields = _fields(t)
    if not fields:
        return False
    if fields[0].name == "tag":
        return True
    if fields[0].name != "header" or len(fields) < 2:
        return False
    return (fields[1].name == "tag"
            or fields[1].type.strip_typedefs().code == gdb.TYPE_CODE_STRUCT)


def lookup(value: gdb.Value):
    name = value.type.name or ""
    if name == "Data.String.String":
        return StringPrinter(value)
    if name.startswith("Data.Vec.Vec "):
        return VecPrinter(value)
    if name.startswith(("Data.Array.Array ", "Prim.Array ")):
        return ArrayPrinter(value)
    if name.startswith("Data.Map.BaseMap "):
        return MapPrinter(value)
    if _is_compact(value.type):
        return CompactPrinter(value)
    if _is_object(value.type):
        return ObjectPrinter(value)
    return None


def register(objfile=None) -> None:
    gdb.printing.register_pretty_printer(objfile, _Collection(), replace=True)


class _Collection(gdb.printing.PrettyPrinter):
    def __init__(self):
        super().__init__("turkey")

    def __call__(self, value: gdb.Value):
        return lookup(value)


register(gdb.current_objfile())
