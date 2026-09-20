[coverdata, out_json | _] = System.argv()
Path.wildcard("_build/**/ebin") |> Enum.each(&:code.add_pathz(String.to_charlist(&1)))
{:ok, dev} = StringIO.open("")
old_leader = Process.group_leader()
Process.group_leader(self(), dev)
:cover.start()
:cover.import(String.to_charlist(coverdata))

modules = :cover.imported_modules()
files =
  Enum.reduce(modules, %{}, fn mod, acc ->
    case :cover.analyse(mod, :calls, :line) do
      {:ok, lines} ->
        source =
          try do
            to_string(mod.module_info(:compile)[:source])
          rescue
            _ -> nil
          end
        if source && File.exists?(source) do
          code_lines = Enum.reject(lines, fn {{_, line}, _} -> line == 0 end)
          missing =
            code_lines
            |> Enum.filter(fn {{_, _}, calls} -> calls == 0 end)
            |> Enum.map(fn {{_, line}, _} -> line end)
            |> Enum.sort()
          total = length(code_lines)
          covered = total - length(missing)
          pct = if total > 0, do: (covered / total) * 100.0, else: 100.0
          Map.put(acc, source, %{
            "missing_lines" => missing,
            "covered" => covered,
            "total" => total,
            "percent_covered" => pct
          })
        else
          acc
        end
      _ ->
        acc
    end
  end)

Process.group_leader(self(), old_leader)
tot_covered = Enum.sum(Enum.map(Map.values(files), & &1["covered"]))
tot_lines = Enum.sum(Enum.map(Map.values(files), & &1["total"]))
pct = if tot_lines > 0, do: (tot_covered / tot_lines) * 100.0, else: 100.0
result = %{"totals" => %{"percent_covered" => pct}, "files" => files}
json = :json.encode(result)
if out_json == "-", do: IO.puts(json), else: File.write!(out_json, json)
