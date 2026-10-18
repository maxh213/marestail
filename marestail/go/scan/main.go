package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/scanner"
	"go/token"
	"os"
	"regexp"
	"strconv"
	"strings"
)

const usageText = "usage: marestail-go-scan comments|complexity|depth|imports|dead FILE... [--uses FILE...]"

var generatedLine = regexp.MustCompile(`^// Code generated .* DO NOT EDIT\.$`)

type parsed struct {
	path string
	file *ast.File
	fset *token.FileSet
}

type commentRec struct {
	File string `json:"file"`
	Line int    `json:"line"`
	Text string `json:"text"`
}

type complexRec struct {
	File       string `json:"file"`
	Name       string `json:"name"`
	Line       int    `json:"line"`
	End        int    `json:"end"`
	Complexity int    `json:"complexity"`
}

type passRec struct {
	Line   int    `json:"line"`
	Name   string `json:"name"`
	Target string `json:"target"`
}

type depthRec struct {
	File         string    `json:"file"`
	Public       []string  `json:"public"`
	Statements   int       `json:"statements"`
	PassThroughs []passRec `json:"pass_throughs"`
}

type importRec struct {
	File string `json:"file"`
	Line int    `json:"line"`
	Path string `json:"path"`
}

type deadRec struct {
	File string `json:"file"`
	Line int    `json:"line"`
	Kind string `json:"kind"`
	Name string `json:"name"`
}

func main() {
	os.Exit(execute(os.Args[1:]))
}

func execute(args []string) int {
	mode, files, uses, ok := split(args)
	if !ok {
		writeLine(usageText)
		return 2
	}
	parsedFiles, err := parseAll(files)
	if err != nil {
		writeParse(err)
		return 1
	}
	parsedUses, err := parseAll(uses)
	if err != nil {
		writeParse(err)
		return 1
	}
	return emit(dispatch(mode, parsedFiles, parsedUses))
}

func split(args []string) (string, []string, []string, bool) {
	if len(args) < 2 || !knownMode(args[0]) {
		return "", nil, nil, false
	}
	files, uses := cut(args[1:])
	if len(files) == 0 {
		return "", nil, nil, false
	}
	return args[0], files, uses, true
}

func knownMode(mode string) bool {
	switch mode {
	case "comments", "complexity", "depth", "imports", "dead":
		return true
	default:
		return false
	}
}

func cut(args []string) ([]string, []string) {
	at := indexOf(args, "--uses")
	if at < 0 {
		return args, nil
	}
	return args[:at], args[at+1:]
}

func indexOf(args []string, want string) int {
	for i, arg := range args {
		if arg == want {
			return i
		}
	}
	return -1
}

func parseAll(paths []string) ([]parsed, error) {
	found := []parsed{}
	fset := token.NewFileSet()
	for _, path := range paths {
		file, err := parser.ParseFile(fset, path, nil, parser.ParseComments|parser.SkipObjectResolution)
		if err != nil {
			return nil, err
		}
		found = append(found, parsed{path: path, file: file, fset: fset})
	}
	return found, nil
}

func writeParse(err error) {
	var list scanner.ErrorList
	if errors.As(err, &list) {
		writeList(list)
		return
	}
	writeLine(err.Error())
}

func writeList(list scanner.ErrorList) {
	for _, item := range list {
		writeLine(fmt.Sprintf("%s:%d: %s", item.Pos.Filename, item.Pos.Line, item.Msg))
	}
}

func writeLine(text string) {
	_, _ = fmt.Fprintln(os.Stderr, text)
}

func emit(value any) int {
	if err := json.NewEncoder(os.Stdout).Encode(value); err != nil {
		writeLine(err.Error())
		return 1
	}
	return 0
}

func dispatch(mode string, files []parsed, uses []parsed) any {
	switch mode {
	case "comments":
		return comments(files)
	case "complexity":
		return complexities(files)
	case "depth":
		return depths(files)
	case "imports":
		return imports(files)
	default:
		return deadOf(files, uses)
	}
}

func comments(files []parsed) []commentRec {
	found := []commentRec{}
	for _, item := range files {
		found = append(found, commentsOf(item)...)
	}
	return found
}

func commentsOf(item parsed) []commentRec {
	if generated(item.fset, item.file) {
		return nil
	}
	skip := cgoGroups(item.file)
	found := []commentRec{}
	for _, group := range item.file.Comments {
		if !skip[group.Pos()] {
			found = append(found, groupComments(item, group)...)
		}
	}
	return found
}

