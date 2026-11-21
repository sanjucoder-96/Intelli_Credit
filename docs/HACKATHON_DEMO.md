# IntelliCredit — 5-Minute Judge Demo

A tight, performable script. It exercises the whole thesis: **evidence in →
citation-backed, quantitatively-controlled, human-reviewed decision out**, and it
ends with the durable-restart proof that separates this from a chatbot.

**Before you start** (see [DEMO_SETUP.md](DEMO_SETUP.md) for detail):

```bash
cp .env.example .env
docker compose up -d --build
make seed
docker compose exec api python -m scripts.provision_demo       # 1 scored case + 1 paused-at-review case
docker compose exec api python -m scripts.provision_documents  # attaches a doc with evidence bounding boxes
```

- Frontend: **http://localhost:3000** · API docs: **http://localhost:8000/docs**
- Log in as the **credit officer** to demo the review step:
  `officer@demo.intellicredit.in` / `Demo!Pass123`
- Two seeded cases:
  - **Acme Textiles Private Limited** — a *completed* run (APPROVE path) → use for
    Evidence, Financial, ML, Decision, CAM.
  - **Deccan Agro Foods Limited** — *paused at human review* with hard fraud
    signals → use for the HITL step.
- The case workspace is one URL (`/cases/{id}`); you switch panels by clicking the
  **tabs** across the top (Documents · Overview · Evidence · Financial · Research ·
  Fraud · ML Risk · Scorecard · Decision · CAM · Review-when-paused).

---

### 0:00 — The problem (say this while the dashboard loads)

> "A credit team receives fragmented evidence about a company — GST returns,
> financial statements, bank statements, legal notices, plus whatever the web
> knows. Turning that into a defensible decision is slow, and if you hand it to an
> LLM, it will happily hallucinate a credit limit. IntelliCredit fixes both: it
> automates the assembly **and** it makes every number deterministic and every
> claim traceable. The LLM reads and writes; it never decides."

Open **http://localhost:3000/dashboard** → point at the portfolio snapshot (active
cases, awaiting review, decisions, risk-grade distribution).

### 0:30 — Upload / ingestion

Open the **Acme Textiles** case → **Documents** tab. Point at the attached
`annual_report_FY2025-26.pdf` and its per-document **stage pills**
(classify → OCR → extract → validate). If you want a live upload, drag any PDF
into the dropzone and watch the pills advance over SSE.

> "Each document runs an idempotent, checkpointed pipeline: text-layer first,
> per-page OCR escalation only where needed, then schema-typed extraction gated by
> arithmetic validators — it never silently accepts a hallucinated digit."

### 1:00 — Evidence (the money shot)

**Evidence** tab. Scroll the **evidence ledger** — each row is a claim with its
source, cited quote, and confidence. Find a row with a **"region"** badge, click
the document source.

> "This is structural explainability. Click the value…"

The document viewer opens the **server-rendered page image** with the cited
**bounding box highlighted**.

> "…and it takes me to the exact **document, page, and region** it came from. Not
> 'the model says so' — the source, on the page."

### 1:30 — Intelligence

Walk three tabs quickly:

- **Financial** — ratios grouped into liquidity / leverage / coverage /
  profitability / efficiency / cash-flow; hover any ratio for its formula.
- **Research** (and **Fraud**) — GST↔bank consistency checks, adverse-news /
  litigation findings with citations and source tiers, and forensic fraud signals
  (e.g. GSTR-3B-vs-bank-credit shortfall, cheque-return frequency), each tagged
  forensic-rule vs. unsupervised-ML.

> "Deterministic forensics are primary and fully explainable; ML contributes one
> honestly-labeled anomaly signal, never a black-box fraud verdict."

### 2:30 — ML default risk

**ML Risk** tab.

> "Here's the calibrated probability of default…"

