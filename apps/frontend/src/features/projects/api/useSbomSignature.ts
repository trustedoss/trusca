/**
 * useSbomSignature — v2.3-s3.
 *
 * Imperative download helper for the project's SBOM signing artifacts (the
 * verification bundle plus individual signature / certificate / attestation /
 * public-key files). Mirrors {@link useVexExport} / {@link useNotice}: the user
 * picks an artifact and expects a single fetch + blob download, not a
 * focus-refetching background query — so this is deliberately NOT a `useQuery`.
 *
 * The cert endpoints (`certificate`, `attestation-certificate`) return 404 on
 * key-based deployments because no Fulcio certificate exists there. That 404 is
 * an EXPECTED branch, not an error: the hook reports it via `lastNotApplicable`
 * so the UI can surface a calm "keyless only / not applicable" hint instead of
 * a destructive error toast. Every other 404 (unsigned scan, missing artifact)
 * surfaces through `error` as usual.
 *
 * Returns `{ download(artifact), busyArtifact, error, lastNotApplicable,
 * clear() }` so the toolbar can disable only the artifact being fetched and
 * render the right inline message per attempt.
 */
import { useCallback, useState } from "react";

import {
  downloadSbomSignatureArtifact,
  type SbomSignatureArtifact,
  type SbomSignatureDownload,
} from "@/lib/projectsApi";
import { triggerBlobDownload } from "@/lib/download";
import { ProblemError } from "@/lib/problem";

/** Artifacts whose 404 means "keyless-only / not applicable", not an error. */
const CERTIFICATE_ARTIFACTS: ReadonlySet<SbomSignatureArtifact> = new Set([
  "certificate",
  "attestation-certificate",
]);

export interface UseSbomSignatureReturn {
  /**
   * Fetch + trigger a browser download for `artifact`. Resolves to the download
   * on success, or `null` when the artifact is a certificate that returned 404
   * on a key-based deployment (an expected "not applicable" branch). Any other
   * failure rejects and is also stored in `error`.
   */
  download: (
    artifact: SbomSignatureArtifact,
  ) => Promise<SbomSignatureDownload | null>;
  /** The artifact currently being fetched, or `null` when idle. */
  busyArtifact: SbomSignatureArtifact | null;
  /** The last genuine failure (not a certificate not-applicable 404). */
  error: ProblemError | Error | null;
  /** The last certificate artifact that was 404 (keyless-only), or `null`. */
  lastNotApplicable: SbomSignatureArtifact | null;
  /** Reset both the error and not-applicable hints. */
  clear: () => void;
}

export function useSbomSignature(
  projectId: string | undefined,
): UseSbomSignatureReturn {
  const [busyArtifact, setBusyArtifact] =
    useState<SbomSignatureArtifact | null>(null);
  const [error, setError] = useState<ProblemError | Error | null>(null);
  const [lastNotApplicable, setLastNotApplicable] =
    useState<SbomSignatureArtifact | null>(null);

  const clear = useCallback(() => {
    setError(null);
    setLastNotApplicable(null);
  }, []);

  const download = useCallback(
    async (artifact: SbomSignatureArtifact) => {
      if (!projectId) {
        throw new Error("SBOM signature download requires a project id");
      }
      setBusyArtifact(artifact);
      setError(null);
      setLastNotApplicable(null);
      try {
        const result = await downloadSbomSignatureArtifact(projectId, artifact);
        triggerBlobDownload(result.blob, result.filename);
        return result;
      } catch (e) {
        // A 404 on a certificate artifact is the expected key-based branch:
        // those deployments simply have no Fulcio certificate. Surface it as a
        // calm "not applicable" hint rather than a destructive error.
        if (
          e instanceof ProblemError &&
          e.status === 404 &&
          CERTIFICATE_ARTIFACTS.has(artifact)
        ) {
          setLastNotApplicable(artifact);
          return null;
        }
        const err = e instanceof Error ? e : new Error(String(e));
        setError(err);
        throw err;
      } finally {
        setBusyArtifact(null);
      }
    },
    [projectId],
  );

  return { download, busyArtifact, error, lastNotApplicable, clear };
}
