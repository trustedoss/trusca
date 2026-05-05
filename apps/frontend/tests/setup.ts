import "@testing-library/jest-dom/vitest";

import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

import i18n from "@/lib/i18n";

afterEach(() => {
  cleanup();
  // Reset to default language so a test that flips i18n state can't leak.
  void i18n.changeLanguage("en");
});
