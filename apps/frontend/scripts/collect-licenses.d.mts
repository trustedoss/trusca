/** Type surface for scripts/collect-licenses.mjs, so tests can import it under tsc. */

export interface LockPackage {
  key: string;
  name: string;
  version: string;
  license: string | undefined;
  optional: boolean;
}

export interface LicenseEntry {
  name: string;
  version: string;
  license: string;
  text: string;
  source: string;
}

export interface FsApi {
  existsSync(p: string): boolean;
  readFileSync(p: string, encoding: "utf8"): string;
  readdirSync(p: string): string[];
}

export function packageNameFromKey(key: string): string;
export function productionPackages(lock: { packages?: Record<string, unknown> }): LockPackage[];
export function normaliseLicense(value: unknown): string | undefined;
export function pickLicenseFile(fileNames: string[]): string | undefined;
export function fallbackFileName(name: string): string;
export function fallbackCandidates(name: string): string[];
export function formatEntry(entry: Omit<LicenseEntry, "source">): string;
export function collect(
  lock: { packages?: Record<string, unknown> },
  options?: { root?: string; fsApi?: FsApi },
): { entries: LicenseEntry[]; problems: string[] };
export function render(entries: LicenseEntry[]): string;
