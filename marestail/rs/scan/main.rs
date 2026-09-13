use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};
use std::str::FromStr;

use proc_macro2::{Delimiter, LineColumn, Span, TokenStream, TokenTree};
use serde_json::{json, Value};
use syn::spanned::Spanned;
use syn::visit::{self, Visit};

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        usage();
    }
    let mode = args.remove(0);
    let root = if args.first().map(String::as_str) == Some("--root") {
        args.remove(0);
        Some(PathBuf::from(args.remove(0)))
    } else {
        None
    };
    let files: Vec<PathBuf> = args.iter().map(PathBuf::from).collect();
    let output = match mode.as_str() {
        "comments" => Value::Array(files.iter().flat_map(|f| comments(f)).collect()),
        "complexity" => Value::Array(files.iter().flat_map(|f| complexity(f)).collect()),
        "depth" => Value::Array(files.iter().map(|f| depth(f)).collect()),
        "deps" => deps(root.as_deref().unwrap_or(Path::new(".")), &files),
        "dead" => {
            let split = files.iter().position(|f| f.as_os_str() == "--uses").unwrap_or(files.len());
            Value::Array(dead(&files[..split], files.get(split + 1..).unwrap_or(&[])))
        }
        _ => usage(),
    };
    print!("{output}");
}

fn usage() -> ! {
    eprintln!("usage: marestail-rs-scan comments|complexity|depth|deps|dead [--root DIR] FILE... [--uses FILE...]");
    std::process::exit(2);
}

fn read(file: &Path) -> String {
    let text = std::fs::read_to_string(file).unwrap_or_else(|error| {
        eprintln!("{}: {error}", file.display());
        std::process::exit(1);
    });
    if text.starts_with("#!") && !text.starts_with("#![") {
        let end = text.find('\n').unwrap_or(text.len());
        return " ".repeat(end) + &text[end..];
    }
    text
}

fn parse(file: &Path) -> syn::File {
    syn::parse_file(&read(file)).unwrap_or_else(|error| {
        let start = error.span().start();
        eprintln!("{}:{}: {error}", file.display(), start.line);
        std::process::exit(1);
    })
}

fn tokens(file: &Path, text: &str) -> TokenStream {
    TokenStream::from_str(text).unwrap_or_else(|error| {
        eprintln!("{}:{}: {error}", file.display(), error.span().start().line);
        std::process::exit(1);
    })
}

fn line(span: Span) -> usize {
    span.start().line
}

struct Source {
    chars: Vec<char>,
    line_starts: Vec<usize>,
}

impl Source {
    fn new(text: &str) -> Self {
        let chars: Vec<char> = text.chars().collect();
        let mut line_starts = vec![0];
        line_starts.extend(chars.iter().enumerate().filter(|(_, c)| **c == '\n').map(|(i, _)| i + 1));
        Source { chars, line_starts }
    }

    fn offset(&self, at: LineColumn) -> usize {
        self.line_starts.get(at.line.saturating_sub(1)).map_or(self.chars.len(), |start| start + at.column)
    }

    fn line_of(&self, offset: usize) -> usize {
        self.line_starts.partition_point(|start| *start <= offset)
    }

    fn starts_with(&self, offset: usize, prefix: &str) -> bool {
        prefix.chars().enumerate().all(|(i, c)| self.chars.get(offset + i) == Some(&c))
    }

    fn snippet(&self, offset: usize) -> String {
        self.chars[offset..].iter().take_while(|c| **c != '\n').take(80).collect::<String>().trim().to_string()
    }
}

fn flatten(stream: TokenStream, spans: &mut Vec<(LineColumn, LineColumn)>, trees: &mut Vec<Vec<TokenTree>>) {
    let level: Vec<TokenTree> = stream.into_iter().collect();
    for tree in &level {
        match tree {
            TokenTree::Group(group) => {
                if group.delimiter() != Delimiter::None {
                    spans.push((group.span_open().start(), group.span_open().end()));
                    spans.push((group.span_close().start(), group.span_close().end()));
                }
                flatten(group.stream(), spans, trees);
            }
            other => spans.push((other.span().start(), other.span().end())),
        }
    }
    trees.push(level);
}

