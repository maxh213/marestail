#!/usr/bin/env escript

main(["mutants", OutDir | Files]) when Files =/= [] ->
    MutDir = filename:join(OutDir, "mutants"),
    ok = filelib:ensure_path(MutDir),
    {Mutants, _, Skipped} = lists:foldl(fun(File, {Acc, Next, Skip}) ->
        {Entries, After, Missed} = file_mutants(File, MutDir, Next),
        {Acc ++ Entries, After, Skip + Missed}
    end, {[], 1, 0}, Files),
    Payload = ["{\"mutants\":[", join([entry_json(Entry) || Entry <- Mutants]),
               "],\"skipped\":", integer_to_list(Skipped), "}"],
    case file:write_file(filename:join(OutDir, "mutants.json"), Payload) of
        ok -> halt(0);
        {error, Reason} -> fail("cannot write manifest: ~p", [Reason])
    end;
main(["run", MutEbin, BaseEbin, TestEbin]) ->
    ok = add_path(BaseEbin, pathz),
    ok = add_path(TestEbin, pathz),
    ok = add_path(MutEbin, patha),
    Tests = [Mod || {Mod, _} <- beam_sources(TestEbin)],
    case run_eunit(Tests) of
        ok -> halt(0);
        error -> halt(1)
    end;
main(_) ->
    io:format(standard_error, "usage: mutation.escript mutants OUT_DIR FILE... | mutation.escript run MUT_EBIN BASE_EBIN TEST_EBIN~n", []),
    halt(2).

fail(Fmt, Args) ->
    io:format(standard_error, "error: " ++ Fmt ++ "~n", Args),
    halt(2).

replacements('=:=') -> ['=/='];
replacements('=/=') -> ['=:='];
replacements('==') -> ['/='];
replacements('/=') -> ['=='];
replacements('>') -> ['>=', '<'];
replacements('>=') -> ['>', '=<'];
replacements('<') -> ['=<', '>'];
replacements('=<') -> ['<', '>='];
replacements('+') -> ['-'];
replacements('-') -> ['+'];
replacements('*') -> ['/'];
replacements('/') -> ['*'];
replacements('andalso') -> ['orelse'];
replacements('orelse') -> ['andalso'];
replacements(_) -> [].

category(Op) when Op =:= '=:='; Op =:= '=/='; Op =:= '=='; Op =:= '/=';
                  Op =:= '>'; Op =:= '<'; Op =:= '>='; Op =:= '=<' ->
    "comparison";
category(Op) when Op =:= '+'; Op =:= '-'; Op =:= '*'; Op =:= '/' ->
    "arithmetic";
category(_) ->
    "boolean".

file_mutants(File, MutDir, NextId) ->
    Forms = parse(File),
    {Matched, Skipped} = match(sites(Forms), op_tokens(File)),
    Lines = split_lines(read_chars(File)),
    make_mutants(Matched, File, Lines, MutDir, NextId, [], Skipped).

make_mutants([], _, _, _, NextId, Acc, Skipped) ->
    {lists:reverse(Acc), NextId, Skipped};
make_mutants([{Line, Col, Op} | Rest], File, Lines, MutDir, NextId, Acc, Skipped) ->
    Reps = replacements(Op),
    Entries = [site_mutant(File, Lines, MutDir, NextId + N, {Line, Col, Op}, Rep)
               || {N, Rep} <- lists:zip(lists:seq(0, length(Reps) - 1), Reps)],
    make_mutants(Rest, File, Lines, MutDir, NextId + length(Reps), lists:reverse(Entries) ++ Acc, Skipped).

site_mutant(File, Lines, MutDir, Id, {Line, Col, Op}, Replacement) ->
    Dir = filename:join(MutDir, integer_to_list(Id)),
    ok = filelib:ensure_path(Dir),
    Path = filename:join(Dir, filename:basename(File)),
    Source = replace(Lines, Line, Col, Op, Replacement, File),
    case file:write_file(Path, Source) of
        ok -> {Id, File, Line, category(Op), atom_to_list(Op), atom_to_list(Replacement), Path};
        {error, Reason} -> fail("cannot write ~s: ~p", [Path, Reason])
    end.

