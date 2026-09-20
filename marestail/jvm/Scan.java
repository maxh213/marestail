import com.sun.source.tree.AnnotationTree;
import com.sun.source.tree.BinaryTree;
import com.sun.source.tree.BlockTree;
import com.sun.source.tree.CaseTree;
import com.sun.source.tree.CatchTree;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.ConditionalExpressionTree;
import com.sun.source.tree.DefaultCaseLabelTree;
import com.sun.source.tree.DoWhileLoopTree;
import com.sun.source.tree.EmptyStatementTree;
import com.sun.source.tree.EnhancedForLoopTree;
import com.sun.source.tree.ExpressionStatementTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.ForLoopTree;
import com.sun.source.tree.IdentifierTree;
import com.sun.source.tree.IfTree;
import com.sun.source.tree.ImportTree;
import com.sun.source.tree.LiteralTree;
import com.sun.source.tree.MemberReferenceTree;
import com.sun.source.tree.MemberSelectTree;
import com.sun.source.tree.MethodInvocationTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.ModifiersTree;
import com.sun.source.tree.NewClassTree;
import com.sun.source.tree.ReturnTree;
import com.sun.source.tree.StatementTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.VariableTree;
import com.sun.source.tree.WhileLoopTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import com.sun.source.util.TreeScanner;
import com.sun.source.util.Trees;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.lang.model.element.Modifier;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

public final class Scan {
    private static final String USAGE =
        "usage: marestail-java-scan comments|complexity|depth|deps|dead|lint --out FILE [--root DIR] "
            + "[--classpath FILE] [--classes DIR] [--release N] FILE... | @FILELIST";
    private static final Set<String> MODES = Set.of("comments", "complexity", "depth", "deps", "dead", "lint");
    private static final Set<String> SERIALIZATION = Set.of(
        "serialVersionUID", "serialPersistentFields", "writeObject", "readObject", "readObjectNoData", "writeReplace", "readResolve");
    private static final Pattern WORD = Pattern.compile("[A-Za-z_$][A-Za-z0-9_$]*");

    private final Map<String, String> flags = new HashMap<>();
    private final Set<Path> files = new LinkedHashSet<>();
    private final Map<Path, CompilationUnitTree> units = new LinkedHashMap<>();
    private final Map<Path, String> texts = new HashMap<>();
    private Path root;
    private SourcePositions positions;

    public static void main(String[] args) {
        int code;
        try {
            code = new Scan().run(args);
        } catch (IOException | RuntimeException error) {
            System.err.println("marestail-java-scan: internal error: " + error);
            code = 6;
        }
        System.exit(code);
    }

    private int run(String[] args) throws IOException {
        if (args.length == 0 || !MODES.contains(args[0])) {
            System.err.println(USAGE);
            return 2;
        }
        String mode = args[0];
        List<String> listed = new ArrayList<>();
        for (int i = 1; i < args.length; i++) {
            if (!args[i].startsWith("--")) {
                listed.add(args[i]);
            } else if (i + 1 < args.length) {
                flags.put(args[i], args[++i]);
            } else {
                System.err.println(args[i] + " needs a value");
                return 2;
            }
        }
        if (!flags.containsKey("--out")) {
            System.err.println(USAGE);
            return 2;
        }
        root = Path.of(flags.getOrDefault("--root", ".")).toAbsolutePath().normalize();
        if (!expand(listed)) {
            return 4;
        }
        Object payload = mode.equals("comments") ? comments() : mode.equals("lint") ? lint() : analyse(mode);
        if (payload == null) {
            return 3;
        }
        StringBuilder out = new StringBuilder();
        json(out, payload);
        Path target = Path.of(flags.get("--out"));
        Files.writeString(target, out.toString(), StandardCharsets.UTF_8);
        return 0;
    }

