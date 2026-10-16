# C# best practices

Applied by the marestail `practices` judge to `*.cs` changes. Test rules apply to test projects (xUnit, NUnit, MSTest) only.

## Tests

- **CS-1 — every test is Arrange, Act, Assert, in that order.** A test method has three phases: set up the inputs and the system under test, perform exactly one action on it, then assert on the outcome. The phases are separated by a blank line, never by comments, and never interleave: no assertions before or between actions, no setup after the act, and no second act that turns one test into two. A test with nothing to arrange still keeps act and assert as separate blocks; an exception test wraps the single act in the framework's `Throws`/`ThrowsAsync` and asserts on the result.