func groupComments(item parsed, group *ast.CommentGroup) []commentRec {
	found := []commentRec{}
	for _, comment := range group.List {
		rec, ok := oneComment(item, comment)
		if ok {
			found = append(found, rec)
		}
	}
	return found
}

func oneComment(item parsed, comment *ast.Comment) (commentRec, bool) {
	if directive(comment.Text) {
		return commentRec{}, false
	}
	return commentRec{File: item.path, Line: item.fset.Position(comment.Pos()).Line, Text: clip(comment.Text)}, true
}

func directive(text string) bool {
	return strings.HasPrefix(text, "//go:") || strings.HasPrefix(text, "//line ") || strings.HasPrefix(text, "//export ") || strings.HasPrefix(text, "//extern ")
}

func clip(text string) string {
	line, _, _ := strings.Cut(text, "\n")
	line = strings.TrimSpace(line)
	if len(line) > 80 {
		return line[:80]
	}
	return line
}

func cgoGroups(file *ast.File) map[token.Pos]bool {
	found := map[token.Pos]bool{}
	for _, decl := range file.Decls {
		markImport(decl, found)
	}
	return found
}

func markImport(decl ast.Decl, found map[token.Pos]bool) {
	gen, ok := decl.(*ast.GenDecl)
	if !ok || gen.Tok != token.IMPORT || !importsC(gen) {
		return
	}
	markDoc(gen.Doc, found)
	markSpecs(gen, found)
}

func importsC(gen *ast.GenDecl) bool {
	for _, spec := range gen.Specs {
		if isC(spec) {
			return true
		}
	}
	return false
}

func isC(spec ast.Spec) bool {
	imp, ok := spec.(*ast.ImportSpec)
	return ok && importPath(imp) == "C"
}

func markSpecs(gen *ast.GenDecl, found map[token.Pos]bool) {
	for _, spec := range gen.Specs {
		imp, ok := spec.(*ast.ImportSpec)
		if ok && importPath(imp) == "C" {
			markDoc(imp.Doc, found)
		}
	}
}

func markDoc(group *ast.CommentGroup, found map[token.Pos]bool) {
	if group != nil {
		found[group.Pos()] = true
	}
}

func importPath(spec *ast.ImportSpec) string {
	value, err := strconv.Unquote(spec.Path.Value)
	if err != nil {
		return spec.Path.Value
	}
	return value
}

func generated(fset *token.FileSet, file *ast.File) bool {
	limit := fset.Position(file.Package).Line
	for _, group := range file.Comments {
		if groupGenerated(fset, group, limit) {
			return true
		}
	}
	return false
}

func groupGenerated(fset *token.FileSet, group *ast.CommentGroup, limit int) bool {
	for _, comment := range group.List {
		if fset.Position(comment.Pos()).Line < limit && generatedText(comment.Text) {
			return true
		}
	}
	return false
}

func generatedText(text string) bool {
	for _, line := range strings.Split(text, "\n") {
		if generatedLine.MatchString(strings.TrimSpace(line)) {
			return true
		}
	}
	return false
}

func complexities(files []parsed) []complexRec {
	found := []complexRec{}
	for _, item := range files {
		found = append(found, complexityOf(item)...)
	}
	return found
}

func complexityOf(item parsed) []complexRec {
	if generated(item.fset, item.file) {
		return nil
	}
	found := []complexRec{}
	for _, decl := range item.file.Decls {
		rec, ok := funcComplexity(item, decl)
		if ok {
			found = append(found, rec)
		}
	}
	return found
}

func funcComplexity(item parsed, decl ast.Decl) (complexRec, bool) {
	fn, ok := decl.(*ast.FuncDecl)
	if !ok || fn.Body == nil {
		return complexRec{}, false
	}
	return complexRec{
		File:       item.path,
		Name:       funcName(fn),
		Line:       item.fset.Position(fn.Name.Pos()).Line,
		End:        item.fset.Position(fn.Body.Rbrace).Line,
		Complexity: score(fn.Body),
	}, true
}

func funcName(fn *ast.FuncDecl) string {
	if fn.Recv == nil || len(fn.Recv.List) == 0 {
		return fn.Name.Name
	}
	return recvType(fn.Recv.List[0].Type) + "." + fn.Name.Name
}

