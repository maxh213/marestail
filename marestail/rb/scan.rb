#!/usr/bin/env ruby
# frozen_string_literal: true

require "json"
require "ripper"
require "pathname"

MAGIC = /\A#\s*(frozen_string_literal|encoding|coding|warn_indent|typed|shareable_constant_value):/i
SKIP_DIR = %w[vendor node_modules tmp log coverage .git .marestail spec test]

def walk(node, &block)
  return unless node.is_a?(Array)

  yield node
  node.each { |child| walk(child, &block) }
end

def ident(node)
  return node[1] if node.is_a?(Array) && node[0] == :@ident
  return ident(node[1]) if node.is_a?(Array) && node[1].is_a?(Array)

  nil
end

def line_of(node)
  return node[2][0] if node.is_a?(Array) && node[0].to_s.start_with?("@") && node[2].is_a?(Array)
  return nil unless node.is_a?(Array)

  node.each do |child|
    found = line_of(child)
    return found if found
  end
  nil
end

def params_of(node)
  names = []
  walk(node) do |child|
    names << child[1] if child[0] == :@ident
  end
  names
end

def statements_of_body(body)
  return [] unless body.is_a?(Array)

  body[0] == :bodystmt ? Array(body[1]) : Array(body)
end

def call_args(call)
  return [] unless call.is_a?(Array)

  args = call[3] || call[2]
  return [] unless args.is_a?(Array)

  list = args[0] == :args_add_block ? args[1] : args
  list = list[1] if list.is_a?(Array) && list[0] == :args_add_star
  Array(list).filter_map { |a| ident(a) }
end

def command_call?(node)
  node.is_a?(Array) && %i[command command_call fcall vcall call].include?(node[0])
end

def complexity_of(node)
  return 0 unless node.is_a?(Array)
  return node.sum { |child| complexity_of(child) } unless node[0].is_a?(Symbol)

  extra = case node[0]
          when :if, :unless, :elsif, :while, :until, :for, :when, :rescue, :if_mod, :unless_mod, :while_mod, :until_mod, :ifop
            1
          when :binary
            %i[&& and || or].include?(node[2]) ? 1 : 0
          else
            0
          end
  extra + node.drop(1).sum { |child| complexity_of(child) }
end

def defs_in(tree)
  found = []
  walk(tree) do |node|
    next unless node[0] == :def || node[0] == :defs

    name_node = node[0] == :def ? node[1] : node[3]
    params = node[0] == :def ? node[2] : node[4]
    body = node[0] == :def ? node[3] : node[5]
    found << { name: ident(name_node) || "unknown", line: line_of(name_node) || 0, params: params, body: body }
  end
  found
end

def comments(file)
  text = File.read(file)
  hits = []
  Ripper.lex(text).each do |pos, type, tok, _|
    next unless type == :on_comment || type == :on_embdoc || type == :on_embdoc_beg
    next if MAGIC.match?(tok)

    hits << { "file" => file, "line" => pos[0], "text" => tok.strip[0, 80] }
  end
  hits
end

def complexity(file)
  tree = Ripper.sexp(File.read(file))
  return [] unless tree

  defs_in(tree).map do |fn|
    { "file" => file, "line" => fn[:line], "name" => fn[:name], "complexity" => 1 + complexity_of(fn[:body]) }
  end
end

def depth(file)
  tree = Ripper.sexp(File.read(file))
  return { "file" => file, "public" => [], "statements" => 0, "pass_throughs" => [] } unless tree

  public = []
  pass = []
  stmts = 0
  private_mode = false
  walk(tree) do |node|
    stmts += 1 if node[0] == :void_stmt || node[0] == :assign || node[0] == :command || node[0] == :def
    if node[0] == :vcall && ident(node) == "private"
      private_mode = true
    end
    next unless node[0] == :def

    fn = ident(node[1])
    public << fn unless private_mode || (fn && fn.start_with?("_"))
    body_stmts = statements_of_body(node[3]).reject { |s| s[0] == :void_stmt }
    next unless body_stmts.size == 1 && command_call?(body_stmts[0])

    params = params_of(node[2])
    args = call_args(body_stmts[0])
    if params == args
      pass << "#{file}:#{line_of(node[1])} #{fn} only forwards its arguments"
    end
  end
  { "file" => file, "public" => public.compact, "statements" => [stmts, 1].max, "pass_throughs" => pass }
end

def constant_name(node)
  return node[1] if node.is_a?(Array) && node[0] == :@const
  return constant_name(node[1]) if node.is_a?(Array) && node[0] == :var_ref
  return [constant_name(node[1]), constant_name(node[2])].compact.join("::") if node.is_a?(Array) && node[0] == :const_path_ref
  return constant_name(node[1]) if node.is_a?(Array) && node[0] == :const_ref

  nil
end

def zeitwerk_name(root, file)
  rel = Pathname.new(file).relative_path_from(Pathname.new(root)).to_s.sub(/\.rb\z/, "")
  parts = rel.split("/")
  if parts[0] == "app" && parts.size >= 3
    parts = parts[2..]
    parts.delete("concerns")
  elsif parts[0] == "lib"
    parts = parts[1..]
  end
  parts.map { |part| part.split("_").map(&:capitalize).join }.join("::")
end

def deps(root, files)
  index = {}
  files.each { |file| index[zeitwerk_name(root, file)] = file }
  findings = []
  files.each do |file|
    tree = Ripper.sexp(File.read(file))
    next unless tree

    used = []
    walk(tree) do |node|
      name = constant_name(node)
      used << name if name && name != zeitwerk_name(root, file)
    end
    used.uniq.each do |name|
      target = index[name]
      next unless target
      next if target == file

      findings << { "from" => file, "to" => target, "constant" => name, "line" => 1 }
    end
  end
  findings
end

def dead(files)
  defs = []
  calls = Hash.new(0)
  files.each do |file|
    tree = Ripper.sexp(File.read(file))
    next unless tree

    private_mode = false
    walk(tree) do |node|
      private_mode = true if node[0] == :vcall && ident(node) == "private"
      if node[0] == :def
        defs << { "file" => file, "line" => line_of(node[1]) || 0, "name" => ident(node[1]), "private" => private_mode || ident(node[1]).to_s.start_with?("_") }
      end
      if %i[fcall command vcall call command_call].include?(node[0])
        name = ident(node[1]) || ident(node)
        calls[name] += 1 if name
      end
    end
  end
  defs.select { |d| d["private"] && calls[d["name"]].to_i.zero? }.map do |d|
    "#{d['file']}:#{d['line']} unused private method '#{d['name']}'"
  end
end

mode = ARGV.shift
files = ARGV
case mode
when "comments"
  print JSON.generate(files.flat_map { |f| comments(f) })
when "complexity"
  print JSON.generate(files.flat_map { |f| complexity(f) })
when "depth"
  print JSON.generate(files.map { |f| depth(f) })
when "deps"
  root = files.shift
  print JSON.generate(deps(root, files))
when "dead"
  print JSON.generate(dead(files))
else
  warn "usage: scan.rb comments|complexity|depth|deps|dead FILE..."
  exit 1
end
