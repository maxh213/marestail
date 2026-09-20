import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const [tsRoot, ...files] = process.argv.slice(2);
const ts = createRequire(`${tsRoot}/package.json`)("typescript");

const FUNCTION_KINDS = new Set([
  ts.SyntaxKind.FunctionDeclaration,
  ts.SyntaxKind.FunctionExpression,
  ts.SyntaxKind.ArrowFunction,
  ts.SyntaxKind.MethodDeclaration,
]);

const analyse = (file) => {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  const exports = [];
  const passThroughs = [];
  let statements = 0;
  const visit = (node) => {
    if (ts.isStatement(node)) statements += 1;
    exports.push(...exportedNames(node));
    if (FUNCTION_KINDS.has(node.kind) && isPassThrough(node)) {
      passThroughs.push({ name: nameOf(node), line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1 });
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return { file, exports, statements, passThroughs };
};

const exportedNames = (node) => {
  if (ts.isExportDeclaration(node) && node.exportClause && ts.isNamedExports(node.exportClause)) {
    return node.exportClause.elements.map((e) => e.name.getText());
  }
  if (ts.isExportAssignment(node)) return ["default"];
  const modifiers = ts.canHaveModifiers(node) ? ts.getModifiers(node) || [] : [];
  if (!modifiers.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) return [];
  if (ts.isVariableStatement(node)) return node.declarationList.declarations.map((d) => d.name.getText());
  if (node.name) return [node.name.getText()];
  return ["default"];
};

const isPassThrough = (fn) => {
  const params = fn.parameters.map((p) => p.name.getText());
  if (params.length === 0) return false;
  const call = returnedCall(fn);
  if (!call) return false;
  const args = call.arguments.map((a) => (ts.isIdentifier(a) ? a.text : null));
  return args.length === params.length && args.every((a, i) => a === params[i]);
};

const returnedCall = (fn) => {
  if (!fn.body) return null;
  if (ts.isCallExpression(fn.body)) return fn.body;
  if (ts.isBlock(fn.body) && fn.body.statements.length === 1) {
    const only = fn.body.statements[0];
    if (ts.isReturnStatement(only) && only.expression && ts.isCallExpression(only.expression)) return only.expression;
  }
  return null;
};

const nameOf = (fn) => {
  if (fn.name && fn.name.getText) return fn.name.getText();
  const parent = fn.parent;
  if (parent && ts.isVariableDeclaration(parent)) return parent.name.getText();
  return "<anonymous>";
};

process.stdout.write(JSON.stringify(files.map(analyse)));
