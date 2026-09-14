package com.example.services;

import com.example.domain.Basket;

public final class Pricing {
    private Pricing() {
    }

    /** Puts a basket total into a size band. */
    public static String classify(int total) {
        if (total > 100) {
            return "large";
        }
        if (total > 50) {
            return "medium";
        }
        if (total > 10) {
            return "small";
        }
        if (total > 0) {
            return "tiny";
        }
        return "empty";
    }

    public static String band(Basket basket, int discount) {
        int total = basket.total(discount);
        if (total < 0)
            return "refund";
        return classify(total);
    }

    private static int withVat(int value) {
        return value * 12 / 10;
    }
}
