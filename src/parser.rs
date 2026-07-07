use crate::ast::*;
use crate::lexer::{Token, TokenKind};
use crate::source::SourceMap;

#[derive(Debug, Clone, PartialEq, Eq)]
enum Diagnostic {
    UnexpectedToken(Option<Token>),
    ImportAliasMustBeUpperIdent,
}

struct Context<'a> {
    pos: usize,
    tokens: Vec<Token>,
    diagnostics: Vec<Diagnostic>,
    source_map: &'a SourceMap,
}

impl<'a> Context<'a> {
    fn peek_n(&mut self, n: usize) -> Option<Token> {
        self.tokens.get(self.pos + n).copied()
    }

    fn prev(&mut self) -> Option<Token> {
        self.pos
            .checked_sub(1)
            .and_then(|n| self.tokens.get(n).copied())
    }

    fn peek(&mut self) -> Option<Token> {
        self.peek_n(0)
    }

    fn next(&mut self) -> Option<Token> {
        let ret = self.peek();
        self.pos += 1;
        ret
    }

    fn match_if(&mut self, cond: impl FnOnce(Token) -> bool) -> Option<Token> {
        if cond(self.peek()?) {
            self.next()
        } else {
            None
        }
    }

    fn matches(&mut self, kind: TokenKind) -> Option<Token> {
        self.match_if(|tok| tok.kind() == kind)
    }

    fn matches_any(&mut self, kinds: &[TokenKind]) -> Option<Token> {
        self.match_if(|tok| kinds.contains(&tok.kind()))
    }
}

pub fn parse(source_map: &SourceMap, tokens: Vec<Token>) -> (Module, Vec<Diagnostic>) {
    let mut ctx = Context {
        pos: 0,
        tokens,
        diagnostics: Vec::new(),
        source_map,
    };

    let module = parse_module(&mut ctx);
    (module, ctx.diagnostics)
}

fn parse_module(ctx: &mut Context) -> Module {
    let start = ctx.peek().expect("Should at least have an EOF token");

    let mut items = Vec::new();
    while let Some(tok) = ctx.peek() {
        if tok.kind() == TokenKind::Newline {
            continue;
        }
        items.push(parse_top_level_item(ctx));
    }

    Module {
        span: start.span(),
        items,
    }
}

fn parse_top_level_item(ctx: &mut Context) -> TopLevelItem {
    let tok = ctx
        .peek()
        .expect("Should only be called if there's at least one token");

    match tok.kind() {
        TokenKind::Import => parse_import_decl(ctx),
        TokenKind::Type => parse_type_group(ctx),
        TokenKind::Fn => parse_fn_group(ctx),
        _ => parse_top_level_stmt(ctx),
    }
}

fn parse_import_decl(ctx: &mut Context) -> TopLevelItem {
    let start_tok = ctx.next().unwrap();
    debug_assert!(start_tok.kind() == TokenKind::Import);
    let path = parse_module_path(ctx);
    let (kind, span) = if let Some(as_) = ctx.matches(TokenKind::As) {
        if let Some(name) = ctx.matches(TokenKind::UpperIdent) {
            let span = as_.span().span(name.span());
            let kind = ImportKind::Alias(ImportAlias {
                span,
                name: Name {
                    span: name.span(),
                    name: name.source(ctx.source_map).to_string(),
                },
            });
            (kind, Some(span))
        } else {
            ctx.diagnostics
                .push(Diagnostic::ImportAliasMustBeUpperIdent);
            (ImportKind::Error, Some(as_.span()))
        }
    } else if let Some(lparen) = ctx.matches(TokenKind::LeftParen) {
        match parse_list(
            ctx,
            parse_import_item,
            TokenKind::Comma,
            TokenKind::RightParen,
        ) {
            Ok(import_list) => {
                let rparen = ctx.matches(TokenKind::RightParen).unwrap();
                (
                    ImportKind::Items(import_list),
                    Some(lparen.span().span(rparen.span())),
                )
            }
            Err(diagnostic) => {
                ctx.diagnostics.push(diagnostic);
                (
                    ImportKind::Error,
                    ctx.peek()
                        .map(|tok| lparen.span().span(tok.span()))
                        .or_else(|| Some(lparen.span())),
                )
            }
        }
    } else if let Some(_) = ctx.matches_any(&[TokenKind::Newline, TokenKind::Semicolon]) {
        (ImportKind::Module, None)
    } else {
        let tok = ctx.next();
        ctx.diagnostics.push(Diagnostic::UnexpectedToken(tok));
        (ImportKind::Error, tok.map(|tok| tok.span()))
    };

    unimplemented!();
}

fn parse_module_path(ctx: &mut Context) -> QualifiedName {
    unimplemented!();
}

fn parse_list<T>(
    ctx: &mut Context,
    parse_fn: impl Fn(&mut Context) -> T,
    delimiter: TokenKind,
    close: TokenKind,
) -> Result<Vec<T>, Diagnostic> {
    let mut items = Vec::new();
    loop {
        if let Some(tok) = ctx.peek()
            && tok.kind() == close
        {
            break;
        }
        items.push(parse_fn(ctx));
        match ctx.peek() {
            Some(tok) if tok.kind() == close => break,
            Some(tok) if tok.kind() == TokenKind::Comma => continue,
            maybe_tok => return Err(Diagnostic::UnexpectedToken(maybe_tok)),
        }
    }
    Ok(items)
}

fn parse_import_item(ctx: &mut Context) -> ImportItem {
    unimplemented!();
}

fn parse_type_group(ctx: &mut Context) -> TopLevelItem {
    unimplemented!();
}

fn parse_fn_group(ctx: &mut Context) -> TopLevelItem {
    unimplemented!();
}

fn parse_top_level_stmt(ctx: &mut Context) -> TopLevelItem {
    unimplemented!();
}
