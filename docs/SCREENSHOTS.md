# IntelliCredit — Screenshots & Visual Assets

The 5–7 strongest screens for the README/submission, with the **exact route, the
exact tab, and the component file** behind each. Nothing here is a fabricated
mockup — these are capture instructions against the *real* running application.

**To produce populated screens, first:** `docker compose up -d --build` →
`make seed` → `docker compose exec api python -m scripts.provision_demo` →
`docker compose exec api python -m scripts.provision_documents`
(see [DEMO_SETUP.md](DEMO_SETUP.md)). Log in as
`officer@demo.intellicredit.in` / `Demo!Pass123`.

The case workspace is a single URL (`/cases/{caseId}`); panels are **tabs**, not
separate URLs, so capture instructions say "click the *X* tab," and the file that
renders each panel is listed for reference.

Recommended viewport: 1440×900, light theme (there is also a dark theme toggle).
Save captures under `docs/assets/` (create it) as `NN-name.png`.

---

## Priority screens

| # | Screen | Route → panel | Component file | What it proves |
|---|--------|---------------|----------------|----------------|
| 1 | **Dashboard** | `/dashboard` | `frontend/src/app/(app)/dashboard/page.tsx` | portfolio snapshot: cases, awaiting-review, decisions, grade distribution |
| 2 | **Evidence viewer + bbox highlight** | `/cases/{Acme}` → **Evidence** tab → click a source with a "region" badge | `components/workspace/panels/evidence.tsx` → `document-viewer.tsx` → `pdf-highlight-viewer.tsx` | structural explainability: claim → document page → highlighted region |
| 3 | **Financial analysis** | `/cases/{Acme}` → **Financial** tab | `components/workspace/panels/financial.tsx` | deterministic ratios grouped by category, each with its formula tooltip |
| 4 | **ML + SHAP** | `/cases/{Acme}` → **ML Risk** tab | `components/workspace/panels/ml-risk.tsx` | calibrated PD, model card + NON-PRODUCTION/relative-signal badge, TreeSHAP reason codes |
| 5 | **Scorecard** | `/cases/{Acme}` → **Scorecard** tab | `components/workspace/panels/scorecard.tsx` | score gauge, five pillars, per-factor printed arithmetic, overlays |
| 6 | **Decision** | `/cases/{Acme}` → **Decision** tab | `components/workspace/panels/decision.tsx` | grade, limit + binding constraint, indicative rate, **risk premium**, supporting/concern factors |
| 7 | **HITL review** | `/cases/{Deccan}` (paused) → **Review** tab | `components/workspace/panels/hitl.tsx` | pause/resume, officer observations + bounded notches, knockout toggles |
| 8 | **CAM + PDF** | `/cases/{Acme}` → **CAM** tab → **Download PDF** | `components/workspace/panels/cam.tsx` | citation-backed memo; capture both the screen and the generated `CAM-*.pdf` |

Supporting captures worth having:

- **Login** (`/login`, `frontend/src/app/login/page.tsx`) — shows the pre-filled
  demo credentials and roles.
- **Case workspace header** (top of any `/cases/{id}`,
  `components/workspace/company-header.tsx`) — the Grade / Decision / **Run mode
  (LIVE / REPLAY / DEGRADED)** / Score / PD / limit / rate strip, plus the
  `WorkflowProgress` stepper — one image that captures the whole outcome.
- **Documents** (`/cases/{id}` → **Documents** tab,
  `components/workspace/panels/documents.tsx`) — the upload dropzone + per-document
  ingestion stage pills (classify → OCR → extract → validate).

---

## Notes for a clean capture

- Use the **Acme Textiles** case for the completed-run panels (Evidence, Financial,
  ML, Scorecard, Decision, CAM) — `provision_demo` completes it on the APPROVE path.
- Use the **Deccan Agro Foods** case for HITL — `provision_demo` leaves it paused;
  the **Review** tab only exists while a run is paused, and it auto-opens.
- For the bbox highlight (#2), pick an evidence row that shows the **"region"**
  badge — that is the one wired to open the highlight modal.
- The CAM panel footer still carries a stale "PDF/DOCX export is a backend
  follow-up" note, but the **Download PDF** button is live — capture the working
  button, not the note.

## Automated capture (optional)

There is no bundled screenshot script. If you automate, drive the **real**
application (a headless browser against `http://localhost:3000` after login), never
a mock. An automated Playwright E2E/capture suite is listed as future work in
[FINAL_STATUS.md](FINAL_STATUS.md); do not fabricate images in its place.
