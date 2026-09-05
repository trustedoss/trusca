/**
 * The harness computes TOTP codes, so the harness has to be right.
 *
 * It is the only implementation in this repository that is not the product's
 * own, and that is the point of it: the e2e scenario computes a code here and
 * the backend checks it there, so the two agreeing is a statement about
 * interoperability rather than about one implementation agreeing with itself.
 *
 * A wrong harness would fail the e2e scenario and look like a product defect,
 * which is a slow and misleading way to find out. These are the RFC 6238
 * Appendix B vectors for SHA-1, the algorithm every authenticator app
 * implements and the one the product fixed on for that reason.
 */
import { describe, expect, it } from "vitest";

import { codeFor } from "../../_harness/MfaHarness";

/** Appendix B's seed, "12345678901234567890", written in base32. */
const SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";

describe("codeFor", () => {
  it.each([
    [59, "287082"],
    [1111111109, "081804"],
    [1111111111, "050471"],
    [1234567890, "005924"],
    [2000000000, "279037"],
  ])("matches RFC 6238 Appendix B at t=%i", (seconds, expected) => {
    expect(codeFor(SEED, seconds)).toBe(expected);
  });

  it("is stable within a step and changes at the boundary", () => {
    // Not decoration: an off-by-one in the counter passes every vector above
    // if it is applied consistently, and shows up here.
    expect(codeFor(SEED, 30)).toBe(codeFor(SEED, 59));
    expect(codeFor(SEED, 60)).not.toBe(codeFor(SEED, 59));
  });

  it("reads a secret written with padding or spaces", () => {
    // What the enrolment screen shows is what gets typed back, and neither
    // trailing "=" nor the spaces a person adds should change the answer.
    expect(codeFor(`${SEED}======`, 59)).toBe("287082");
    expect(codeFor("GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ", 59)).toBe("287082");
  });
});
