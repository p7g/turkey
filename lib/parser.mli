type parse_error = Lexing.position * string

val show_parse_error : parse_error -> string
val parse : string -> (Ast.program, parse_error) result
