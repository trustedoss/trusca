/**
 * AssigneeBadge — ER28b / ER54.
 *
 * Three states, and the whole point is that two of them are easy to collapse.
 * `!assignee_is_active` is true for BOTH `null` and `false`, so a natural
 * implementation folds "nobody owns this" together with "somebody who cannot
 * act owns this" — which is the confusion the field was added to remove.
 *
 * So `null` and `false` are asserted to render DIFFERENTLY from each other,
 * not merely to render. A folded implementation still renders, still passes
 * axe, and still looks fine.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AssigneeBadge } from "@/features/projects/components/AssigneeBadge";

const ME = "11111111-1111-1111-1111-111111111111";
const SOMEBODY_ELSE = "22222222-2222-2222-2222-222222222222";

function renderBadge(props: Parameters<typeof AssigneeBadge>[0]) {
  return render(<AssigneeBadge {...props} />);
}

describe("AssigneeBadge", () => {
  it("reads as unassigned when nobody owns it", () => {
    renderBadge({ assigneeUserId: null, assigneeIsActive: null });
    expect(screen.getByTestId("vulnerability-assignee-badge")).toHaveAttribute(
      "data-state",
      "unassigned",
    );
  });

  it("reads as assigned when an active person owns it", () => {
    renderBadge({
      assigneeUserId: SOMEBODY_ELSE,
      assigneeIsActive: true,
      currentUserId: ME,
    });
    expect(screen.getByTestId("vulnerability-assignee-badge")).toHaveAttribute(
      "data-state",
      "assigned",
    );
  });

  it("reads as blocked when the owner cannot act", () => {
    renderBadge({
      assigneeUserId: SOMEBODY_ELSE,
      assigneeIsActive: false,
      currentUserId: ME,
    });
    expect(screen.getByTestId("vulnerability-assignee-badge")).toHaveAttribute(
      "data-state",
      "inactive",
    );
  });

  it("keeps unassigned and cannot-act visibly apart", () => {
    // The assertion this file exists for. Both are falsy, so an implementation
    // written with `!value` renders one of these for both inputs and nothing
    // else fails.
    const { unmount } = renderBadge({
      assigneeUserId: null,
      assigneeIsActive: null,
    });
    const unassignedState = screen
      .getByTestId("vulnerability-assignee-badge")
      .getAttribute("data-state");
    const unassignedText = screen.getByTestId(
      "vulnerability-assignee-badge",
    ).textContent;
    unmount();

    renderBadge({
      assigneeUserId: SOMEBODY_ELSE,
      assigneeIsActive: false,
      currentUserId: ME,
    });
    const blocked = screen.getByTestId("vulnerability-assignee-badge");

    expect(blocked.getAttribute("data-state")).not.toBe(unassignedState);
    // Different words too, not only a different colour: colour is never the
    // only signal, and a screen reader gets the label rather than the tone.
    expect(blocked.textContent).not.toBe(unassignedText);
  });

  it("says the work is yours when you own it", () => {
    renderBadge({
      assigneeUserId: ME,
      assigneeIsActive: true,
      currentUserId: ME,
    });
    const mine = screen.getByTestId("vulnerability-assignee-badge");
    expect(mine).toHaveAttribute("data-state", "assigned");

    // ...and that is different wording from somebody else's, or the filter
    // "mine" and the badge would disagree about what the row shows.
    const mineText = mine.textContent;
    render(
      <AssigneeBadge
        assigneeUserId={SOMEBODY_ELSE}
        assigneeIsActive={true}
        currentUserId={ME}
        data-testid="other-badge"
      />,
    );
    expect(screen.getByTestId("other-badge").textContent).not.toBe(mineText);
  });

  it("does not claim a finding is yours before the session loads", () => {
    // `currentUserId` is null until the user resolves. A null-to-null
    // comparison must not read as ownership.
    renderBadge({
      assigneeUserId: null,
      assigneeIsActive: null,
      currentUserId: null,
    });
    expect(screen.getByTestId("vulnerability-assignee-badge")).toHaveAttribute(
      "data-state",
      "unassigned",
    );
  });
});