replace(Lines, Line, Col, Op, Replacement, File) ->
    Text = lists:nth(Line, Lines),
    Original = atom_to_list(Op),
    case lists:sublist(Text, Col, length(Original)) of
        Original -> ok;
        Found -> fail("cannot locate ~s at ~s:~p:~p (found ~s)", [Original, File, Line, Col, Found])
    end,
    Prefix = lists:sublist(Text, Col - 1),
    Suffix = lists:nthtail(Col - 1 + length(Original), Text),
    Kept = lists:sublist(Lines, Line - 1),
    Rest = lists:nthtail(Line, Lines),
    lists:flatten(lists:join($\n, Kept ++ [Prefix ++ atom_to_list(Replacement) ++ Suffix] ++ Rest)).

parse(File) ->
    case epp:parse_file(File, [{includes, [filename:dirname(File)]}]) of
        {ok, Forms} ->
            case [Desc || {error, {_, _, Desc}} <- Forms] of
                [] -> Forms;
                [Desc | _] -> fail("cannot parse ~s: ~ts", [File, Desc])
            end;
        {error, Reason} ->
            fail("cannot read ~s: ~p", [File, Reason])
    end.

sites(Forms) ->
    lists:append([walk(Clauses) || {function, _, _, _, Clauses} <- Forms]).

walk({op, Anno, Op, Left, Right}) ->
    walk(Left) ++
        case replacements(Op) of
            [] -> [];
            _ -> [{line_of(Anno), Op}]
        end ++
        walk(Right);
walk(Tuple) when is_tuple(Tuple) ->
    walk(tuple_to_list(Tuple));
walk(List) when is_list(List) ->
    lists:append([walk(Item) || Item <- List]);
walk(_) ->
    [].

op_tokens(File) ->
    Tokens = lists:append(scan_forms(read_chars(File), {1, 1}, [])),
    classify(Tokens, undefined, []).

classify([], _, Acc) ->
    lists:reverse(Acc);
classify([{white_space, _, _} | Rest], Prev, Acc) ->
    classify(Rest, Prev, Acc);
classify([Token | Rest], Prev, Acc) ->
    case op_kind(Token, Prev) of
        none -> classify(Rest, Token, Acc);
        Entry -> classify(Rest, Token, [Entry | Acc])
    end.

op_kind({Op, Anno}, Prev) when Op =:= '-'; Op =:= '+' ->
    Kind = case ends_expr(Prev) of
        true -> binary;
        false -> unary
    end,
    {line_of(Anno), col_of(Anno), Op, Kind};
op_kind({Op, Anno}, _) ->
    case replacements(Op) of
        [] -> none;
        _ -> {line_of(Anno), col_of(Anno), Op, binary}
    end;
op_kind(_, _) ->
    none.

ends_expr({var, _, _}) -> true;
ends_expr({atom, _, _}) -> true;
ends_expr({integer, _, _}) -> true;
ends_expr({float, _, _}) -> true;
ends_expr({char, _, _}) -> true;
ends_expr({string, _, _}) -> true;
ends_expr({')', _}) -> true;
ends_expr({']', _}) -> true;
ends_expr({'}', _}) -> true;
ends_expr({'>>', _}) -> true;
ends_expr({'end', _}) -> true;
ends_expr(_) -> false.

match(Sites, Tokens) ->
    match(Sites, Tokens, [], 0).

match([], _, Acc, Skipped) ->
    {lists:reverse(Acc), Skipped};
match([{Line, Op} | Sites], Tokens, Acc, Skipped) ->
    case take(Tokens, Line, Op) of
        {ok, Col, Rest} -> match(Sites, Rest, [{Line, Col, Op} | Acc], Skipped);
        miss -> match(Sites, Tokens, Acc, Skipped + 1)
    end.

take([{Line, Col, Op, binary} | Rest], Line, Op) ->
    {ok, Col, Rest};
take([{Later, _, _, _} | _], Line, _) when Later > Line ->
    miss;
take([_ | Rest], Line, Op) ->
    take(Rest, Line, Op);
