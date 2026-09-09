namespace Sample.Domain;

public sealed class Basket
{
    private readonly List<decimal> lines;

    public Basket(IEnumerable<decimal> lines)
    {
        this.lines = lines.ToList();
    }

    // TODO: value added tax is not handled here yet
    public decimal Total(decimal discount)
    {
        return Net(discount);
    }

    private decimal Net(decimal discount)
    {
        var total = 0m;
        foreach (var line in lines)
        {
            total += line;
        }

        return total - discount;
    }
}
