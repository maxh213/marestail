defmodule Marestail.DeadCode do
  @skip_names [:__info__, :module_info, :__struct__, :__impl__, :__protocol__, :__using__, :__before_compile__, :__after_compile__, :__on_definition__, :__deriving__, :behaviour_info, :__changeset__, :__schema__, :__mix_recompile__?, :__components__, :__live__, :__phoenix_verify_routes__]
  @skip_name_arity [{:child_spec, 1}, {:start_link, 0}, {:start_link, 1}, {:start_link, 2}, {:start, 2}, {:stop, 1}]
  @phoenix ~w(Controller Live LiveView View HTML JSON Router Endpoint Telemetry Presence Channel Socket Plug Mailer Components Component Layouts ErrorHTML ErrorJSON)

  def main(argv) do
    {opts, _, _} = OptionParser.parse(argv, strict: [out: :string, ignore_modules: :string, ignore: :string, preset: :string])
    ignore_modules = compile_patterns(opts[:ignore_modules], opts[:preset])
    ignore = String.split(opts[:ignore] || "", ",", trim: true)
    beams = own_beams()
    modules = Enum.map(beams, &load/1) |> Enum.reject(&is_nil/1)
    used = modules |> Enum.flat_map(& &1.calls) |> MapSet.new()
    dead =
      for m <- modules, not ignored_module?(m.module, ignore_modules), {name, arity} <- m.exports,
          not skip?(name, arity), {name, arity} not in m.callbacks,
          not MapSet.member?(used, {m.module, name, arity}),
          "#{inspect(m.module)}.#{name}/#{arity}" not in ignore do
        %{module: inspect(m.module), function: name, arity: arity, file: m.file, line: Map.get(m.lines, {name, arity}, 0)}
      end
    json = "[" <> Enum.map_join(dead, ",", &encode/1) <> "]"
    if opts[:out], do: File.write!(opts[:out], json), else: IO.puts(json)
  end

  defp encode(entry) do
    fields = Enum.map(entry, fn {key, value} -> ~s("#{key}":) <> encode_value(value) end)
    "{" <> Enum.join(fields, ",") <> "}"
  end

  defp encode_value(value) when is_integer(value), do: Integer.to_string(value)
  defp encode_value(value), do: inspect(to_string(value))

  defp own_beams do
    apps = case Mix.Project.apps_paths() do
      nil -> [Mix.Project.config()[:app]]
      paths -> Map.keys(paths)
    end
    Enum.flat_map(apps, fn app -> Path.wildcard(Path.join([Mix.Project.build_path(), "lib", to_string(app), "ebin", "*.beam"])) end)
  end

  defp load(path) do
    case :beam_lib.chunks(String.to_charlist(path), [:abstract_code, :exports]) do
      {:ok, {module, [{:abstract_code, {:raw_abstract_v1, forms}}, {:exports, exports}]}} ->
        %{module: module, exports: exports, calls: calls(forms, module), callbacks: callbacks(forms), file: file(forms), lines: lines(forms)}
      _ -> nil
    end
  end

  defp calls(forms, self) do
    forms |> Enum.flat_map(&walk(&1, self)) |> Enum.uniq()
  end

  defp walk({:call, _, {:remote, _, {:atom, _, m}, {:atom, _, f}}, args}, self), do: [{m, f, length(args)} | Enum.flat_map(args, &walk(&1, self))]
  defp walk({:call, _, {:atom, _, f}, args}, self), do: [{self, f, length(args)} | Enum.flat_map(args, &walk(&1, self))]
  defp walk({:fun, _, {:function, {:atom, _, m}, {:atom, _, f}, {:integer, _, a}}}, _self), do: [{m, f, a}]
  defp walk({:fun, _, {:function, f, a}}, self), do: [{self, f, a}]
  defp walk(node, self) when is_tuple(node), do: node |> Tuple.to_list() |> Enum.flat_map(&walk(&1, self))
  defp walk(node, self) when is_list(node), do: Enum.flat_map(node, &walk(&1, self))
  defp walk(_, _), do: []

  defp callbacks(forms) do
    for {:attribute, _, :behaviour, behaviour} <- forms, cb <- behaviour_callbacks(behaviour), do: cb
  end

  defp behaviour_callbacks(behaviour) do
    Code.ensure_loaded(behaviour)
    if function_exported?(behaviour, :behaviour_info, 1), do: behaviour.behaviour_info(:callbacks), else: []
  rescue
    _ -> []
  end

  defp file(forms) do
    Enum.find_value(forms, "?", fn
      {:attribute, _, :file, {file, _}} -> to_string(file)
      _ -> nil
    end)
  end

  defp lines(forms) do
    for {:function, anno, name, arity, _} <- forms, into: %{}, do: {{name, arity}, line_of(anno)}
  end

  defp line_of({line, _}), do: line
  defp line_of(line) when is_integer(line), do: line
  defp line_of(_), do: 0

  defp skip?(name, arity) do
    name in @skip_names or {name, arity} in @skip_name_arity or String.starts_with?(to_string(name), "MACRO-")
  end

  defp compile_patterns(list, preset) do
    extra = if preset == "phoenix", do: Enum.map(@phoenix, &("#{&1}$")), else: []
    (String.split(list || "", ",", trim: true) ++ extra) |> Enum.map(&Regex.compile!/1)
  end

  defp ignored_module?(module, patterns), do: Enum.any?(patterns, &Regex.match?(&1, inspect(module)))
end

Marestail.DeadCode.main(System.argv())
