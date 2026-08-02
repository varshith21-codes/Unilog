# AXIOM — Verifiable Product Intelligence for Industrial Commerce

> Every attribute has a source. Every claim has a proof. Every decision has a number attached to it.

AXIOM ingests fragmentary product information — a part number, an ERP description stub, a
supplier PDF, a manufacturer URL, a photo — and produces a complete, class-conformant,
unit-normalized, **citation-backed** product record, together with a formal statement of what
was verified, what was inferred, and what could not be established at all.

The design blueprint is in [`docs/AXIOM-Product-Intelligence-Blueprint.md`](docs/AXIOM-Product-Intelligence-Blueprint.md).

## The thesis: build the trust layer, not the text layer

Generating plausible product copy from a part number is a solved problem, and by 2026 every
PIM ships it. It is also the wrong problem. An industrial distributor cannot publish a
pressure rating that a model invented, because the downstream cost of a wrong spec is a failed
installation, a returned pallet, or a liability claim — not a bounced-back page view.

So the hard part is not producing values. It is knowing **which values you are allowed to
believe**, and being able to prove it afterwards. AXIOM is built around that:

| Instead of | AXIOM does |
|---|---|
| "The model is usually right" | A measured error bound on everything auto-published |
| Free-text enrichment | Evidence-or-null, enforced in the type system |
| A confidence number the model made up | Confidence estimated from independently checkable signals |
| "Human review recommended" | A queue ordered by failure mode, with the evidence on screen |
| A best-effort feed | A pre-flight gate that refuses to publish an unreviewed value |

## Core principles

1. **Evidence or null.** A specification value cannot be written without a verifiable evidence
   span. No evidence means a typed gap record, not a guess. This is enforced by a Pydantic
   validator, not by convention — discipline does not survive a deadline.
2. **Extraction and generation are separate subsystems.** Specs are extracted and proven.
   Marketing copy is generated, but constrained to reference only already-verified facts.
3. **Deterministic where determinism exists.** Unit conversion, check digits, dimensional
   consistency and arithmetic are code, not model output.
4. **Confidence is first-class and calibrated** against real reviewer outcomes.
5. **Everything is versioned and replayable** — prompt version, model ID, schema version,
   source document hash.

## Quickstart

Requires Python 3.11+ (Node 20+ only for the CDK app and the Next.js dashboards).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,api,docintel]"

pytest -q                      # 651 tests, no AWS credentials needed
ruff check .
```

Everything above runs offline. The pipeline itself needs Bedrock:

```powershell
$env:AWS_PROFILE = "axiom"

# prove every cascade tier is actually invocable, and pin the model IDs
python scripts/preflight_bedrock.py --region us-east-2 --write

# run one SKU end to end and save a review session
python scripts/run_pipeline.py data/samples/ba100.txt `
  --sku BA-100-075 --brand "milwaukee vlv" `
  --include-optional --risk-budget 0.05 --save-session --out data/out
