/**
 * Setting up a second factor, in the three steps the backend requires.
 *
 * The steps are not decoration. A secret is stored the moment it is shown, and
 * the factor is turned on only after a code proves the authenticator produced
 * something from it. Collapsing that into one action locks out anybody who
 * closes the tab between scanning and confirming: the next sign-in asks for a
 * code their app was never set up to make.
 *
 * The recovery codes appear once. They are stored as hashes, so this render is
 * the only time they exist in a readable form, and the copy says so rather than
 * leaving somebody to discover it when they come looking.
 */
import { KeyRound, Loader2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { problemMessage } from "@/lib/problemMessage";

import {
  confirmMfaEnrolment,
  regenerateRecoveryCodes,
  startMfaEnrolment,
  type MfaEnrolStart,
} from "./api/mfaApi";

type Step = "idle" | "scan" | "done";

export function MfaEnrolment({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation("auth");
  const [step, setStep] = useState<Step>(enabled ? "done" : "idle");
  const [enrolment, setEnrolment] = useState<MfaEnrolStart | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function begin() {
    setError(null);
    setBusy(true);
    try {
      setEnrolment(await startMfaEnrolment());
      setStep("scan");
    } catch (err) {
      setError(problemMessage(err, t, { prefix: "errors" }));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: React.FormEvent) {
    event.preventDefault();
    if (!enrolment) return;
    setError(null);
    setBusy(true);
    try {
      const issued = await confirmMfaEnrolment({
        mfa_token: enrolment.mfa_token,
        code: code.trim(),
      });
      setCodes(issued.codes);
      setStep("done");
      // The secret is gone from this component the moment it is no longer
      // needed. It stays encrypted on the server either way; keeping a copy
      // here only widens where it can be read from.
      setEnrolment(null);
      setCode("");
    } catch (err) {
      setError(problemMessage(err, t, { prefix: "errors" }));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  async function reissue() {
    setError(null);
    setBusy(true);
    try {
      setCodes((await regenerateRecoveryCodes()).codes);
    } catch (err) {
      setError(problemMessage(err, t, { prefix: "errors" }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4" data-testid="profile-mfa">
      <div className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h2 className="text-sm font-semibold">{t("mfa.title")}</h2>
      </div>

      {error ? (
        <Alert variant="destructive" data-testid="profile-mfa-error">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {step === "idle" ? (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">{t("mfa.off_description")}</p>
          <Button onClick={begin} disabled={busy} data-testid="profile-mfa-start">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
            {t("mfa.start")}
          </Button>
        </div>
      ) : null}

      {step === "scan" && enrolment ? (
        <form onSubmit={confirm} className="space-y-3" data-testid="profile-mfa-scan">
          <p className="text-sm text-muted-foreground">{t("mfa.scan_description")}</p>
          <div className="space-y-1">
            <Label htmlFor="profile-mfa-uri">{t("mfa.setup_key_label")}</Label>
            {/* The secret in plain text rather than only a QR code. A camera
                that will not focus, a desktop authenticator, or a screen
                reader all end here, and without it those people cannot
                enrol at all. */}
            <Input
              id="profile-mfa-uri"
              readOnly
              value={enrolment.secret}
              className="font-mono text-xs"
              data-testid="profile-mfa-secret"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="profile-mfa-code">{t("mfa.code_label")}</Label>
            <Input
              id="profile-mfa-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              autoComplete="one-time-code"
              inputMode="numeric"
              data-testid="profile-mfa-code"
            />
          </div>
          <Button
            type="submit"
            disabled={busy || code.trim().length === 0}
            data-testid="profile-mfa-confirm"
          >
            {t("mfa.confirm")}
          </Button>
        </form>
      ) : null}

      {step === "done" ? (
        <div className="space-y-3" data-testid="profile-mfa-enabled">
          <p className="text-sm text-muted-foreground">{t("mfa.on_description")}</p>
          <Button
            variant="outline"
            onClick={reissue}
            disabled={busy}
            data-testid="profile-mfa-reissue"
          >
            {t("mfa.reissue")}
          </Button>
        </div>
      ) : null}

      {codes ? (
        <Alert data-testid="profile-mfa-codes">
          <AlertDescription className="space-y-2">
            <p className="font-medium">{t("mfa.codes_title")}</p>
            <p className="text-xs">{t("mfa.codes_warning")}</p>
            <ul className="grid grid-cols-2 gap-1 font-mono text-xs">
              {codes.map((value) => (
                <li key={value}>{value}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}
    </section>
  );
}