fn comments(file: &Path) -> Vec<Value> {
    let text = read(file);
    let source = Source::new(&text);
    let mut spans = Vec::new();
    let mut trees = Vec::new();
    flatten(tokens(file, &text), &mut spans, &mut trees);
    let mut ranges: Vec<(usize, usize)> = spans.iter().map(|(start, end)| (source.offset(*start), source.offset(*end))).collect();
    ranges.sort_unstable();
    let mut hits = Vec::new();
    let mut cursor = 0;
    for (start, end) in ranges.iter().copied().chain(std::iter::once((source.chars.len(), source.chars.len()))) {
        if start > cursor {
            gap_comments(&source, cursor, start, &mut hits);
        }
        cursor = cursor.max(end);
    }
    for level in &trees {
        doc_attributes(&source, level, &mut hits);
    }
    hits.sort_unstable();
    hits.dedup();
    hits.into_iter().map(|(line, text)| json!({"file": file, "line": line, "text": text})).collect()
}

fn gap_comments(source: &Source, from: usize, to: usize, hits: &mut Vec<(usize, String)>) {
    let mut at = from;
    while at + 1 < to {
        if source.starts_with(at, "//") {
            hits.push((source.line_of(at), source.snippet(at)));
            at = (at..to).find(|i| source.chars[*i] == '\n').unwrap_or(to);
        } else if source.starts_with(at, "/*") {
            hits.push((source.line_of(at), source.snippet(at)));
            at = block_end(source, at, to);
        } else {
            at += 1;
        }
    }
}

fn block_end(source: &Source, from: usize, to: usize) -> usize {
    let mut depth = 0;
    let mut at = from;
    while at + 1 < to {
        if source.starts_with(at, "/*") {
            depth += 1;
            at += 2;
        } else if source.starts_with(at, "*/") {
            depth -= 1;
            at += 2;
            if depth == 0 {
                return at;
            }
        } else {
            at += 1;
        }
    }
    to
}

fn doc_attributes(source: &Source, level: &[TokenTree], hits: &mut Vec<(usize, String)>) {
    for (index, tree) in level.iter().enumerate() {
        let TokenTree::Punct(punct) = tree else { continue };
        if punct.as_char() != '#' {
            continue;
        }
        let offset = source.offset(punct.span().start());
        if ["///", "//!", "/**", "/*!"].iter().any(|prefix| source.starts_with(offset, prefix)) {
            hits.push((line(punct.span()), source.snippet(offset)));
            continue;
        }
        if explicit_doc(&level[index + 1..]) {
            hits.push((line(punct.span()), source.snippet(offset)));
        }
    }
}

fn explicit_doc(rest: &[TokenTree]) -> bool {
    let rest = match rest.first() {
        Some(TokenTree::Punct(bang)) if bang.as_char() == '!' => &rest[1..],
        _ => rest,
    };
    let Some(TokenTree::Group(group)) = rest.first() else { return false };
    group.delimiter() == Delimiter::Bracket && matches!(group.stream().into_iter().next(), Some(TokenTree::Ident(ident)) if ident == "doc")
}

fn is_test(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|attr| {
        let path = attr.path();
        if path.segments.last().is_some_and(|segment| segment.ident == "test") {
            return true;
        }
        path.is_ident("cfg") && attr.parse_args::<syn::Ident>().is_ok_and(|ident| ident == "test")
    })
}

fn type_name(ty: &syn::Type) -> String {
    match ty {
        syn::Type::Path(path) => path.path.segments.last().map_or("?".into(), |segment| segment.ident.to_string()),
        syn::Type::Reference(reference) => type_name(&reference.elem),
        _ => "?".into(),
    }
}

struct Function<'a> {
    name: String,
    sig: &'a syn::Signature,
    block: &'a syn::Block,
    trait_impl: bool,
}

