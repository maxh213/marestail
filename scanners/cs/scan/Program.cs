using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace Marestail.Cs.Scan;

public static class Program
{
    private const string Usage =
        "usage: marestail-cs-scan comments|complexity|depth|deps|dead [--out FILE] [--root DIR] FILE... | @FILELIST";

    private static readonly JsonSerializerOptions Json = new() { WriteIndented = false };
    private static readonly string[] Defines = { "DEBUG", "TRACE" };

    private static string _root;

    public static int Main(string[] rawArgs)
    {
        try
        {
            return Run(rawArgs);
        }
        catch (Exception error)
        {
            Console.Error.WriteLine($"marestail-cs-scan: internal error: {error.GetType().Name}: {error.Message}");
            return 6;
        }
    }

    private static int Run(string[] rawArgs)
    {
        if (rawArgs.Length == 0)
        {
            Console.Error.WriteLine(Usage);
            return 2;
        }

        var mode = rawArgs[0];
        var rest = rawArgs.Skip(1).ToList();
        if (!TakeValue(rest, "--out", out var outPath)) return 2;
        if (!TakeValue(rest, "--root", out var rootPath)) return 2;
        _root = rootPath is null ? null : Path.GetFullPath(rootPath);

        if (!Expand(rest, out var listed)) return 4;

        var files = FullPathsWithoutDuplicates(listed);

        var missing = files.Where(f => !File.Exists(f)).ToList();
        if (missing.Count > 0)
        {
            foreach (var file in missing) Console.Error.WriteLine($"{Rel(file)}:0 file does not exist");
            return 4;
        }

        var trees = new Dictionary<string, CompilationUnitSyntax>(StringComparer.Ordinal);
        foreach (var file in files) trees[file] = Parse(file);

        var broken = new List<string>();
        foreach (var file in files)
        {
            foreach (var diagnostic in trees[file].SyntaxTree.GetDiagnostics()
                         .Where(d => d.Severity == DiagnosticSeverity.Error).Take(3))
            {
                broken.Add($"{Rel(file)}:{diagnostic.Location.GetLineSpan().StartLinePosition.Line + 1} {diagnostic.GetMessage()}");
            }
        }
        if (broken.Count > 0)
        {
            foreach (var line in broken) Console.Error.WriteLine(line);
            return 3;
        }

        object payload = mode switch
        {
            "comments" => files.SelectMany(f => Comments(f, trees[f])).ToList(),
            "complexity" => files.SelectMany(f => Complexity(f, trees[f])).ToList(),
            "depth" => files.Select(f => Depth(f, trees[f])).ToList(),
            "deps" => Deps(files, trees),
            "dead" => Dead(files, trees),
            _ => null,
        };
        if (payload is null)
        {
            Console.Error.WriteLine($"unknown mode '{mode}'");
            Console.Error.WriteLine(Usage);
            return 2;
        }

        var text = JsonSerializer.Serialize(payload, Json);
        if (outPath is null)
        {
            Console.Out.Write(text);
            return 0;
        }
        var directory = Path.GetDirectoryName(Path.GetFullPath(outPath));
        if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
        File.WriteAllText(outPath, text);
        return 0;
    }

