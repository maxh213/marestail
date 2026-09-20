#!/usr/bin/env escript

main(Files) when Files =/= [] ->
    Entries = lists:flatmap(fun analyse/1, Files),
    io:put_chars(["[", join(Entries), "]"]),
    halt(0);
main(_) ->
    io:format(standard_error, "usage: complexity.escript FILE...~n", []),
    halt(2).

fail(Fmt, Args) ->
    io:format(standard_error, "error: " ++ Fmt ++ "~n", Args),
    halt(2).

analyse(File) ->
    case epp:parse_file(File, [{includes, [filename:dirname(File)]}]) of
        {ok, Forms} ->
            case [Desc || {error, {_, _, Desc}} <- Forms] of
                [] ->
                    [entry(Form, File) || {function, _, _, _, _} = Form <- Forms];
                [Desc | _] ->
                    fail("cannot parse ~s: ~ts", [File, Desc])
            end;
        {error, Reason} ->
            fail("cannot read ~s: ~p", [File, Reason])
    end.

entry({function, Anno, Name, Arity, Clauses}, File) ->
    Label = atom_to_list(Name) ++ "/" ++ integer_to_list(Arity),
    ["{\"file\":", js(File), ",\"line\":", integer_to_list(line_of(Anno)),
     ",\"name\":", js(Label), ",\"complexity\":", integer_to_list(1 + cc(Clauses)), "}"].

line_of({Line, _}) -> Line;
line_of(Line) when is_integer(Line) -> Line;
line_of(_) -> 0.

cc({'case', _, Expr, Clauses}) ->
    length(Clauses) + cc(Expr) + cc(Clauses);
cc({'if', _, Clauses}) ->
    length(Clauses) + cc(Clauses);
cc({'receive', _, Clauses}) ->
    length(Clauses) + cc(Clauses);
cc({'receive', _, Clauses, AfterExpr, AfterBody}) ->
    length(Clauses) + 1 + cc(Clauses) + cc(AfterExpr) + cc(AfterBody);
cc({'try', _, Exprs, Clauses, CatchClauses, After}) ->
    length(Clauses) + length(CatchClauses) + cc(Exprs) + cc(Clauses) + cc(CatchClauses) + cc(After);
cc({'op', _, Op, Left, Right}) when Op =:= 'andalso'; Op =:= 'orelse' ->
    1 + cc(Left) + cc(Right);
cc(Tuple) when is_tuple(Tuple) ->
    cc(tuple_to_list(Tuple));
cc(List) when is_list(List) ->
    lists:sum([cc(Item) || Item <- List]);
cc(_) ->
    0.

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
