type R = R { x : Int }
type R2 = R2 { x : String }

fun get(r) = r.x

fun main() {
    let r = R { x = 123 }
    print(Int.toString(get(r)))
    let r2 = R2 { x = "hello!" }
    print(get(r2))
}
