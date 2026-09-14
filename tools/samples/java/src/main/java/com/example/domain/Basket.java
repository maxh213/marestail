package com.example.domain;

import com.example.services.Pricing;
import java.util.List;

public final class Basket {
    private final List<Integer> lines;

    public Basket(List<Integer> lines) {
        this.lines = List.copyOf(lines);
    }

    // TODO: value added tax is not handled here yet
    public int total(int discount) {
        return net(discount);
    }

    public String band(int discount) {
        return Pricing.band(this, discount);
    }

    private int net(int discount) {
        int sum = 0;
        for (int line : lines) {
            sum += line;
        }
        return sum - discount;
    }
}
