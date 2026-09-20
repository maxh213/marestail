defmodule Depth do
  @operators [
    :+, :-, :*, :/, :++, :--, :<>,
    :==, :!=, :===, :!==, :<, :>, :<=, :>=,
    :&&, :||, :and, :or, :not
  ]

  def run(files) do
    results = Enum.map(files, &analyse_file/1)
    IO.puts(:json.encode(results))
  end

  defp analyse_file(file) do
    case File.read(file) do
      {:ok, text} ->
        case Code.string_to_quoted(text) do
          {:ok, ast} ->
            {_, acc} = Macro.prewalk(ast, %{"public" => [], "statements" => 0, "pass_throughs" => []}, fn
              {:def, meta, [{name, _, args}, [do: body]]} = node, state ->
                arity = if is_list(args), do: length(args), else: 0
                fn_name = "#{name}/#{arity}"
                pass = if is_pass_through?(args, body), do: ["#{file}:#{meta[:line] || 0} #{fn_name} only forwards its arguments"], else: []
                new_state = %{state |
                  "public" => [fn_name | state["public"]],
                  "statements" => state["statements"] + 1,
                  "pass_throughs" => pass ++ state["pass_throughs"]
                }
                {node, new_state}

              {:defp, _meta, _} = node, state ->
                {node, %{state | "statements" => state["statements"] + 1}}

              node, state ->
                {node, state}
            end)
            Map.put(acc, "file", file)

          _ ->
            %{"file" => file, "public" => [], "statements" => 0, "pass_throughs" => []}
        end
      _ ->
        %{"file" => file, "public" => [], "statements" => 0, "pass_throughs" => []}
    end
  end

  defp is_pass_through?(args, body) when is_list(args) and length(args) > 0 do
    case body do
      {{:., _, [_, target_name]}, _, call_args} ->
        target_name not in @operators and args_match?(args, call_args)

      {target_name, _, call_args} when is_list(call_args) ->
        target_name not in @operators and args_match?(args, call_args)

      _ ->
        false
    end
  end
  defp is_pass_through?(_, _), do: false

  defp args_match?(args, call_args) do
    arg_names = Enum.map(args, fn {name, _, _} when is_atom(name) -> name; _ -> nil end)
    call_names = Enum.map(call_args, fn {name, _, _} when is_atom(name) -> name; _ -> nil end)
    arg_names == call_names and nil not in arg_names
  end
end

Depth.run(System.argv())