take([], _, _) ->
    miss.

scan_forms(Chars, Loc, Acc) ->
    case erl_scan:tokens([], Chars, Loc, [return]) of
        {done, {ok, Tokens, EndLoc}, Rest} ->
            scan_forms(Rest, EndLoc, [Tokens | Acc]);
        {done, {eof, _}, _} ->
            lists:reverse(Acc);
        {more, Cont} ->
            case erl_scan:tokens(Cont, eof, Loc, [return]) of
                {done, {ok, Tokens, EndLoc}, Rest} -> scan_forms(Rest, EndLoc, [Tokens | Acc]);
                {done, {eof, _}, _} -> lists:reverse(Acc);
                {error, {_, _, Desc}, _} -> fail("cannot tokenize: ~ts", [Desc])
            end;
        {error, {_, _, Desc}, _} ->
            fail("cannot tokenize: ~ts", [Desc])
    end.

read_chars(File) ->
    case file:read_file(File) of
        {ok, Bin} -> binary_to_list(Bin);
        {error, Reason} -> fail("cannot read ~s: ~p", [File, Reason])
    end.

split_lines(Chars) ->
    split_lines(Chars, [], []).

split_lines([], Line, Acc) ->
    lists:reverse([lists:reverse(Line) | Acc]);
split_lines([$\n | Rest], Line, Acc) ->
    split_lines(Rest, [], [lists:reverse(Line) | Acc]);
split_lines([C | Rest], Line, Acc) ->
    split_lines(Rest, [C | Line], Acc).

line_of({Line, _}) -> Line;
line_of(Line) when is_integer(Line) -> Line;
line_of(_) -> 0.

col_of({_, Col}) -> Col;
col_of(_) -> 1.

add_path(Dir, Where) ->
    Added = case Where of
        patha -> code:add_patha(Dir);
        pathz -> code:add_pathz(Dir)
    end,
    case Added of
        true -> ok;
        _ -> fail("no such directory: ~s", [Dir])
    end.

beam_sources(Dir) ->
    Beams = lists:sort(filelib:wildcard(filename:join(Dir, "*.beam"))),
    [beam_source(Beam) || Beam <- Beams].

beam_source(Beam) ->
    Mod = list_to_atom(filename:basename(Beam, ".beam")),
    case beam_lib:chunks(Beam, [compile_info]) of
        {ok, {Mod, [{compile_info, Info}]}} ->
            case proplists:get_value(source, Info) of
                undefined -> fail("no source recorded in ~s", [Beam]);
                Source -> {Mod, Source}
            end;
        _ ->
            fail("cannot read compile info from ~s", [Beam])
    end.

run_eunit(Tests) ->
    try eunit:test(Tests, []) of
        ok -> ok;
        _ -> error
    catch
        Class:Reason:Stack ->
            io:format(standard_error, "eunit crashed: ~p ~p~n~p~n", [Class, Reason, Stack]),
            error
    end.

entry_json({Id, File, Line, Category, Original, Replacement, Path}) ->
    ["{\"id\":", integer_to_list(Id), ",\"file\":", js(File), ",\"line\":", integer_to_list(Line),
     ",\"operator\":", js(Category), ",\"original\":", js(Original), ",\"replacement\":", js(Replacement),
     ",\"mutant\":", js(Path), "}"].

js(Str) -> [$", escape(Str), $"].

escape([]) -> [];
escape([$" | Rest]) -> [$\\, $" | escape(Rest)];
escape([$\\ | Rest]) -> [$\\, $\\ | escape(Rest)];
escape([$\n | Rest]) -> [$\\, $n | escape(Rest)];
escape([$\r | Rest]) -> [$\\, $r | escape(Rest)];
escape([$\t | Rest]) -> [$\\, $t | escape(Rest)];
escape([C | Rest]) when C < 32 -> [io_lib:format("\\u~4.16.0b", [C]) | escape(Rest)];
escape([C | Rest]) -> [C | escape(Rest)].

join([]) -> [];
join([One]) -> [One];
join([One | Rest]) -> [One, "," | join(Rest)].
