/**
 * Playwright config for the design-review capture matrix.
 *
 * Inherits everything from the docs capture config (shared seed and login in
 * globalSetup, one worker, Desktop Chrome) and narrows it to the one spec the
 * docs config ignores: `tests/screenshots/capture_review.spec.ts`. The spec
 * sets its own viewport per describe block and writes its PNGs to the
 * gitignored `tests/screenshots/.review/`, never into the docs tree.
 *
 * Run by the `capture_review` dispatch input of ui-gates.yml, which uploads
 * that folder as the `review-captures` artifact.
 */
import { defineConfig } from "@playwright/test";

import screenshotsConfig from "./playwright.screenshots.config";

export default defineConfig({
  ...screenshotsConfig,
  testIgnore: [],
  testMatch: ["capture_review.spec.ts"],
  outputDir: "./tests/screenshots/.review-output",
});