```

That prints every stage — classification, extraction with quotes, normalization, validation
verdicts, the accept/queue decision per value with the feature that drove it, and the channel
pre-flight result. Artifacts land in `data/out/`: a signed certificate and one payload per
channel that passed pre-flight.

## The review workspace

```powershell
$env:PYTHONPATH = "packages;."
python -m uvicorn apps.api.main:app --port 8000
# open http://127.0.0.1:8000/
```

The workspace exists to make a decision take seconds. That imposes one hard requirement:
**the evidence has to be on screen next to the value.** A reviewer who has to open the source
PDF and hunt for a number is doing the original job by hand, and the claimed speed-up is gone.
So a session carries the source text alongside the values, with every citation resolved to a
page, a line, and where applicable an exact table cell.

The queue is ordered **by failure mode first**, then by ascending confidence. Working one
reason code at a time — every unverified citation, then every blocking rule failure — is much
faster than walking SKU by SKU, because the reviewer holds one question in mind across a run.

Every decision folds back into the per-attribute and per-supplier priors, which feed confidence
estimation, which moves the auto-accept threshold. The response to a decision reports the prior
movement, so the flywheel is visible rather than asserted. Priors are shrunk toward neutral in
proportion to sample count: one accept does not earn an attribute a confident prior.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Session count, whether calibration exists |
| `GET /api/sessions` | Every session on disk with queue counts by reason |
| `GET /api/session/{sku}` | Full session: items, evidence, validations, source pages |
| `POST /api/session/{sku}/decision/{attr}` | Accept / reject / correct; returns prior movement |
| `GET /api/policy?epsilon=` | The risk dial: threshold, coverage, and the full risk-coverage curve |

**The API is unauthenticated.** It is bound to localhost for the demo and reads and writes
local files. Anything beyond a laptop needs an authentication layer in front of it, plus tenant
scoping on the session directory — `_session_path` currently guards only against path
traversal, not against cross-tenant access.

### Two front-end surfaces, deliberately

- **`apps/api/static/index.html`** — the live review workspace. Self-contained HTML, Tailwind
  from CDN, no build step. Reads and writes through the API above: real decisions, real prior
  movement, keyboard-driven queue, risk slider.
- **`apps/console/`** — a Next.js dashboard app (portfolio scoreboard, certificate views,
  per-SKU review views) driven by a **static fixture** from
  `scripts/export_console_fixture.py`. Read-only.

These are not yet one application. The Next.js app was built before the API existed, and its
`src/lib/data.ts` is an explicit seam: swapping the fixture read for a `fetch` against
`apps/api` is the whole change needed to unify them. Doing that is the next front-end task,
and it is called out here rather than hidden because a repo with two consoles should say so.

## Measured results

The backtest hides known-correct values from the golden set, runs the real pipeline against the
source documents alone, and scores what comes back. Latest run on `pvf_valves_v1`:

```powershell
$env:AWS_PROFILE = "axiom"
python scripts/run_backtest.py --detail          # reports only
python scripts/run_backtest.py --write --detail  # also writes the calibration artifacts
```

**9 products, 180 comparisons (55 of them known-absent), 51s, 9 model calls.**

| Outcome | Count |
|---|---|
| correct | 124 |
| **wrong value** (the dangerous failure) | **0** |
| missed | 1 |
| correctly abstained | 55 |
| **hallucinated** | **0** |

| Metric | Value |
|---|---|
| precision | 100.0% |
| recall | 99.2% |
| F1 | 99.6% |
| abstention correctness | 100.0% |
| citation coverage | 100.0% |

Scoring uses **five outcomes, not two**. Binary right/wrong lets a system that fabricates
freely benchmark like an honest one, because a confident wrong answer and a correct abstention
both collapse to "not correct". Separating *missed* from *wrong value* from *hallucinated* is
what makes the numbers mean anything.

### Risk-controlled auto-accept

| Error budget | Threshold | Coverage | Upper bound | Status |
|---|---|---|---|---|
| 2% | — | 0.0% | 2.1% | not achievable |
| 5% | 0.685 | 100.0% | 2.1% | ok |
| 10% | 0.685 | 100.0% | 2.1% | ok |

The threshold is chosen against a **one-sided Wilson upper bound**, not the observed error
rate. Zero errors in a small sample does not mean zero risk: with 124 samples the true rate
could still be 2.1%, which is why the 2% budget is correctly reported as unreachable even
though the observed error rate is 0%. Thresholding on the point estimate would manufacture a
guarantee out of sample size, which is worse than no guarantee because it invites reliance.

### What these numbers do not prove

Stated plainly, because a benchmark oversold is worse than no benchmark:

- **The set is small.** 9 products, 180 comparisons. Wide confidence intervals on everything.
- **Author coupling.** I wrote both the source fixtures and the ground truth. That is the
  weakest part of the evaluation. Real supplier PDFs from a distributor with independently
  maintained ground truth would be a materially harder test.
- **Recall varies run to run.** An earlier run scored 125/0 missed; this one missed
  `operating_torque` on BA-100-050, where the datasheet qualifies the torque figure to one size
  only. Precision and hallucination rate have been stable at 100% / 0% across runs — the
  failure mode is abstention, not fabrication, which is the correct direction for this design.
- **One vertical.** Bronze and brass valves (PVF). The schema is declarative, so a new class is
  YAML rather than code, but that claim is untested outside valves.

## Repository layout

```
axiom/
├── docs/
│   ├── AXIOM-Product-Intelligence-Blueprint.md   # the design document
│   └── AWS-SETUP.md
├── infra/                     # AWS CDK app (TypeScript) — synthesizes, not deployed
├── schema/                    # THE SOURCE OF TRUTH (declarative, no code)
│   ├── attributes/            # reusable attribute dictionary
│   ├── classes/               # class bindings, cross-field rules, channel profiles
│   ├── brands.yaml            # brand master with alias resolution
│   └── constants.yaml         # domain facts referenced by rules
├── packages/axiom/
│   ├── core/                  # domain models, evidence, gaps, certificate builder
│   ├── ingest/                # content-addressed artifact store
│   ├── docintel/              # document parsing, quote location, span resolution
│   ├── schema/                # schema loader, integrity checks, prompt generation
│   ├── classify/              # class assignment + derived ETIM / UNSPSC
│   ├── extract/               # model cascade, evidence-bound extraction
│   ├── normalize/             # unit registry, datasheet value parsers, MPN cleaning
│   ├── validate/              # validation layers L0–L3 + AST rule evaluator
│   ├── confidence/            # features, calibration, Wilson risk policy
│   ├── review/                # review sessions, decisions, prior updates
│   ├── syndicate/             # channel pre-flight, exporters, publication gate
│   ├── evaluation/            # backtest harness, five-outcome scoring
│   └── config/                # models.yaml (generated by the preflight script)
├── apps/
│   ├── api/                   # FastAPI review workspace + static console
│   └── console/               # Next.js dashboards (static fixture)
├── scripts/
│   ├── preflight_bedrock.py   # prove every cascade tier is invocable; pin model IDs
│   ├── smoke_extraction.py    # prove the evidence contract holds against live models
│   ├── run_pipeline.py        # one SKU, end to end, fully reported
│   ├── run_backtest.py        # the numbers above; writes calibration artifacts
│   ├── bootstrap_lite.ps1     # CDK staging bucket without the bootstrap IAM roles
│   └── export_console_fixture.py
├── data/
│   ├── golden/                # ground truth — the highest-value directory here
│   ├── calibration/           # calibration set + learned priors (governs auto-accept)
│   ├── samples/               # demo datasheets
│   ├── sessions/              # saved review sessions
│   └── cache/                 # content-addressed artifact store
└── tests/                     # 651 tests
```

Adding an attribute means editing YAML in `schema/`. No Python change, no prompt change — the
extraction prompt is generated from the dictionary, and the registry refuses to load a schema
that is internally inconsistent.

## Pipeline stages

`scripts/run_pipeline.py` is the clearest read of the whole flow. It runs:

1. **ingest** — content-addressed store, SHA-256 keyed, so every citation is anchored to an
   immutable document version
2. **parse** — pages, lines with bounding boxes, and tables with addressable cells
3. **classify** — the class decides which attributes to ask for, so it must run first
4. **extract** — cascade of models, `value_raw` plus a verbatim quote and nothing else
5. **normalize** — units, enums, ranges to canonical form
6. **validate** — L0 type/format, L1 dimension, L2 cross-field rules, L3 plausibility
7. **score and decide** — confidence features, calibrator, risk policy, accept or queue
8. **certificate and channel exports** — signed certificate, pre-flight gate, per-channel payloads

Extraction never normalises and never validates. Keeping those separate is what makes a
failure attributable: a bad unit conversion cannot masquerade as a bad extraction.

## AWS configuration

Account `860510875713`, region **us-east-2 (Ohio)**, profile `axiom`. See
[`docs/AWS-SETUP.md`](docs/AWS-SETUP.md). Note the region caveat: this account has **zero**
Bedrock token quota in `us-east-1`, which presents as a `ThrottlingException` on the very first
call rather than as a quota error.

### Model cascade

Pinned in `packages/axiom/config/models.yaml` (generated — re-run the preflight rather than
editing it). Anthropic models are unavailable on this account because they are billed via AWS
Marketplace and require a payment instrument, so the cascade runs on the open-weight families:

| Tier | Model | Smoke-test latency |
|---|---|---|
| micro / volume | `zai.glm-4.7-flash` | 3.2 s |
| mid | `zai.glm-4.7` | 4.6 s |
| frontier | `zai.glm-5` | 37.8 s |
| vision | `qwen.qwen3-vl-235b-a22b` | — |
| embedding | `amazon.titan-embed-text-v2:0` (dim 1024) | — |

### Infrastructure

The storage foundation is **deployed** to `us-east-2` as stack `Axiom-dev-Storage`:

| Resource | Name |
|---|---|
| Landing bucket (versioned) | `axiom-dev-landing-860510875713` |
| Artifact bucket | `axiom-dev-artifacts-860510875713` |
| Jobs table (on-demand, PITR, 2 GSIs) | `axiom-dev-jobs` |
| Ingest queue (DLQ after 3 receives) | `axiom-dev-ingest` |
| Ingest DLQ | `axiom-dev-ingest-dlq` |

Nothing in the pipeline requires it yet — `run_pipeline.py` reads and writes locally and talks
to Bedrock directly. The stack is the foundation Tier 2's batch orchestration builds on.

```powershell
./scripts/bootstrap_lite.ps1     # creates the CDK staging bucket; idempotent

