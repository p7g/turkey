open Ast
open Lexer

let (let*) = Result.bind

type parse_error = Lexing.position * string

let show_parse_error ((pos, msg) : parse_error) =
  let pos_str = Int.to_string pos.pos_lnum ^ ":" ^ Int.to_string pos.pos_cnum in
  "Parse error " ^ pos.pos_fname ^ " at " ^ pos_str ^ ": " ^ msg

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

let expect tok =
  let matches other = if tok = other then Some () else None in
  expect_with matches (Lexer.tok_to_string tok)

let expect_one toks =
  let matches other = if List.mem other toks then Some other else None in
  let msg = (
    "one of: "
    ^ String.concat ", " (List.map Lexer.tok_to_string toks)
  )
  in expect_with matches msg

let expect_upper_ident msg =
  let f = function UPPER_ID n -> Some n | _ -> None in
  expect_with f msg

let expect_lower_ident msg =
  let f = function LOWER_ID n -> Some n | _ -> None in
  expect_with f msg

let take_with f state =
  match f state.current with
  | Some v -> advance state; Some v
  | None -> None

let take tok state =
  let matches other = if other == tok then Some () else None in
  Option.is_some (take_with matches state)

let take_one toks =
  let matches other = if List.mem other toks then Some other else None in
  take_with matches

let rec skip_newlines state = if take NEWLINE state then skip_newlines state

