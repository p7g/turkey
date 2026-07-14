type parse_error = Lexing.position * string

val parse : string -> (Ast.program, parse_error) result