fn functions<'a>(items: &'a [syn::Item], prefix: &str, found: &mut Vec<Function<'a>>) {
    for item in items {
        match item {
            syn::Item::Fn(function) if !is_test(&function.attrs) => found.push(Function {
                name: format!("{prefix}{}", function.sig.ident),
                sig: &function.sig,
                block: &function.block,
                trait_impl: false,
            }),
            syn::Item::Impl(block) if !is_test(&block.attrs) => {
                let owner = type_name(&block.self_ty);
                for member in &block.items {
                    if let syn::ImplItem::Fn(method) = member {
                        if !is_test(&method.attrs) {
                            found.push(Function {
                                name: format!("{prefix}{owner}::{}", method.sig.ident),
                                sig: &method.sig,
                                block: &method.block,
                                trait_impl: block.trait_.is_some(),
                            });
                        }
                    }
                }
            }
            syn::Item::Trait(definition) if !is_test(&definition.attrs) => {
                for member in &definition.items {
                    if let syn::TraitItem::Fn(syn::TraitItemFn { sig, default: Some(block), .. }) = member {
                        found.push(Function { name: format!("{prefix}{}::{}", definition.ident, sig.ident), sig, block, trait_impl: false });
                    }
                }
            }
            syn::Item::Mod(module) if !is_test(&module.attrs) => {
                if let Some((_, content)) = &module.content {
                    functions(content, &format!("{prefix}{}::", module.ident), found);
                }
            }
            _ => {}
        }
    }
}

#[derive(Default)]
struct Complexity {
    count: usize,
}

impl<'ast> Visit<'ast> for Complexity {
    fn visit_item(&mut self, _: &'ast syn::Item) {}

    fn visit_expr_if(&mut self, node: &'ast syn::ExprIf) {
        self.count += 1;
        visit::visit_expr_if(self, node);
    }

    fn visit_expr_while(&mut self, node: &'ast syn::ExprWhile) {
        self.count += 1;
        visit::visit_expr_while(self, node);
    }

    fn visit_expr_for_loop(&mut self, node: &'ast syn::ExprForLoop) {
        self.count += 1;
        visit::visit_expr_for_loop(self, node);
    }

    fn visit_expr_match(&mut self, node: &'ast syn::ExprMatch) {
        self.count += node.arms.len().saturating_sub(1) + node.arms.iter().filter(|arm| arm.guard.is_some()).count();
        visit::visit_expr_match(self, node);
    }

    fn visit_expr_binary(&mut self, node: &'ast syn::ExprBinary) {
        if matches!(node.op, syn::BinOp::And(_) | syn::BinOp::Or(_)) {
            self.count += 1;
        }
        visit::visit_expr_binary(self, node);
    }

    fn visit_local(&mut self, node: &'ast syn::Local) {
        if node.init.as_ref().is_some_and(|init| init.diverge.is_some()) {
            self.count += 1;
        }
        visit::visit_local(self, node);
    }
}

fn complexity(file: &Path) -> Vec<Value> {
    let tree = parse(file);
    let mut found = Vec::new();
    functions(&tree.items, "", &mut found);
    found
        .iter()
        .map(|function| {
            let mut counter = Complexity::default();
            counter.visit_block(function.block);
            json!({
                "file": file,
                "name": function.name,
                "line": line(function.sig.ident.span()),
                "end": function.block.brace_token.span.close().end().line,
                "complexity": 1 + counter.count,
            })
        })
        .collect()
}

#[derive(Default)]
struct Statements {
    count: usize,
}

impl<'ast> Visit<'ast> for Statements {
    fn visit_stmt(&mut self, node: &'ast syn::Stmt) {
        self.count += 1;
        visit::visit_stmt(self, node);
    }