let rec list_inner seps term parse state =
  skip_newlines state;
  if Option.is_some (take_one seps state) then
    (* automatically support trailing sep if there's a termination token *)
    match (state.current, term) with
    | (x, Some y) when x = y ->
        advance state;
        Ok []
    | _ ->
      let* item = parse state in
      let* rest = list_inner seps term parse state in
      Ok (item :: rest)
  else
    match term with
    | Some t ->
        let* _ = expect t state in
        Ok []
    | _ -> Ok []

let list seps term parse state =
  let* x = parse state in
  let* xs = list_inner seps term parse state in
  Ok (x :: xs)

let list_opt seps term parse state =
  if Some state.current = term then
    Ok []
  else
    list seps term parse state

let stmt_sep = expect_one [NEWLINE; SEMICOLON; EOF]

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
  let* names = list [COMMA] (Some RBRACK) parse_item state in
  Ok names

let rec type_expr_or_ctor state =
  match state.current with
  | LOWER_ID n -> advance state; Ok (Either.Right (TVar (state.pos, n)))
  | LPAREN ->
      let pos = state.pos in
      advance state;
      skip_newlines state;
      if take RPAREN state then
        Ok (Either.Right (TUnit pos))
      else
        let* t = type_expr state in
        skip_newlines state;
        if take RPAREN state then
          Ok (Either.Right t)
        else if take COMMA state then begin
          skip_newlines state;
          let* rest = list [COMMA] (Some RPAREN) type_expr state in
          Ok (Either.Right (TTuple (pos, t :: rest)))
        end else
          unexpected_token "comma or right paren" state
  | FUN ->
      let pos = state.pos in
      advance state;
      skip_newlines state;
      let* _ = expect LPAREN state in
      skip_newlines state;
      let* params = list_opt [COMMA] (Some RPAREN) type_expr state in
      skip_newlines state;
      let* _ = expect ARROW state in
      skip_newlines state;
      let* ret = type_expr state in
      Ok (Either.Right (TFun (pos, params, ret)))
  | UPPER_ID n ->
      let pos = state.pos in
      advance state;
      (match state.current with
      | LBRACK ->
          advance state;
          skip_newlines state;
          let* args = list [COMMA] (Some RBRACK) type_expr state in
          Ok (Either.Right (TInst (pos, n, Some args)))
      | LPAREN | LBRACE ->
          let* payload = ctor_payload_decl state in
          Ok (Either.Left (pos, n, payload))
      | _ -> Ok (Either.Right (TInst (pos, n, None))))
  | _ -> unexpected_token "type expression" state

and type_expr state =
  let pos = state.pos in
  let* e = type_expr_or_ctor state in
  match e with
  | Left _ -> Error (pos, "Expected type expression but got constructor decl")
  | Right expr -> Ok expr

and ctor_payload_decl state =
  match state.current with
    | LPAREN ->
        advance state;
        skip_newlines state;
        let* fields = list [COMMA] (Some RPAREN) type_expr state in
        skip_newlines state;
        Ok (Some (CtorTuple fields))
    | LBRACE ->
        advance state;
        skip_newlines state;
        let* fields = list [COMMA] (Some RBRACE) record_field state in
        skip_newlines state;
        Ok (Some (CtorRecord fields))
    | _ -> Ok None

and record_field state =
  let pos = state.pos in
  let* name = expect_lower_ident "lowercase name" state in
  skip_newlines state;
  let* _ = expect COLON state in
  skip_newlines state;
  let* ty = type_expr state in
  Ok (pos, name, ty)

let type_ctor state =
  let pos = state.pos in
  let* name = expect_upper_ident "uppercase name" state in
  let* payload = ctor_payload_decl state in
  Ok (pos, name, payload)

let type_ctors state =
  match state.current with
  | UPPER_ID _ -> list [PIPE] None type_ctor state
  | _ -> Ok []

let type_rhs state =
  if take PIPE state then
    let* ctors = type_ctors state in
    Ok (VariantType ctors)
  else begin
    let* v1 = type_expr_or_ctor state in
    match v1 with
    | Left ctor ->
        skip_newlines state;
        let* rest = type_ctors state in
        Ok (VariantType (ctor :: rest))
    | Right expr ->
        Ok (TypeAlias expr)
  end

let type_decl obj loc state =
  let* name = expect_upper_ident "uppercase type name" state in
  skip_newlines state;
  let* params =
    match state.current with
    | LBRACK -> type_params state
    | _ -> Ok []
  in skip_newlines state;
  let* _ = expect EQ state in
  skip_newlines state;
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

let binary tok_to_op nextprec state =
  let rec loop lhs =
    match List.assoc_opt state.current tok_to_op with
    | Some op ->
        skip_newlines state;
        let loc = state.pos in
        let* rhs = nextprec state in
        loop (BinaryOp (loc, lhs, op, rhs))
    | None ->
        Ok lhs
  in Result.bind (nextprec state) loop

let rec expr state = annotated_expr state

and annotated_expr state =
  let loc = state.pos in
  let* e = logical_or state in
  if take COLON state then (
    skip_newlines state;
    let* ty = type_expr state in
    Ok (Annotate (loc, e, ty))
  ) else Ok e

and logical_or state = binary [(PIPEPIPE, Or)] logical_and state
and logical_and state = binary [(AMPAMP, And)] comparison state

and comparison state =
  let* lhs = term state in
  let op = match state.current with
  | EQEQ -> Some Eq
  | BANGEQ -> Some Neq
  | LT -> Some Lt
  | LE -> Some Le
  | GT -> Some Gt
  | GE -> Some Ge
  | _ -> None
  in match op with
  | None -> Ok lhs
  | Some op ->
      let pos = state.pos in
      advance state;
      skip_newlines state;
      let* rhs = term state in
      Ok (BinaryOp (pos, lhs, op, rhs))

and term state = binary [(PLUS, Add); (MINUS, Sub)] factor state
and factor state = binary [(STAR, Mul); (SLASH, Div)] unary state

and unary state =
  let loc = state.pos in
  match state.current with
  | BANG ->
      skip_newlines state;
      let* rhs = unary state in
      Ok (UnaryOp (loc, Not, rhs))
  | MINUS ->
      skip_newlines state;
      let* rhs = unary state in
      Ok (UnaryOp (loc, Neg, rhs))
  | _ -> postfix state

and postfix state =
  let rec loop lhs =
    let pos = state.pos in
    match state.current with
    | LPAREN ->
        advance state;
        skip_newlines state;
        let* args = list_opt [COMMA] (Some RPAREN) expr state in
        loop (Call (pos, lhs, args))
    | DOT ->
        advance state;
        skip_newlines state;
        let* lhs' = match state.current with
        | INT_LIT n -> Ok (TupleAccess (pos, lhs, n))
        | LOWER_ID n -> Ok (FieldAccess (pos, lhs, n))
        | _ -> unexpected_token "tuple index or field name" state
        in loop lhs'
    | LBRACK ->
        advance state;
        skip_newlines state;
        let* idx = expr state in
        let* _ = expect RBRACK state in
        loop (Index (pos, lhs, idx))
    | _ -> Ok lhs

  in Result.bind (primary state) loop

and primary state =
  let pos = state.pos in
  match state.current with
  | MATCH -> match_expr state
  | LOOP -> loop_expr state
  | WHILE -> while_expr state
  | FUN -> fun_expr state
  | BREAK ->
      advance state;
      if List.mem state.current [NEWLINE; SEMICOLON] then
        Ok (Break (pos, None))
      else
        let* e = expr state in
        Ok (Break (pos, Some e))
  | CONTINUE -> advance state; Ok (Continue pos)
  | RETURN ->
      advance state;
      if List.mem state.current [NEWLINE; SEMICOLON] then
        Ok (Return (pos, None))
      else
        let* e = expr state in
        Ok (Return (pos, Some e))
  | TRUE -> advance state; Ok (BoolLit (pos, true))
  | FALSE -> advance state; Ok (BoolLit (pos, false))
  | INT_LIT i -> advance state; Ok (IntLit (pos, i))
  | FLOAT_LIT f -> advance state; Ok (FloatLit (pos, f))
  | STRING_LIT s -> advance state; Ok (StrLit (pos, s))
  | CHAR_LIT c -> advance state; Ok (CharLit (pos, c))
  | LPAREN ->
      advance state;
      skip_newlines state;
      (match state.current with
      | RPAREN -> advance state; Ok (UnitLit pos)
      | _ ->
          let* e = expr state in
          skip_newlines state;
          let* tok = expect_one [RPAREN; COMMA] state in
          if tok = RPAREN then
            Ok e
          else
            let* rest = list [COMMA] (Some RPAREN) expr state in
            Ok (Tuple (pos, e :: rest )))
  | LOWER_ID n -> Ok (Var (pos, n))
  | UPPER_ID n ->
      advance state;
      let* payload = match state.current with
      | LPAREN | LBRACE ->
          let* p = ctor_payload state in
          Ok (Some p)
      | _ -> Ok None
      in Ok (Ctor (pos, n, payload))
  | _ -> unexpected_token "expression" state

and ctor_payload = failwith "unimplemented"
and match_expr = failwith "unimplemented"
and loop_expr = failwith "unimplemented"
and while_expr = failwith "unimplemented"
and fun_expr = failwith "unimplemented"

and statement state =
  let pos = state.pos in
  match state.current with
  | LET -> let* items = let_decl state in Ok (SLet (pos, items))
  | VAR -> let* items = var_decl state in Ok (SVar (pos, items))
  | FUN -> let* items = fun_group state in Ok (SFun items)
  | _ -> expr_or_assignment state

and expr_or_assignment state = failwith "unimplemented"

and block state =
  let* _ = expect LBRACE state in
  let maybe_stmt state =
    skip_newlines state;
    if state.current = RBRACE then
      Ok None
    else
      let* s = statement state in
      Ok (Some s)
  in
  let* ss = list_opt [NEWLINE; SEMICOLON] (Some RBRACE) maybe_stmt state in
  skip_newlines state;
  Ok (List.filter_map (fun x -> x) ss)

and fun_params state =
  let* _ = expect LPAREN state in
  skip_newlines state;
  let one state =
    let loc = state.pos in
    let* name = expect_lower_ident "lowercase param name" state in
    skip_newlines state;
    let* ann = if take COLON state then (
      skip_newlines state;
      let* ty = type_expr state in
      Ok (Some ty)
    ) else
      Ok None
    in Ok (loc, name, ann)
  in list_opt [COMMA] (Some RPAREN) one state

and fun_body state =
  if take EQ state then (
    skip_newlines state;
    let* e = expr state in
    Ok (FunBodyExpr e)
  ) else (
    skip_newlines state;
    let* b = block state in
    Ok (FunBodyBlock b)
  )

and fun_decl loc state =
  let* name = expect_lower_ident "lowercase function name" state in
  skip_newlines state;
  let* params = fun_params state in
  skip_newlines state;
  let* ret = if take ARROW state then (
    skip_newlines state;
    let* ty = type_expr state in
    Ok (Some ty)
  ) else Ok None in
  skip_newlines state;
  let* body = fun_body state in
  Ok ((loc, name, params, ret, body) : fun_decl)

and fun_group_items state = decl_group FUN fun_decl state
and fun_group state =
  let* items = fun_group_items state in
  Ok items

and let_decl state = failwith "TODO"
and var_decl state = failwith "TODO"

let top_level_item state =
  let pos = state.pos in
  match state.current with
  | IMPORT -> import state
  | TYPE -> type_group state
  | FUN -> let* items = fun_group state in Ok (TopFun items)
  | LET -> let* items = let_decl state in Ok (TopLet (pos, items))
  | VAR -> let* items = var_decl state in Ok (TopVar (pos, items))
  | tok -> unexpected_token "top-level item" state

let rec program state =
  skip_newlines state;
  match state.current with
  | EOF -> Ok []
  | _ ->
      let* s = top_level_item state in
      let* _ = stmt_sep state in
      let* ss = program state in
      Ok (s :: ss)

let parse s =
  let buf = Lexing.from_string s in
  let state = init buf in
  program state
