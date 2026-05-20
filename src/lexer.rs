use crate::source::{FileId, SourceMap, Span};

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TokenKind {
    And,
    AmpAmp,
    Arrow,
    As,
    Break,
    Char,
    Colon,
    Comma,
    Continue,
    Dot,
    Else,
    Eof,
    Equal,
    EqualEqual,
    Error,
    False,
    FatArrow,
    Float,
    Fn,
    Greater,
    GreaterEqual,
    Ident,
    If,
    Import,
    Int,
    LeftBrace,
    LeftBracket,
    LeftParen,
    Less,
    LessEqual,
    Let,
    Loop,
    Match,
    Minus,
    Newline,
    Not,
    NotEqual,
    Pipe,
    PipePipe,
    Plus,
    Return,
    RightBrace,
    RightBracket,
    RightParen,
    Semicolon,
    Slash,
    Star,
    String,
    True,
    Type,
    Var,
    While,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Token {
    kind: TokenKind,
    span: Span,
}

impl Token {
    pub fn kind(&self) -> TokenKind {
        self.kind
    }

    pub fn span(&self) -> Span {
        self.span
    }

    pub fn source<'src>(&self, source_map: &'src SourceMap) -> &'src str {
        self.span.source(source_map)
    }
}

#[derive(Debug)]
pub struct Lexer<'src> {
    file_id: FileId,
    source: &'src str,
    pos: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Diagnostic {
    UnexpectedToken,
    UnexpectedEof,
    EmptyCharLiteral,
    InvalidCharLiteral,
    UnterminatedCharLiteral,
    UnterminatedStringLiteral,
}

pub fn lex(source_map: &SourceMap, file_id: FileId) -> (Vec<Token>, Vec<Diagnostic>) {
    let mut tokens = Vec::new();
    let mut diagnostics = Vec::new();
    let mut lexer = Lexer::new(source_map, file_id);

    loop {
        let tok = lexer.next_token(&mut diagnostics);
        let is_eof = tok.kind == TokenKind::Eof;
        tokens.push(tok);
        if is_eof {
            break;
        }
    }

    (tokens, diagnostics)
}

impl<'src> Lexer<'src> {
    fn new(source_map: &'src SourceMap, file_id: FileId) -> Self {
        Lexer {
            file_id: file_id,
            source: source_map.file(file_id).source(),
            pos: 0,
        }
    }

    fn peek_n(&self, n: usize) -> Option<char> {
        self.source[self.pos..].chars().nth(n)
    }

    fn peek(&self) -> Option<char> {
        self.peek_n(0)
    }

    fn bump(&mut self) -> Option<char> {
        let ch = self.peek()?;
        self.pos += ch.len_utf8();
        Some(ch)
    }

    fn bump_if(&mut self, c: char) -> bool {
        match self.peek() {
            Some(c2) if c2 == c => {
                self.bump();
                true
            }
            _ => false,
        }
    }

    fn span(&self, start: usize) -> Span {
        Span::new(self.file_id, start, self.pos)
    }

    fn token(&self, start: usize, kind: TokenKind) -> Token {
        Token {
            kind,
            span: self.span(start),
        }
    }

    fn if_else(
        &mut self,
        start: usize,
        cond: char,
        then: TokenKind,
        otherwise: TokenKind,
    ) -> Token {
        let kind = if self.bump_if(cond) { then } else { otherwise };
        self.token(start, kind)
    }

