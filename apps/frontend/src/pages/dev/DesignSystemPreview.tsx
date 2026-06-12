/**
 * Design System Preview — W11-A, expanded into a living reference in W12-E.
 *
 * Dev-only sample page. Originally the visual confirm gate for the W11-A
 * token redefinition (Vercel base + Linear polish, light single-theme); W12-E
 * grew it into a living component reference + a manual review / visual-
 * regression surface covering the W12 primitives.
 *
 * Routing:
 *   - Mounted at `/dev/design-preview` (see router.tsx).
 *   - The route element is gated by `import.meta.env.DEV` so a production
 *     build silently 404s the path (falls through to /login redirect).
 *
 * i18n:
 *   - Copy is INTENTIONALLY static English (no `t()` calls). This page is
 *     a designer-facing artifact, not a user-facing feature, and we don't
 *     want translation churn for sample text. `npm run i18n:check` passes
 *     because no t() keys are introduced.
 *
 * Scope:
 *   - W11-A: colour tokens, Button, Card, Input, a dense table row, and the
 *     elevation / radius / motion scales.
 *   - W12-E: the typography primitives, Badge tones, the EmptyState medallion,
 *     a per-column TableRowsSkeleton, and the Alert + toast feedback surfaces —
 *     so every shared primitive can be eyeballed (and screenshot-diffed) in
 *     one place.
 */
import {
  AlertTriangle,
  CheckCircle2,
  FolderOpen,
  GitBranch,
  MoreHorizontal,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { BrandCandidates } from "@/pages/dev/BrandCandidates";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TableRowsSkeleton } from "@/components/ui/skeletons";
import { useToast } from "@/components/ui/toast";
import {
  Body,
  Caption,
  Eyebrow,
  PageTitle,
  SectionTitle,
  Subtitle,
} from "@/components/ui/typography";

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div className="space-y-1">
        {/* Dogfood the typography primitives (W12-E). */}
        <SectionTitle className="text-lg">{title}</SectionTitle>
        {description ? <Subtitle>{description}</Subtitle> : null}
      </div>
      {children}
    </section>
  );
}

function Swatch({ token, varName }: { token: string; varName: string }) {
  return (
    <div className="flex items-center gap-3">
      <div
        className="h-10 w-10 rounded-md border border-border"
        style={{ background: `hsl(var(${varName}))` }}
        aria-hidden
      />
      <div className="text-xs">
        <div className="font-medium">{token}</div>
        <div className="font-mono text-muted-foreground">{varName}</div>
      </div>
    </div>
  );
}

