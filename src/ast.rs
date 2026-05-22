use crate::source::Span;

pub struct Module {
    span: Span,
    items: Vec<TopLevelItem>,
}

pub struct TopLevelItem {
    span: Span,
    kind: TopLevelItemKind,
}

pub enum TopLevelItemKind {
    ImportDecl(ImportDecl),
    TypeGroup(TypeGroup),
    FnGroup(FnGroup),
    Statement(Statement),
    Error,
}

pub struct Name {
    span: Span,
    name: String,
}

pub struct QualifiedName {
    span: Span,
    path: Vec<Name>,
}

pub struct ImportDecl {
    span: Span,
    module_path: QualifiedName,
    kind: ImportKind,
}

pub enum ImportKind {
    Module,
    Alias(ImportAlias),
    Items(Vec<ImportItem>),
}

pub struct ImportAlias {
    span: Span,
    name: Name,
}

pub struct ImportItem {
    span: Span,
    name: Name,
    alias: Option<ImportAlias>,
}

pub struct TypeGroup {
    span: Span,
    decls: Vec<TypeDecl>,
}

pub struct TypeDecl {
    span: Span,
    name: Name,
    type_params: Vec<Name>,
    value: TypeDeclValue,
}

pub enum TypeDeclValue {
    Alias(TypeExpr),
    Constructors(Vec<VariantConstructor>),
}

pub struct VariantConstructor {
    span: Span,
    name: Name,
    payload: Option<ConstructorPayload>,
}

pub enum ConstructorPayload {
    Tuple(TuplePayload),
    Record(RecordPayload),
}

pub struct TuplePayload {
    span: Span,
    items: Vec<TypeExpr>,
}

pub struct RecordPayload {
    span: Span,
    items: Vec<RecordPayloadField>,
}

pub struct RecordPayloadField {
    span: Span,
    name: Name,
    type_: TypeExpr,
}

pub struct TypeExpr {
    span: Span,
    kind: TypeExprKind,
}

pub enum TypeExprKind {
    Variable(Name),
    Instance(QualifiedName, Vec<TypeExpr>),
    Tuple(Vec<TypeExpr>),
    Fn(Vec<TypeExpr>, Box<TypeExpr>),
    Error,
}

pub struct FnGroup {
    span: Span,
    decls: Vec<FnDecl>,
}

pub struct FnDecl {
    span: Span,
    name: Name,
    params: Vec<FnParam>,
    return_type: Option<TypeExpr>,
    body: ExprOrBody,
}

pub struct Body {
    span: Span,
    statements: Vec<Statement>,
}

pub enum ExprOrBody {
    Statement(Statement),
    Body(Body),
}

pub struct FnParam {
    span: Span,
    name: Name,
    type_: Option<TypeExpr>,
}

pub struct Statement {
    span: Span,
    kind: StatementKind,
}

pub enum StatementKind {
    Let(Pattern, Expression),
    Var(Pattern, Expression),
    Assignment(Expression, Expression),
    Expression(Expression),
    Error,
}

pub struct Expression {
    span: Span,
    kind: ExpressionKind,
}

pub enum ExpressionKind {
    IntLiteral(IntLiteral),
    FloatLiteral(FloatLiteral),
    StringLiteral(StringLiteral),
    CharLiteral(CharLiteral),
    BoolLiteral(bool),
    TupleLiteral(Vec<Expression>),
    VariableReference(QualifiedName),
    If(If),
    While(Box<Expression>, Body),
    Loop(Body),
    Match(Box<Expression>, Vec<MatchArm>),
    Unary(UnaryOperator, Box<Expression>),
    Binary(Box<Expression>, BinaryOperator, Box<Expression>),
    RecordField(Box<Expression>, Name),
    TupleField(Box<Expression>, usize),
    Index(Box<Expression>, Box<Expression>),
    Call(Box<Expression>, Vec<Expression>),
    RecordConstruction(RecordConstruction),
    Error,
}

pub struct IntLiteral {
    raw: String,
}

pub struct FloatLiteral {
    raw: String,
}

pub struct StringLiteral {
    raw: String,
}

pub struct CharLiteral {
    raw: String,
}

pub struct If {
    condition: Box<Expression>,
    then_body: Body,
    else_branch: Option<Else>,
}

pub enum Else {
    If(Box<If>),
    Body(Body),
}

pub struct MatchArm {
    span: Span,
    pattern: Pattern,
    guard: Option<Box<Expression>>,
    body: ExprOrBody,
}

pub struct BinaryOperator {
    span: Span,
    kind: BinaryOperatorKind,
}

pub enum BinaryOperatorKind {
    LogicalOr,
    LogicalAnd,
    Equal,
    NotEqual,
    LessThan,
    LessThanOrEqual,
    GreaterThan,
    GreaterThanOrEqual,
    Addition,
    Subtraction,
    Product,
    Quotient,
}

pub struct UnaryOperator {
    span: Span,
    kind: UnaryOperatorKind,
}

pub enum UnaryOperatorKind {
    Negation,
    LogicalNot,
}

pub struct RecordConstruction {
    path: QualifiedName,
    fields: Vec<RecordConstructionField>,
}

pub struct RecordConstructionField {
    span: Span,
    name: Name,
    value: Expression,
}

pub struct Pattern {
    span: Span,
    kind: PatternKind,
}

pub enum PatternKind {
    Or(Vec<Pattern>),
    As(Box<Pattern>, Name),
    Wildcard,
    Binding(Name),
    Variant(QualifiedName, Option<ConstructorPayloadPattern>),
    Tuple(Vec<Pattern>),
    BoolLiteral(bool),
    IntLiteral(IntLiteral),
    FloatLiteral(FloatLiteral),
    StringLiteral(StringLiteral),
    CharLiteral(CharLiteral),
    Error,
}

pub struct ConstructorPayloadPattern {
    span: Span,
    kind: ConstructorPayloadPatternKind,
}

pub enum ConstructorPayloadPatternKind {
    Tuple(Vec<Pattern>),
    Record(Vec<RecordFieldPattern>),
}

pub struct RecordFieldPattern {
    span: Span,
    name: Name,
    value: Pattern,
}