    fn visit_item(&mut self, node: &'ast syn::Item) {
        self.count += 1;
        visit::visit_item(self, node);
    }
}

fn public_items(items: &[syn::Item], names: &mut Vec<String>) {
    for item in items {
        let (vis, ident) = match item {
            syn::Item::Fn(i) => (&i.vis, i.sig.ident.to_string()),
            syn::Item::Struct(i) => (&i.vis, i.ident.to_string()),
            syn::Item::Enum(i) => (&i.vis, i.ident.to_string()),
            syn::Item::Trait(i) => (&i.vis, i.ident.to_string()),
            syn::Item::Const(i) => (&i.vis, i.ident.to_string()),
            syn::Item::Static(i) => (&i.vis, i.ident.to_string()),
            syn::Item::Type(i) => (&i.vis, i.ident.to_string()),
            syn::Item::Impl(block) if block.trait_.is_none() => {
                let owner = type_name(&block.self_ty);
                for member in &block.items {
                    if let syn::ImplItem::Fn(method) = member {
                        if !matches!(method.vis, syn::Visibility::Inherited) {
                            names.push(format!("{owner}::{}", method.sig.ident));
                        }
                    }
                }
                continue;
            }
            _ => continue,
        };
        if !matches!(vis, syn::Visibility::Inherited) {
            names.push(ident);
        }
    }
}

fn depth(file: &Path) -> Value {
    let tree = parse(file);
    let mut public = Vec::new();
    public_items(&tree.items, &mut public);
    let mut statements = Statements::default();
    statements.visit_file(&tree);
    let mut found = Vec::new();
    functions(&tree.items, "", &mut found);
    let pass_throughs: Vec<Value> = found
        .iter()
        .filter(|function| !function.trait_impl)
        .filter_map(|function| forwarded(function).map(|target| json!({"line": line(function.sig.ident.span()), "name": function.name, "target": target})))
        .collect();
    json!({"file": file, "public": public, "statements": statements.count.max(1), "pass_throughs": pass_throughs})
}

fn forwarded(function: &Function) -> Option<String> {
    let params: Option<Vec<String>> = function
        .sig
        .inputs
        .iter()
        .filter_map(|input| match input {
            syn::FnArg::Receiver(_) => None,
            syn::FnArg::Typed(typed) => Some(match &*typed.pat {
                syn::Pat::Ident(ident) => Some(ident.ident.to_string()),
                _ => None,
            }),
        })
        .collect();
    let params = params?;
    if params.is_empty() || function.block.stmts.len() != 1 {
        return None;
    }
    let expr = match &function.block.stmts[0] {
        syn::Stmt::Expr(syn::Expr::Return(syn::ExprReturn { expr: Some(inner), .. }), _) => &**inner,
        syn::Stmt::Expr(expr, None) => expr,
        _ => return None,
    };
    let (target, args) = match expr {
        syn::Expr::Call(call) => (quote_path(&call.func), &call.args),
        syn::Expr::MethodCall(call) => (format!("{}.{}", quote_path(&call.receiver), call.method), &call.args),
        _ => return None,
    };
    let passed: Vec<String> = args
        .iter()
        .filter_map(|arg| match arg {
            syn::Expr::Path(path) => path.path.get_ident().map(ToString::to_string),
            _ => None,
        })
        .collect();
    (passed.len() == args.len() && passed == params).then_some(target)
}

fn quote_path(expr: &syn::Expr) -> String {
    match expr {
        syn::Expr::Path(path) => path.path.segments.iter().map(|s| s.ident.to_string()).collect::<Vec<_>>().join("::"),
        syn::Expr::Field(field) => match &field.member {
            syn::Member::Named(name) => format!("{}.{name}", quote_path(&field.base)),
            syn::Member::Unnamed(index) => format!("{}.{}", quote_path(&field.base), index.index),
        },
        _ => "?".into(),
    }
}

fn crate_dir(root: &Path, file: &Path) -> PathBuf {
    file.ancestors()
        .skip(1)
        .take_while(|dir| dir.starts_with(root))
        .find(|dir| dir.join("Cargo.toml").is_file())
        .map_or_else(|| root.to_path_buf(), Path::to_path_buf)
}

fn module_path(crate_root: &Path, file: &Path) -> Vec<String> {
    let relative = file.strip_prefix(crate_root.join("src")).unwrap_or(file);
    let mut parts: Vec<String> = relative.iter().map(|part| part.to_string_lossy().to_string()).collect();
    let last = parts.pop().unwrap_or_default();
    match last.as_str() {
        "lib.rs" | "main.rs" if parts.is_empty() => {}
        "mod.rs" => {}
        _ => parts.push(last.trim_end_matches(".rs").to_string()),
    }
    parts
}

fn package_name(crate_root: &Path) -> Option<String> {
    let manifest = std::fs::read_to_string(crate_root.join("Cargo.toml")).ok()?;
    let package = manifest.split("[package]").nth(1)?;
    let line = package.lines().take_while(|l| !l.trim_start().starts_with('[')).find(|l| l.trim_start().starts_with("name"))?;
    Some(line.split('"').nth(1)?.replace('-', "_"))
}

#[derive(Default)]
struct Paths {
    found: Vec<(Vec<String>, usize)>,
}

impl Paths {
    fn use_tree(&mut self, tree: &syn::UseTree, prefix: &mut Vec<String>, at: usize) {
        match tree {
            syn::UseTree::Path(path) => {
                prefix.push(path.ident.to_string());
                self.use_tree(&path.tree, prefix, at);
                prefix.pop();
            }
            syn::UseTree::Name(name) => self.found.push(([prefix.clone(), vec![name.ident.to_string()]].concat(), at)),
            syn::UseTree::Rename(rename) => self.found.push(([prefix.clone(), vec![rename.ident.to_string()]].concat(), at)),
            syn::UseTree::Glob(_) => self.found.push((prefix.clone(), at)),
            syn::UseTree::Group(group) => group.items.iter().for_each(|item| self.use_tree(item, prefix, at)),
        }
    }
}

impl<'ast> Visit<'ast> for Paths {
    fn visit_item_use(&mut self, node: &'ast syn::ItemUse) {
        self.use_tree(&node.tree, &mut Vec::new(), line(node.span()));
    }

