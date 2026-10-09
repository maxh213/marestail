# marestail playwright sample

A tiny `@playwright/test` suite used to prove `[ts] runner = "playwright"` collects
istanbul coverage through `c8`. The planted gap is the uncovered `n > 10` branch
in `src/box.ts`.

```sh
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install
npx playwright test
```