func recvType(expr ast.Expr) string {
	switch node := expr.(type) {
	case *ast.Ident:
		return node.Name
	case *ast.StarExpr:
		return recvType(node.X)
	case *ast.IndexExpr:
		return recvType(node.X)
	case *ast.IndexListExpr:
		return recvType(node.X)
	case *ast.ParenExpr:
		return recvType(node.X)
	default:
		return ""
	}
}

func score(body *ast.BlockStmt) int {
	total := 1
	ast.Inspect(body, func(node ast.Node) bool {
		total += weight(node)
		return true
	})
	return total
}

func weight(node ast.Node) int {
	if node == nil {
		return 0
	}
	return flowWeight(node) + logicWeight(node)
}

func flowWeight(node ast.Node) int {
	switch node.(type) {
	case *ast.IfStmt, *ast.ForStmt, *ast.RangeStmt:
		return 1
	default:
		return 0
	}
}

func logicWeight(node ast.Node) int {
	if clause, ok := node.(*ast.CaseClause); ok {
		return listed(len(clause.List))
	}
	if clause, ok := node.(*ast.CommClause); ok {
		return present(clause.Comm)
	}
	return binaryWeight(node)
}

func listed(count int) int {
	if count == 0 {
		return 0
	}
	return 1
}

func present(node ast.Node) int {
	if node == nil {
		return 0
	}
	return 1
}

func binaryWeight(node ast.Node) int {
	expr, ok := node.(*ast.BinaryExpr)
	if !ok {
		return 0
	}
	if expr.Op == token.LAND || expr.Op == token.LOR {
		return 1
	}
	return 0
}

func depths(files []parsed) []depthRec {
	found := []depthRec{}
	for _, item := range files {
		rec, ok := oneDepth(item)
		if ok {
			found = append(found, rec)
		}
	}
	return found
}

func oneDepth(item parsed) (depthRec, bool) {
	if generated(item.fset, item.file) {
		return depthRec{}, false
	}
	return depthRec{
		File:         item.path,
		Public:       publics(item.file),
		Statements:   statementCount(item.file),
		PassThroughs: passThroughs(item),
	}, true
}

func publics(file *ast.File) []string {
	found := []string{}
	for _, decl := range file.Decls {
		found = append(found, declNames(decl)...)
	}
	return found
}

func declNames(decl ast.Decl) []string {
	switch node := decl.(type) {
	case *ast.FuncDecl:
		return funcNames(node)
	case *ast.GenDecl:
		return genNames(node)
	default:
		return nil
	}
}

func funcNames(fn *ast.FuncDecl) []string {
	name := exportedFunc(fn)
	if name == "" {
		return nil
	}
	return []string{name}
}

func exportedFunc(fn *ast.FuncDecl) string {
	if !ast.IsExported(fn.Name.Name) {
		return ""
	}
	if fn.Recv == nil || len(fn.Recv.List) == 0 {
		return fn.Name.Name
	}
	return recvType(fn.Recv.List[0].Type) + "." + fn.Name.Name
}

func genNames(gen *ast.GenDecl) []string {
	found := []string{}
	for _, spec := range gen.Specs {
		found = append(found, specNames(spec)...)
	}
	return found
}

func specNames(spec ast.Spec) []string {
	switch node := spec.(type) {
	case *ast.TypeSpec:
		return exportedList([]*ast.Ident{node.Name})
	case *ast.ValueSpec:
		return exportedList(node.Names)
	default:
		return nil
	}
}

func exportedList(names []*ast.Ident) []string {
	found := []string{}
	for _, name := range names {
		if ast.IsExported(name.Name) {
			found = append(found, name.Name)
		}
	}
	return found
}

func statementCount(file *ast.File) int {
	total := len(file.Decls)
	ast.Inspect(file, func(node ast.Node) bool {
		total += stmtScore(node)
		return true
	})
	if total < 1 {
		return 1
	}
	return total
}

func stmtScore(node ast.Node) int {
	if countedStmt(node) {
		return 1
	}
	return 0
}

func countedStmt(node ast.Node) bool {
	if node == nil {
		return false
	}
	if _, ok := node.(*ast.BlockStmt); ok {
		return false
	}
	return isStmt(node)
}

func isStmt(node ast.Node) bool {
	return leadingStmt(node) || middleStmt(node) || trailingStmt(node)
}

