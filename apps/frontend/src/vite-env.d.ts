/// <reference types="vite/client" />

/**
 * Every build-time variable the app reads from `import.meta.env`.
 *
 * Declared here so a self-hosted deploy can find out these knobs exist: the
 * repository ships no `.env.example` for the frontend, and a variable that
 * lives only inside the function that reads it is a variable nobody knows to
 * set. Each one is optional and each reader has a working default.
 *
 * Keep this list complete. A partial list reads as a complete one, and the
 * variable left out is the one an operator never finds.
 */
interface ImportMetaEnv {
  /**
   * Backend origin. Defaults to `http://localhost:8000`; trailing slashes are
   * stripped. See `src/lib/api.ts`.
   */
  readonly VITE_API_BASE_URL?: string;
  /**
   * Where the in-app documentation link points. Defaults to the public site
   * at `https://trustedoss.github.io/trusca/`; set this to a mirror when the
   * deployment has no route to the public one. See `src/lib/docsUrl.ts`.
   */
  readonly VITE_DOCS_URL?: string;
  /**
   * Where the standalone BomLens viewer is hosted, for the links out of the
   * demo sandbox. See `src/lib/demoSandbox.ts`.
   */
  readonly VITE_BOMLENS_URL?: string;
  /**
   * Seeds the read-only banner on the first frame, before `/health` answers.
   * A hint only: the backend flag is authoritative and wins once known. See
   * `src/hooks/useDemoMode.ts`.
   */
  readonly VITE_DEMO_READ_ONLY?: string | boolean;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
