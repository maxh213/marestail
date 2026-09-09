defmodule Comments do
  def run(files) do
    results = Enum.flat_map(files, &scan_file/1)
    IO.puts(:json.encode(results))
  end

  defp scan_file(file) do
    case File.read(file) do
      {:ok, text} ->
        case Code.string_to_quoted_with_comments(text, unquote_literals: false) do
          {:ok, ast, comments} ->
            comment_hits =
              comments
              |> Enum.reject(fn c -> c.line == 1 and String.starts_with?(c.text, "#!") end)
              |> Enum.map(fn c -> %{"file" => file, "line" => c.line, "text" => c.text} end)

            {_, doc_hits} =
              Macro.prewalk(ast, [], fn
                {:@, meta, [{name, _, [doc]}]} = node, acc
                when name in [:doc, :moduledoc] and (is_binary(doc) or is_boolean(doc)) ->
                  {node, [%{"file" => file, "line" => meta[:line] || 0, "text" => to_string(name)} | acc]}

                node, acc ->
                  {node, acc}
              end)

            comment_hits ++ doc_hits

          _ ->
            []
        end
      _ ->
        []
    end
  end
end

Comments.run(System.argv())
