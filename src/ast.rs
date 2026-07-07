use crate::source::Span;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Module {
    pub span: Span,
    pub items: Vec<TopLevelItem>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TopLevelItem {
    pub span: Span,
    pub kind: TopLevelItemKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TopLevelItemKind {
    ImportDecl(ImportDecl),
    TypeGroup(TypeGroup),
    FnGroup(FnGroup),
    Statement(Statement),
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Name {
    pub span: Span,
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QualifiedName {
    pub span: Span,
    pub path: Vec<Name>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportDecl {
    pub span: Span,
    pub module_path: QualifiedName,
    pub kind: ImportKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ImportKind {
    Module,
    Alias(ImportAlias),
    Items(Vec<ImportItem>),
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportAlias {
    pub span: Span,
    pub name: Name,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportItem {
    pub span: Span,
    pub name: Name,
    pub alias: Option<ImportAlias>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypeGroup {
    pub span: Span,
    pub decls: Vec<TypeDecl>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypeDecl {
    pub span: Span,
    pub name: Name,
    pub type_params: Vec<Name>,
    pub value: TypeDeclValue,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TypeDeclValue {
    Alias(TypeExpr),
    Constructors(Vec<VariantConstructor>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VariantConstructor {
    pub span: Span,
    pub name: Name,
    pub payload: Option<ConstructorPayload>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConstructorPayload {
    Tuple(TuplePayload),
    Record(RecordPayload),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TuplePayload {
    pub span: Span,
    pub items: Vec<TypeExpr>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordPayload {
    pub span: Span,
    pub items: Vec<RecordPayloadField>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordPayloadField {
    pub span: Span,
    pub name: Name,
    pub type_: TypeExpr,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypeExpr {
    pub span: Span,
    pub kind: TypeExprKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TypeExprKind {
    Variable(Name),
    Instance(QualifiedName, Vec<TypeExpr>),
    Tuple(Vec<TypeExpr>),
    Fn(Vec<TypeExpr>, Box<TypeExpr>),
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FnGroup {
    pub span: Span,
    pub decls: Vec<FnDecl>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FnDecl {
    pub vspan: Span,
    pub vname: Name,
    pub vparams: Vec<FnParam>,
    pub vreturn_type: Option<TypeExpr>,
    pub vbody: ExprOrBody,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Body {
    pub span: Span,
    pub statements: Vec<Statement>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExprOrBody {
    Expression(Expression),
    Body(Body),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FnParam {
    pub span: Span,
    pub name: Name,
    pub type_: Option<TypeExpr>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Statement {
    pub span: Span,
    pub kind: StatementKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StatementKind {
    Let(Pattern, Expression),
    Var(Pattern, Expression),
    Assignment(Expression, Expression),
    Expression(Expression),
    Break(Option<Expression>),
    Continue,
    Return(Option<Expression>),
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Expression {
    pub span: Span,
    pub kind: ExpressionKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
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
    AnnotatedExpression(Box<Expression>, TypeExpr),
    FnExpr(Vec<FnParam>, Option<TypeExpr>, Box<ExprOrBody>),
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IntLiteral {
    pub raw: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FloatLiteral {
    pub raw: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StringLiteral {
    pub raw: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CharLiteral {
    pub raw: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct If {
    pub condition: Box<Expression>,
    pub then_body: Body,
    pub else_branch: Option<Else>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Else {
    If(Box<If>),
    Body(Body),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MatchArm {
    pub span: Span,
    pub pattern: Pattern,
    pub guard: Option<Box<Expression>>,
    pub body: ExprOrBody,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BinaryOperator {
    pub span: Span,
    pub kind: BinaryOperatorKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnaryOperator {
    pub span: Span,
    pub kind: UnaryOperatorKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UnaryOperatorKind {
    Negation,
    LogicalNot,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordConstruction {
    pub path: QualifiedName,
    pub fields: Vec<RecordConstructionField>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordConstructionField {
    pub span: Span,
    pub name: Name,
    pub value: Expression,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pattern {
    pub span: Span,
    pub kind: PatternKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConstructorPayloadPattern {
    pub span: Span,
    pub kind: ConstructorPayloadPatternKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConstructorPayloadPatternKind {
    Tuple(Vec<Pattern>),
    Record(Vec<RecordFieldPattern>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordFieldPattern {
    pub span: Span,
    pub name: Name,
    pub value: Pattern,
}
