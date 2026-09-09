import argv
import glance
import gleam/io
import gleam/json
import gleam/list
import gleam/option.{None, Some}
import gleam/string
import simplifile

pub fn main() -> Nil {
  case argv.load().arguments {
    ["complexity", ..files] -> emit(complexity_files(files))
    ["depth", ..files] -> emit(depth_files(files))
    ["deps", ..files] -> emit(deps_files(files))
    ["dead", ..files] -> emit(dead_files(files))
    _ -> {
      io.println_error(
        "usage: marestail_gl_scan <complexity|depth|deps|dead> <files...>",
      )
      halt(1)
    }
  }
}

fn emit(value: json.Json) -> Nil {
  io.println(json.to_string(value))
}

@external(erlang, "erlang", "halt")
fn halt(code: Int) -> Nil

fn parse(path: String) -> Result(#(String, glance.Module), String) {
  case simplifile.read(path) {
    Ok(src) ->
      case glance.module(src) {
        Ok(mod) -> Ok(#(src, mod))
        Error(_) -> Error("parse failed: " <> path)
      }
    Error(_) -> Error("read failed: " <> path)
  }
}

fn complexity_files(files: List(String)) -> json.Json {
  files
  |> list.flat_map(fn(path) {
    case parse(path) {
      Ok(#(src, mod)) -> complexity_module(path, src, mod)
      Error(_) -> []
    }
  })
  |> json.preprocessed_array
}

fn complexity_module(
  path: String,
  src: String,
  mod: glance.Module,
) -> List(json.Json) {
  list.map(mod.functions, fn(def) {
    let glance.Definition(_, fun) = def
    let start = byte_to_line(src, fun.location.start)
    let end = byte_to_line(src, fun.location.end)
    let cc = function_complexity(fun)
    json.object([
      #("file", json.string(path)),
      #("line", json.int(start)),
      #("end_line", json.int(end)),
      #("name", json.string(fun.name)),
      #("complexity", json.int(cc)),
    ])
  })
}

fn function_complexity(fun: glance.Function) -> Int {
  1 + list.fold(fun.body, 0, fn(acc, stmt) { acc + statement_complexity(stmt) })
}

fn statement_complexity(stmt: glance.Statement) -> Int {
  case stmt {
    glance.Use(_, _, function) -> expression_complexity(function)
    glance.Assignment(_, _, _, _, value) -> expression_complexity(value)
    glance.Assert(_, expression, _) -> expression_complexity(expression)
    glance.Expression(expression) -> expression_complexity(expression)
  }
}

fn expression_complexity(expr: glance.Expression) -> Int {
  case expr {
    glance.Case(_, subjects, clauses) -> {
      let subject_cc =
        list.fold(subjects, 0, fn(acc, s) { acc + expression_complexity(s) })
      let clause_cc =
        list.fold(clauses, 0, fn(acc, clause) {
          acc + 1 + clause_complexity(clause)
        })
      subject_cc + clause_cc
    }
    glance.BinaryOperator(_, name, left, right) -> {
      let op = case name {
        glance.And | glance.Or -> 1
        _ -> 0
      }
      op + expression_complexity(left) + expression_complexity(right)
    }
    glance.Block(_, statements) ->
      list.fold(statements, 0, fn(acc, stmt) {
        acc + statement_complexity(stmt)
      })
    glance.NegateInt(_, value) | glance.NegateBool(_, value) ->
      expression_complexity(value)
    glance.Tuple(_, elements) ->
      list.fold(elements, 0, fn(acc, e) { acc + expression_complexity(e) })
    glance.List(_, elements, rest) -> {
      let rest_cc = case rest {
        Some(e) -> expression_complexity(e)
        None -> 0
      }
      list.fold(elements, rest_cc, fn(acc, e) { acc + expression_complexity(e) })
    }
    glance.Fn(_, _, _, body) ->
      list.fold(body, 0, fn(acc, stmt) { acc + statement_complexity(stmt) })
    glance.RecordUpdate(_, _, _, record, fields) -> {
      let field_cc =
        list.fold(fields, 0, fn(acc, field) {
          case field.item {
            Some(e) -> acc + expression_complexity(e)
            None -> acc
          }
        })
      expression_complexity(record) + field_cc
    }
    glance.FieldAccess(_, container, _) -> expression_complexity(container)
    glance.Call(_, function, arguments) -> {
      let arg_cc =
        list.fold(arguments, 0, fn(acc, field) {
          case field {
            glance.LabelledField(_, _, item)
            | glance.UnlabelledField(item) -> acc + expression_complexity(item)
            glance.ShorthandField(_, _) -> acc
          }
        })
      expression_complexity(function) + arg_cc
    }
    glance.TupleIndex(_, tuple, _) -> expression_complexity(tuple)
    glance.FnCapture(_, _, function, before, after) -> {
      let fold_fields = fn(acc, field) {
        case field {
          glance.LabelledField(_, _, item) | glance.UnlabelledField(item) ->
            acc + expression_complexity(item)
          glance.ShorthandField(_, _) -> acc
        }
      }
      expression_complexity(function)
      + list.fold(before, 0, fold_fields)
      + list.fold(after, 0, fold_fields)
    }
    glance.BitString(_, segments) ->
      list.fold(segments, 0, fn(acc, segment) {
        let #(value, _) = segment
        acc + expression_complexity(value)
      })
    glance.Echo(_, expression, _) ->
      case expression {
        Some(e) -> expression_complexity(e)
        None -> 0
      }
    glance.Panic(_, _) | glance.Todo(_, _) -> 0
    glance.Int(_, _)
    | glance.Float(_, _)
    | glance.String(_, _)
    | glance.Variable(_, _) -> 0
  }
}

fn clause_complexity(clause: glance.Clause) -> Int {
  let guard_cc = case clause.guard {
    Some(g) -> expression_complexity(g)
    None -> 0
  }
  guard_cc + expression_complexity(clause.body)
}

fn depth_files(files: List(String)) -> json.Json {
  files
  |> list.filter_map(fn(path) {
    case parse(path) {
      Ok(#(src, mod)) -> Ok(depth_module(path, src, mod))
      Error(_) -> Error(Nil)
    }
  })
  |> json.preprocessed_array
}

fn depth_module(path: String, src: String, mod: glance.Module) -> json.Json {
  let public =
    list.filter_map(mod.functions, fn(def) {
      let glance.Definition(_, fun) = def
      case fun.publicity {
        glance.Public -> Ok(fun.name)
        glance.Private -> Error(Nil)
      }
    })
  let statements =
    list.fold(mod.functions, 0, fn(acc, def) {
      let glance.Definition(_, fun) = def
      acc + list.length(fun.body)
    })
  let pass_throughs =
    list.filter_map(mod.functions, fn(def) {
      let glance.Definition(_, fun) = def
      case is_pass_through(fun) {
        True -> {
          let line = byte_to_line(src, fun.location.start)
          Ok(json.object([
            #("line", json.int(line)),
            #("name", json.string(fun.name)),
          ]))
        }
        False -> Error(Nil)
      }
    })
  json.object([
    #("file", json.string(path)),
    #("public", json.array(public, json.string)),
    #("statements", json.int(statements)),
    #("pass_throughs", json.preprocessed_array(pass_throughs)),
  ])
}

fn is_pass_through(fun: glance.Function) -> Bool {
  case fun.body, fun.parameters {
    [glance.Expression(glance.Call(_, _function, arguments))], params -> {
      let param_names =
        list.filter_map(params, fn(p) {
          case p.name {
            glance.Named(name) -> Ok(name)
            glance.Discarded(_) -> Error(Nil)
          }
        })
      let arg_names =
        list.filter_map(arguments, fn(field) {
          case field {
            glance.UnlabelledField(glance.Variable(_, name)) -> Ok(name)
            glance.LabelledField(_, _, glance.Variable(_, name)) -> Ok(name)
            _ -> Error(Nil)
          }
        })
      param_names != []
      && list.length(arg_names) == list.length(arguments)
      && arg_names == param_names
    }
    _, _ -> False
  }
}

fn deps_files(files: List(String)) -> json.Json {
  files
  |> list.flat_map(fn(path) {
    case parse(path) {
      Ok(#(src, mod)) -> deps_module(path, src, mod)
      Error(_) -> []
    }
  })
  |> json.preprocessed_array
}

fn deps_module(
  path: String,
  src: String,
  mod: glance.Module,
) -> List(json.Json) {
  list.map(mod.imports, fn(def) {
    let glance.Definition(_, imp) = def
    let line = byte_to_line(src, imp.location.start)
    json.object([
      #("from", json.string(path)),
      #("to", json.string(imp.module)),
      #("line", json.int(line)),
    ])
  })
}

fn dead_files(files: List(String)) -> json.Json {
  let parsed =
    list.filter_map(files, fn(path) {
      case parse(path) {
        Ok(#(src, mod)) -> Ok(#(path, src, mod))
        Error(_) -> Error(Nil)
      }
    })
  let all_text =
    list.map(parsed, fn(item) {
      let #(_, src, _) = item
      src
    })
    |> string.join("\n")
  parsed
  |> list.flat_map(fn(item) {
    let #(path, src, mod) = item
    list.filter_map(mod.functions, fn(def) {
      let glance.Definition(_, fun) = def
      case fun.publicity {
        glance.Private -> {
          let mentions = count_mentions(all_text, fun.name)
          // definition itself counts as one mention in this file's source
          case mentions <= 1 {
            True -> {
              let line = byte_to_line(src, fun.location.start)
              Ok(json.object([
                #("file", json.string(path)),
                #("line", json.int(line)),
                #("name", json.string(fun.name)),
              ]))
            }
            False -> Error(Nil)
          }
        }
        glance.Public -> Error(Nil)
      }
    })
  })
  |> json.preprocessed_array
}

fn count_mentions(src: String, name: String) -> Int {
  count_mentions_loop(src, name, 0)
}

fn count_mentions_loop(src: String, name: String, acc: Int) -> Int {
  case string.split_once(src, name) {
    Ok(#(_, rest)) -> count_mentions_loop(rest, name, acc + 1)
    Error(_) -> acc
  }
}

fn byte_to_line(src: String, offset: Int) -> Int {
  src
  |> string.to_utf_codepoints
  |> list.take(offset)
  |> list.fold(1, fn(line, cp) {
    case string.utf_codepoint_to_int(cp) {
      10 -> line + 1
      _ -> line
    }
  })
}
