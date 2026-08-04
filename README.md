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

pytest -q                      # 753 tests, no AWS credentials needed
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
| `GET /api/health` | Session and bundle counts, whether calibration exists |
| `GET /api/sessions` | Every session on disk with queue counts by reason |
| `GET /api/session/{sku}` | Full session: items, evidence, validations, source pages |
| `POST /api/session/{sku}/decision/{attr}` | Accept / reject / correct; returns prior movement |
| `GET /api/policy?epsilon=` | The risk dial: threshold, coverage, and the full risk-coverage curve |
| `GET /api/console/dataset` | Everything the dashboards render, with review decisions joined in |
| `GET /api/console/stats` | Counts only — cheap enough to poll |

`/api/console/dataset` reports the policy the bundles were **actually decided under**, not a
freshly computed one. `/api/policy` is the separate what-if dial. Serving a different threshold
than the one that produced the accept/queue decisions would make every score badge in the UI
disagree with its own explanation.

**The API is unauthenticated.** It is bound to localhost for the demo and reads and writes
local files. Anything beyond a laptop needs an authentication layer in front of it, plus tenant
scoping on the session directory — `_session_path` currently guards only against path
traversal, not against cross-tenant access.

### The console

```powershell
# terminal 1 — the API
$env:PYTHONPATH = "packages;."
python -m uvicorn apps.api.main:app --port 8000

# terminal 2 — the console
cd apps/console
npm install
npm run dev          # http://localhost:3000
```

Four screens: a portfolio overview (quality scoreboard, cost meter, interactive risk dial), the
review queue, the per-SKU review workspace with the evidence viewer, and the enrichment
certificate. All of it renders **real pipeline output** — the API serves bundles written by
`run_pipeline.py --save-session`, and decisions made in the workspace post back and persist.

`apps/api/static/index.html` is a second, dependency-free review console served at
`http://127.0.0.1:8000/`. It exists so the review workspace is demonstrable with nothing but
Python installed — no Node, no build step.

**How the data flows, and one distinction that matters:**

```
run_pipeline.py ──> data/console/{sku}.bundle.json   what the machine produced
                └─> data/sessions/{sku}.json         what humans decided
                                  │
              GET /api/console/dataset  ── joins them ──> console
```

A bundle is never rewritten by a review decision. It is the record of what the extractor
actually said under a given schema and model version, and every accuracy number in the project
is computed against it — if accepting a value edited that record in place, "how often was the
model right?" would become unanswerable, because the only surviving copy would already agree
with the reviewer. So `overlay_review_decisions` joins the two at read time instead. The
invariant is asserted directly in `tests/test_console.py`.

The projection itself lives in `packages/axiom/console/`, shared by the API and the offline
fixture exporter so the two cannot serve different shapes for the same data.

**When the API is down**, the console falls back to the checked-in fixture and *says so*: the
provenance footer switches to an "Offline fixture" badge and warns that those model responses
are hand-seeded rather than real. Showing seeded numbers while implying they came from a live
model would be the most dishonest thing this console could do, so `meta.live` is carried in the
data rather than left to a comment.

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
| 1% | — | 0.0% | 2.1% | not achievable |
| 2% | — | 0.0% | 2.1% | not achievable |
| 5% | 0.685 | 100.0% | 2.1% | ok |
| 10% | 0.685 | 100.0% | 2.1% | ok |

The threshold is chosen against a **one-sided Wilson upper bound**, not the observed error
rate. Zero errors in a small sample does not mean zero risk: with 125 samples the true rate
could still be 2.1%, which is why the 2% budget is correctly reported as unreachable even
though the observed error rate is 0%. Thresholding on the point estimate would manufacture a
guarantee out of sample size, which is worse than no guarantee because it invites reliance.

The console renders the whole curve as an interactive dial (`GET /api/policy?epsilon=` serves
31 points). Dragging the budget moves the operating point, and the chart shows something
genuinely counterintuitive: **the bound tightens as coverage rises**, from 16.2% at 11% coverage
to 2.1% at 100%. That is not a bug. With no observed errors, a stricter threshold only shrinks
the accepted sample, and a Wilson bound on a smaller sample is wider. What buys a tighter
guarantee is more reviewed data, not more caution — which is the same reason the review workspace
folds every decision back into the priors.

### Cost to enrich

Token prices are **fetched from the AWS Price List API**, never hand-entered
(`scripts/fetch_bedrock_prices.py --write` writes `packages/axiom/config/prices.yaml`). A model
with no published on-demand price reports no cost at all rather than a guess, because a
plausible-but-wrong cost-per-SKU figure ends a procurement conversation on false information.

