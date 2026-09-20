import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const [tsRoot, ...files] = process.argv.slice(2);
const ts = createRequire(`${tsRoot}/package.json`)("typescript");

const commentsIn = (file) => {
  const text = readFileSync(file, "utf8");
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  const seen = new Map();
  const record = (ranges) => (ranges || []).forEach((range) => seen.set(range.pos, range));
  const visit = (node) => {
    record(ts.getLeadingCommentRanges(text, node.getFullStart()));
    record(ts.getTrailingCommentRanges(text, node.getEnd()));
    node.getChildren(source).forEach(visit);
  };
  visit(source);
  return [...seen.values()].map((range) => ({
    file,
    line: source.getLineAndCharacterOfPosition(range.pos).line + 1,
    text: text.slice(range.pos, range.end).split("\n")[0].trim().slice(0, 80),
  }));
};

process.stdout.write(JSON.stringify(files.flatMap(commentsIn)));
