using Sample.Domain;
using Sample.Services;
using Xunit;

namespace Sample.Tests;

public class PricingTests
{
    [Fact]
    public void ClassifiesALargeTotal()
    {
        Assert.Equal("large", Pricing.Classify(120m));
    }

    [Fact]
    public void ClassifiesAMediumTotal()
    {
        Assert.Equal("medium", Pricing.Classify(60m));
    }

    [Fact]
    public void ClassifiesASmallTotal()
    {
        Assert.Equal("small", Pricing.Classify(20m));
    }

    [Fact]
    public void BandsABasket()
    {
        var basket = new Basket([10m, 15m]);
        Assert.Equal("small", Pricing.Band(basket, 0m));
    }

    [Fact]
    public void ConvertsMoney()
    {
        Assert.Equal(8.5m, new Money(10m).ToEuros().Amount);
    }
}