cd infra
npm install
$env:CDK_DEFAULT_ACCOUNT = "860510875713"; $env:CDK_DEFAULT_REGION = "us-east-2"
npx cdk deploy --all
```

**Note there is no `cdk bootstrap` step.** A normal bootstrap creates four IAM roles, and the
deploying principal holds `PowerUserAccess`, which grants everything *except* `iam:*`. Rather
than escalate a laptop-resident access key to effective administrator, the app uses
`CliCredentialsStackSynthesizer` and deploys with the CLI's own credentials — which works
because this stack declares no IAM resources. The full reasoning, the recovery procedure for a
half-bootstrapped account, and the scoped IAM policy needed once Tier 2 adds Lambda are in
[`docs/AWS-SETUP.md`](docs/AWS-SETUP.md#cdk-deployment-without-bootstrap).

## Build status

Tracked against blueprint Part 12.

- [x] **Tier 1 — the spine and the trust layer.** Core domain models with the evidence
      invariant, unit registry, declarative schema registry, document parsing with addressable
      tables, model cascade, evidence-bound extraction, normalization, validation L0–L3,
      classification, confidence calibration, Wilson risk policy, channel pre-flight and
      exporters, signed certificates, backtest harness, review workspace.
- [ ] **Tier 2 — differentiators.** Automated Reasoning (L6) via Bedrock Guardrails,
      risk-coverage curve in the UI, variant table explosion, constrained copy generation with
      claim-checking, Quality Index dashboard, cost-per-SKU meter.
- [ ] **Tier 3 — scale and polish.** Batch orchestration, multi-source cross-validation (L4),
      before/after cohort study.

Storage infrastructure is deployed (see above). Compute is not — the pipeline runs locally.

### Known gaps

- The repo is **not under version control** yet. `git init` is overdue.
- The two front-end surfaces are not unified (see above).
- The API has no authentication or tenant scoping.
- L4 (cross-source agreement) and L6 (formal verification) exist only as members of the
  `ValidationLayer` enum. No validator implements them, so they produce no results at all —
  the layer list in the blueprint is ahead of the code here, and certificates reflect L0–L3.
- HTS classification is deliberately never auto-published: published benchmarks put accuracy
  near 40% at the 10-digit level, which is not publishable at any confidence this system can
  honestly assign.
