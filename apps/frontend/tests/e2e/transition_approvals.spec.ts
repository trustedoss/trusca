/**
 * Risk acceptance needs two people, end to end (B3).
 *
 * The unit tests prove each rule at its own layer. What only a browser run can
 * show is that the rules connect: the transition is refused, the request the
 * user sends from that refusal reaches somebody else's queue, and their
 * agreement actually moves the finding. Any one of those working alone still
 * leaves a control nobody can complete.
 *
 * The two-person part is why this spec builds a second user rather than
 * reusing the seeded one. A single account cannot exercise the rule at all,
 * which is precisely the constraint the guide warns operators about.
 *
 * Pre-requisites (auto-skip otherwise), as the other authenticated specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@transition-approvals`.
 */
import { expect, test } from "@playwright/test";

import { ApprovalsHarness } from "../_harness/ApprovalsHarness";
import { AuthHarness } from "../_harness/auth";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

function runNonce(): string {
  return Date.now().toString(36);
}

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed: bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

test.describe("@transition-approvals a status change two people have to agree on", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("the requester cannot wave their own request through, a colleague can", async ({
    page,
  }, testInfo) => {
    const nonce = runNonce();
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [`b3-approval-${nonce}`],
      withScan: true,
      componentCount: 3,
      componentPrefix: `b3appr${nonce}`,
      vulnerabilityCount: 2,
      superAdmin: true,
      // The second administrator. Without one there is nobody who may agree,
      // and the request would sit in the queue for ever.
      extraMembers: 1,
      extraTeamAdmin: true,
    });
    if (!seed) return;

    const approver = seed.extra_members?.[0];
    expect(approver, "the seed did not produce a second team admin").toBeTruthy();
    const approverEmail = approver!.email;
    const approverPassword = seed.password;

    const approvals = new ApprovalsHarness(page);
    const requesterToken = await approvals.apiLogin(seed.email, seed.password);

    await approvals.apiSetApprovalRequiredStatuses(
      requesterToken,
      seed.team_id,
      ["suppressed"],
    );

    // A finding in `analyzing`, the only state `suppressed` is reachable from
    // once triage has begun.
    const findingId = await approvals.apiFirstFindingId(
      requesterToken,
      seed.project_ids[0],
    );
    await approvals.apiTransitionFinding(
      requesterToken,
      findingId,
      "analyzing",
      "starting triage on this finding",
    );

    // The ordinary transition is refused now, and the finding stays put.
    const refused = await approvals.apiTransitionFindingRaw(
      requesterToken,
      findingId,
      "suppressed",
      "accepted for this release",
    );
    expect(refused.status).toBe(409);
    expect(refused.body.approval_required).toBe(true);
    expect(await approvals.apiFindingStatus(requesterToken, findingId)).toBe(
      "analyzing",
    );

    const request = await approvals.apiRequestTransition(
      requesterToken,
      findingId,
      "suppressed",
      "accepted for this release, tracked in the risk register",
    );

    // The requester sees their own request, and no way to decide it.
    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await approvals.gotoApprovals();
    await approvals.expectTransitionApproval(request.id);
    await approvals.expectOwnRequestNotDecidable(request.id);

    // The colleague decides it, and the finding moves.
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(approverEmail, approverPassword);
    await approvals.gotoApprovals();
    await approvals.expectTransitionApproval(request.id);
    await approvals.approveTransition(request.id);

    const approverToken = await approvals.apiLogin(
      approverEmail,
      approverPassword,
    );
    await expect
      .poll(() => approvals.apiFindingStatus(approverToken, findingId))
      .toBe("suppressed");
  });
});
