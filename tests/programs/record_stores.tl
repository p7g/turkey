type Fields = Fields {
    number : Int, fraction : Float, byte : Byte, character : Char,
    flag : Bool, text : String, unit : Unit,
}
fun main() {
    let items = [Fields {
        number = -3, fraction = 1.25, byte = Byte.truncate(255),
        character = 'λ', flag = False, text = "kept", unit = (),
    }]
    for var i = 0; i < 3; i = i + 1 {
        let r = items[0]
        r.number = r.number - i
        r.fraction = r.fraction + 0.5
        r.byte = Byte.truncate(i + 128)
        r.character = '界'
        r.flag = !r.flag
        r.text = r.text + Int.toString(i)
        r.unit = ()
    }
    let r = items[0]
    print(r.number)
    print(r.fraction)
    print(Byte.toInt(r.byte))
    print(r.character)
    print(r.flag)
    print(r.text)
}
