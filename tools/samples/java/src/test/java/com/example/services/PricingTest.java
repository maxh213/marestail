package com.example.services;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.example.domain.Basket;
import java.util.List;
import org.junit.jupiter.api.Test;

class PricingTest {
    @Test
    void classifiesALargeTotal() {
        assertEquals("large", Pricing.classify(120));
    }

    @Test
    void classifiesAMediumTotal() {
        assertEquals("medium", Pricing.classify(60));
    }

    @Test
    void classifiesASmallTotal() {
        assertEquals("small", Pricing.classify(20));
    }

    @Test
    void bandsABasket() {
        assertEquals("small", new Basket(List.of(10, 15)).band(0));
    }
}
