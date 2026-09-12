-module(box_tests).

-include_lib("eunit/include/eunit.hrl").

new_test() ->
    ?assertEqual({box, 7}, box:new(7)).

value_test() ->
    ?assertEqual(7, box:value(box:new(7))).

tag_test() ->
    ?assertEqual({tagged, 7}, box:tag(box:new(7))).

classify_tiny_test() ->
    ?assertEqual(tiny, box:classify(5)).

classify_small_test() ->
    ?assertEqual(small, box:classify(15)).

classify_mid_test() ->
    ?assertEqual(mid, box:classify(25)).

classify_big_test() ->
    ?assertEqual(big, box:classify(35)).

classify_huge_test() ->
    ?assertEqual(huge, box:classify(45)).

fetch_test() ->
    ?assertEqual(7, box_store:fetch(box:new(7))).

describe_test() ->
    ?assertEqual("7", box_store:describe(box:new(7))).

format_value_test() ->
    ?assertEqual("7", box_util:format_value(7)).
