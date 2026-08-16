/// <reference types="vite/client" />

/**
 * Build-time configuration the app reads from `import.meta.env`.
 *
 * Declared here so a self-hosted deploy can find out these knobs exist: the
 * repository ships no `.env.example` for the frontend, and a variable that
 * lives only inside the function that reads it is a variable nobody knows to
 * set. Every entry is optional and every reader has a working default.
 */
interface ImportMetaEnv {
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
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