    private boolean expand(List<String> listed) throws IOException {
        for (String entry : listed) {
            if (!entry.startsWith("@")) {
                files.add(Path.of(entry).toAbsolutePath().normalize());
                continue;
            }
            Path list = Path.of(entry.substring(1));
            if (!Files.isRegularFile(list)) {
                System.err.println(list + ":0 file list does not exist");
                return false;
            }
            for (String line : Files.readAllLines(list, StandardCharsets.UTF_8)) {
                if (!line.isBlank()) {
                    files.add(Path.of(line.strip()).toAbsolutePath().normalize());
                }
            }
        }
        boolean present = true;
        for (Path file : files) {
            if (!Files.isRegularFile(file)) {
                System.err.println(rel(file) + ":0 file does not exist");
                present = false;
            }
        }
        return present;
    }

    private Object analyse(String mode) throws IOException {
        if (!parse()) {
            return null;
        }
        List<Object> found = new ArrayList<>();
        switch (mode) {
            case "complexity" -> units.forEach((file, unit) -> found.addAll(complexity(file, unit)));
            case "depth" -> units.forEach((file, unit) -> found.add(depth(file, unit)));
            case "dead" -> found.addAll(dead());
            default -> {
                return deps();
            }
        }
        return found;
    }

    private static JavaCompiler compiler() {
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            System.err.println("no system java compiler: run marestail with a JDK, not a JRE");
        }
        return compiler;
    }

    private boolean parse() throws IOException {
        JavaCompiler compiler = compiler();
        if (compiler == null) {
            return false;
        }
        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        StandardJavaFileManager manager = compiler.getStandardFileManager(diagnostics, Locale.ROOT, StandardCharsets.UTF_8);
        JavacTask task = (JavacTask) compiler.getTask(null, manager, diagnostics, List.of("-proc:none"), null, manager.getJavaFileObjectsFromPaths(files));
        positions = Trees.instance(task).getSourcePositions();
        for (CompilationUnitTree unit : task.parse()) {
            Path file = Path.of(unit.getSourceFile().toUri()).toAbsolutePath().normalize();
            units.put(file, unit);
            texts.put(file, unit.getSourceFile().getCharContent(true).toString());
        }
        boolean clean = true;
        for (Diagnostic<? extends JavaFileObject> diagnostic : diagnostics.getDiagnostics()) {
            if (diagnostic.getKind() == Diagnostic.Kind.ERROR) {
                System.err.println(where(diagnostic) + " " + firstLine(diagnostic.getMessage(Locale.ROOT)));
                clean = false;
            }
        }
        return clean;
    }

    private Object lint() throws IOException {
        JavaCompiler compiler = compiler();
        if (compiler == null) {
            return null;
        }
        List<String> options = new ArrayList<>(List.of("-Xlint:all,-processing", "-encoding", "UTF-8", "-Xmaxwarns", "100000", "-Xmaxerrs", "100000"));
        if (Runtime.version().feature() >= 21) {
            options.add("-proc:full");
        }
        if (flags.containsKey("--classpath")) {
            String classpath = Files.readString(Path.of(flags.get("--classpath")), StandardCharsets.UTF_8).strip();
            if (!classpath.isEmpty()) {
                options.addAll(List.of("-classpath", classpath));
            }
        }
        Path classes = Path.of(flags.getOrDefault("--classes", root.resolve(".marestail/java-lint-classes").toString()));
        Files.createDirectories(classes);
        options.addAll(List.of("-d", classes.toString()));
        if (flags.containsKey("--release")) {
            options.addAll(List.of("--release", flags.get("--release")));
        }
        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        StandardJavaFileManager manager = compiler.getStandardFileManager(diagnostics, Locale.ROOT, StandardCharsets.UTF_8);
        compiler.getTask(null, manager, diagnostics, options, null, manager.getJavaFileObjectsFromPaths(files)).call();
        List<Object> found = new ArrayList<>();
        for (Diagnostic<? extends JavaFileObject> diagnostic : diagnostics.getDiagnostics()) {
            if (diagnostic.getKind() == Diagnostic.Kind.NOTE || diagnostic.getKind() == Diagnostic.Kind.OTHER) {
                continue;
            }
            String file = diagnostic.getSource() == null ? null : rel(Path.of(diagnostic.getSource().toUri()));
            found.add(record(
                "file", file,
                "line", Math.max(diagnostic.getLineNumber(), 1),
                "kind", diagnostic.getKind().name(),
                "code", diagnostic.getCode(),
                "message", firstLine(diagnostic.getMessage(Locale.ROOT))));
        }
        return found;
    }

    private String where(Diagnostic<? extends JavaFileObject> diagnostic) {
        String file = diagnostic.getSource() == null ? "?" : rel(Path.of(diagnostic.getSource().toUri()));
        return file + ":" + Math.max(diagnostic.getLineNumber(), 0);
    }

    private List<Object> comments() throws IOException {
        List<Object> found = new ArrayList<>();
        for (Path file : files) {
            String text = Files.readString(file, StandardCharsets.UTF_8);
            int line = 1;
            int i = 0;
            while (i < text.length()) {
                char c = text.charAt(i);
                char next = i + 1 < text.length() ? text.charAt(i + 1) : '\0';
                int end;
                if (c == '/' && next == '/') {
                    end = text.indexOf('\n', i);
                    end = end < 0 ? text.length() : end;
                    found.add(record("file", rel(file), "line", line, "text", snippet(text.substring(i, end))));
                } else if (c == '/' && next == '*') {
                    int close = text.indexOf("*/", i + 2);
                    end = close < 0 ? text.length() : close + 2;
                    found.add(record("file", rel(file), "line", line, "text", snippet(text.substring(i, end))));
                } else if (c == '"' && text.startsWith("\"\"\"", i)) {
                    end = skipQuoted(text, i + 3, "\"\"\"");
                } else if (c == '"' || c == '\'') {
                    end = skipQuoted(text, i + 1, String.valueOf(c));
                } else {
                    end = i + 1;
                }
                for (int k = i; k < end; k++) {
                    if (text.charAt(k) == '\n') {
                        line++;
                    }
                }
                i = end;
            }
        }
        return found;
    }

    private static int skipQuoted(String text, int from, String close) {
        int i = from;
        while (i < text.length()) {
            if (text.charAt(i) == '\\') {
                i += 2;
            } else if (text.startsWith(close, i)) {
                return i + close.length();
            } else if (text.charAt(i) == '\n' && close.length() == 1) {
                return i;
            } else {
                i++;
            }
        }
        return text.length();
    }

    private long line(CompilationUnitTree unit, long offset) {
        return unit.getLineMap().getLineNumber(offset);
    }

    private long startLine(CompilationUnitTree unit, Tree tree) {
        return line(unit, positions.getStartPosition(unit, tree));
    }

    private long endLine(CompilationUnitTree unit, Tree tree) {
        return line(unit, positions.getEndPosition(unit, tree));
    }

    private long nameLine(Path file, CompilationUnitTree unit, MethodTree method, String name) {
        long from = positions.getStartPosition(unit, method);
        List<Tree> before = new ArrayList<>(method.getTypeParameters());
        before.add(method.getModifiers());
        if (method.getReturnType() != null) {
            before.add(method.getReturnType());
        }
        for (Tree part : before) {
            from = Math.max(from, positions.getEndPosition(unit, part));
        }
        Matcher matcher = Pattern.compile("\\b" + Pattern.quote(name) + "\\b").matcher(texts.get(file));
        return from >= 0 && matcher.find((int) from) ? line(unit, matcher.start()) : startLine(unit, method);
    }

    private static boolean constructor(MethodTree method) {
        return method.getName().contentEquals("<init>");
    }

    private static boolean isPrivate(ModifiersTree modifiers) {
        return modifiers.getFlags().contains(Modifier.PRIVATE);
    }

    private class Members extends TreePathScanner<Void, Void> {
        protected final Deque<String> owners = new ArrayDeque<>();

        @Override
        public Void visitClass(ClassTree type, Void unused) {
            String name = type.getSimpleName().toString();
            owners.addLast(name.isEmpty() ? "<anonymous>" : name);
            try {
                return super.visitClass(type, unused);
            } finally {
                owners.removeLast();
            }
        }

        protected String simpleName(MethodTree method) {
            return constructor(method) ? owners.getLast() : method.getName().toString();
        }
    }

    private List<Object> complexity(Path file, CompilationUnitTree unit) {
        List<Object> found = new ArrayList<>();
        new Members() {
            @Override
            public Void visitMethod(MethodTree method, Void unused) {
                if (method.getBody() != null) {
                    String member = constructor(method) ? "<init>" : method.getName().toString();
                    found.add(record(
                        "file", rel(file),
                        "line", nameLine(file, unit, method, simpleName(method)),
                        "startLine", startLine(unit, method),
                        "endLine", endLine(unit, method),
                        "name", String.join(".", owners) + "." + member,
                        "complexity", 1 + new Branches().count(method.getBody())));
                }
                return super.visitMethod(method, unused);
            }
        }.scan(new TreePath(unit), null);
        return found;
    }

    private static final class Branches extends TreeScanner<Integer, Void> {
        int count(Tree tree) {
            Integer total = scan(tree, null);
            return total == null ? 0 : total;
        }

        private static int plus(long own, Integer nested) {
            return (int) own + (nested == null ? 0 : nested);
        }

        @Override
        public Integer reduce(Integer left, Integer right) {
            return plus(left == null ? 0 : left, right);
        }

        @Override
        public Integer visitClass(ClassTree type, Void unused) {
            return 0;
        }

        @Override
        public Integer visitIf(IfTree tree, Void unused) {
            return plus(1, super.visitIf(tree, unused));
        }

        @Override
        public Integer visitWhileLoop(WhileLoopTree tree, Void unused) {
            return plus(1, super.visitWhileLoop(tree, unused));
        }

        @Override
        public Integer visitDoWhileLoop(DoWhileLoopTree tree, Void unused) {
            return plus(1, super.visitDoWhileLoop(tree, unused));
        }

        @Override
        public Integer visitForLoop(ForLoopTree tree, Void unused) {
            return plus(1, super.visitForLoop(tree, unused));
        }

        @Override
        public Integer visitEnhancedForLoop(EnhancedForLoopTree tree, Void unused) {
            return plus(1, super.visitEnhancedForLoop(tree, unused));
        }

        @Override
        public Integer visitCatch(CatchTree tree, Void unused) {
            return plus(1, super.visitCatch(tree, unused));
        }

        @Override
        public Integer visitConditionalExpression(ConditionalExpressionTree tree, Void unused) {
            return plus(1, super.visitConditionalExpression(tree, unused));
        }

        @Override
        public Integer visitCase(CaseTree tree, Void unused) {
            long labels = tree.getLabels().stream().filter(label -> !(label instanceof DefaultCaseLabelTree)).count();
            return plus(labels, super.visitCase(tree, unused));
        }

        @Override
        public Integer visitBinary(BinaryTree tree, Void unused) {
            boolean logical = tree.getKind() == Tree.Kind.CONDITIONAL_AND || tree.getKind() == Tree.Kind.CONDITIONAL_OR;
            return plus(logical ? 1 : 0, super.visitBinary(tree, unused));
        }
    }

    private Object depth(Path file, CompilationUnitTree unit) {
        List<String> surface = new ArrayList<>();
        for (Tree declaration : unit.getTypeDecls()) {
            if (declaration instanceof ClassTree type) {
                surface(type, "", surface);
            }
        }
        int[] statements = {0};
        List<Object> passThroughs = new ArrayList<>();
        new Members() {
            @Override
            public Void scan(Tree tree, Void unused) {
                if (tree instanceof StatementTree && isStatement(tree, getCurrentPath())) {
                    statements[0]++;
                }
                return super.scan(tree, unused);
            }

            @Override
            public Void visitMethod(MethodTree method, Void unused) {
                String target = passThrough(method);
                if (target != null) {
                    passThroughs.add(record(
                        "line", nameLine(file, unit, method, simpleName(method)),
                        "name", String.join(".", owners) + "." + method.getName(),
                        "target", target));
                }
                return super.visitMethod(method, unused);
            }
        }.scan(new TreePath(unit), null);
        return record("file", rel(file), "public", surface, "statements", statements[0], "pass_throughs", passThroughs);
    }

    private static boolean isStatement(Tree tree, TreePath parent) {
        if (tree instanceof BlockTree || tree instanceof ClassTree || tree instanceof EmptyStatementTree) {
            return false;
        }
        if (tree instanceof VariableTree) {
            Tree owner = parent == null ? null : parent.getLeaf();
            return owner instanceof BlockTree || owner instanceof CaseTree;
        }
        return true;
    }

    private static void surface(ClassTree type, String prefix, List<String> surface) {
        if (isPrivate(type.getModifiers())) {
            return;
        }
        String name = prefix + type.getSimpleName();
        surface.add(name);
        for (Tree member : type.getMembers()) {
            if (member instanceof ClassTree nested) {
                surface(nested, name + ".", surface);
            } else if (member instanceof MethodTree method && !isPrivate(method.getModifiers())) {
                surface.add(name + "." + (constructor(method) ? "<init>" : method.getName()));
            } else if (member instanceof VariableTree field && !enumConstant(type, field) && (component(type, field) || !isPrivate(field.getModifiers()))) {
                surface.add(name + "." + field.getName());
            }
        }
    }

    private static boolean component(ClassTree type, VariableTree field) {
        return type.getKind() == Tree.Kind.RECORD && !field.getModifiers().getFlags().contains(Modifier.STATIC);
    }

    private static boolean enumConstant(ClassTree type, VariableTree field) {
        return type.getKind() == Tree.Kind.ENUM && field.getInitializer() instanceof NewClassTree;
    }

    private static boolean overrides(MethodTree method) {
        for (AnnotationTree annotation : method.getModifiers().getAnnotations()) {
            String name = annotation.getAnnotationType().toString();
            if (name.equals("Override") || name.equals("java.lang.Override")) {
                return true;
            }
        }
        return false;
    }

    private static String passThrough(MethodTree method) {
        if (constructor(method) || method.getBody() == null || method.getParameters().isEmpty() || overrides(method)) {
            return null;
        }
        List<? extends StatementTree> body = method.getBody().getStatements();
        if (body.size() != 1) {
            return null;
        }
        ExpressionTree expression = null;
        if (body.get(0) instanceof ReturnTree returned) {
            expression = returned.getExpression();
        } else if (body.get(0) instanceof ExpressionStatementTree statement) {
            expression = statement.getExpression();
        }
        if (!(expression instanceof MethodInvocationTree call) || call.getArguments().size() != method.getParameters().size()) {
            return null;
        }
        for (int i = 0; i < call.getArguments().size(); i++) {
            if (!(call.getArguments().get(i) instanceof IdentifierTree argument)
                || !argument.getName().contentEquals(method.getParameters().get(i).getName())) {
                return null;
            }
        }
        return call.getMethodSelect().toString();
    }

    private record Edge(Path to, String symbol, long line) {
    }

    private Map<String, Object> deps() {
        Map<String, Path> index = new HashMap<>();
        Map<Path, String> packages = new HashMap<>();
        units.forEach((file, unit) -> {
            String name = unit.getPackageName() == null ? "" : unit.getPackageName().toString();
            packages.put(file, name);
            for (Tree declaration : unit.getTypeDecls()) {
                if (declaration instanceof ClassTree type) {
                    index(type, name.isEmpty() ? "" : name + ".", file, index);
                }
            }
        });
        List<Object> records = new ArrayList<>();
        List<Object> edges = new ArrayList<>();
        units.forEach((file, unit) -> {
            Map<String, Edge> found = new LinkedHashMap<>();
            Map<String, String> named = new HashMap<>();
            List<String> onDemand = new ArrayList<>();
            List<Object> imports = new ArrayList<>();
            for (ImportTree declaration : unit.getImports()) {
                String name = declaration.getQualifiedIdentifier().toString();
                long line = startLine(unit, declaration);
                imports.add(record("name", name, "line", line, "static", declaration.isStatic()));
                boolean wildcard = name.endsWith(".*");
                String base = wildcard ? name.substring(0, name.length() - 2) : name;
                if (declaration.isStatic() && !wildcard) {
                    base = base.substring(0, Math.max(base.lastIndexOf('.'), 0));
                } else if (wildcard) {
                    onDemand.add(base);
                } else {
                    named.put(base.substring(base.lastIndexOf('.') + 1), base);
                }
                note(found, index, file, known(base, index), line);
            }
            Set<String> declared = new HashSet<>();
            List<String> types = new ArrayList<>();
            new TreeScanner<Void, Void>() {
                @Override
                public Void visitClass(ClassTree type, Void unused) {
                    declared.add(type.getSimpleName().toString());
                    return super.visitClass(type, unused);
                }
            }.scan(unit, null);
            index.forEach((symbol, owner) -> {
                if (owner.equals(file)) {
                    types.add(symbol);
                }
            });
            String own = packages.get(file);
            new TreeScanner<Void, Void>() {
                @Override
                public Void visitIdentifier(IdentifierTree identifier, Void unused) {
                    String name = identifier.getName().toString();
                    if (!name.isEmpty() && Character.isUpperCase(name.charAt(0)) && !declared.contains(name)) {
                        String symbol = named.containsKey(name) ? named.get(name) : resolve(name, own, onDemand, index);
                        note(found, index, file, index.containsKey(symbol) ? symbol : null, startLine(unit, identifier));
                    }
                    return null;
                }

                @Override
                public Void visitMemberSelect(MemberSelectTree select, Void unused) {
                    String text = select.toString();
                    if (index.containsKey(text)) {
                        note(found, index, file, text, startLine(unit, select));
                        return null;
                    }
                    return super.visitMemberSelect(select, unused);
                }
            }.scan(unit.getTypeDecls(), null);
            found.values().stream()
                .sorted(Comparator.comparingLong(Edge::line).thenComparing(Edge::symbol))
                .forEach(edge -> edges.add(record(
                    "from", rel(file), "to", rel(edge.to()), "toPackage", packages.get(edge.to()), "symbol", edge.symbol(), "line", edge.line())));
            types.sort(null);
            records.add(record("path", rel(file), "package", own, "types", types, "imports", imports));
        });
        return record("files", records, "edges", edges);
    }

    private static void index(ClassTree type, String prefix, Path file, Map<String, Path> index) {
        String name = prefix + type.getSimpleName();
        index.putIfAbsent(name, file);
        for (Tree member : type.getMembers()) {
            if (member instanceof ClassTree nested) {
                index(nested, name + ".", file, index);
            }
        }
    }

    private static String known(String name, Map<String, Path> index) {
        String candidate = name;
        while (!candidate.isEmpty()) {
            if (index.containsKey(candidate)) {
                return candidate;
            }
            candidate = candidate.substring(0, Math.max(candidate.lastIndexOf('.'), 0));
        }
        return null;
    }

    private static String resolve(String name, String own, List<String> onDemand, Map<String, Path> index) {
        String local = own.isEmpty() ? name : own + "." + name;
        if (index.containsKey(local)) {
            return local;
        }
        for (String prefix : onDemand) {
            if (index.containsKey(prefix + "." + name)) {
                return prefix + "." + name;
            }
        }
        return null;
    }

    private static void note(Map<String, Edge> found, Map<String, Path> index, Path file, String symbol, long line) {
        if (symbol == null || index.get(symbol).equals(file)) {
            return;
        }
        Path to = index.get(symbol);
        found.putIfAbsent(to + "|" + symbol, new Edge(to, symbol, line));
    }

    private List<Object> dead() {
        Set<String> used = new HashSet<>();
        for (CompilationUnitTree unit : units.values()) {
            new TreeScanner<Void, Void>() {
                @Override
                public Void visitIdentifier(IdentifierTree identifier, Void unused) {
                    used.add(identifier.getName().toString());
                    return null;
                }

                @Override
                public Void visitMemberSelect(MemberSelectTree select, Void unused) {
                    used.add(select.getIdentifier().toString());
                    return super.visitMemberSelect(select, unused);
                }

                @Override
                public Void visitMemberReference(MemberReferenceTree reference, Void unused) {
                    used.add(reference.getName().toString());
                    return super.visitMemberReference(reference, unused);
                }

                @Override
                public Void visitLiteral(LiteralTree literal, Void unused) {
                    if (literal.getValue() instanceof String value) {
                        WORD.matcher(value).results().forEach(match -> used.add(match.group()));
                    }
                    return null;
                }
            }.scan(unit, null);
        }
        List<Object> found = new ArrayList<>();
        units.forEach((file, unit) -> new TreeScanner<Void, Void>() {
            @Override
            public Void visitClass(ClassTree type, Void unused) {
                boolean annotated = !type.getModifiers().getAnnotations().isEmpty();
                for (Tree member : type.getMembers()) {
                    if (member instanceof MethodTree method && unusedMethod(method, used)) {
                        found.add(record("file", rel(file), "line", nameLine(file, unit, method, method.getName().toString()), "kind", "private method", "name", method.getName().toString()));
                    } else if (member instanceof VariableTree field && !annotated && !component(type, field) && unusedField(field, used)) {
                        found.add(record("file", rel(file), "line", startLine(unit, field), "kind", "private field", "name", field.getName().toString()));
                    }
                }
                return super.visitClass(type, unused);
            }
        }.scan(unit, null));
        return found;
    }

    private static boolean unusedMethod(MethodTree method, Set<String> used) {
        String name = method.getName().toString();
        return isPrivate(method.getModifiers()) && method.getBody() != null && method.getModifiers().getAnnotations().isEmpty()
            && !constructor(method) && !SERIALIZATION.contains(name) && !used.contains(name);
    }

    private static boolean unusedField(VariableTree field, Set<String> used) {
        String name = field.getName().toString();
        return isPrivate(field.getModifiers()) && field.getModifiers().getAnnotations().isEmpty()
            && !SERIALIZATION.contains(name) && !used.contains(name);
    }

    private String rel(Path file) {
        Path full = file.toAbsolutePath().normalize();
        return full.startsWith(root) ? root.relativize(full).toString().replace('\\', '/') : full.toString();
    }

    private static String firstLine(String text) {
        String trimmed = text == null ? "" : text.strip();
        int cut = trimmed.indexOf('\n');
        return cut < 0 ? trimmed : trimmed.substring(0, cut).strip();
    }

    private static String snippet(String text) {
        String flat = text.strip().replaceAll("\\s+", " ");
        return flat.length() <= 80 ? flat : flat.substring(0, 80);
    }

    private static Map<String, Object> record(Object... pairs) {
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) {
            map.put((String) pairs[i], pairs[i + 1]);
        }
        return map;
    }

    private static void json(StringBuilder out, Object value) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String text) {
            quote(out, text);
        } else if (value instanceof Number || value instanceof Boolean) {
            out.append(value);
        } else if (value instanceof Map<?, ?> map) {
            out.append('{');
            String separator = "";
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                out.append(separator);
                quote(out, entry.getKey().toString());
                out.append(':');
                json(out, entry.getValue());
                separator = ",";
            }
            out.append('}');
        } else if (value instanceof Iterable<?> items) {
            out.append('[');
            String separator = "";
            for (Object item : items) {
                out.append(separator);
                json(out, item);
                separator = ",";
            }
            out.append(']');
        } else {
            quote(out, value.toString());
        }
    }

    private static void quote(StringBuilder out, String text) {
        out.append('"');
        for (char c : text.toCharArray()) {
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        out.append('"');
    }
}