    fn visit_path(&mut self, node: &'ast syn::Path) {
        let segments: Vec<String> = node.segments.iter().map(|s| s.ident.to_string()).collect();
        if segments.len() > 1 {
            self.found.push((segments, line(node.span())));
        }
        visit::visit_path(self, node);
    }

    fn visit_item_mod(&mut self, node: &'ast syn::ItemMod) {
        if !is_test(&node.attrs) {
            visit::visit_item_mod(self, node);
        }
    }
}

type ModuleIndex = HashMap<(PathBuf, Vec<String>), PathBuf>;

fn resolve(index: &ModuleIndex, crates: &HashMap<String, PathBuf>, home: &(PathBuf, Vec<String>), segments: &[String]) -> Option<(PathBuf, usize)> {
    let (crate_root, module) = home;
    let first = segments.first()?.as_str();
    let (target_crate, mut absolute, rest) = match first {
        "crate" => (crate_root.clone(), Vec::new(), &segments[1..]),
        "self" => (crate_root.clone(), module.clone(), &segments[1..]),
        "super" => {
            let supers = segments.iter().take_while(|s| *s == "super").count();
            (crate_root.clone(), module[..module.len().saturating_sub(supers)].to_vec(), &segments[supers..])
        }
        name if crates.contains_key(name) => (crates[name].clone(), Vec::new(), &segments[1..]),
        name if index.contains_key(&(crate_root.clone(), [module.clone(), vec![name.to_string()]].concat())) => (crate_root.clone(), module.clone(), segments),
        _ => return None,
    };
    let base = absolute.len();
    absolute.extend(rest.iter().cloned());
    (base..=absolute.len()).rev().find_map(|len| index.get(&(target_crate.clone(), absolute[..len].to_vec())).map(|file| (file.clone(), len)))
}

fn deps(root: &Path, files: &[PathBuf]) -> Value {
    let homes: Vec<(PathBuf, Vec<String>)> = files
        .iter()
        .map(|file| {
            let crate_root = crate_dir(root, file);
            let module = module_path(&crate_root, file);
            (crate_root, module)
        })
        .collect();
    let index: ModuleIndex = homes.iter().cloned().zip(files.iter().cloned()).collect();
    let crates: HashMap<String, PathBuf> = homes
        .iter()
        .filter(|(crate_root, _)| crate_root.join("src/lib.rs").is_file())
        .filter_map(|(crate_root, _)| package_name(crate_root).map(|name| (name, crate_root.clone())))
        .collect();
    let mut edges = Vec::new();
    for (file, home) in files.iter().zip(&homes) {
        let mut paths = Paths::default();
        paths.visit_file(&parse(file));
        let mut seen: BTreeMap<PathBuf, (usize, String)> = BTreeMap::new();
        for (segments, at) in paths.found {
            if let Some((target, _)) = resolve(&index, &crates, home, &segments) {
                if &target != file {
                    seen.entry(target).or_insert((at, segments.join("::")));
                }
            }
        }
        edges.extend(seen.into_iter().map(|(target, (at, symbol))| json!({"from": file, "to": target, "symbol": symbol, "line": at})));
    }
    Value::Array(edges)
}

fn count_idents(stream: TokenStream, counts: &mut HashMap<String, usize>) {
    for tree in stream {
        match tree {
            TokenTree::Ident(ident) => *counts.entry(ident.to_string().trim_start_matches("r#").to_string()).or_default() += 1,
            TokenTree::Group(group) => count_idents(group.stream(), counts),
            _ => {}
        }
    }
}

fn exported(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|attr| attr.path().is_ident("no_mangle") || attr.path().is_ident("export_name") || attr.meta.path().is_ident("unsafe"))
}

