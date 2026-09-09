#!/usr/bin/env escript
%% -*- erlang -*-
%%! -noshell
%%% Extract per-line cover for a Gleam package after `gleam test`.
%%% Gleam embeds -file("src/....gleam", N) in generated Erlang; cover reports those
%%% attributed numbers (which can run past the Gleam source length within a function).
%%% We map each uncovered attributed line back to its -file Gleam anchor line.
%%% Usage: coverage.escript <project_root> <package_name> <out_json>
-mode(compile).

main([Root, Package, OutJson]) ->
    AbsRoot = filename:absname(Root),
    ok = file:set_cwd(AbsRoot),
    ErlangRoot = filename:join([AbsRoot, "build", "dev", "erlang"]),
    case filelib:is_dir(ErlangRoot) of
        false -> fail("no build/dev/erlang; run gleam test first");
        true -> ok
    end,
    {ok, Packages} = file:list_dir(ErlangRoot),
    lists:foreach(
        fun(P) -> code:add_patha(filename:join([ErlangRoot, P, "ebin"])) end,
        Packages
    ),
    Ebin = filename:join([ErlangRoot, Package, "ebin"]),
    case filelib:is_dir(Ebin) of
        false -> fail("missing ebin for package " ++ Package);
        true -> ok
    end,
    Artefacts = filename:join([ErlangRoot, Package, "_gleam_artefacts"]),
    cover:start(),
    Results = cover:compile_beam_directory(Ebin),
    Mods = [M || {ok, M} <- Results],
    case Mods of
        [] -> fail("cover compiled zero modules from " ++ Ebin);
        _ -> ok
    end,
    case run_tests() of
        ok -> ok;
        error -> fail("tests failed under cover");
        {error, Reason} -> fail(io_lib:format("tests failed under cover: ~p", [Reason]))
    end,
    SrcMods = [M || M <- Mods, is_package_src(M, Package)],
    Files = analyse_all(SrcMods, AbsRoot, Package, Artefacts),
    TotCovered = lists:sum([maps:get(<<"covered">>, F) || F <- maps:values(Files)]),
    TotLines = lists:sum([maps:get(<<"total">>, F) || F <- maps:values(Files)]),
    Pct = case TotLines of 0 -> 100.0; _ -> TotCovered / TotLines * 100.0 end,
    Result = #{
        <<"totals">> => #{<<"percent_covered">> => Pct},
        <<"files">> => Files
    },
    case file:write_file(OutJson, encode_json(Result)) of
        ok -> halt(0);
        {error, E} -> fail(io_lib:format("write ~s failed: ~p", [OutJson, E]))
    end;
main(_) ->
    io:format(standard_error, "usage: coverage.escript <root> <package> <out.json>~n", []),
    halt(2).

run_tests() ->
    case filelib:is_dir("test") of
        false -> fail("no test/ directory");
        true -> ok
    end,
    Paths = filelib:wildcard("**/*.{gleam,erl}", "test"),
    Modules = [list_to_atom(gleam_to_erlang_module_name(P)) || P <- Paths],
    case Modules of
        [] -> fail("no test modules found under test/");
        _ ->
            case code:which(eunit) of
                non_existing -> fail("eunit not available");
                _ -> eunit:test(Modules, [no_tty])
            end
    end.

gleam_to_erlang_module_name(Path) ->
    case lists:suffix(".gleam", Path) of
        true ->
            Base = lists:sublist(Path, length(Path) - 6),
            replace_slash(Base);
        false ->
            filename:basename(Path, ".erl")
    end.

replace_slash(Path) ->
    lists:map(fun($/) -> $@; (C) -> C end, Path).

is_package_src(Mod, Package) ->
    Name = atom_to_list(Mod),
    lists:prefix(Package, Name)
        andalso not lists:suffix("_test", Name)
        andalso not lists:suffix("_tests", Name)
        andalso not lists:suffix("_ffi", Name)
        andalso string:find(Name, "@@") =:= nomatch.

analyse_all(Mods, Root, Package, Artefacts) ->
    lists:foldl(
        fun(Mod, Acc) ->
            case cover:analyse(Mod, calls, line) of
                {ok, Lines} ->
                    Rel = module_to_gleam_path(Mod, Package),
                    Abs = filename:join(Root, Rel),
                    case filelib:is_file(Abs) of
                        true ->
                            Anchors = load_file_anchors(Artefacts, Mod, Rel),
                            Mapped = map_cover_lines(Lines, Anchors, Abs),
                            Missing = lists:usort([L || {L, 0} <- Mapped]),
                            Total = length(Mapped),
                            Covered = Total - length(Missing),
                            Pct = case Total of 0 -> 100.0; _ -> Covered / Total * 100.0 end,
                            Acc#{list_to_binary(Rel) => #{
                                <<"missing_lines">> => Missing,
                                <<"covered">> => Covered,
                                <<"total">> => Total,
                                <<"percent_covered">> => Pct,
                                <<"line_hits">> => [
                                    #{<<"line">> => L, <<"hits">> => C}
                                    || {L, C} <- lists:keysort(1, Mapped)
                                ]
                            }};
                        false ->
                            Acc
                    end;
                _ ->
                    Acc
            end
        end,
        #{},
        Mods
    ).