export function DesignSystemPreview() {
  const { toast } = useToast();
  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-5xl space-y-10 px-6 py-10">
        <header className="space-y-2 border-b border-border pb-6">
          <Eyebrow className="block tracking-wider">
            Design system · living reference (dev only)
          </Eyebrow>
          <h1 className="text-3xl font-semibold tracking-tight">
            Vercel base + Linear polish
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Sample of the new token set applied to two foundational
            components. The rest of the app still uses these same tokens —
            walk a few pages after this to spot any regressions before we
            green-light Phase B.
          </p>
        </header>

        <Section title="Color tokens" description="Light single-theme. Severity tokens unchanged.">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <Swatch token="background" varName="--background" />
            <Swatch token="foreground" varName="--foreground" />
            <Swatch token="card" varName="--card" />
            <Swatch token="muted" varName="--muted" />
            <Swatch token="border" varName="--border" />
            <Swatch token="primary" varName="--primary" />
          </div>
          <div className="grid grid-cols-5 gap-3 pt-2">
            <div className="space-y-1 text-center">
              <div className="h-10 rounded-md bg-risk-critical" aria-hidden />
              <div className="text-xs font-medium">Critical</div>
            </div>
            <div className="space-y-1 text-center">
              <div className="h-10 rounded-md bg-risk-high" aria-hidden />
              <div className="text-xs font-medium">High</div>
            </div>
            <div className="space-y-1 text-center">
              <div className="h-10 rounded-md bg-risk-medium" aria-hidden />
              <div className="text-xs font-medium">Medium</div>
            </div>
            <div className="space-y-1 text-center">
              <div className="h-10 rounded-md bg-risk-low" aria-hidden />
              <div className="text-xs font-medium">Low</div>
            </div>
            <div className="space-y-1 text-center">
              <div className="h-10 rounded-md bg-risk-info" aria-hidden />
              <div className="text-xs font-medium">Info</div>
            </div>
          </div>
        </Section>

        <Section title="Buttons" description="Primary near-black + subtle shadow; hover transitions at 150 ms ease-out.">
          <div className="flex flex-wrap items-center gap-3">
            <Button>Deploy</Button>
            <Button variant="secondary">Cancel</Button>
            <Button variant="outline">View logs</Button>
            <Button variant="ghost">Settings</Button>
            <Button variant="destructive">Delete</Button>
            <Button variant="link">Read the docs</Button>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm">Small</Button>
            <Button size="default">Default</Button>
            <Button size="lg">Large</Button>
            <Button size="icon" aria-label="Open menu">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </div>
        </Section>

        <Section title="Card" description="Off-white canvas + white card + subtle shadow. Vercel domains pattern.">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>frontend-admin</CardTitle>
                <CardDescription>
                  Last scan 2h ago · 14 components · 3 findings
                </CardDescription>
              </CardHeader>
              <CardContent className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <GitBranch className="h-4 w-4" />
                  <span className="font-mono text-xs">main</span>
                </div>
                <Badge tone="critical">
                  <AlertTriangle className="h-3 w-3" />2 Critical
                </Badge>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>backend-api</CardTitle>
                <CardDescription>
                  Last scan 5m ago · 38 components · 0 findings
                </CardDescription>
              </CardHeader>
              <CardContent className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <GitBranch className="h-4 w-4" />
                  <span className="font-mono text-xs">develop</span>
                </div>
                <Badge tone="success">
                  <CheckCircle2 className="h-3 w-3" />
                  Clean
                </Badge>
              </CardContent>
            </Card>
          </div>
        </Section>

        <Section title="Input + dense table row" description="40 px row density preserved. Hover row tint = --accent.">
          <div className="space-y-3">
            <Input placeholder="Search projects…" className="max-w-sm" />
            <div className="overflow-hidden rounded-md border border-border bg-card">
              <div className="grid grid-cols-[1fr_120px_100px_80px] items-center gap-3 border-b border-border bg-muted px-3 py-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <span>Project</span>
                <span>Branch</span>
                <span>Status</span>
                <span className="text-right">Findings</span>
              </div>
              {[
                { name: "frontend-admin", branch: "main", tone: "critical" as const, label: "Critical", count: 2 },
                { name: "backend-api", branch: "develop", tone: "success" as const, label: "Clean", count: 0 },
                { name: "mobile-app", branch: "release/1.2.0", tone: "high" as const, label: "High", count: 5 },
                { name: "shared-utils", branch: "main", tone: "medium" as const, label: "Medium", count: 1 },
                { name: "docs-site", branch: "main", tone: "info" as const, label: "Info", count: 3 },
              ].map((row) => (
                <div
                  key={row.name}
                  className="grid h-row grid-cols-[1fr_120px_100px_80px] items-center gap-3 border-b border-border px-3 text-sm transition-colors duration-fast ease-out-soft last:border-b-0 hover:bg-accent"
                >
                  <span className="font-medium">{row.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {row.branch}
                  </span>
                  <span>
                    <Badge tone={row.tone}>{row.label}</Badge>
                  </span>
                  <span className="text-right font-mono text-xs">
                    {row.count}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </Section>

        <Section title="Elevation + radius + motion" description="Shadow scale, radius hierarchy, motion durations.">
          <div className="grid grid-cols-3 gap-4">
            <div className="rounded-md border border-border bg-card p-4 shadow-sm">
              <div className="text-sm font-medium">shadow-sm</div>
              <div className="text-xs text-muted-foreground">Cards, buttons</div>
            </div>
            <div className="rounded-md border border-border bg-card p-4 shadow-md">
              <div className="text-sm font-medium">shadow-md</div>
              <div className="text-xs text-muted-foreground">Dropdown, popover</div>
            </div>
            <div className="rounded-md border border-border bg-card p-4 shadow-lg">
              <div className="text-sm font-medium">shadow-lg</div>
              <div className="text-xs text-muted-foreground">Drawer, dialog</div>
            </div>
          </div>
          <div className="grid grid-cols-4 gap-4">
            <div className="flex h-16 items-center justify-center rounded-sm border border-border bg-card text-xs">
              rounded-sm · 4px
            </div>
            <div className="flex h-16 items-center justify-center rounded-md border border-border bg-card text-xs">
              rounded-md · 6px
            </div>
            <div className="flex h-16 items-center justify-center rounded-lg border border-border bg-card text-xs">
              rounded-lg · 8px
            </div>
            <div className="flex h-16 items-center justify-center rounded-xl border border-border bg-card text-xs">
              rounded-xl · 12px
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Motion: <span className="font-mono">duration-fast</span> 150ms ·{" "}
            <span className="font-mono">duration-base</span> 200ms ·{" "}
            <span className="font-mono">duration-slow</span> 250ms · easing{" "}
            <span className="font-mono">ease-out-soft</span> (Linear-style).
          </p>
        </Section>

        <Section
          title="Typography"
          description="One scale, one component each (W12-A) — never hand-roll a page title."
        >
          <div className="space-y-2">
            <PageTitle>PageTitle — 18px semibold</PageTitle>
            <SectionTitle>SectionTitle — 16px semibold</SectionTitle>
            <Subtitle>Subtitle — 14px muted, sits under a page title.</Subtitle>
            <Body>Body — 14px default copy.</Body>
            <Body muted>Body muted — secondary copy.</Body>
            <Caption>Caption — 12px meta (timestamps, counts).</Caption>
            <div>
              <Eyebrow>Eyebrow — overline / column label</Eyebrow>
            </div>
          </div>
        </Section>

        <Section
          title="Badges"
          description="Severity tones + neutral. Colour is never the only signal — each pairs a word with the tint."
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="critical">Critical</Badge>
            <Badge tone="high">High</Badge>
            <Badge tone="medium">Medium</Badge>
            <Badge tone="low">Low</Badge>
            <Badge tone="info">Info</Badge>
            <Badge tone="success">Clean</Badge>
            <Badge variant="outline">Neutral</Badge>
          </div>
        </Section>

        <Section
          title="Empty + loading states"
          description="Layered medallion empty state (W12-D) and a per-column table skeleton."
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="rounded-md border border-border bg-card">
              <EmptyState
                icon={<FolderOpen />}
                title="No projects yet"
                description="Register your first repository to start scanning."
                action={<Button size="sm">Register project</Button>}
              />
            </div>
            <div className="overflow-hidden rounded-md border border-border bg-card">
              <table className="w-full text-sm" aria-busy>
                <tbody>
                  <TableRowsSkeleton
                    rows={5}
                    columns={["w-40", "w-16", "w-20", "w-24"]}
                  />
                </tbody>
              </table>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Skeleton className="h-8 w-8 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-24" />
            </div>
          </div>
        </Section>

        <Section
          title="Feedback — alerts + toast"
          description="Inline alerts for form errors; toasts (W12-B) for success / non-blocking notices."
        >
          <div className="space-y-3">
            <Alert>
              <AlertDescription>
                Default alert — neutral inline message.
              </AlertDescription>
            </Alert>
            <Alert variant="destructive">
              <AlertDescription>
                Destructive alert — a validation error stays inline next to the
                field.
              </AlertDescription>
            </Alert>
            <div className="flex flex-wrap gap-3">
              <Button
                size="sm"
                onClick={() =>
                  toast("Saved", { tone: "success", key: "preview_saved" })
                }
              >
                Trigger success toast
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  toast("Something went wrong", {
                    tone: "error",
                    key: "preview_error",
                  })
                }
              >
                Trigger error toast
              </Button>
            </div>
          </div>
        </Section>

        <Section
          title="Brand — TRUSCA logo candidates"
          description="W1 rebrand: three new-motif symbol candidates. Pick one; it becomes the favicon, docs logo, and sidebar monogram."
        >
          <BrandCandidates />
        </Section>

        <footer className="border-t border-border pt-6 text-xs text-muted-foreground">
          Hover any button or table row to feel the 150 ms transition. Focus
          a button with Tab — the 2 px ring + 2 px offset is the new focus
          signal.
        </footer>
      </div>
    </div>
  );
}

export default DesignSystemPreview;
