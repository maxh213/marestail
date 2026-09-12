#!/usr/bin/env escript

main([Ebin, TestEbin, OutJson]) ->
    ok = add_path(Ebin),
    ok = add_path(TestEbin),
    Sources = beam_sources(Ebin),
    Tests = [Mod || {Mod, _} <- beam_sources(TestEbin)],
    {ok, _} = cover:start(),
    lists:foreach(fun({Mod, _}) -> ok = instrument(Mod) end, Sources),
    Result = run_eunit(Tests),
    Files = lists:foldl(fun({Mod, Source}, Acc) ->
        case cover:analyse(Mod, calls, line) of
            {ok, Lines} -> [file_entry(Source, Lines) | Acc];
            _ -> Acc
        end
    end, [], Sources),
    Payload = payload(Files),
    case file:write_file(OutJson, Payload) of
        ok -> ok;
        {error, WriteReason} -> fail("cannot write ~s: ~p", [OutJson, WriteReason])
    end,
    cover:stop(),
    case Result of
        ok -> halt(0);
        error -> halt(1)
    end;
main(_) ->
    io:format(standard_error, "usage: eunit_cover.escript EBIN_DIR TEST_EBIN_DIR OUT_JSON~n", []),
    halt(2).

add_path(Dir) ->
    case code:add_pathz(Dir) of
        true -> ok;
        _ -> fail("no such directory: ~s", [Dir])
    end.

fail(Fmt, Args) ->
    io:format(standard_error, "error: " ++ Fmt ++ "~n", Args),
    halt(2).

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

instrument(Mod) ->
    case cover:compile_beam(Mod) of
        {ok, Mod} -> ok;
        Error -> fail("cover cannot instrument ~p: ~p", [Mod, Error])
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

file_entry(Source, Lines) ->
    Code = [Entry || Entry = {{_, Line}, _} <- Lines, Line =/= 0],
    Missing = lists:sort([Line || {{_, Line}, 0} <- Code]),
    Total = length(Code),
    {Source, Missing, Total - length(Missing), Total}.

payload(Files) ->
    Covered = lists:sum([C || {_, _, C, _} <- Files]),
    Total = lists:sum([T || {_, _, _, T} <- Files]),
    Entries = [file_json(Entry) || Entry <- lists:sort(Files)],
    ["{\"totals\":{\"percent_covered\":", num(percent(Covered, Total)), "},\"files\":{", join(Entries), "}}"].

file_json({Source, Missing, Covered, Total}) ->
    [js(Source), ":{\"missing_lines\":[", join([integer_to_list(Line) || Line <- Missing]),
     "],\"covered\":", integer_to_list(Covered), ",\"total\":", integer_to_list(Total),
     ",\"percent_covered\":", num(percent(Covered, Total)), "}"].

percent(_, 0) -> 100.0;
percent(Covered, Total) -> Covered / Total * 100.0.

num(Value) -> float_to_list(Value, [{decimals, 4}, compact]).

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
