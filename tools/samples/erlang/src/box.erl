% a leftover comment
-module(box).

-export([new/1, value/1, tag/1, classify/1, raw_value/1]).

new(Value) ->
    {box, Value}.

value(Box) ->
    box_store:fetch(Box).

raw_value({box, Value}) ->
    Value.

tag(Box) ->
    case raw_value(Box) of
        empty ->
            empty;
        Value ->
            {tagged, Value}
    end.

classify(N) when is_integer(N) ->
    if
        N > 10 ->
            if
                N > 20 ->
                    if
                        N > 30 ->
                            if
                                N > 40 -> huge;
                                true -> big
                            end;
                        true -> mid
                    end;
                true -> small
            end;
        true -> tiny
    end.
