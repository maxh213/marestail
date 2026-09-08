import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const [tsRoot, ...files] = process.argv.slice(2);
const ts = createRequire(`${tsRoot}/package.json`)("typescript");

const BRANCHING = new Set([
  ts.SyntaxKind.IfStatement,
  ts.SyntaxKind.ConditionalExpression,
  ts.SyntaxKind.ForStatement,
  ts.SyntaxKind.ForInStatement,
  ts.SyntaxKind.ForOfStatement,
  ts.SyntaxKind.WhileStatement,
  ts.SyntaxKind.DoStatement,
  ts.SyntaxKind.CaseClause,
  ts.SyntaxKind.CatchClause,
]);

const SHORT_CIRCUIT = new Set([
  ts.SyntaxKind.AmpersandAmpersandToken,
  ts.SyntaxKind.BarBarToken,
  ts.SyntaxKind.QuestionQuestionToken,
]);

const FUNCTION_KINDS = new Set([
  ts.SyntaxKind.FunctionDeclaration,
  ts.SyntaxKind.FunctionExpression,
  ts.SyntaxKind.ArrowFunction,
  ts.SyntaxKind.MethodDeclaration,
  ts.SyntaxKind.Constructor,
  ts.SyntaxKind.GetAccessor,
  ts.SyntaxKind.SetAccessor,
]);

const analyse = (file) => {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  const results = [];
  const visit = (node) => {
    if (FUNCTION_KINDS.has(node.kind)) results.push(measure(node, source, file));
    ts.forEachChild(node, visit);
  };
  visit(source);
  return results;
};

const measure = (fn, source, file) => {
  let complexity = 1;
  const count = (node) => {
    if (node !== fn && FUNCTION_KINDS.has(node.kind)) return;
    if (BRANCHING.has(node.kind)) complexity += 1;
    if (node.kind === ts.SyntaxKind.BinaryExpression && SHORT_CIRCUIT.has(node.operatorToken.kind)) complexity += 1;
    ts.forEachChild(node, count);
  };
  ts.forEachChild(fn, count);
  const line = source.getLineAndCharacterOfPosition(fn.getStart(source)).line + 1;
  const endLine = source.getLineAndCharacterOfPosition(fn.getEnd()).line + 1;
  return { file, name: nameOf(fn), line, endLine, complexity };
};

const nameOf = (fn) => {
  if (fn.name && fn.name.getText) return fn.name.getText();
  const parent = fn.parent;
  if (parent && ts.isVariableDeclaration(parent)) return parent.name.getText();
  if (parent && ts.isPropertyAssignment(parent)) return parent.name.getText();
  return "<anonymous>";
};

process.stdout.write(JSON.stringify(files.flatMap(analyse)));