| Measured on BA-100-075 | |
|---|---|
| cost per SKU | **$0.000963** |
| cost per attribute value | $0.000064 |
| calls / tokens | 2 calls, 3,779 in / 1,745 out |
| projected at 500,000 SKUs | **~$481** |

Tokens are attributed to the tier that burned them, not split by call count. That distinction
matters: escalation re-sends a whole prompt to a dearer model, so apportioning by call share
would credit most of those tokens to the cheap tier and systematically flatter the cascade.

The cascade's premise checks out against the real rates — `glm-4.7-flash` is $0.07/M input
against `glm-5` at $1.00/M, a 14x spread, which is why starting cheap and escalating only on
unusable output is worth the complexity.

Extrapolation assumes these documents are typical. Longer datasheets cost more, and a corpus
that escalates often costs several times this.

### The trust layer, ablated

`scripts/run_ablation.py` runs the golden set twice against the same model, same prompt and same
documents, differing only in whether a value without a locatable quote is discarded or kept. The
second arm is what a generic enrichment pipeline publishes.

**The result was null.** Both arms scored 125 correct, 0 wrong, 0 hallucinated, 100% citation
coverage. On clean, text-extractable datasheets this model does not invent quotes, so the
evidence contract never fires.

That is worth reporting rather than burying. It reframes the contract honestly: on this corpus it
is not a filter that catches a misbehaving model, it is a **guarantee that bounds the worst
case**. Its value is in the tail, and this corpus has no tail. A harder one — scanned PDFs,
values stated only in prose — would exercise it properly.

### Variant explosion: one datasheet, every part number

An industrial datasheet almost never describes one product. It describes a series — a shared
specification block plus an ordering table with a row per orderable part number. Running the
pipeline once per SKU re-reads the same document N times and asks a model to pick the right table
row N times.

```powershell
python scripts/explode_variants.py data/samples/ba100.txt --sku BA-100-050 `
  --brand "milwaukee vlv" --include-optional --risk-budget 0.05