    fn next_token(&mut self, diagnostics: &mut Vec<Diagnostic>) -> Token {
        loop {
            let start = self.pos;
            let Some(c) = self.bump() else {
                return self.token(self.pos, TokenKind::Eof);
            };

            return match c {
                // skip whitespace
                ' ' | '\t' | '\r' => continue,

                '(' => self.token(start, TokenKind::LeftParen),
                ')' => self.token(start, TokenKind::RightParen),
                '[' => self.token(start, TokenKind::LeftBracket),
                ']' => self.token(start, TokenKind::RightBracket),
                '{' => self.token(start, TokenKind::LeftBrace),
                '}' => self.token(start, TokenKind::RightBrace),
                '*' => self.token(start, TokenKind::Star),
                '.' => self.token(start, TokenKind::Dot),
                ',' => self.token(start, TokenKind::Comma),
                ':' => self.token(start, TokenKind::Colon),
                ';' => self.token(start, TokenKind::Semicolon),
                '+' => self.token(start, TokenKind::Plus),
                '\n' => self.token(start, TokenKind::Newline),

                '-' => self.if_else(start, '>', TokenKind::Arrow, TokenKind::Minus),
                '!' => self.if_else(start, '=', TokenKind::NotEqual, TokenKind::Not),
                '=' => self.if_else(start, '=', TokenKind::EqualEqual, TokenKind::Equal),
                '>' => self.if_else(start, '=', TokenKind::GreaterEqual, TokenKind::Greater),
                '<' => self.if_else(start, '=', TokenKind::LessEqual, TokenKind::Less),
                '|' => self.if_else(start, '|', TokenKind::PipePipe, TokenKind::Pipe),

                '&' if self.peek() == Some('&') => {
                    self.bump();
                    self.token(start, TokenKind::AmpAmp)
                }

                '/' => {
                    if self.bump_if('/') {
                        // line comment
                        loop {
                            let start = self.pos;
                            return match self.bump() {
                                None => self.token(start, TokenKind::Eof),
                                Some('\n') => self.token(start, TokenKind::Newline),
                                _ => continue,
                            };
                        }
                    }

                    self.token(start, TokenKind::Slash)
                }

                'a'..='z' | 'A'..='Z' | '_' => self.ident_or_kw(start, diagnostics),

                '\'' => self.char(start, diagnostics),
                '"' => self.string(start, diagnostics),
                '0'..='9' => self.number(start, diagnostics),

                _ => {
                    diagnostics.push(Diagnostic::UnexpectedToken);
                    self.token(start, TokenKind::Error)
                }
            };
        }
    }

    fn kw(&self, ident: &str) -> Option<TokenKind> {
        Some(match ident {
            "and" => TokenKind::And,
            "as" => TokenKind::As,
            "break" => TokenKind::Break,
            "continue" => TokenKind::Continue,
            "else" => TokenKind::Else,
            "false" => TokenKind::False,
            "fn" => TokenKind::Fn,
            "if" => TokenKind::If,
            "import" => TokenKind::Import,
            "let" => TokenKind::Let,
            "loop" => TokenKind::Loop,
            "match" => TokenKind::Match,
            "return" => TokenKind::Return,
            "true" => TokenKind::True,
            "type" => TokenKind::Type,
            "var" => TokenKind::Var,
            "while" => TokenKind::While,
            _ => return None,
        })
    }

    fn ident_or_kw(&mut self, start: usize, diagnostics: &mut Vec<Diagnostic>) -> Token {
        while let Some('a'..='z' | 'A'..='Z' | '0'..='9' | '_') = self.peek() {
            self.bump();
        }
        Token {
            kind: self
                .kw(&self.source[start..self.pos])
                .unwrap_or(TokenKind::Ident),
            span: self.span(start),
        }
    }

    fn char(&mut self, start: usize, diagnostics: &mut Vec<Diagnostic>) -> Token {
        if self.bump_if('\'') {
            diagnostics.push(Diagnostic::EmptyCharLiteral);
            return self.token(start, TokenKind::Error);
        }

        // FIXME: escape sequences
        let mut len = 0;
        loop {
            match self.bump() {
                Some('\'') => break,
                Some('\n') | None => {
                    diagnostics.push(Diagnostic::UnterminatedCharLiteral);
                    return self.token(start, TokenKind::Error);
                },
                _ => len += 1,
            }
        }

        if len != 1 {
            diagnostics.push(Diagnostic::InvalidCharLiteral);
            return self.token(start, TokenKind::Error);
        }

        self.token(start, TokenKind::Char)
    }

    fn string(&mut self, start: usize, diagnostics: &mut Vec<Diagnostic>) -> Token {
        // FIXME: escape sequences
        let diag = loop {
            match self.bump() {
                Some('"') => break None,
                Some('\n') | None => break Some(Diagnostic::UnterminatedStringLiteral),
                _ => {},
            }
        };

        let kind = if diag.is_some() { TokenKind::Error } else { TokenKind::String };
        self.token(start, kind)
    }

    fn number(&mut self, start: usize, diagnostics: &mut Vec<Diagnostic>) -> Token {
        // FIXME:
        // int binary octal hex
        // float scientific
        // any can have suffix

        while let Some('0'..='9') = self.peek() {
            self.bump();
        }

        self.token(start, TokenKind::Int)
    }
}
