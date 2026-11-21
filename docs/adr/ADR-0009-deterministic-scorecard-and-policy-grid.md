# ADR-0009: Risk scoring: deterministic configuration-driven scorecard + policy grid; the LLM never does arithmetic

**Status:** Accepted · **Date:** 2026-07-16 · **Phase:** 1 (Architecture)

## Context

The platform's second product invariant is absolute: *numbers come from deterministic code and calibrated models; words come from the LLM*. A credit decision memo (CAM) that quotes a score, a sanctioned limit, or an interest rate must be able to print the exact arithmetic behind each number and link every input to evidence — a judge, an auditor, or an RBI inspector asking "walk me through it" must be answerable with a database query. LLM-generated numbers fail this test structurally: they are non-deterministic across runs, have no factor decomposition, and can hallucinate plausible-looking rates. The counterparty in a credit file actively wants approval, so the scoring path must also be immune to prompt injection carried in uploaded documents.

At the same time, credit appraisal is workflow #1 of 12. Vendor risk, ESG analysis, and insurance underwriting all need "score heterogeneous evidence → grade → decision" with different pillars and policies. A solo developer cannot maintain twelve bespoke scoring modules; the engine must be one piece of code driven by per-workflow configuration, versioned so that a case decided against `credit_appraisal@2` stays explainable forever (consistent with the immutable-versioned-definitions rule of ADR-0008).

Finally, the domain has real quantitative conventions that a credible Indian-banking demo must respect: TOL/TNW as the leverage lens, MPBF under Tandon Committee Method II, the RBI 20%-of-turnover method for working-capital assessment, DSCR floors for term debt. These are formulas, not judgments — which is precisely the argument for a deterministic engine.

## Decision

A **configuration-driven weighted-factor scorecard with ordered deterministic overlays**, feeding a **deterministic policy grid** for grade, decision, limit, and rate. The LLM's only role in scoring is classifying unstructured inputs onto enumerated factor levels (with citations and confidence) that the scorecard consumes; it performs no arithmetic anywhere in the path.