    private static List<string> FullPathsWithoutDuplicates(List<string> listed)
    {
        var files = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var entry in listed)
        {
            var full = Path.GetFullPath(entry);
            if (seen.Add(full)) files.Add(full);
        }
        return files;
    }

    private static bool TakeValue(List<string> args, string flag, out string value)
    {
        value = null;
        var index = args.IndexOf(flag);
        if (index < 0) return true;
        if (index + 1 >= args.Count)
        {
            Console.Error.WriteLine($"{flag} needs a value");
            return false;
        }
        value = args[index + 1];
        args.RemoveRange(index, 2);
        return true;
    }

    private static bool Expand(List<string> args, out List<string> files)
    {
        files = new List<string>();
        foreach (var arg in args)
        {
            if (!arg.StartsWith("@"))
            {
                files.Add(arg);
                continue;
            }
            var list = arg[1..];
            if (!File.Exists(list))
            {
                Console.Error.WriteLine($"{list}:0 file list does not exist");
                return false;
            }
            foreach (var line in File.ReadAllLines(list))
            {
                if (line.Trim().Length > 0) files.Add(line.Trim());
            }
        }
        return true;
    }

    private static string Rel(string file)
    {
        if (_root is null) return file;
        var relative = Path.GetRelativePath(_root, Path.GetFullPath(file)).Replace('\\', '/');
        return relative.StartsWith("../") ? Path.GetFullPath(file) : relative;
    }

    private static CompilationUnitSyntax Parse(string file)
    {
        var text = File.ReadAllText(file);
        var options = new CSharpParseOptions(LanguageVersion.Latest, preprocessorSymbols: Defines);
        return CSharpSyntaxTree.ParseText(text, options, path: file).GetCompilationUnitRoot();
    }

    private static int Line(SyntaxNode node) =>
        node.SyntaxTree.GetLineSpan(node.Span).StartLinePosition.Line + 1;

    private static int EndLine(SyntaxNode node) =>
        node.SyntaxTree.GetLineSpan(node.Span).EndLinePosition.Line + 1;

    private static int Line(SyntaxToken token) =>
        token.SyntaxTree.GetLineSpan(token.Span).StartLinePosition.Line + 1;

    private static int Line(SyntaxTrivia trivia) =>
        trivia.SyntaxTree.GetLineSpan(trivia.Span).StartLinePosition.Line + 1;

    private static string Snippet(string text)
    {
        var flat = Regex.Replace(text.Trim(), @"\s+", " ");
        return flat.Length <= 80 ? flat : flat[..80];
    }

    private static readonly SyntaxKind[] CommentKinds =
    {
        SyntaxKind.SingleLineCommentTrivia,
        SyntaxKind.MultiLineCommentTrivia,
        SyntaxKind.SingleLineDocumentationCommentTrivia,
        SyntaxKind.MultiLineDocumentationCommentTrivia,
    };

    private static List<object> Comments(string file, CompilationUnitSyntax root)
    {
        var relative = Rel(file);
        var found = new List<object>();
        foreach (var trivia in root.DescendantTrivia(descendIntoTrivia: true))
        {
            if (!CommentKinds.Contains(trivia.Kind())) continue;
            found.Add(new { file = relative, line = Line(trivia), text = Snippet(trivia.ToFullString()) });
        }
        return found;
    }

    private static List<object> Complexity(string file, CompilationUnitSyntax root)
    {
        var relative = Rel(file);
        var found = new List<object>();

        var globals = root.Members.OfType<GlobalStatementSyntax>().ToList();
        if (globals.Count > 0)
        {
            var score = 1;
            foreach (var statement in globals) score += Branches(statement, statement);
            found.Add(new
            {
                file = relative,
                line = Line(globals[0]),
                startLine = Line(globals[0]),
                endLine = EndLine(globals[^1]),
                name = "<top-level statements>",
                complexity = score,
                hasBody = true,
            });
        }

        foreach (var node in root.DescendantNodes())
        {
            var name = MemberName(node);
            if (name is null) continue;
            found.Add(new
            {
                file = relative,
                line = Line(AnchorToken(node)),
                startLine = Line(node),
                endLine = EndLine(node),
                name,
                complexity = 1 + Branches(node, node),
                hasBody = HasBody(node),
            });
        }
        return found;
    }

    private static SyntaxToken AnchorToken(SyntaxNode node) => node switch
    {
        MethodDeclarationSyntax m => m.Identifier,
        ConstructorDeclarationSyntax c => c.Identifier,
        DestructorDeclarationSyntax d => d.Identifier,
        OperatorDeclarationSyntax o => o.OperatorToken,
        ConversionOperatorDeclarationSyntax v => v.OperatorKeyword,
        LocalFunctionStatementSyntax l => l.Identifier,
        AccessorDeclarationSyntax a => a.Keyword,
        PropertyDeclarationSyntax p => p.Identifier,
        IndexerDeclarationSyntax i => i.ThisKeyword,
        _ => node.GetFirstToken(),
    };

    private static bool HasBody(SyntaxNode node) => node switch
    {
        BaseMethodDeclarationSyntax m => m.Body is not null || m.ExpressionBody is not null,
        LocalFunctionStatementSyntax l => l.Body is not null || l.ExpressionBody is not null,
        AccessorDeclarationSyntax a => a.Body is not null || a.ExpressionBody is not null,
        PropertyDeclarationSyntax p => p.ExpressionBody is not null,
        IndexerDeclarationSyntax i => i.ExpressionBody is not null,
        _ => false,
    };

    private static string MemberName(SyntaxNode node) => node switch
    {
        MethodDeclarationSyntax m => Owner(m) + m.Identifier.ValueText,
        ConstructorDeclarationSyntax c => Owner(c) + ".ctor",
        DestructorDeclarationSyntax d => Owner(d) + ".dtor",
        OperatorDeclarationSyntax o => Owner(o) + "operator " + o.OperatorToken.ValueText,
        ConversionOperatorDeclarationSyntax v => Owner(v) + "operator " + v.Type,
        AccessorDeclarationSyntax a when a.Body is not null || a.ExpressionBody is not null =>
            Owner(a) + PropertyName(a) + "." + a.Keyword.ValueText,
        PropertyDeclarationSyntax p when p.ExpressionBody is not null => Owner(p) + p.Identifier.ValueText,
        IndexerDeclarationSyntax i when i.ExpressionBody is not null => Owner(i) + "this[]",
        _ => null,
    };

    private static string PropertyName(SyntaxNode node)
    {
        var owner = node.Ancestors().FirstOrDefault(a => a is BasePropertyDeclarationSyntax);
        return owner switch
        {
            PropertyDeclarationSyntax p => p.Identifier.ValueText,
            IndexerDeclarationSyntax => "this[]",
            EventDeclarationSyntax e => e.Identifier.ValueText,
            _ => "?",
        };
    }

    private static string Owner(SyntaxNode node)
    {
        var names = node.Ancestors().OfType<BaseTypeDeclarationSyntax>()
            .Select(t => t.Identifier.ValueText).Reverse().ToList();
        return names.Count == 0 ? "" : string.Join(".", names) + ".";
    }

    private static bool IsMemberBoundary(SyntaxNode node) =>
        node is MemberDeclarationSyntax and not GlobalStatementSyntax || node is AccessorDeclarationSyntax;

    private static int Branches(SyntaxNode member, SyntaxNode self)
    {
        var score = 0;
        foreach (var node in member.DescendantNodes(descendIntoChildren: child =>
                     ReferenceEquals(child, self) || !IsMemberBoundary(child)))
        {
            if (!ReferenceEquals(node, self) && IsMemberBoundary(node)) continue;
            score += node switch
            {
                IfStatementSyntax => 1,
                WhileStatementSyntax => 1,
                DoStatementSyntax => 1,
                ForStatementSyntax => 1,
                ForEachStatementSyntax => 1,
                ForEachVariableStatementSyntax => 1,
                CaseSwitchLabelSyntax => 1,
                CasePatternSwitchLabelSyntax => 1,
                SwitchExpressionArmSyntax => 1,
                CatchClauseSyntax => 1,
                CatchFilterClauseSyntax => 1,
                ConditionalExpressionSyntax => 1,
                ConditionalAccessExpressionSyntax => 1,
                BinaryExpressionSyntax b when b.IsKind(SyntaxKind.LogicalAndExpression)
                    || b.IsKind(SyntaxKind.LogicalOrExpression)
                    || b.IsKind(SyntaxKind.CoalesceExpression) => 1,
                AssignmentExpressionSyntax a when a.IsKind(SyntaxKind.CoalesceAssignmentExpression) => 1,
                _ => 0,
            };
        }
        return score;
    }

    private static object Depth(string file, CompilationUnitSyntax root)
    {
        var surface = new List<string>();
        foreach (var type in root.DescendantNodes().OfType<BaseTypeDeclarationSyntax>())
        {
            if (IsPrivate(type.Modifiers, type)) continue;
            var typeName = Owner(type) + type.Identifier.ValueText;
            surface.Add(typeName);
            if (type is RecordDeclarationSyntax record && record.ParameterList is not null)
            {
                foreach (var parameter in record.ParameterList.Parameters)
                {
                    surface.Add(typeName + "." + parameter.Identifier.ValueText);
                }
            }
            if (type is not TypeDeclarationSyntax declaration) continue;
            foreach (var member in declaration.Members)
            {
                if (member is BaseTypeDeclarationSyntax) continue;
                if (IsPrivate(member.Modifiers, member)) continue;
                foreach (var name in MemberNames(member)) surface.Add(typeName + "." + name);
            }
        }
        var statements = root.DescendantNodes().OfType<StatementSyntax>().Count(s => s is not BlockSyntax);
        var passThroughs = new List<object>();
        foreach (var method in root.DescendantNodes().OfType<MethodDeclarationSyntax>())
        {
            if (IsPassThrough(method, out var target))
            {
                passThroughs.Add(new { line = Line(method.Identifier), name = method.Identifier.ValueText, target });
            }
        }
        return new { file = Rel(file), @public = surface, statements, pass_throughs = passThroughs };
    }

    private static bool IsPrivate(SyntaxTokenList modifiers, SyntaxNode node)
    {
        if (modifiers.Any(SyntaxKind.PublicKeyword)) return false;
        if (modifiers.Any(SyntaxKind.InternalKeyword)) return false;
        if (modifiers.Any(SyntaxKind.ProtectedKeyword)) return false;
        if (modifiers.Any(SyntaxKind.PrivateKeyword)) return true;
        var parent = node.Ancestors().FirstOrDefault(a => a is BaseTypeDeclarationSyntax);
        if (parent is InterfaceDeclarationSyntax) return false;
        return parent is not null;
    }

    private static IEnumerable<string> MemberNames(MemberDeclarationSyntax member)
    {
        switch (member)
        {
            case MethodDeclarationSyntax m: yield return m.Identifier.ValueText; break;
            case PropertyDeclarationSyntax p: yield return p.Identifier.ValueText; break;
            case EventDeclarationSyntax e: yield return e.Identifier.ValueText; break;
            case IndexerDeclarationSyntax: yield return "this[]"; break;
            case ConstructorDeclarationSyntax: yield return ".ctor"; break;
            case OperatorDeclarationSyntax o: yield return "operator " + o.OperatorToken.ValueText; break;
            case ConversionOperatorDeclarationSyntax: yield return "operator"; break;
            case DelegateDeclarationSyntax d: yield return d.Identifier.ValueText; break;
            case FieldDeclarationSyntax f:
                foreach (var v in f.Declaration.Variables) yield return v.Identifier.ValueText;
                break;
            case EventFieldDeclarationSyntax ef:
                foreach (var v in ef.Declaration.Variables) yield return v.Identifier.ValueText;
                break;
        }
    }

    private static bool IsPassThrough(MethodDeclarationSyntax method, out string target)
    {
        target = null;
        var parameters = method.ParameterList.Parameters;
        if (parameters.Count == 0) return false;
        if (method.Modifiers.Any(SyntaxKind.PartialKeyword)) return false;
        ExpressionSyntax expression = null;
        if (method.ExpressionBody is not null) expression = method.ExpressionBody.Expression;
        else if (method.Body is { Statements.Count: 1 })
        {
            expression = method.Body.Statements[0] switch
            {
                ReturnStatementSyntax r => r.Expression,
                ExpressionStatementSyntax e => e.Expression,
                _ => null,
            };
        }
        while (expression is AwaitExpressionSyntax awaited) expression = awaited.Expression;
        if (expression is not InvocationExpressionSyntax call) return false;
        var arguments = call.ArgumentList.Arguments;
        if (arguments.Count != parameters.Count) return false;
        for (var i = 0; i < arguments.Count; i++)
        {
            var argument = arguments[i];
            if (argument.NameColon is not null) return false;
            if (!argument.RefKindKeyword.IsKind(SyntaxKind.None)) return false;
            if (argument.Expression is not IdentifierNameSyntax id) return false;
            if (id.Identifier.ValueText != parameters[i].Identifier.ValueText) return false;
        }
        target = call.Expression.ToString();
        return true;
    }

    private static object Deps(List<string> files, Dictionary<string, CompilationUnitSyntax> trees)
    {
        var index = new Dictionary<string, string>(StringComparer.Ordinal);
        var namespaces = new HashSet<string>(StringComparer.Ordinal);
        foreach (var file in files)
        {
            foreach (var type in trees[file].DescendantNodes().OfType<BaseTypeDeclarationSyntax>())
            {
                var full = QualifiedName(type);
                if (!index.ContainsKey(full)) index[full] = file;
            }
            foreach (var declared in trees[file].DescendantNodes().OfType<BaseNamespaceDeclarationSyntax>())
            {
                namespaces.Add(declared.Name.ToString());
            }
        }

        var records = new List<object>();
        var edges = new List<object>();
        foreach (var file in files)
        {
            var root = trees[file];
            var relative = Rel(file);
            var ns = root.DescendantNodes().OfType<BaseNamespaceDeclarationSyntax>()
                .Select(n => n.Name.ToString()).FirstOrDefault() ?? "";
            var declaredHere = root.DescendantNodes().OfType<BaseTypeDeclarationSyntax>()
                .Select(QualifiedName).ToList();

            var usings = new List<object>();
            var prefixes = new List<string>();
            foreach (var part in Prefixes(ns)) prefixes.Add(part);
            foreach (var directive in root.DescendantNodes().OfType<UsingDirectiveSyntax>())
            {
                var name = directive.Name?.ToString() ?? "";
                var isStatic = !directive.StaticKeyword.IsKind(SyntaxKind.None);
                var kind = isStatic ? "using-static" : directive.Alias is null ? "using" : "alias";
                usings.Add(new
                {
                    name,
                    line = Line(directive),
                    kind,
                    global = !directive.GlobalKeyword.IsKind(SyntaxKind.None),
                    @static = isStatic,
                    @internal = IsInternalNamespace(name, namespaces),
                });
                if (kind == "using" && name.Length > 0) prefixes.Add(name);
            }
            prefixes.Add("");

            var own = new HashSet<string>(root.DescendantNodes().OfType<BaseTypeDeclarationSyntax>()
                .Select(t => t.Identifier.ValueText), StringComparer.Ordinal);
            var refs = new SortedSet<string>(StringComparer.Ordinal);
            var found = new Dictionary<string, (string To, string Symbol, int Line)>(StringComparer.Ordinal);

            foreach (var node in root.DescendantNodes())
            {
                if (node.Ancestors().OfType<UsingDirectiveSyntax>().Any()) continue;
                string name = null;
                var qualified = false;
                switch (node)
                {
                    case QualifiedNameSyntax q when q.Parent is not QualifiedNameSyntax:
                        name = q.ToString();
                        qualified = true;
                        break;
                    case GenericNameSyntax g when g.Parent is not QualifiedNameSyntax:
                        name = g.Identifier.ValueText;
                        break;
                    case IdentifierNameSyntax id when id.Parent is not QualifiedNameSyntax
                        && (id.Parent is not MemberAccessExpressionSyntax member || !ReferenceEquals(member.Name, id)):
                        name = id.Identifier.ValueText;
                        break;
                }
                if (string.IsNullOrEmpty(name)) continue;
                if (qualified)
                {
                    name = WithoutGenericArguments(name);
                }
                else
                {
                    if (!char.IsUpper(name[0])) continue;
                    if (own.Contains(name)) continue;
                    refs.Add(name);
                }

                var target = Resolve(name, prefixes, index);
                if (target is null) continue;
                if (declaredHere.Contains(target.Value.Symbol)) continue;
                if (string.Equals(target.Value.File, file, StringComparison.Ordinal)) continue;
                var key = Rel(target.Value.File) + "|" + target.Value.Symbol;
                if (!found.ContainsKey(key)) found[key] = (Rel(target.Value.File), target.Value.Symbol, Line(node));
            }

            foreach (var edge in found.Values.OrderBy(e => e.Line).ThenBy(e => e.Symbol, StringComparer.Ordinal))
            {
                edges.Add(new { from = relative, to = edge.To, symbol = edge.Symbol, line = edge.Line });
            }
            records.Add(new { path = relative, @namespace = ns, types = declaredHere, usings, refs = refs.ToList() });
        }
        return new { files = records, edges };
    }

    private static string WithoutGenericArguments(string name)
    {
        var cut = name.IndexOf('<');
        return cut > 0 ? name[..cut] : name;
    }

    private static (string File, string Symbol)? Resolve(
        string name, List<string> prefixes, Dictionary<string, string> index)
    {
        foreach (var spelling in new[] { name, name + "Attribute" })
        {
            foreach (var prefix in prefixes)
            {
                var candidate = prefix.Length == 0 ? spelling : prefix + "." + spelling;
                if (index.TryGetValue(candidate, out var file)) return (file, candidate);
            }
        }
        return null;
    }

    private static IEnumerable<string> Prefixes(string ns)
    {
        while (ns.Length > 0)
        {
            yield return ns;
            var cut = ns.LastIndexOf('.');
            ns = cut < 0 ? "" : ns[..cut];
        }
    }

    private static bool IsInternalNamespace(string name, HashSet<string> namespaces)
    {
        if (name.Length == 0) return false;
        foreach (var declared in namespaces)
        {
            if (declared == name || declared.StartsWith(name + ".", StringComparison.Ordinal)) return true;
        }
        return false;
    }

    private static string QualifiedName(BaseTypeDeclarationSyntax type)
    {
        var builder = new StringBuilder();
        var ns = type.Ancestors().OfType<BaseNamespaceDeclarationSyntax>()
            .Select(n => n.Name.ToString()).Reverse().ToList();
        if (ns.Count > 0) builder.Append(string.Join(".", ns)).Append('.');
        builder.Append(Owner(type)).Append(type.Identifier.ValueText);
        return builder.ToString();
    }

    private static readonly Regex Word = new(@"[A-Za-z_][A-Za-z0-9_]*", RegexOptions.Compiled);

    private static List<object> Dead(List<string> files, Dictionary<string, CompilationUnitSyntax> trees)
    {
        var used = new HashSet<string>(StringComparer.Ordinal);
        foreach (var root in trees.Values)
        {
            foreach (var node in root.DescendantNodes())
            {
                switch (node)
                {
                    case IdentifierNameSyntax id: used.Add(id.Identifier.ValueText); break;
                    case GenericNameSyntax g: used.Add(g.Identifier.ValueText); break;
                }
            }
            foreach (var token in root.DescendantTokens())
            {
                if (token.IsKind(SyntaxKind.StringLiteralToken)
                    || token.IsKind(SyntaxKind.InterpolatedStringTextToken)
                    || token.IsKind(SyntaxKind.SingleLineRawStringLiteralToken)
                    || token.IsKind(SyntaxKind.MultiLineRawStringLiteralToken))
                {
                    foreach (Match match in Word.Matches(token.ValueText)) used.Add(match.Value);
                }
            }
        }

        var found = new List<object>();
        foreach (var file in files)
        {
            var relative = Rel(file);
            foreach (var member in trees[file].DescendantNodes().OfType<MemberDeclarationSyntax>())
            {
                var owner = member.Ancestors().OfType<TypeDeclarationSyntax>().FirstOrDefault();
                if (owner is null) continue;
                if (MayContinueInAFileThatWasNotScanned(owner)) continue;
                if (!EffectivelyPrivate(member.Modifiers, owner)) continue;

                if (member is MethodDeclarationSyntax method)
                {
                    if (MayBeBoundByAnAttribute(method.AttributeLists)) continue;
                    if (method.ExplicitInterfaceSpecifier is not null) continue;
                    if (method.Modifiers.Any(SyntaxKind.PartialKeyword)
                        || method.Modifiers.Any(SyntaxKind.ExternKeyword)
                        || method.Modifiers.Any(SyntaxKind.AbstractKeyword)
                        || method.Modifiers.Any(SyntaxKind.VirtualKeyword)
                        || method.Modifiers.Any(SyntaxKind.OverrideKeyword)) continue;
                    var name = method.Identifier.ValueText;
                    if (name == "Main") continue;
                    if (used.Contains(name)) continue;
                    found.Add(new { file = relative, line = Line(method.Identifier), kind = "private method", name });
                }
                else if (member is FieldDeclarationSyntax field)
                {
                    if (MayBeBoundByAnAttribute(field.AttributeLists)) continue;
                    if (MayBeBoundByAnAttribute(owner.AttributeLists)) continue;
                    foreach (var variable in field.Declaration.Variables)
                    {
                        var name = variable.Identifier.ValueText;
                        if (used.Contains(name)) continue;
                        found.Add(new { file = relative, line = Line(variable.Identifier), kind = "private field", name });
                    }
                }
            }
        }
        return found;
    }

    private static bool MayContinueInAFileThatWasNotScanned(TypeDeclarationSyntax owner) =>
        owner.Modifiers.Any(SyntaxKind.PartialKeyword);

    private static bool MayBeBoundByAnAttribute(SyntaxList<AttributeListSyntax> attributes) =>
        attributes.Count > 0;

    private static bool EffectivelyPrivate(SyntaxTokenList modifiers, TypeDeclarationSyntax owner)
    {
        if (modifiers.Any(SyntaxKind.PublicKeyword)) return false;
        if (modifiers.Any(SyntaxKind.InternalKeyword)) return false;
        if (modifiers.Any(SyntaxKind.ProtectedKeyword)) return false;
        if (modifiers.Any(SyntaxKind.PrivateKeyword)) return true;
        return owner is ClassDeclarationSyntax or StructDeclarationSyntax or RecordDeclarationSyntax;
    }
}