%% Parse generated Erlang for -file("path", Line). anchors; return
%% sorted list of {AttributedStart, GleamRelPath, GleamStartLine}.
load_file_anchors(Artefacts, Mod, DefaultRel) ->
    Erl = filename:join(Artefacts, atom_to_list(Mod) ++ ".erl"),
    case file:read_file(Erl) of
        {ok, Bin} ->
            Text = unicode:characters_to_list(Bin),
            case re:run(Text, "-file\\(\"([^\"]+)\",\\s*([0-9]+)\\)\\.",
                        [global, unicode, {capture, all_but_first, list}]) of
                {match, Captures} ->
                    lists:map(
                        fun([Path, LineStr]) ->
                            {list_to_integer(LineStr), Path, list_to_integer(LineStr)}
                        end,
                        Captures
                    );
                nomatch ->
                    [{1, DefaultRel, 1}]
            end;
        _ ->
            [{1, DefaultRel, 1}]
    end.

%% Map cover {AttributedLine, Hits} to {GleamAnchorLine, Hits}, keeping
%% only lines whose -file path is a .gleam source under src/.
map_cover_lines(Lines, Anchors, GleamAbs) ->
    GleamLen = length(string:split(unicode:characters_to_list(element(2, file:read_file(GleamAbs))), "\n", all)),
    SortedAnchors = lists:keysort(1, Anchors),
    lists:filtermap(
        fun({{_, AttrLine}, Hits}) when AttrLine > 0 ->
            case anchor_for(AttrLine, SortedAnchors) of
                {Path, GleamStart} ->
                    case lists:suffix(".gleam", Path) of
                        true ->
                            %% Prefer the attributed line when it falls inside the Gleam file;
                            %% otherwise report the function's -file anchor (fail-closed).
                            ReportLine = case AttrLine =< GleamLen of
                                true -> AttrLine;
                                false -> GleamStart
                            end,
                            case is_reportable_gleam_line(GleamAbs, ReportLine) of
                                true -> {true, {ReportLine, Hits}};
                                false -> false
                            end;
                        false ->
                            false
                    end;
                none ->
                    false
            end;
           (_) ->
            false
        end,
        Lines
    ).

anchor_for(_Line, []) ->
    none;
anchor_for(Line, [{Start, Path, GleamStart}]) when Line >= Start ->
    {Path, GleamStart};
anchor_for(Line, [{Start, Path, GleamStart}, {Next, _, _} | _])
  when Line >= Start, Line < Next ->
    {Path, GleamStart};
anchor_for(Line, [_ | Rest]) ->
    anchor_for(Line, Rest).

is_reportable_gleam_line(Abs, Line) ->
    {ok, Bin} = file:read_file(Abs),
    GleamLines = string:split(unicode:characters_to_list(Bin), "\n", all),
    case Line > 0 andalso Line =< length(GleamLines) of
        false -> false;
        true ->
            Text = string:trim(lists:nth(Line, GleamLines)),
            Text =/= ""
                andalso not lists:prefix("//", Text)
                andalso not lists:prefix("import ", Text)
                andalso not lists:prefix("@", Text)
    end.

module_to_gleam_path(Mod, Package) ->
    Name = atom_to_list(Mod),
    case Name of
        Package ->
            filename:join("src", Package ++ ".gleam");
        _ ->
            Prefix = Package ++ "@",
            case lists:prefix(Prefix, Name) of
                true ->
                    Rest = lists:nthtail(length(Prefix), Name),
                    Parts = string:tokens(Rest, "@"),
                    filename:join(["src", Package | Parts]) ++ ".gleam";
                false ->
                    filename:join("src", Name ++ ".gleam")
            end
    end.

fail(Msg) ->
    io:format(standard_error, "~s~n", [Msg]),
    halt(1).

encode_json(Term) ->
    case erlang:function_exported(json, encode, 1) of
        true -> iolist_to_binary(json:encode(Term));
        false -> iolist_to_binary(manual_encode(Term))
    end.

manual_encode(Map) when is_map(Map) ->
    Pairs = maps:to_list(Map),
    Encoded = lists:join($,, [manual_pair(K, V) || {K, V} <- Pairs]),
    [${, Encoded, $}];
manual_encode(List) when is_list(List) ->
    case List of
        [] -> "[]";
        [H | _] when is_integer(H) ->
            [$[, lists:join($,, [integer_to_list(I) || I <- List]), $]];
        _ ->
            [$[, lists:join($,, [manual_encode(I) || I <- List]), $]]
    end;
manual_encode(Bin) when is_binary(Bin) ->
    [$", escape(binary_to_list(Bin)), $"];
manual_encode(Float) when is_float(Float) ->
    io_lib:format("~.6f", [Float]);
manual_encode(Int) when is_integer(Int) ->
    integer_to_list(Int).

manual_pair(Key, Value) when is_binary(Key) ->
    [$", escape(binary_to_list(Key)), $", $:, manual_encode(Value)].

escape([]) -> [];
escape([$\" | Rest]) -> [$\\, $\" | escape(Rest)];
escape([$\\ | Rest]) -> [$\\, $\\ | escape(Rest)];
escape([C | Rest]) when C < 32 -> escape(Rest);
escape([C | Rest]) -> [C | escape(Rest)].