func leadingStmt(node ast.Node) bool {
	switch node.(type) {
	case *ast.DeclStmt, *ast.EmptyStmt, *ast.LabeledStmt, *ast.ExprStmt, *ast.SendStmt, *ast.IncDecStmt, *ast.AssignStmt:
		return true
	default:
		return false
	}
}

func middleStmt(node ast.Node) bool {
	switch node.(type) {
	case *ast.GoStmt, *ast.DeferStmt, *ast.ReturnStmt, *ast.BranchStmt, *ast.IfStmt, *ast.SwitchStmt, *ast.TypeSwitchStmt:
		return true
	default:
		return false
	}
}

func trailingStmt(node ast.Node) bool {
	switch node.(type) {
	case *ast.CaseClause, *ast.CommClause, *ast.SelectStmt, *ast.ForStmt, *ast.RangeStmt, *ast.BadStmt:
		return true
	default:
		return false
	}
}

func passThroughs(item parsed) []passRec {
	found := []passRec{}
	for _, decl := range item.file.Decls {
		rec, ok := onePass(item, decl)
		if ok {
			found = append(found, rec)
		}
	}
	return found
}

func onePass(item parsed, decl ast.Decl) (passRec, bool) {
	fn, ok := decl.(*ast.FuncDecl)
	if !ok {
		return passRec{}, false
	}
	target, yes := forwarded(fn)
	if !yes {
		return passRec{}, false
	}
	return passRec{Line: item.fset.Position(fn.Name.Pos()).Line, Name: fn.Name.Name, Target: target}, true
}

func forwarded(fn *ast.FuncDecl) (string, bool) {
	if fn.Recv != nil || fn.Body == nil {
		return "", false
	}
	call := soleCall(fn.Body)
	if call == nil {
		return "", false
	}
	return matchedCall(fn, call)
}

func soleCall(body *ast.BlockStmt) *ast.CallExpr {
	if len(body.List) != 1 {
		return nil
	}
	return callExpr(body.List[0])
}

func callExpr(stmt ast.Stmt) *ast.CallExpr {
	switch node := stmt.(type) {
	case *ast.ReturnStmt:
		return returnCall(node)
	case *ast.ExprStmt:
		return exprCall(node.X)
	default:
		return nil
	}
}

func returnCall(stmt *ast.ReturnStmt) *ast.CallExpr {
	if len(stmt.Results) != 1 {
		return nil
	}
	return exprCall(stmt.Results[0])
}

func exprCall(expr ast.Expr) *ast.CallExpr {
	call, ok := expr.(*ast.CallExpr)
	if ok {
		return call
	}
	return nil
}

func matchedCall(fn *ast.FuncDecl, call *ast.CallExpr) (string, bool) {
	names := paramNames(fn.Type.Params)
	if len(names) == 0 || !argsMatch(call, names, variadic(fn.Type.Params)) {
		return "", false
	}
	target := callText(call.Fun)
	if target == "" {
		return "", false
	}
	return target, true
}

func paramNames(fields *ast.FieldList) []string {
	if fields == nil {
		return nil
	}
	names := []string{}
	for _, field := range fields.List {
		if len(field.Names) == 0 {
			return nil
		}
		names = appendNames(names, field.Names)
	}
	return names
}

func appendNames(names []string, idents []*ast.Ident) []string {
	for _, name := range idents {
		names = append(names, name.Name)
	}
	return names
}

func variadic(fields *ast.FieldList) bool {
	if fields == nil || len(fields.List) == 0 {
		return false
	}
	_, ok := fields.List[len(fields.List)-1].Type.(*ast.Ellipsis)
	return ok
}

func argsMatch(call *ast.CallExpr, names []string, spread bool) bool {
	if len(call.Args) != len(names) || !sameNames(call.Args, names) {
		return false
	}
	if call.Ellipsis == token.NoPos {
		return true
	}
	return spread
}

func sameNames(args []ast.Expr, names []string) bool {
	for i, arg := range args {
		ident, ok := arg.(*ast.Ident)
		if !ok || ident.Name != names[i] {
			return false
		}
	}
	return true
}

func callText(expr ast.Expr) string {
	switch node := expr.(type) {
	case *ast.Ident:
		return node.Name
	case *ast.SelectorExpr:
		return callText(node.X) + "." + node.Sel.Name
	case *ast.ParenExpr:
		return callText(node.X)
	default:
		return ""
	}
}

