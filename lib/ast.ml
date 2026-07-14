(* Abstract syntax tree for Turkey *)

type loc = Lexing.position

type upper_ident = string
type lower_ident = string
type module_path = upper_ident

(* ---------- types ---------- *)

type type_expr =
  | TVar of loc * lower_ident
  | TInst of loc * module_path * type_expr list option
  | TTuple of loc * type_expr list
  | TFun of loc * type_expr list * type_expr
  | TUnit of loc

type field_type = loc * lower_ident * type_expr

type ctor_payload =
  | CtorTuple of type_expr list
  | CtorRecord of field_type list

type variant_ctor = loc * upper_ident * ctor_payload option

type type_rhs =
  | TypeAlias of type_expr
  | VariantType of variant_ctor list

type type_binding = loc * upper_ident * lower_ident list * type_rhs

type type_decl =
  | TypeDecl of type_binding
  | ObjectTypeDecl of type_binding

type type_group = type_decl list

type fun_param = loc * lower_ident * type_expr option

(* ---------- mutually recursive AST types ---------- *)

type var_annotation = type_expr

and let_decl = pattern * var_annotation option * expr

and var_decl = pattern * var_annotation option * expr

and fun_body =
  | FunBodyExpr of expr
  | FunBodyBlock of block

and fun_decl = loc * lower_ident * fun_param list * type_expr option * fun_body

and fun_group = fun_decl list

and block = statement list

and statement =
  | SLet of loc * let_decl
  | SVar of loc * var_decl
  | SFun of fun_group
  | SAssign of loc * assignment
  | SExpr of expr

and assignment = expr * expr

and cond =
  | CondExpr of expr
  | CondLet of let_decl

and expr =
  | UnitLit of loc
  | BoolLit of loc * bool
  | IntLit of loc * int
  | FloatLit of loc * float
  | StrLit of loc * string
  | CharLit of loc * char
  | Var of loc * lower_ident
  | Ctor of loc * module_path * ctor_payload option
  | Tuple of loc * expr list
  | Record of loc * (lower_ident * expr option) list
  | Call of loc * expr * expr list
  | Index of loc * expr * expr
  | FieldAccess of loc * expr * lower_ident
  | TupleAccess of loc * expr * int
  | UnaryOp of loc * unop * expr
  | BinaryOp of loc * expr * binop * expr
  | Annotate of loc * expr * type_expr
  | If of loc * cond * block * block option
  | Match of loc * expr * match_arm list
  | Loop of loc * block
  | While of loc * cond * block
  | FunExpr of loc * fun_param list * type_expr option * fun_body
  | Break of loc * expr option
  | Continue of loc
  | Return of loc * expr option

and binop =
  | Add | Sub | Mul | Div
  | Eq | Neq | Lt | Le | Gt | Ge
  | And | Or

and unop = Neg | Not

and pattern =
  | PWildcard of loc
  | PVar of loc * lower_ident
  | PCtor of loc * module_path * pattern_ctor_payload option
  | PTuple of loc * pattern list
  | PLit of loc * literal_pattern

and pattern_ctor_payload =
  | PCtorTuple of pattern list
  | PCtorRecord of (lower_ident * pattern option) list

and literal_pattern =
  | LPUnit of loc
  | LPBool of loc * bool
  | LPInt of loc * int
  | LPFloat of loc * float
  | LPStr of loc * string
  | LPChar of loc * char

and match_arm = pattern * expr option * expr

and top_level_item =
  | TopImport of loc * module_path
  | TopType of type_group
  | TopLet of loc * let_decl
  | TopVar of loc * var_decl
  | TopFun of fun_group

and program = top_level_item list