Point at: the large **PD** number, the **model card** (name/version, algorithm =
XGBoost, feature-schema version, and the **NON-PRODUCTION / relative-signal**
badge), and the **"Why this PD"** panel — TreeSHAP reason codes split into raises-
vs-lowers with the exact log-odds contribution bars.

> "Every driver is an exact TreeSHAP contribution. And we label it honestly: this
> model is trained on **foreign** public bankruptcy data, so it's a **relative**
> risk signal re-normalized inside the scorecard — not a claimed Indian PD.
> Calibration is real though: ECE dropped from 0.26 to about 0.003 after isotonic
> calibration."

### 3:00 — Human in the loop

Switch to the **Deccan Agro Foods** case (paused). It opens on the **Review** tab
with a yellow "paused for credit officer review" banner.

> "The run **pauses** here. The workflow state is checkpointed in Postgres — this
> is a real interrupt, not a spinner."

Add an observation (e.g. *"Site visit: factory at 40% capacity"*), set its bounded
impact notch, optionally toggle a knockout, and click **Submit review**.

> "The officer's qualitative note maps onto a scorecard factor with a **bounded**
> adjustment, the run **resumes**, and the score is **recomputed** — the
> before/after delta is recorded in the trace. The human moves the number,
> auditably; the human can't move it invisibly."

### 3:45 — The decision

Back on **Acme** → **Scorecard** tab, then **Decision** tab.

- **Scorecard**: the score gauge, the five pillars, and — expand a factor — the
  **printed arithmetic** (formula · input · normalized · weight · contribution),
  plus any overlays that fired.
- **Decision**: recommendation + grade, **recommended limit** with its *binding
  constraint*, **indicative rate**, **risk premium**, base rate, and the limit
  arithmetic string; plus the supporting-vs-concern factor lists.

> "Grade, limit, and rate are pure functions of that breakdown. The limit is the
> min of EBITDA-multiple, MPBF Tandon-II, and the turnover method — and it tells
> you which one bound. The rate is base + grade premium + itemized adjustments.
> No model 'chose' these."

### 4:15 — The CAM

**CAM** tab.

> "The Credit Appraisal Memo. The only free prose is the executive summary; every
> figure is re-derived deterministically and every claim is cited."

Click **Download PDF** → open the generated `CAM-Acme…​.pdf`.

> "Same content, screen and PDF — the renderer refuses uncited claims."

### 4:45 — Architecture proof (durable resume)

In a terminal, run the narrated chaos demo (or do it live with `docker compose
kill worker && docker compose up -d worker`):

```bash
docker compose exec api python -m scripts.chaos_demo
```

```text
   worker A starts the run  →  pauses at the HITL gate
        ↓  KILL worker A
   worker B starts, resumes from the Postgres checkpoint
        ↓
   run COMPLETES — exactly one execution row, no node run twice
```

> "I can kill the worker mid-run. A fresh worker resumes from the durable
> checkpoint and finishes — **no duplicated work, no double decision**. That's
> LangGraph checkpointing on Postgres, and it's why this is infrastructure, not a
> notebook."

### 5:00 — Close

> "Every important number is deterministic and reproducible; every claim traces to
> its source; a human reviews and can override; and the whole run survives a crash.
> **Traceable, reproducible, quantitatively controlled, and reviewable by a
> human** — that's IntelliCredit."

---

## If something isn't available

- **No demo data?** Re-run `make seed` then the two `provision_*` scripts above.
- **A tab is empty?** It needs a completed run — use the **Acme** case (the
  `provision_demo` script completes it); the **Review** tab only exists while a run
  is paused (the **Deccan** case).
- **Offline / no API keys?** Expected. The seeded runs use an offline scripted
  caller; the run-mode badge in the case header shows LIVE / REPLAY / DEGRADED
  truthfully. Set `IC_AGENT__LLM_MODE=replay` for a fully offline demo.
- **Live LLM 503 during a live run?** The gateway retries and can fall back to a
  recording (badge → DEGRADED); it never fabricates an answer.
