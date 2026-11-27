# Modules

Every `.gob` file is a module, and its path is its name. `Geometry.gob`, in
the same directory as the program, is the module `Geometry`.
`Shapes/Circle.gob` would be `Shapes.Circle`.

A module's declarations are private unless you export them. List the public
names in an `export` declaration. `Shape(..)` exports a type with its
constructors. `Shape` alone would export the type and keep its constructors
private.

To use a module, import it:

* `import Geometry` gives you its exports under qualified names:
  `Geometry.area`, `Geometry.Shape`.
* `import Geometry as Geo` gives the module an alias, so you write `Geo.area`.
* `import Geometry (Shape(..))` also imports the listed names unqualified.

The program has two files, and combines the last two forms.

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
import Geometry as Geo (Shape(..))

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

`pi` isn't exported, so only `Geometry` can use it.

Every module imports the Prelude automatically. The Prelude provides `print`
and `Option`, and the qualifiers `String`, `Vec` and `Map`.

**Try this:** in `Geometry.gob`, change `Shape(..)` to `Shape` in the export
list, and run the program. The compiler reports that `Geometry` doesn't export
the constructors that `main.gob` uses.

## In the reference

* [Modules and files](../ref/modules.md#modules-and-files)
* [Export declarations](../ref/modules.md#export-declarations)
* [Imports](../ref/modules.md#imports)
* [Qualified names](../ref/modules.md#qualified-names)
* [The Prelude](../ref/modules.md#the-prelude)
