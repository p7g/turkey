{
type tok =
  | NEWLINE
  | EOF

  (* operators *)
  | PIPEPIPE | AMPAMP | EQEQ | BANGEQ | LE | GE | LT | GT
  | PLUS | MINUS | STAR | SLASH | BANG | EQ | COLON | PIPE

  (* delimitors *)
  | LPAREN | RPAREN | LBRACE | RBRACE | LBRACK | RBRACK

  (* separators *)
  | COMMA | SEMICOLON | UNDERSCORE | ARROW | FATARROW | DOT

  (* keywords *)
  | AND | AS | BREAK | CONTINUE | ELSE | FALSE
  | FUN | IF | IMPORT | LET | LOOP | MATCH | OBJECT
  | RETURN | TRUE | TYPE | VAR | WHILE

  (* with values *)
  | LOWER_ID of string
  | UPPER_ID of string
  | CHAR_LIT of char
  | STRING_LIT of string
  | INT_LIT of int
  | FLOAT_LIT of float

let tok_to_string = function
  | NEWLINE -> "newline" | EOF -> "EOF"
  | PIPEPIPE -> "||" | AMPAMP -> "&&"
  | EQEQ -> "==" | BANGEQ -> "!=" | LE -> "<=" | GE -> ">=" | LT -> "<" | GT -> ">"
  | PLUS -> "+" | MINUS -> "-" | STAR -> "*" | SLASH -> "/"
  | BANG -> "!"
  | EQ -> "="
  | COLON -> ":"
  | PIPE -> "|"
  | LPAREN -> "(" | RPAREN -> ")"
  | LBRACE -> "{" | RBRACE -> "}"
  | LBRACK -> "[" | RBRACK -> "]"
  | COMMA -> ","
  | SEMICOLON -> ";"
  | UNDERSCORE -> "_"
  | ARROW -> "->" | FATARROW -> "=>"
  | DOT -> "."
  | AND -> "and" | AS -> "as" | BREAK -> "break" | CONTINUE -> "continue"
  | ELSE -> "else" | FALSE -> "false" | FUN -> "fun" | IF -> "if"
  | IMPORT -> "import" | LET -> "let" | LOOP -> "loop" | MATCH -> "match"
  | OBJECT -> "object" | RETURN -> "return" | TRUE -> "true" | TYPE -> "type"
  | VAR -> "var" | WHILE -> "while"
  | LOWER_ID id -> id | UPPER_ID id -> id
  | CHAR_LIT c -> Char.escaped c | STRING_LIT s -> String.escaped s
  | INT_LIT n -> Int.to_string n | FLOAT_LIT n -> Float.to_string n
}

let whitespace = [' ' '\t' '\r']
let digit = ['0'-'9']
let upper = ['A'-'Z']
let lower = ['a'-'z']
let ident_char = upper | lower | digit | '_'

rule token = parse
  | whitespace+ { token lexbuf }
  | '\n' { Lexing.new_line lexbuf; NEWLINE }
  | "//" [^ '\n']* { token lexbuf }
  | "||" { PIPEPIPE }
  | "&&" { AMPAMP }
  | "==" { EQEQ }
  | "!=" { BANGEQ }
  | "<=" { LE }
  | ">=" { GE }
  | "->" { ARROW }
  | "=>" { FATARROW }
  | '<' { LT }
  | '>' { GT }
  | '+' { PLUS }
  | '-' { MINUS }
  | '*' { STAR }
  | '/' { SLASH }
  | '!' { BANG }
  | '=' { EQ }
  | ':' { COLON }
  | '|' { PIPE }
  | '.' { DOT }
  | '(' { LPAREN }
  | ')' { RPAREN }
  | '{' { LBRACE }
  | '}' { RBRACE }
  | '[' { LBRACK }
  | ']' { RBRACK }
  | ',' { COMMA }
  | ';' { SEMICOLON }
  | '_' { UNDERSCORE }
  | '"' { read_string (Buffer.create 16) lexbuf }
  | '\'' { read_char lexbuf }
  | digit+ '.' digit+ as s { FLOAT_LIT (float_of_string s) }
  | digit+ as s { INT_LIT (int_of_string s) }
  | lower ident_char* as id {
      match id with
      | "and" -> AND
      | "as" -> AS
      | "break" -> BREAK
      | "continue" -> CONTINUE
      | "else" -> ELSE
      | "false" -> FALSE
      | "fun" -> FUN
      | "if" -> IF
      | "import" -> IMPORT
      | "let" -> LET
      | "loop" -> LOOP
      | "match" -> MATCH
      | "object" -> OBJECT
      | "return" -> RETURN
      | "true" -> TRUE
      | "type" -> TYPE
      | "var" -> VAR
      | "while" -> WHILE
      | _ -> LOWER_ID id
    }
  | upper ident_char* as id { UPPER_ID id }
  | eof { EOF }
  | _ as c { failwith (Printf.sprintf "unexpected character: '%c'" c) }

and read_string buf = parse
  | '"' { STRING_LIT (Buffer.contents buf) }
  | '\\' '"' { Buffer.add_char buf '"'; read_string buf lexbuf }
  | '\\' '\\' { Buffer.add_char buf '\\'; read_string buf lexbuf }
  | '\\' 'n' { Buffer.add_char buf '\n'; read_string buf lexbuf }
  | '\\' 't' { Buffer.add_char buf '\t'; read_string buf lexbuf }
  | '\\' 'r' { Buffer.add_char buf '\r'; read_string buf lexbuf }
  | [^ '"' '\\']+ {
      Buffer.add_string buf (Lexing.lexeme lexbuf);
      read_string buf lexbuf
    }
  | _ { failwith "unterminated string literal" }

and read_char = parse
  | [^ '\'' '\\'] '\'' { CHAR_LIT (Lexing.lexeme_char lexbuf 0) }
  | '\\' 'n' '\'' { CHAR_LIT '\n' }
  | '\\' 't' '\'' { CHAR_LIT '\t' }
  | '\\' 'r' '\'' { CHAR_LIT '\r' }
  | '\\' '\\' '\'' { CHAR_LIT '\\' }
  | '\\' '\'' '\'' { CHAR_LIT '\'' }
  | '\\' '"' '\'' { CHAR_LIT '"' }
  | _ { failwith "unterminated char literal" }
