#!/usr/bin/env escript

main(Files) when Files =/= [] ->
    Entries = lists:flatmap(fun scan/1, Files),
    io:put_chars(["[", join(Entries), "]"]),
    halt(0);
main(_) ->
    io:format(standard_error, "usage: comments.escript FILE...~n", []),
    halt(2).

scan(File) ->
    case file:read_file(File) of
        {ok, Bin} ->
            case erl_scan:string(binary_to_list(Bin), 1, [return_comments]) of
                {ok, Tokens, _} ->
                    [entry(File, Line, Text) || {comment, Line, Text} <- Tokens];
                _ ->
                    []
            end;
        _ ->
            []
    end.

entry(File, Line, Text) ->
    ["{\"file\":", js(File), ",\"line\":", integer_to_list(Line), ",\"text\":", js(Text), "}"].

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
