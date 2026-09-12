-module(box_util).

-export([format_value/1]).

-compile({nowarn_unused_function, {suffix, 0}}).

format_value(Value) ->
    lists:flatten(io_lib:format("~p", [Value])).

suffix() ->
    "-v1".
