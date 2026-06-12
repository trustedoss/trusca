/**
 * TRUSCA logo candidates — W1 of the TRUSCA rebrand.
 *
 * Dev-only artifact rendered inside /dev/design-preview ("Brand" section).
 * Three new-motif symbol candidates, each shown as: dark tile (app sidebar /
 * favicon context), transparent-light variant (docs navbar context), small
 * sizes for favicon legibility, and a wordmark lockup.
 *
 * Once one is picked, the winning symbol is promoted to:
 *   - docs-site/static/img/logo.svg + favicon.svg
 *   - apps/frontend/public/favicon.svg (+ index.html link)
 *   - the AppShell collapsed-rail monogram
 * and this file stays as the design record (other candidates remain for
 * reference, same as the rest of this gallery).
 *
 * Palette intentionally reuses existing tokens only: warm near-black #18181b
 * (foreground / primary), off-white #fafafa (background), and #2563eb — the
 * blue already used as the de-facto brand accent (README badges, Low/info).
 */

type SymbolProps = {
  /** Rendered box size in px. */
  size?: number;
  /** "tile" = dark rounded square (app); "light" = transparent, dark strokes (docs). */
  variant?: "tile" | "light";
};

const INK = "#18181b";
const PAPER = "#fafafa";
const ACCENT = "#2563eb";

/** Candidate A — "Hex Check": package hexagon + verification check. */
export function TruscaSymbolHex({ size = 32, variant = "tile" }: SymbolProps) {
  const stroke = variant === "tile" ? PAPER : INK;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-label="TRUSCA — hex check candidate"
    >
      {variant === "tile" ? <rect width="32" height="32" rx="7" fill={INK} /> : null}
      <path
        d="M16 6.5 L24.2 11.25 V20.75 L16 25.5 L7.8 20.75 V11.25 Z"
        fill="none"
        stroke={stroke}
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M12.6 16.2 L15.1 18.7 L19.6 13.4"
        fill="none"
        stroke={variant === "tile" ? stroke : ACCENT}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Candidate B — "Scan Line": bold T crossed by a scanner beam (the SCA). */
export function TruscaSymbolScan({ size = 32, variant = "tile" }: SymbolProps) {
  const glyph = variant === "tile" ? PAPER : INK;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-label="TRUSCA — scan line candidate"
    >
      {variant === "tile" ? <rect width="32" height="32" rx="7" fill={INK} /> : null}
      <rect x="8.5" y="8" width="15" height="3.4" rx="1.7" fill={glyph} />
      <rect x="14.3" y="8" width="3.4" height="16" rx="1.7" fill={glyph} />
      <rect x="6.5" y="17.2" width="19" height="2.2" rx="1.1" fill={ACCENT} />
    </svg>
  );
}

/** Candidate C — "Stacked SBOM": component list bars, top entry verified. */
export function TruscaSymbolStack({ size = 32, variant = "tile" }: SymbolProps) {
  const bar = variant === "tile" ? PAPER : INK;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-label="TRUSCA — stacked SBOM candidate"
    >
      {variant === "tile" ? <rect width="32" height="32" rx="7" fill={INK} /> : null}
      <rect x="7.5" y="8.5" width="11" height="3.2" rx="1.6" fill={bar} />
      <path
        d="M21.4 10.6 L23.3 12.5 L26.3 8.6"
        fill="none"
        stroke={ACCENT}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <rect x="7.5" y="14.4" width="17" height="3.2" rx="1.6" fill={bar} />
      <rect x="7.5" y="20.3" width="13.5" height="3.2" rx="1.6" fill={bar} />
    </svg>
  );
}

/** Wordmark lockup: TRU (ink) + SCA (accent), Inter semibold tracking-tight. */
function Wordmark({ className = "text-2xl" }: { className?: string }) {
  return (
    <span className={`font-semibold tracking-tight ${className}`}>
      <span style={{ color: INK }}>TRU</span>
      <span style={{ color: ACCENT }}>SCA</span>
    </span>
  );
}

function CandidateRow({
  id,
  name,
  story,
  Symbol,
}: {
  id: string;
  name: string;
  story: string;
  Symbol: (p: SymbolProps) => React.JSX.Element;
}) {
  return (
    <div
      data-testid={`brand-candidate-${id}`}
      className="rounded-lg border border-border bg-card p-5 shadow-sm"
    >
      <div className="mb-4 flex items-baseline gap-3">
        <span className="font-mono text-xs text-muted-foreground">{id}</span>
        <span className="text-sm font-semibold">{name}</span>
        <span className="text-xs text-muted-foreground">{story}</span>
      </div>
      <div className="flex flex-wrap items-center gap-8">
        {/* Lockup — the primary impression. */}
        <div className="flex items-center gap-2.5">
          <Symbol size={36} />
          <Wordmark />
        </div>
        {/* Docs navbar context: light variant on transparent. */}
        <div className="flex items-center gap-2.5 rounded-md border border-dashed border-border px-3 py-2">
          <Symbol size={24} variant="light" />
          <Wordmark className="text-base" />
        </div>
        {/* Favicon legibility ladder. */}
        <div className="flex items-end gap-3">
          <Symbol size={32} />
          <Symbol size={20} />
          <Symbol size={16} />
          <span className="text-[10px] text-muted-foreground">32 / 20 / 16 px</span>
        </div>
      </div>
    </div>
  );
}

/** The Brand section body, mounted by DesignSystemPreview. */
export function BrandCandidates() {
  return (
    <div className="space-y-4">
      <CandidateRow
        id="A"
        name="Hex Check"
        story="패키지(육각) 안에 검증 체크 — verified component."
        Symbol={TruscaSymbolHex}
      />
      <CandidateRow
        id="B"
        name="Scan Line"
        story="TRUSCA의 T를 가로지르는 스캔 빔 — SCA in action."
        Symbol={TruscaSymbolScan}
      />
      <CandidateRow
        id="C"
        name="Stacked SBOM"
        story="구성요소 목록(SBOM) 막대, 최상단 항목이 검증됨."
        Symbol={TruscaSymbolStack}
      />
      <p className="text-xs text-muted-foreground">
        Wordmark: Inter semibold tracking-tight, TRU = foreground(#18181b) ·
        SCA = accent(#2563eb — README 배지에 이미 쓰는 블루). 심볼 팔레트는
        기존 토큰만 사용.
      </p>
    </div>
  );
}
