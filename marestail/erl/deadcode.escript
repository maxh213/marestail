#!/usr/bin/env escript

-define(MAGIC, [module_info, record_info, init, handle_call, handle_cast, handle_info,
                handle_continue, terminate, code_change, format_status, start, start_link,
                stop, main, prep_stop]).

main(["--ignore", Patterns | Beams]) when Beams =/= [] ->
    Ignore = compile(Patterns),
    run(Beams, Ignore);
main(Beams) when Beams =/= [] ->
    run(Beams, []);
main(_) ->
    io:format(standard_error, "usage: deadcode.escript [--ignore REGEX,...] BEAM...~n", []),
    halt(2).

compile(Patterns) ->
    [element(2, re:compile(P)) || P <- string:tokens(Patterns, ",")].

run(Beams, Ignore) ->
    Modules = lists:filtermap(fun load/1, Beams),
    Dead = lists:flatmap(fun(M) -> dead(M, Ignore) end, Modules),
    io:put_chars(["[", join([entry(D) || D <- Dead]), "]"]),
    halt(0).

fail(Fmt, Args) ->
    io:format(standard_error, "error: " ++ Fmt ++ "~n", Args),
    halt(2).

load(Beam) ->
    case beam_lib:chunks(Beam, [abstract_code]) of
        {ok, {Mod, [{abstract_code, {raw_abstract_v1, Forms}}]}} ->
            case lists:suffix("_tests", atom_to_list(Mod)) of
                true ->
                    false;
                false ->
                    {true, #{module => Mod, forms => Forms}}
            end;
        {ok, {_, [{abstract_code, no_abstract_code}]}} ->
            fail("no abstract code in ~s (compile with +debug_info)", [Beam]);
        _ ->
            fail("cannot read abstract code from ~s", [Beam])
    end.

dead(#{module := Mod, forms := Forms}, Ignore) ->
    Exports = lists:append([Es || {attribute, _, export, Es} <- Forms]),
    Called = calls(Forms),
    [{Mod, Name, Arity, line_of(Anno)} ||
        {function, Anno, Name, Arity, _} <- Forms,
        not lists:member({Name, Arity}, Exports),
        not lists:member({Name, Arity}, Called),
        not magic(Name),
        not test_fun(Name),
        not ignored(Ignore, Mod, Name, Arity)].

magic(Name) ->
    lists:member(Name, ?MAGIC).

test_fun(Name) ->
    S = atom_to_list(Name),
    lists:suffix("_test", S) orelse lists:suffix("_test_", S).

ignored(Ignore, Mod, Name, Arity) ->
    Label = atom_to_list(Mod) ++ "." ++ atom_to_list(Name) ++ "/" ++ integer_to_list(Arity),
    lists:any(fun(Re) -> re:run(Label, Re, [{capture, none}]) =:= match end, Ignore).

calls(Forms) ->
    lists:usort(lists:flatmap(fun walk/1, Forms)).

walk({call, _, {atom, _, Fun}, Args}) when is_atom(Fun), is_list(Args) ->
    [{Fun, length(Args)} | lists:flatmap(fun walk/1, Args)];
walk({'fun', _, {function, Fun, Arity}}) when is_atom(Fun), is_integer(Arity) ->
    [{Fun, Arity}];
walk({'fun', _, {function, {atom, _, Fun}, {integer, _, Arity}}}) ->
    [{Fun, Arity}];
walk(Node) when is_tuple(Node) ->
    lists:flatmap(fun walk/1, tuple_to_list(Node));
walk(List) when is_list(List) ->
    lists:flatmap(fun walk/1, List);
walk(_) ->
    [].

entry({Mod, Name, Arity, Line}) ->
    ["{\"module\":", js(atom_to_list(Mod)), ",\"function\":", js(atom_to_list(Name)),
     ",\"arity\":", integer_to_list(Arity), ",\"line\":", integer_to_list(Line), "}"].

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
