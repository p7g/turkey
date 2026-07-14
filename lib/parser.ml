open Ast
open Lexer

let (let*) = Result.bind

type parse_error = Lexing.position * string

type state = {
  lexbuf : Lexing.lexbuf;
  mutable current : Lexer.tok;
  mutable pos : Lexing.position;
}

let advance state =
  state.current <- Lexer.token state.lexbuf;
  state.pos <- state.lexbuf.Lexing.lex_start_p

let init lexbuf = {
  lexbuf;
  current = Lexer.token lexbuf;
  pos = lexbuf.Lexing.lex_start_p;
}

let unexpected_token msg state =
  let err = (
    "Unexpected token "
    ^ Lexer.tok_to_string state.current
    ^ ", expected "
    ^ msg
  )
  in Error (state.pos, err)

let expect_with f msg state =
  match f state.current with
  | Some v -> advance state; Ok v
  | None   -> unexpected_token msg state

let expect tok state =
  let matches other = if tok = other then Some () else None in
  expect_with matches (Lexer.tok_to_string tok) state

let expect_upper_ident msg state =
  let f = function UPPER_ID n -> Some n | _ -> None in
  expect_with f msg state

let expect_lower_ident msg state =
  let f = function LOWER_ID n -> Some n | _ -> None in
  expect_with f msg state

let take_with f state =
  match f state.current with
  | Some v -> advance state; Some v
  | None -> None

let take tok state =
  let matches other = if other == tok then Some () else None in
  Option.is_some (take_with matches state)

let rec skip_newlines state = if take NEWLINE state then skip_newlines state

let rec list_inner sep term parse state =
  if take sep state then
    let* item = parse state in
    let* rest = list_inner sep term parse state in
    Ok (item :: rest)
  else
    match term with
    | Some t ->
        let* _ = expect t state in
        Ok []
    | _ -> Ok []

let list sep term parse state =
  let* x = parse state in
  let* xs = list_inner sep term parse state in
  Ok (x :: xs)

let import state =
  let loc = state.pos in
  let* _ = expect IMPORT state in
  let* modname = expect_upper_ident "uppercase module name" state in
  Ok (TopImport (loc, modname))

let rec decl_group_items loc parse state =
  let* item = parse loc state in
  skip_newlines state;
  let newloc = state.pos in
  if take AND state then
    let* rest = decl_group_items newloc parse state in
    Ok (item :: rest)
  else
    Ok [item]

let decl_group leading_tok parse state =
  let loc = state.pos in
  let* _ = expect leading_tok state in
  let* items = decl_group_items loc parse state in
  Ok items

let type_params state =
  let* _ = expect LBRACK state in
  let parse_item = expect_lower_ident "expected lowercase name" in
  let* names = list COMMA (Some LBRACK) parse_item state in
  Ok names

let rec type_expr_or_ctor state =
  match state.current with
  | LOWER_ID n -> advance state; Ok (Either.Right (TVar (state.pos, n)))
  | LPAREN ->
      let pos = state.pos in
      advance state;
      if take RPAREN state then
        Ok (Either.Right (TUnit pos))
      else
        let* t = type_expr state in
        if take RPAREN state then
          Ok (Either.Right t)
        else if take COMMA state then
          let* rest = list COMMA (Some RPAREN) type_expr state in
          Ok (Either.Right (TTuple (pos, t :: rest)))
        else
          unexpected_token "comma or right paren" state
  | FUN ->
      let pos = state.pos in
      advance state;
      let* _ = expect LPAREN state in
      let* params = list COMMA (Some RPAREN) type_expr state in
      let* _ = expect ARROW state in
      let* ret = type_expr state in
      Ok (Either.Right (TFun (pos, params, ret)))
  | UPPER_ID n ->
      let pos = state.pos in
      advance state;
      (match state.current with
      | LBRACK ->
          advance state;
          let* args = list COMMA (Some RBRACK) type_expr state in
          Ok (Either.Right (TInst (pos, n, Some args)))
      | LPAREN | LBRACE ->
          let* payload = ctor_payload state in
          Ok (Either.Left (pos, n, payload))
      | _ -> Ok (Either.Right (TInst (pos, n, None))))
  | _ -> unexpected_token "type expression" state

and type_expr state =
  let pos = state.pos in
  let* e = type_expr_or_ctor state in
  match e with
  | Left _ -> Error (pos, "Expected type expression but got constructor decl")
  | Right expr -> Ok expr

and ctor_payload state =
  match state.current with
    | LPAREN ->
        advance state;
        let* fields = list COMMA (Some RPAREN) type_expr state in
        skip_newlines state;
        Ok (Some (CtorTuple fields))
    | LBRACE ->
        advance state;
        let* fields = list COMMA (Some RBRACE) record_field state in
        skip_newlines state;
        Ok (Some (CtorRecord fields))
    | _ -> Ok None

and record_field state =
  let pos = state.pos in
  let* name = expect_lower_ident "lowercase name" state in
  let* _ = expect EQ state in
  let* ty = type_expr state in
  Ok (pos, name, ty)

let type_ctor state =
  let pos = state.pos in
  let* name = expect_upper_ident "uppercase name" state in
  let* payload = ctor_payload state in
  Ok (pos, name, payload)

let type_ctors = list PIPE None type_ctor

let type_rhs state =
  if take PIPE state then
    let* ctors = type_ctors state in
    Ok (VariantType ctors)
  else begin
    let* v1 = type_expr_or_ctor state in
    match v1 with
    | Left ctor ->
        let* rest = type_ctors state in
        Ok (VariantType (ctor :: rest))
    | Right expr ->
        Ok (TypeAlias expr)
  end

let type_decl obj loc state =
  let* name = expect_upper_ident "uppercase type name" state in
  let* params = type_params state in
  let* _ = expect EQ state in
  let* rhs = type_rhs state in
  if obj then
    Ok (ObjectTypeDecl (loc, name, params, rhs))
  else
    Ok (TypeDecl (loc, name, params, rhs))

let type_group state =
  let obj = match state.current with
  | OBJECT -> advance state; true
  | _ -> false
  in
  let* items = decl_group TYPE (type_decl obj) state in
  Ok (TopType items)

let fun_group state = failwith "TODO"
let let_decl state = failwith "TODO"
let var_decl state = failwith "TODO"

let top_level_item state =
  match state.current with
  | IMPORT -> import state
  | TYPE -> type_group state
  | FUN -> fun_group state
  | LET -> let_decl state
  | VAR -> var_decl state
  | tok -> unexpected_token "top-level item" state

let rec program state =
  skip_newlines state;
  match state.current with
  | EOF -> Ok []
  | _ ->
      let* s = top_level_item state in
      let* ss = program state in
      Ok (s :: ss)

let parse s =
  let buf = Lexing.from_string s in
  let state = init buf in
  program state
