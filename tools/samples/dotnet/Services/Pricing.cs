using Sample.Domain;

namespace Sample.Services;

public static class Pricing
{
    /// <summary>Puts a basket total into a size band.</summary>
    public static string Classify(decimal total)
    {
        if (total > 100m)
        {
            return "large";
        }

        if (total > 50m)
        {
            return "medium";
        }

        if (total > 10m)
        {
            return "small";
        }

        if (total > 0m)
        {
            return "tiny";
        }

        return "empty";
    }

    public static string Band(Basket basket, decimal discount)
    {
        var total = basket.Total(discount);
        if (total < 0m)
            return "refund";

        return Classify(total);
    }

    private static decimal WithVat(decimal value)
    {
        return value * 1.2m;
    }
}