func imports(files []parsed) []importRec {
	found := []importRec{}
	for _, item := range files {
		found = append(found, importsOf(item)...)
	}
	return found
}

func importsOf(item parsed) []importRec {
	if generated(item.fset, item.file) {
		return nil
	}
	found := []importRec{}
	for _, decl := range item.file.Decls {
		found = append(found, declImports(item, decl)...)
	}
	return found
}

func declImports(item parsed, decl ast.Decl) []importRec {
	gen, ok := decl.(*ast.GenDecl)
	if !ok || gen.Tok != token.IMPORT {
		return nil
	}
	found := []importRec{}
	for _, spec := range gen.Specs {
		found = append(found, specImport(item, spec)...)
	}
	return found
}

func specImport(item parsed, spec ast.Spec) []importRec {
	imp, ok := spec.(*ast.ImportSpec)
	if !ok {
		return nil
	}
	return []importRec{{File: item.path, Line: item.fset.Position(imp.Pos()).Line, Path: importPath(imp)}}
}

func deadOf(defs []parsed, uses []parsed) []deadRec {
	counts := map[string]int{}
	tally(defs, counts)
	tally(uses, counts)
	defined := map[string]int{}
	return unreferenced(sitesOf(defs, defined), counts, defined)
}

func tally(files []parsed, counts map[string]int) {
	for _, item := range files {
		countFile(item.file, counts)
	}
}

func countFile(file *ast.File, counts map[string]int) {
	ast.Inspect(file, func(node ast.Node) bool {
		countIdent(node, counts)
		return true
	})
}

func countIdent(node ast.Node, counts map[string]int) {
	ident, ok := node.(*ast.Ident)
	if ok && ident.Name != "_" {
		counts[ident.Name]++
	}
}

func sitesOf(files []parsed, defined map[string]int) []deadRec {
	found := []deadRec{}
	for _, item := range files {
		if item.file.Name.Name == "main" || generated(item.fset, item.file) {
			continue
		}
		found = append(found, fileSites(item, defined)...)
	}
	return found
}

func fileSites(item parsed, defined map[string]int) []deadRec {
	found := []deadRec{}
	for _, decl := range item.file.Decls {
		found = append(found, declSites(item, decl, defined)...)
	}
	return found
}

func declSites(item parsed, decl ast.Decl, defined map[string]int) []deadRec {
	switch node := decl.(type) {
	case *ast.FuncDecl:
		return funcSite(item, node, defined)
	case *ast.GenDecl:
		return genSites(item, node, defined)
	default:
		return nil
	}
}

func funcSite(item parsed, fn *ast.FuncDecl, defined map[string]int) []deadRec {
	if fn.Recv != nil || !ast.IsExported(fn.Name.Name) {
		return nil
	}
	defined[fn.Name.Name]++
	return []deadRec{{File: item.path, Line: item.fset.Position(fn.Name.Pos()).Line, Kind: "function", Name: fn.Name.Name}}
}

func genSites(item parsed, gen *ast.GenDecl, defined map[string]int) []deadRec {
	found := []deadRec{}
	for _, spec := range gen.Specs {
		found = append(found, specSites(item, gen.Tok, spec, defined)...)
	}
	return found
}

func specSites(item parsed, tok token.Token, spec ast.Spec, defined map[string]int) []deadRec {
	switch node := spec.(type) {
	case *ast.TypeSpec:
		return namedSites(item, node.Name, "type", defined)
	case *ast.ValueSpec:
		return valueSites(item, tok, node, defined)
	default:
		return nil
	}
}

func valueSites(item parsed, tok token.Token, spec *ast.ValueSpec, defined map[string]int) []deadRec {
	kind := "var"
	if tok == token.CONST {
		kind = "const"
	}
	found := []deadRec{}
	for _, name := range spec.Names {
		found = append(found, namedSites(item, name, kind, defined)...)
	}
	return found
}

func namedSites(item parsed, name *ast.Ident, kind string, defined map[string]int) []deadRec {
	if !ast.IsExported(name.Name) {
		return nil
	}
	defined[name.Name]++
	return []deadRec{{File: item.path, Line: item.fset.Position(name.Pos()).Line, Kind: kind, Name: name.Name}}
}

func unreferenced(sites []deadRec, counts map[string]int, defined map[string]int) []deadRec {
	found := []deadRec{}
	for _, site := range sites {
		if counts[site.Name] <= defined[site.Name] {
			found = append(found, site)
		}
	}
	return found
}
