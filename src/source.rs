use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct FileId(usize);

#[derive(Debug)]
pub struct SourceFile {
    path: Option<PathBuf>,
    source: String,
    line_starts: Vec<usize>,
}

impl SourceFile {
    pub fn path(&self) -> Option<&Path> {
        self.path.as_deref()
    }

    pub fn source(&self) -> &str {
        &self.source
    }

    pub fn line_starts(&self) -> &[usize] {
        &self.line_starts
    }

    pub fn line_col(&self, offset: usize) -> Option<(usize, usize)> {
        if offset > self.source.len() {
            return None;
        }

        let line = match self.line_starts.binary_search(&offset) {
            Ok(line) => line,
            Err(next_line) => next_line.saturating_sub(1),
        };

        let col = offset - self.line_starts[line];
        Some((line, col))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Span {
    file_id: FileId,
    start: usize,
    end: usize,
}

impl Span {
    pub fn new(file_id: FileId, start: usize, end: usize) -> Self {
        debug_assert!(start <= end);
        Self {
            file_id,
            start,
            end,
        }
    }

    pub fn file_id(self) -> FileId {
        self.file_id
    }
    pub fn start(self) -> usize {
        self.start
    }
    pub fn end(self) -> usize {
        self.end
    }

    pub fn len(self) -> usize {
        self.end - self.start
    }

    pub fn is_empty(self) -> bool {
        self.start == self.end
    }

    pub fn source(self, source_map: &SourceMap) -> &str {
        let file = source_map.file(self.file_id);
        &file.source()[self.start..self.end]
    }

    pub fn span(self, to: Span) -> Span {
        debug_assert!(self.file_id == to.file_id);
        debug_assert!(self.start <= to.start);
        debug_assert!(self.end <= to.end);
        Span {
            file_id: self.file_id,
            start: self.start,
            end: self.end,
        }
    }
}

#[derive(Debug, Default)]
pub struct SourceMap {
    sources: Vec<SourceFile>,
}

impl SourceMap {
    pub fn file(&self, id: FileId) -> &SourceFile {
        &self.sources[id.0]
    }

    pub fn register(&mut self, path: Option<PathBuf>, source: String) -> FileId {
        let id = FileId(self.sources.len());
        let line_starts = compute_line_starts(&source);
        self.sources.push(SourceFile {
            path,
            source,
            line_starts,
        });
        id
    }
}

fn compute_line_starts(source: &str) -> Vec<usize> {
    let mut starts = vec![0];

    for (idx, byte) in source.bytes().enumerate() {
        if byte == b'\n' {
            starts.push(idx + 1);
        }
    }

    starts
}
