import { expect, test } from "@playwright/test";
import { classify } from "../src/box";

test("small", () => {
  expect(classify(1)).toBe("small");
});
