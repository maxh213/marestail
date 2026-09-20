#!/usr/bin/env escript

main(Files) when Files =/= [] ->
    Entries = [analyse(F) || F <- Files],
    io:put_chars(["[", join(Entries), "]"]),
    halt(0);
main(_) ->
    io:format(standard_error, "usage: depth.escript FILE...~n", []),
    halt(2).

fail(Fmt, Args) ->
    io:format(standard_error, "error: " ++ Fmt ++ "~n", Args),
    halt(2).

analyse(File) ->
    case epp:parse_file(File, [{includes, [filename:dirname(File)]}]) of
        {ok, Forms} ->
            case [Desc || {error, {_, _, Desc}} <- Forms] of
                [] ->
                    entry(Forms, File);
                [Desc | _] ->
                    fail("cannot parse ~s: ~ts", [File, Desc])
            end;
        {error, Reason} ->
            fail("cannot read ~s: ~p", [File, Reason])
    end.

entry(Forms, File) ->
    Exports = lists:append([Es || {attribute, _, export, Es} <- Forms]),
    Functions = [Form || {function, _, _, _, _} = Form <- Forms],
    Pass = lists:flatmap(fun(Form) -> pass_through(Form, Exports) end, Functions),
    ["{\"file\":", js(File), ",\"public\":[", join([js(label(N, A)) || {N, A} <- Exports]),
     "],\"statements\":", integer_to_list(length(Functions)),
     ",\"pass_throughs\":[", join(Pass), "]}"].

label(Name, Arity) ->
    atom_to_list(Name) ++ "/" ++ integer_to_list(Arity).

pass_through({function, Anno, Name, Arity, Clauses}, Exports) ->
    case lists:member({Name, Arity}, Exports) andalso forwards(Clauses) of
        true ->
            [["{\"line\":", integer_to_list(line_of(Anno)), ",\"name\":", js(label(Name, Arity)), "}"]];
        false ->
            []
    end.

forwards([{clause, _, Patterns, [], [Call]}]) ->
    Names = [N || {var, _, N} <- Patterns],
    length(Names) > 0 andalso length(Names) =:= length(Patterns)
        andalso length(Names) =:= length(lists:usort(Names)) andalso forwards_call(Call, Names);
forwards(_) ->
    false.

forwards_call({call, _, {remote, _, {atom, _, _}, {atom, _, _}}, Args}, Names) ->
    args_match(Args, Names);
forwards_call({call, _, {atom, _, _}, Args}, Names) ->
    args_match(Args, Names);
forwards_call(_, _) ->
    false.

args_match(Args, Names) when is_list(Args) ->
    length(Args) =:= length(Names) andalso [N || {var, _, N} <- Args] =:= Names;
args_match(_, _) ->
    false.

line_of({Line, _}) -> Line;
line_of(Line) when is_integer(Line) -> Line;
line_of(Anno) when is_list(Anno) ->
    case lists:keyfind(location, 1, Anno) of
        {location, Loc} -> line_of(Loc);
        false -> 0
    end;
line_of(_) -> 0.

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