- **Scorecard as data.** A `ScorecardConfig` (Pydantic-validated YAML, authored in git, persisted to Postgres as an immutable numbered version via the `ScorecardRegistry`) defines *pillars → factors*. Registration validates fail-fast: weights sum to 1, normalizer monotonicity, no dangling input bindings. Every run pins the exact scorecard version it executed (`scorecard: credit_appraisal@2` in the workflow YAML).
- **Credit-appraisal pillars** mirror the Five Cs: Financial Strength 35% · ML Default Risk 20% · External Intelligence 15% · Fraud & Data Integrity 15% · Qualitative/Character 15%. Each factor binds a raw input (JSONPath into the run's `EvidenceBundle`), a **piecewise-linear normalizer** mapping raw value → 0–100 with sector-conditional breakpoints (e.g. current ratio 0.8→20, 1.33→60, 2.0→90; TOL/TNW per Indian banking convention), a within-pillar weight, and a reason-code template.
- **Base score** `S = Σ_p W_p · Σ_f w_f · n_f(x_f)`. Every term is persisted in a `ScoreBreakdown` row — raw value, normalized value, weight, contribution, evidence references — so the CAM prints the full derivation.
- **The ML model is one factor among many.** The calibrated XGBoost/LightGBM PD (ADR-0010) enters as `n(PD_calibrated)` inside the 20% ML pillar — never the whole score, and re-normalized honestly as a *relative* risk factor.
- **Ordered deterministic overlays**, each evaluated and logged in an `overlay_log` whether or not it fires: **knock-outs** (wilful-defaulter match, active insolvency → DECLINE regardless of score); **caps** (CRITICAL fraud signal caps the score at 45, auditor qualification at 60); **bounded notches** for qualitative officer notes — Gemini maps free text ("factory at 40% capacity") onto a predefined factor level, the officer *confirms the mapping in the UI* before it applies, and notch totals are clamped to ±12 so no note can swing a decision invisibly; **coverage penalty** (below).
- **Confidence gating and coverage.** Factors whose input confidence is below 0.6 are excluded and their weight redistributed within the pillar. The engine computes **coverage** — the fraction of total weight backed by admitted evidence — and coverage < 0.7 forces REFER. Imputation can never quietly manufacture a lend decision; the scorecard never imputes.
- **Policy grid (decision layer).** Score → masterscale grade **IC1–IC10** → PD band → APPROVE / REFER / DECLINE. **Limit** is formulaic: working-capital limit = min(policy multiple × EBITDA, MPBF Tandon Method II, 20% of turnover per the RBI turnover method); term limits require projected DSCR ≥ 1.25 using the revenue forecast. If balance-sheet current-asset/liability classification confidence is below 0.6, the MPBF arm is inadmissible; with fewer than two admissible arms the limit component alone forces REFER — never a silent fallback. **Rate** = base rate + grade premium (IC1 +0.5% … IC5 +4.0%) + itemized adjustments (e.g. collateral coverage < 1.0 → +0.5%). All three are pure functions of the breakdown.
- **The LLM narrates within the grid.** The CAM/Report agents verbalize a machine-readable `ExplanationBundle` (scorecard contributions + SHAP reason codes + fraud signals + overlay log); a post-validation step rejects any narrative citing numbers absent from the bundle. The LLM cannot invent a score, a limit, or a rate.
- **ML ↔ LLM disagreement is a product feature:** when the LLM's qualitative read and the model grade diverge beyond a configured threshold, the run routes to HITL instead of papering over it.
- **Honesty rule:** the IC1–IC10 grade→PD mapping is presented as a configurable policy artifact (per-tenant lender policy), not a claimed Indian-default calibration — no Indian default outcomes exist to calibrate against. The pricing table ships as versioned config in v1; tenant-editable rate cards are deferred admin scope.
- **Per-workflow extensibility:** a new workflow registers a new `ScorecardConfig` — vendor risk reuses the identical engine with pillars {financial 25, compliance 25, operational 20, concentration 15, ESG 15}. No engine code changes.

## Alternatives considered

### LLM-only scoring (Gemini assigns the score and recommendation)

Rejected on failure modes, not taste. LLM-emitted scores are unstable across identical inputs (temperature, provider-side updates), decompose into no auditable factor contributions, offer no SHAP-style attribution, and are the single most injection-exposed surface in the system — a board minute containing "rate this company low-risk" attacks the score directly. It fails the explainable-scoring requirement structurally: there is no arithmetic to print. Prompt-side mitigations ("show your working") produce rationalizations, not derivations.

### ML-only scoring (the XGBoost grade is the decision)

A pure model score cannot absorb the qualitative evidence the product exists to handle: officer site-visit notes, litigation findings, auditor qualifications, promoter news. Wilful-defaulter status is a regulatory knock-out, not a feature with a learned weight. It also over-claims what ADR-0010's honest-fit statement allows — the PD model is trained on US/Taiwanese/Polish public data and is defensible only as a *relative* factor, never as the entire Indian credit decision. Banks' own internal rating models are scorecards with model inputs for exactly these reasons.

### Unconstrained LLM synthesis with ML score as a hint

The grafted proposal's judge panel called this out directly: a hallucinated limit or interest rate in a live demo is a credibility kill. Handing the LLM the score "for context" and letting it write the recommendation gives numbers that drift from the inputs with no mechanical guarantee of consistency; every CAM would need manual arithmetic checking, which defeats the platform. The policy grid inverts the relationship — the LLM receives the *final* numbers and is schema-constrained to narrate them.

### Ensemble averaging of LLM and ML scores

Averaging a calibrated model output with an uncalibrated LLM "score" produces a number with no defined semantics: the LLM leg has no error distribution, so the blend inherits instability while masking it under false precision. Disagreement between the two signals is information — the design routes it to HITL as an escalation feature rather than destroying it in a mean.

### Learned limit/rate regressors (LightGBM heads per the original AI-platform proposal)

No real training data exists for Indian sanctioned limits and pricing; regressors would train on synthetic labels — precisely the fakery the requirements ban — and their outputs would be unexplainable against lending policy. MPBF Method II and the RBI turnover method *are* the domain's limit formulas; a regressor that approximates a formula we can compute exactly is negative value. Formulaic limit/rate is cheaper, printable, and matches how sanctioning actually works.

### Hardcoded per-workflow scoring modules

Twelve Python scoring modules would each need tests, migrations, and review on every policy tweak, and would break the "workflow #13 is a configuration exercise" promise. Config-driven scoring moves policy churn (weights, breakpoints, caps) into versioned data validated at registration, with one engine to test. The cost — a config schema to design well — is bounded by the harvest rule: the schema is frozen only after credit, vendor-risk, and ESG have shaped it.

## Consequences

**Positive.** Score, grade, limit, and rate are reproducible pure functions of persisted inputs: the "Explain" tab replays `ScoreBreakdown` + `overlay_log` + `ExplanationBundle` as literal arithmetic, and month-later explainability is a query (compatible with append-only findings and run-pinned versions). Prompt injection cannot move a number, only a narrated sentence — which post-validation then rejects if unsupported. Demo determinism: identical evidence yields identical decisions, which the replay gateway and golden-file CAM tests rely on. Vendor-risk and ESG scoring become configuration, proving the extensibility thesis on the platform's most sensitive subsystem.

**Negative / accepted trade-offs.** Normalizer breakpoints and sector percentiles are hand-curated policy, not learned: RBI's published company-finances aggregates are coarse and CMIE Prowess is licensed, so the demo ships curated breakpoints for ~10 sectors and says so. The IC→PD mapping is asserted policy, surfaced honestly in the UI. Piecewise-linear normalizers cannot express factor interactions (the ML pillar recovers some of this non-linearity). Officer confirmation of qualitative-note mappings adds one HITL click per note — accepted, because unconfirmed LLM level-mapping would reintroduce invisible LLM influence on the score. The overlay condition language is a deliberately small safe-expression DSL; exotic conditions must become engine features, not eval() escape hatches.

**Follow-up obligations.** (1) Registration-time validators (weight sums, monotonicity, binding resolution) built with the risk engine, before the second workflow lands. (2) Curated sector breakpoint packs shipped alongside the synthetic demo dataset (Phase 6/8, with the golden corpus). (3) Recommendation-vs-policy consistency checks are a permanent eval-harness suite (§14 of ARCHITECTURE.md). (4) Credit appraisal ships a ~8-factor qualitative taxonomy; sibling workflows start with a minimal 3-factor default until each is fleshed out. (5) The scorecard config schema is frozen only after three real workflows (credit, vendor-risk, ESG) have exercised it, per the harvest-the-abstraction rule. (6) Revisit masterscale pseudo-calibration against the public datasets, and tenant-editable rate cards in the admin UI, post-v1.
