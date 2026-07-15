(* Abstract syntax tree for Turkey *)

type loc = Lexing.position = {
  pos_fname : string;
  pos_lnum : int;
  pos_bol : int;
  pos_cnum : int;
}
[@@deriving show]

type upper_ident = string
[@@deriving show]
type lower_ident = string
[@@deriving show]
type module_path = upper_ident
[@@deriving show]

(* ---------- types ---------- *)

type type_expr =
  | TVar of loc * lower_ident
  | TInst of loc * module_path * type_expr list option
  | TTuple of loc * type_expr list
  | TFun of loc * type_expr list * type_expr
  | TUnit of loc
  [@@deriving show]

type field_type = loc * lower_ident * type_expr
[@@deriving show]

type ctor_payload =
  | CtorTuple of type_expr list
  | CtorRecord of field_type list
  [@@deriving show]

type variant_ctor = loc * upper_ident * ctor_payload option
[@@deriving show]

type type_rhs =
  | TypeAlias of type_expr
  | VariantType of variant_ctor list
  [@@deriving show]

type type_binding = loc * upper_ident * lower_ident list * type_rhs
[@@deriving show]

type type_decl =
  | TypeDecl of type_binding
  | ObjectTypeDecl of type_binding
  [@@deriving show]

type type_group = type_decl list
[@@deriving show]

type fun_param = loc * lower_ident * type_expr option
[@@deriving show]

(* ---------- mutually recursive AST types ---------- *)

type var_annotation = type_expr
[@@deriving show]

and let_decl = pattern * var_annotation option * expr
[@@deriving show]

and var_decl = pattern * var_annotation option * expr
[@@deriving show]

and fun_body =
  | FunBodyExpr of expr
  | FunBodyBlock of block
  [@@deriving show]

and fun_decl = loc * lower_ident * fun_param list * type_expr option * fun_body
[@@deriving show]

and fun_group = fun_decl list
[@@deriving show]

and block = statement list
[@@deriving show]

and statement =
  | SLet of loc * let_decl
  | SVar of loc * var_decl
  | SFun of fun_group
  | SAssign of loc * assignment
  | SExpr of expr
  [@@deriving show]

and assignment = expr * expr
[@@deriving show]

and cond =
  | CondExpr of expr
  | CondLet of let_decl
  [@@deriving show]

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
  [@@deriving show]

and binop =
  | Add | Sub | Mul | Div
  | Eq | Neq | Lt | Le | Gt | Ge
  | And | Or
  [@@deriving show]

and unop = Neg | Not
[@@deriving show]

and pattern =
  | PWildcard of loc
  | PVar of loc * lower_ident
  | PCtor of loc * module_path * pattern_ctor_payload option
  | PTuple of loc * pattern list
  | PLit of loc * literal_pattern
  [@@deriving show]

and pattern_ctor_payload =
  | PCtorTuple of pattern list
  | PCtorRecord of (lower_ident * pattern option) list
  [@@deriving show]

and literal_pattern =
  | LPUnit of loc
  | LPBool of loc * bool
  | LPInt of loc * int
  | LPFloat of loc * float
  | LPStr of loc * string
  | LPChar of loc * char
  [@@deriving show]

and match_arm = pattern * expr option * expr
[@@deriving show]

and top_level_item =
  | TopImport of loc * module_path
  | TopType of type_group
  | TopLet of loc * let_decl
  | TopVar of loc * var_decl
  | TopFun of fun_group
  [@@deriving show]

and program = top_level_item list
[@@deriving show]