fn definitions(items: &[syn::Item], file: &Path, found: &mut Vec<(PathBuf, usize, &'static str, String)>) {
    for item in items {
        let (vis, ident, kind, attrs) = match item {
            syn::Item::Fn(i) => (&i.vis, &i.sig.ident, "function", &i.attrs),
            syn::Item::Struct(i) => (&i.vis, &i.ident, "struct", &i.attrs),
            syn::Item::Enum(i) => (&i.vis, &i.ident, "enum", &i.attrs),
            syn::Item::Trait(i) => (&i.vis, &i.ident, "trait", &i.attrs),
            syn::Item::Const(i) => (&i.vis, &i.ident, "const", &i.attrs),
            syn::Item::Static(i) => (&i.vis, &i.ident, "static", &i.attrs),
            syn::Item::Type(i) => (&i.vis, &i.ident, "type", &i.attrs),
            syn::Item::Impl(block) if block.trait_.is_none() && !is_test(&block.attrs) => {
                for member in &block.items {
                    if let syn::ImplItem::Fn(method) = member {
                        if !matches!(method.vis, syn::Visibility::Inherited) && !is_test(&method.attrs) && !exported(&method.attrs) {
                            found.push((file.to_path_buf(), line(method.sig.ident.span()), "method", method.sig.ident.to_string()));
                        }
                    }
                }
                continue;
            }
            syn::Item::Mod(module) if !is_test(&module.attrs) => {
                if let Some((_, content)) = &module.content {
                    definitions(content, file, found);
                }
                continue;
            }
            _ => continue,
        };
        if !matches!(vis, syn::Visibility::Inherited) && !is_test(attrs) && !exported(attrs) && ident != "main" {
            found.push((file.to_path_buf(), line(ident.span()), kind, ident.to_string()));
        }
    }
}

fn dead(files: &[PathBuf], uses: &[PathBuf]) -> Vec<Value> {
    let mut counts = HashMap::new();
    for file in uses {
        count_idents(tokens(file, &read(file)), &mut counts);
    }
    let mut found = Vec::new();
    for file in files {
        let text = read(file);
        count_idents(tokens(file, &text), &mut counts);
        if file.file_name().is_some_and(|name| name == "lib.rs") {
            continue;
        }
        let tree = parse(file);
        definitions(&tree.items, file, &mut found);
    }
    let mut defined: HashMap<&str, usize> = HashMap::new();
    for (_, _, _, name) in &found {
        *defined.entry(name.as_str()).or_default() += 1;
    }
    found
        .iter()
        .filter(|(_, _, _, name)| counts.get(name).copied().unwrap_or(0) <= defined[name.as_str()])
        .map(|(file, at, kind, name)| json!({"file": file, "line": at, "kind": kind, "name": name}))
        .collect()
}