```

**Two model calls produce five complete, publishable records.**

| SKU | parent | values | publishable | gaps |
|---|---|---|---|---|
| BA-100-025 | BA-100-050 | 15 | 15 | 8 |
| BA-100-050 | — | 16 | 16 | 7 |
| BA-100-075 | BA-100-050 | 15 | 15 | 8 |
| BA-100-100 | BA-100-050 | 15 | 15 | 8 |
| BA-100-125 | BA-100-050 | 15 | 15 | 8 |

Cost falls from $0.000978 per SKU to **$0.000195** — about $97 per 500,000 SKUs instead of $477.
But the saving is not the main point.

**The row-selection step no longer involves a model.** Per-variant values come from deterministic
cell lookup, so each variant's size, handle and carton quantity are read from *its own row* and
cited to an exact cell (`t1:r5:c1`). Reading a neighbouring row produces a value that is
genuinely in the document, correctly cited, and wrong for the part — the same failure class as
the wrong-document bug above, and equally invisible to quote verification. Removing the model
from that step makes the error unreachable rather than merely unlikely.

Inheritance is not unconditional. `Operating Torque … 18-22 ft-lb (1/2" size)` is stated for one
size only, so BA-100-050 keeps it and the other four record it as *inapplicable* rather than
missing — which stops a buyer chasing a supplier for a figure that was never meant to exist for
their part.

Column-to-attribute binding is declarative (`table_headers` in `schema/attributes/*.yaml`), and
any column no attribute claims is **reported rather than dropped**. That paid for itself
immediately: the gate valve datasheet's `Master Carton` column was unmapped, which was a one-line
schema fix, and `Handwheel Dia` is still surfaced as a genuine schema gap.

Two bugs in this feature were caught by its own tests, both worth recording because they are the
same species as the ones the system exists to prevent:

- Size-qualifier matching used `startswith`. Folding `1/2" size` gives `12size`, which starts
  with the `1` of a 1" variant, so the 1" valve was silently treated as the size the note applied
  to. Now matched as whole extracted tokens.
- The variant a size-scoped spec *does* apply to was never given the value — the code skipped the
  gap but forgot to add the value.

### An actual defect, found by adversarial testing

The null ablation prompted a harder question, and this one found a real bug.

`scripts/run_adversarial.py` supplies the **wrong document**: it asks for a ball valve part
number while handing over the gate valve datasheet. Nothing about that SKU is in the source, so
the only correct behaviour is to abstain on everything.

The system failed badly. **39 of 64 requested attributes were fabricated — and every single one
carried a quote that verified**, because the words genuinely were in the document. They described
a different product.

This bounds the headline claim precisely. "Evidence or null" guarantees a published value can be
traced to text in its source. It does **not** guarantee that text is about the requested product,
and stricter quote checking could never catch the difference, because the citation is real.
Wrong-product attribution is a targeting failure, not a citation failure.

The fix is a deterministic pre-flight gate in `Extractor.extract`: look for the part number in
the parsed document first, and if it is absent, record every attribute as a gap and make no model
call at all. Separator-insensitive, because catalogues and ERPs disagree about hyphens, but no
looser than that.

| | before the gate | after |
|---|---|---|
| fabricated values | 39 / 64 | **0 / 64** |
| of those, with a verifiable quote | 39 | 0 |
| cost of the run | $0.0024 | $0.000000 |

Zero cost because the refusal needs no inference. The backtest was re-run to confirm no
regression: still 125 correct, 100% precision and recall. The gap reason is deliberately
`NO_SOURCE_AVAILABLE` rather than `NOT_PRESENT_IN_ANY_SOURCE` — "this datasheet is about a
different product" needs a different remedy from "this datasheet does not state that value", and
collapsing them would send a buyer chasing a supplier for data that was never missing.

The gate can be disabled for genuine series-level documents where orderable part numbers are
never printed. It is opt-in, because that is also the excuse a wrong-document bug would hide
behind.

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
│   ├── console/               # projection of pipeline output for the UI (presentation only)
│   ├── syndicate/             # channel pre-flight, exporters, publication gate
│   ├── evaluation/            # backtest harness, five-outcome scoring
│   └── config/                # models.yaml (generated by the preflight script)
├── apps/
│   ├── api/                   # FastAPI: review + console dataset, plus a no-build console
│   └── console/               # Next.js dashboards and review workspace
├── scripts/
│   ├── preflight_bedrock.py   # prove every cascade tier is invocable; pin model IDs
│   ├── fetch_bedrock_prices.py# pin real token prices from the AWS Price List API
│   ├── smoke_extraction.py    # prove the evidence contract holds against live models
│   ├── run_pipeline.py        # one SKU, end to end, fully reported
│   ├── run_backtest.py        # the numbers above; writes calibration artifacts
│   ├── explode_variants.py    # one datasheet -> a record per orderable part number
│   ├── run_ablation.py        # same model, trust layer off — what does the gate buy?
│   ├── run_adversarial.py     # wrong-document negative control; exits non-zero on fabrication
│   ├── bootstrap_lite.ps1     # CDK staging bucket without the bootstrap IAM roles
│   └── export_console_fixture.py
├── data/
│   ├── golden/                # ground truth — the highest-value directory here
│   ├── calibration/           # calibration set + learned priors (governs auto-accept)
│   ├── samples/               # demo datasheets
│   ├── sessions/              # review state: what humans decided
│   ├── console/               # pipeline output: what the machine produced
│   └── cache/                 # content-addressed artifact store
└── tests/                     # 753 tests
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
      exporters, signed certificates, backtest harness, review workspace, and a console served
      from live pipeline output.
- [ ] **Tier 2 — differentiators.** In progress:
      - [x] Cost-per-SKU meter, priced from the AWS Price List API, surfaced in the console
      - [x] Ablation harness and adversarial negative control (found and fixed the targeting bug)
      - [ ] Automated Reasoning (L6) via Bedrock Guardrails
      - [x] Risk–coverage curve as an interactive dial in the console (hand-drawn SVG, no
            charting dependency)
      - [x] Variant table explosion with parent-child linkage
      - [ ] Constrained copy generation with a claim-check pass
      - [ ] Quality Index dashboard with a before/after cohort
- [ ] **Tier 3 — scale and polish.** Batch orchestration, multi-source cross-validation (L4),
      before/after cohort study.

Storage infrastructure is deployed (see above). Compute is not — the pipeline runs locally.

### Known gaps

- The API has no authentication or tenant scoping. `_session_path` guards against path
  traversal but nothing stops one tenant reading another's sessions.
- `apps/console/src/lib/types.ts` is a hand-maintained mirror of the Python models. The API and
  the fixture exporter share one projection so they cannot drift from each other, but the
  TypeScript can still drift from both. Generating it from the OpenAPI schema is the fix.
- The golden set is 9 SKUs against a blueprint target of 100–300 (see the caveats above).
- L4 (cross-source agreement) and L6 (formal verification) exist only as members of the
  `ValidationLayer` enum. No validator implements them, so they produce no results at all —
  the layer list in the blueprint is ahead of the code here, and certificates reflect L0–L3.
- HTS classification is deliberately never auto-published: published benchmarks put accuracy
  near 40% at the 10-digit level, which is not publishable at any confidence this system can
  honestly assign.
