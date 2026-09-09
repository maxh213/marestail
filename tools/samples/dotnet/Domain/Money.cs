using Sample.Services;

namespace Sample.Domain;

public sealed class Money
{
    public Money(decimal amount)
    {
        Amount = amount;
    }

    public decimal Amount { get; }

    public Money ToEuros()
    {
        return new Money(Amount * RateTable.Euro);
    }
}
