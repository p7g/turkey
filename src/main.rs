mod lexer;
mod source;

fn main() {
    let mut args = std::env::args();

    let Some(file_name) = args.nth(1) else {
        eprintln!("usage: turkey <file>");
        std::process::exit(1);
    };

    let path = std::path::PathBuf::from(file_name);
    let source = match std::fs::read_to_string(&path) {
        Ok(source) => source,
        Err(err) => {
            eprintln!("{}", err);
            std::process::exit(1);
        }
    };

    let mut sourcemap = source::SourceMap::default();
    let file_id = sourcemap.register(Some(path), source);
    let (tokens, diagnostics) = lexer::lex(&sourcemap, file_id);

    let mut diagnostics = diagnostics.into_iter();
    for (i, token) in tokens.into_iter().enumerate() {
        if token.kind() == lexer::TokenKind::Error {
            let diag = diagnostics
                .next()
                .expect("Each error should have a diagnostic");
            eprintln!("{}: {:?} ({:?})", i, token.source(&sourcemap), diag);
        } else {
            println!("{}: {:?} ({:?})", i, token.source(&sourcemap), token);
        }
    }
}
