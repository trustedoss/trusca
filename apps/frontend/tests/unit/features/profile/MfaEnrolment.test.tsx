/**
 * Enrolling in a second factor, in the order the backend insists on.
 *
 * The step that matters is the one between showing a secret and turning the
 * factor on. Collapsing them locks out anybody who closes the tab in between:
 * the next sign-in asks for a code their app was never set up to produce, and
 * the only way back is an administrator.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/profile/api/mfaApi", () => ({
  startMfaEnrolment: vi.fn(),
  confirmMfaEnrolment: vi.fn(),
  regenerateRecoveryCodes: vi.fn(),
}));

import { MfaEnrolment } from "@/features/profile/MfaEnrolment";
import {
  confirmMfaEnrolment,
  regenerateRecoveryCodes,
  startMfaEnrolment,
} from "@/features/profile/api/mfaApi";

const mockedStart = vi.mocked(startMfaEnrolment);
const mockedConfirm = vi.mocked(confirmMfaEnrolment);
const mockedReissue = vi.mocked(regenerateRecoveryCodes);

const ENROLMENT = {
  secret: "ABCDEFGHIJKLMNOP",
  provisioning_uri: "otpauth://totp/TRUSCA:a@b.c?secret=ABCDEFGHIJKLMNOP",
  mfa_token: "enrol-token",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MfaEnrolment", () => {
  it("does not enable anything until a code is confirmed", async () => {
    // The secret is stored server-side when this call returns, and the factor
    // is still off. A screen that claimed otherwise would send somebody away
    // believing they are protected, or worse, believing they can sign in.
    mockedStart.mockResolvedValueOnce(ENROLMENT);

    const user = userEvent.setup();
    render(<MfaEnrolment enabled={false} />);
    await user.click(screen.getByTestId("profile-mfa-start"));

    expect(await screen.findByTestId("profile-mfa-scan")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-mfa-enabled")).not.toBeInTheDocument();
    expect(screen.queryByTestId("profile-mfa-codes")).not.toBeInTheDocument();
    expect(mockedConfirm).not.toHaveBeenCalled();
  });

  it("shows the setup key in text, not only as something to scan", async () => {
    // A camera that will not focus, a desktop authenticator, or a screen
    // reader all end here. Without the key in text those people cannot enrol.
    mockedStart.mockResolvedValueOnce(ENROLMENT);

    const user = userEvent.setup();
    render(<MfaEnrolment enabled={false} />);
    await user.click(screen.getByTestId("profile-mfa-start"));

    expect(await screen.findByTestId("profile-mfa-secret")).toHaveValue(
      ENROLMENT.secret,
    );
  });

  it("shows the recovery codes once the factor is on", async () => {
    mockedStart.mockResolvedValueOnce(ENROLMENT);
    mockedConfirm.mockResolvedValueOnce({ codes: ["AAAAA-BBBBB", "CCCCC-DDDDD"] });

    const user = userEvent.setup();
    render(<MfaEnrolment enabled={false} />);
    await user.click(screen.getByTestId("profile-mfa-start"));
    await user.type(await screen.findByTestId("profile-mfa-code"), "123456");
    await user.click(screen.getByTestId("profile-mfa-confirm"));

    const codes = await screen.findByTestId("profile-mfa-codes");
    expect(codes).toHaveTextContent("AAAAA-BBBBB");
    expect(codes).toHaveTextContent("CCCCC-DDDDD");
    // Said in the copy, because the codes are stored as hashes and this render
    // is the only time they exist in a readable form.
    expect(codes.textContent ?? "").toMatch(/once|한 번/i);
  });

  it("keeps the scan step open when the code is rejected", async () => {
    // The secret is already stored, so sending somebody back to the start
    // would issue a second one and orphan whatever they just scanned.
    mockedStart.mockResolvedValueOnce(ENROLMENT);
    mockedConfirm.mockRejectedValueOnce(new Error("nope"));

    const user = userEvent.setup();
    render(<MfaEnrolment enabled={false} />);
    await user.click(screen.getByTestId("profile-mfa-start"));
    await user.type(await screen.findByTestId("profile-mfa-code"), "000000");
    await user.click(screen.getByTestId("profile-mfa-confirm"));

    expect(await screen.findByTestId("profile-mfa-error")).toBeInTheDocument();
    expect(screen.getByTestId("profile-mfa-scan")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-mfa-enabled")).not.toBeInTheDocument();
  });

  it("offers reissue rather than enrolment when the factor is already on", async () => {
    render(<MfaEnrolment enabled />);

    expect(screen.getByTestId("profile-mfa-enabled")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-mfa-start")).not.toBeInTheDocument();
  });

  it("replaces the shown codes when new ones are issued", async () => {
    mockedReissue.mockResolvedValueOnce({ codes: ["EEEEE-FFFFF"] });

    const user = userEvent.setup();
    render(<MfaEnrolment enabled />);
    await user.click(screen.getByTestId("profile-mfa-reissue"));

    await waitFor(() => {
      expect(screen.getByTestId("profile-mfa-codes")).toHaveTextContent(
        "EEEEE-FFFFF",
      );
    });
  });
});
