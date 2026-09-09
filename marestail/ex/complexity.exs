defmodule Complexity do
  def run(files) do
    results = Enum.flat_map(files, &analyse_file/1)
    IO.puts(:json.encode(results))
  end

  defp analyse_file(file) do
    case File.read(file) do
      {:ok, text} ->
        case Code.string_to_quoted(text) do
          {:ok, ast} ->
            extract_functions(ast, file)
          _ ->
            []
        end
      _ ->
        []
    end
  end

  defp extract_functions(ast, file) do
    {_, functions} = Macro.prewalk(ast, [], fn
      {def_op, meta, [{name, _, args}, [do: body]]} = node, acc when def_op in [:def, :defp, :defmacro] ->
        arity = if is_list(args), do: length(args), else: 0
        cc = count_complexity(body)
        fn_info = %{
          "file" => file,
          "line" => meta[:line] || 0,
          "name" => "#{name}/#{arity}",
          "complexity" => cc
        }
        {node, [fn_info | acc]}
      node, acc ->
        {node, acc}
    end)
    Enum.reverse(functions)
  end

  defp count_complexity(ast) do
    {_, cc} = Macro.prewalk(ast, 1, fn
      {op, _, _} = node, acc when op in [:if, :unless, :case, :cond, :with, :&&, :||, :when] ->
        {node, acc + 1}
      node, acc ->
        {node, acc}
    end)
    cc
  end
end

Complexity.run(System.argv())
