# scan-bench UX capture metadata

- captured_at: 2026-05-28T03:54:12.466Z
- git_sha: 6d7c36ddc4ba
- account: frontend-admin@demo.trustedoss.dev (team_admin)
- dataset_project: fx-maven-node (project_id 155a9c99-df8b-4d7b-83a1-71e1624471e6)
- viewport: 1440×900
- deviceScaleFactor: 2 (Retina)
- ui_language: en (primary) + ko (core 3)
- portal_base: http://localhost:5173
- api_base: http://localhost:8000

## Re-capture
```
cd apps/frontend && npx playwright test ux-audit/capture-ours
```

## EN captures

- **O1** `dashboard.png` + `dashboard-full.png`
- **O2** `project-list.png` + `project-list-full.png`
- **O3** `project-detail-overview.png` + `project-detail-overview-full.png`
- **O4** `project-detail-components.png` + `project-detail-components-full.png`
- **O5** `project-detail-vulnerabilities.png` + `project-detail-vulnerabilities-full.png`
- **O6** `drawer-vulnerability-detail.png`
- **O7** `project-detail-reports.png` + `project-detail-reports-full.png`
- **O8** `scans-queue.png` + `scans-queue-full.png`

## KO captures

- `ko/dashboard.png`
- `ko/project-detail-overview.png`
- `ko/project-detail-vulnerabilities.png`
