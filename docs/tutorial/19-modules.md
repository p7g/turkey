# Modules

Each `.gob` file is a module, named by its path: `Geometry.gob` beside the
program is the module `Geometry`, and `Shapes/Circle.gob` would be
`Shapes.Circle`. A module's `export` list says which of its names others may
use. `Shape(..)` exports the type with its constructors, and `Shape` alone
would export the type and keep them hidden.

An import loads a module and names what it brings in. `import Geometry`
gives the qualified names `Geometry.area` and `Geometry.Shape`, `as` gives
the module a shorter name, and a list after it brings names in unqualified.

The browser compiles one file at a time, so this lesson's program can't run
on the page. It shows the output `turkey build Main.gob` gives.

<!-- module: Geometry.gob -->
```kotlin
export (Shape(..), area, unit)

type Shape = Square(Float) | Circle(Float)

let unit = Square(1.0)

fun area(shape) = match shape {
	Square(side)   -> side * side
	Circle(radius) -> pi * radius * radius
}

let pi = 3.14159
```

<!-- run -->
```kotlin
import Geometry as Geo
import Geometry (Shape(..))

fun main() {
	print(Geo.area(Geo.unit))
	print(Geo.area(Circle(2.0)))
	for shape in [Square(3.0), Circle(1.0)] {
		print(Geo.area(shape))
	}
}
```

```text
1.0
12.56636
9.0
3.14159
```

`pi` isn't exported, so it is the module's own. The Prelude is imported into
every module without asking, which is where `print`, `Option` and the
qualifiers `String`, `Vec` and `Map` come from.

## In the reference

* [Modules and files](../ref/modules.md#modules-and-files)
* [Export declarations](../ref/modules.md#export-declarations)
* [Imports](../ref/modules.md#imports)
* [Qualified names](../ref/modules.md#qualified-names)
* [The Prelude](../ref/modules.md#the-prelude)
