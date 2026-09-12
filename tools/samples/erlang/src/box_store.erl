-module(box_store).

-export([fetch/1, describe/1]).

fetch({box, Value}) ->
    Value.

describe(Box) ->
    Prefix = "box",
    box_util:format_value(box:raw_value(Box)).
