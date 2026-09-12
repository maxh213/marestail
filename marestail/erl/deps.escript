#!/usr/bin/env escript

main(Beams) when Beams =/= [] ->
    Edges = lists:flatmap(fun edges/1, Beams),
    io:put_chars(["[", join([edge_json(Edge) || Edge <- Edges]), "]"]),
    halt(0);
main(_) ->
    io:format(standard_error, "usage: deps.escript BEAM...~n", []),
    halt(2).

fail(Fmt, Args) ->
    io:format(standard_error, "error: " ++ Fmt ++ "~n", Args),
    halt(2).

edges(Beam) ->
    case beam_lib:chunks(Beam, [abstract_code]) of
        {ok, {Mod, [{abstract_code, {raw_abstract_v1, Forms}}]}} ->
            behaviours(Forms, Mod) ++ calls(Forms, Mod);
        {ok, {_, [{abstract_code, no_abstract_code}]}} ->
            fail("no abstract code in ~s (compile with +debug_info)", [Beam]);
        _ ->
            fail("cannot read abstract code from ~s", [Beam])
    end.

behaviours(Forms, Mod) ->
    [{Mod, Target, line_of(Anno), "behaviour"} ||
        {attribute, Anno, Kind, Target} <- Forms,
        Kind =:= behaviour orelse Kind =:= behavior,
        is_atom(Target), Target =/= Mod].

calls(Node, Mod) when is_tuple(Node) ->
    Own = case Node of
        {call, Anno, {remote, _, {atom, _, Target}, {atom, _, Fun}}, Args} when is_atom(Target), is_atom(Fun), Target =/= Mod ->
            [{Mod, Target, line_of(Anno), atom_to_list(Fun) ++ "/" ++ integer_to_list(length(Args))}];
        _ ->
            []
    end,
    Own ++ calls(tuple_to_list(Node), Mod);
calls(List, Mod) when is_list(List) ->
    lists:flatmap(fun(Item) -> calls(Item, Mod) end, List);
calls(_, _) ->
    [].

line_of({Line, _}) -> Line;
line_of(Line) when is_integer(Line) -> Line;
line_of(_) -> 0.

edge_json({From, To, Line, Via}) ->
    ["{\"from\":", js(atom_to_list(From)), ",\"to\":", js(atom_to_list(To)),
     ",\"line\":", integer_to_list(Line), ",\"fun\":", js(Via), "}"].

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
