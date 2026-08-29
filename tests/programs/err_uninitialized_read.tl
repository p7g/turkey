fun main() {
    let a = Array.new(4)
    -- Raising the length exposes slots that were never written.
    a.length = 2
    print(Int.toString(a[1]))
}
