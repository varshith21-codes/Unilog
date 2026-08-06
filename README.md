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

pytest -q                      # 863 tests, no AWS credentials needed
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

**15 products, 312 comparisons (85 of them known-absent), 93s, 15 model calls.**

| Outcome | Count |
|---|---|
| correct | 222 |
| **wrong value** (the dangerous failure) | **2** |
| missed | 3 |
| correctly abstained | 85 |
| **hallucinated** | **0** |

| Metric | Value |
|---|---|
| precision | 99.1% |
| recall | 97.8% |
| F1 | 98.4% |
| abstention correctness | 100.0% |
| citation coverage | 100.0% |

Scoring uses **five outcomes, not two**. Binary right/wrong lets a system that fabricates
freely benchmark like an honest one, because a confident wrong answer and a correct abstention
both collapse to "not correct". Separating *missed* from *wrong value* from *hallucinated* is
what makes the numbers mean anything.

The two remaining wrong values and all three misses are the same attribute, `handle_type`, on
the datasheet where a footnote *overrides* the prose: the description promises a lever on every
valve, then a note replaces it with a tee handle for DN25 and up. Applying that requires
reasoning about a size threshold, and the model gets it right about half the time. It is
reported here rather than tuned away because `handle_type` at 61.5% is the honest answer to
"what should be worked on next", and the hardest-attributes table exists to say so.

### Risk-controlled auto-accept

| Error budget | Threshold | Coverage | Upper bound | Status |
|---|---|---|---|---|
| 2% | 0.719 | 74.1% | 1.6% | ok |
| 5% | 0.685 | 100.0% | 2.7% | ok |
| 10% | 0.685 | 100.0% | 2.7% | ok |

The threshold is chosen against a **one-sided Wilson upper bound**, not the observed error
rate. Zero errors in a small sample does not mean zero risk, which is why a budget can be
reported as unreachable even when the observed error rate is 0% — on the earlier 9-product
corpus the 2% budget was correctly refused, because with 125 samples the true rate could still
have been 2.1%. Thresholding on the point estimate would manufacture a guarantee out of sample
size, which is worse than no guarantee because it invites reliance.

That budget became reachable by *earning* it rather than by relaxing the bound: 312 comparisons
instead of 180 tightened the interval, and the entailment gate below removed the errors that
were widening it.

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

| Outcome | AXIOM | control | delta |
|---|---|---|---|
| correct | 220 | 216 | +4 |
| wrong value | 2 | 10 | −8 |
| **hallucinated** | **0** | **8** | **−8** |
| precision | 99.1% | 92.3% | +6.8 pts |
| recall | 96.9% | 95.2% | +1.8 pts |

**The trade, stated plainly:** the contract withheld 12 values. 3 of them were correct and 9 were
wrong or invented. Enforcing evidence cost 3 good values to prevent 9 bad ones.

**This ablation used to come back null**, and that is the more interesting half of the story. On
the original corpus — two clean, text-extractable datasheets — both arms scored identically,
because the model never invented a *quote*. The contract never fired, so the honest report was
that it bounded a worst case this corpus did not contain.

The corpus now has a tail: a third datasheet as a PDF, with DN sizing, a footnote that overrides
the prose, reduced-port rows sharing a size with their full-port twins, and a part number that
exists only to be discontinued. Adding it turned the null result into the table above — and the
first run against it exposed a real hole in the contract, described next.

### A verified quote is not a supporting quote

The first backtest against the harder corpus returned **10 hallucinations with citation coverage
still reporting 100%**. Both numbers were correct, which is what made it interesting.

The clearest instance: the extractor returned `selling_uom = "Each"` citing the quote `"Ctn Qty"`.
That quote verified at `match_score 1.0` — those words genuinely are on the page, as the header of
the carton-quantity column. The citation resolved to a real highlight on a real page. And the
value was invented. A reviewer clicking through to the evidence would have found a column header
and no unit of measure anywhere near it.

Quote verification and entailment are different questions, and the contract was only asking the
first one:

- **verification** — is this text really in the source document?
- **entailment** — does that text actually state this value?

A fabrication that passes verification is worse than an obvious one, because it arrives wearing
the uniform of a checked fact. Evidence-or-null only means anything if "evidence" means evidence
*for this value*.

`packages/axiom/extract/entailment.py` closes it with three mechanical checks and no second model
call:

**Header cells are not values.** A value citing row 0 of a table is rejected outright — a header
names what a column *means*, so it can never state a value for a part. This kills the `Ctn Qty`
case structurally rather than by guessing at wording.

**Enum values must be spoken by the quote.** The quote is scanned for every allowed value and
alias. This rejects invented values, and it also *corrects under-read ones*: a quote reading
"Seats are reinforced PTFE" contains `ptfe` (→ PTFE) and `reinforced ptfe` (→ RPTFE). Where two
matches cover **the same text**, the longer one is a strictly better reading of it, and the
evidence outranks the model's paraphrase of the evidence.

Where two matches cover **different** text, it deliberately does nothing. `FNPT x FNPT solder
ends` names two incompatible end connections; that is a contradiction in the document, and
resolving a real engineering conflict by string length would be worse than leaving it to the
rules layer.

**Numbers must appear in their own citation.** Extraction is contractually forbidden from
normalising — it returns `value_raw` verbatim — so a magnitude absent from its own quote was not
read from the document. This is the only thing that catches reading the *wrong row* of an
ordering table, where both rows are real text and the citation is genuine either way.

Booleans are exempt on purpose. `lead_free_compliant: true` is a conclusion drawn from "lead-free
bronze alloy C89833"; the token "true" will never appear in the quote, and demanding it would
reject every correct answer.

Result: **hallucinations 10 → 0, precision 92.4% → 99.1%, abstention correctness 88.2% → 100%**,
and the 2% error budget became reachable for the first time.

The first version of the gate was **too strict, and the backtest caught that too.** It resolved
the whole `value_raw` as an exact alias, so `"NPT threaded, female both ends"` — the datasheet's
own phrasing — matched nothing and was discarded. That silently traded 10 hallucinations for 13
false abstentions, which barely moves a hallucination count while making the system less useful,
and the resulting gaps are indistinguishable from genuine ones. The fix was to scan the value the
same way the quote is scanned. Recall went from 91.2% back to 97.8% with every fabrication still
caught.

### The hardest targeting case: a part the document mentions in order to retire it

Adding the PDF to the adversarial harness produced the most convincing wrong answer this system
has generated: **12 values for `77C-102`, all 12 carrying verifiable quotes**, for a part that
cannot be bought.

The existing targeting gate asks "is this part number in the document?" — and `77C-102` is. It
appears exactly once, in `NOTE 2: Catalog No 77C-102 (DN10) is discontinued and superseded by
77C-103. Do not order.` The gate opens, a model call is made, and every shared specification in
the surrounding prose — alloy, pressure rating, seats, temperature, approvals — is sitting right
there reading as though it applies.

So the presence check was answering the wrong question. "Is this part number here" and "does this
document *offer* this part" are different, and only the second one is a reason to extract.

Two signals, both deterministic:

- **An ordering-table row wins outright.** A part with a row is being sold, whatever the notes
  elsewhere say. This is what keeps the gate from retiring `77C-103` — which is named *inside* the
  withdrawal note, as the replacement.
- **Otherwise, if every mention withdraws it, refuse.** Phrasings are declared in
  `schema/constants.yaml` under `WITHDRAWAL_MARKERS`, because withdrawal is a wording question,
  not a logic question, and a merchandiser who meets a new phrasing should be able to add it
  without touching Python.

The remedy is a new one: `DELIST_PRODUCT`, not `RETRY_WITH_BETTER_SOURCE`. Absent and withdrawn
are not the same state and their fixes are not interchangeable — an absent part number means
somebody attached the wrong file, while a withdrawn one means the file is right and the part is
dead. Telling someone to go and find a better datasheet would waste the single most useful thing
the document said.

Adversarial harness: **87 attributes requested across 4 cases, 0 fabricated, $0.00** — no model
call is made in any of them.

### L6: formal verification of claims, by an SMT solver

```powershell
python scripts/deploy_reasoning_policy.py --write
```

Compiles `schema/reasoning_policy.yaml` into a live Bedrock Automated Reasoning policy, attaches
it to a guardrail, and pins the ARNs to a generated config. **Deployed and working** in us-east-2.

This answers a question no other layer can. L2 evaluates cross-field rules against already-
structured values. The claim checker proves a sentence *came from* a verified attribute. L6 proves
a sentence is not *self-contradictory* — and returns the identifier of the rule it violated, which
is a proof rather than a score.

Against a real record (Bronze C84400 body, NSF-61 held, NSF-372 not held, NPT threaded):

| Claim | Verdict | Rule |
|---|---|---|
| "This valve is lead-free." | **invalid** | `RLEADEDALLOY` |
| "This valve has solder end connections." | **invalid** | `RSOLDERMATCH` |
| "This valve has NPT threaded end connections." | satisfiable | — |
| "This valve is approved for potable drinking water." | **invalid** | `RPOTABLELEAD` |

That last one is a compound inference across three premises — leaded alloy, potable claim, no
NSF-372 — and it is worth being precise about what it means, because the obvious reading is wrong.

The extractor is **correct**: the datasheet states NSF/ANSI 61, NSF-61 *is* the drinking-water
components standard, so `potable_water_approved: true` faithfully records what the source asserts.
The golden set is also correct to list `lead_free_compliant` as absent, since NSF-61 says nothing
about lead content.

What L6 flags is that **the source's own combination is regulatorily questionable**. A C84400 body
is roughly 6% lead, and US potable-water law has required ≤0.25% since 2014, so a leaded bronze
valve marketed for potable service without NSF-372 is a real problem — with the manufacturer's
data, not with the extraction of it.

That is the layer earning its place. L0–L3 check internal consistency; L6 checks the claim against
the outside world and disagrees with the datasheet. A pipeline that only ever agreed with its
sources could never surface this.

Premises are rendered **deterministically** from publishable values only. A queued value is stated
as unestablished rather than assumed, because a formal proof resting on an unverified premise is
worse than no proof: it looks exactly as sound as a real one.

**Honest limitation.** Automated Reasoning policies are **enum-only** — there are no `Int`, `Real`
or `Bool` sorts (verified against the live API; declaring one is rejected, and `Bool` is reserved).
So numeric rules cannot be expressed here at all. "Steam rating must not exceed the WOG rating"
stays in L2, which has real arithmetic. Encoding it as enum bands would lose precision, and a
sound proof over the wrong model is worse than no proof.

None of the API surface is guessable, so it is recorded in the policy YAML's own header. Learned
by probing:

- Rule expressions are **SMT-LIB S-expressions**: `(= x y)`, `(not p)`, `(and ..)`, `(or ..)`,
  `(=> p q)`, `(ite c a b)`. Infix `a == b` and `distinct` are rejected.
- Rule ids must match `[A-Z][0-9A-Z]{11}` — exactly twelve characters, no underscores.
- Premises and claims must **both** be in *guarded* content. Facts sent with a
  `grounding_source` qualifier are silently ignored by this policy type; coverage metrics show
  them excluded and every claim then returns trivially `satisfiable`. This cost the longest
  detour of the session.
- The guardrail requires a cross-Region profile (`us.guardrail.v1:0`) or creation is refused,
  and guardrail profiles are not discoverable through `list_inference_profiles`.
- Every content error is reported as the same opaque `Policy is not valid.`, so the deploy script
  validates locally first and names the offending rule. That immediately caught one of my own
  rule ids being eleven characters.

A verdict of `translationAmbiguous`, `tooComplex` or `noTranslations` is recorded as **skipped,
never passed**. The solver failing to form an opinion is not the claim being verified, and the
confidence features count a skipped check differently from a passing one.

### Generated copy, and proving it stayed inside the facts

Extraction and generation are separate subsystems. Specs are extracted and proven; prose is
generated and then **checked**.

```powershell
python scripts/generate_copy.py --sku BA-100-075 --audit
```

The generator never sees a product record. It sees a fact sheet built only from **publishable**
values — anything queued for review, inferred, or absent is withheld, including the *list* of
what could not be established, since showing a model its own gaps invites it to fill them in.

Then every checkable assertion in the output is verified:

| Claim type | How it is checked |
|---|---|
| Quantities | Arithmetic, after unit conversion, 1% relative tolerance |
| Standards references | `UL`, `NSF/ANSI 61`, `MSS SP-110` must appear verbatim in a verified value |
| Material designations | `C84400`, `RPTFE`, `316` must match a verified value |
| Regulated claims | "lead-free" requires the attribute present, verified **and true** |
| Comparatives and guarantees | Rejected on sight — no fact sheet can substantiate them |

**Not an LLM judge.** A second model shares the writer's failure mode: both fluent, neither
checkable, and a disagreement between them is unresolvable. Editorial policy lives in
`schema/copy_policy.yaml`, because "never write 'lifetime guarantee'" is a legal position and
belongs where counsel can read it.

One unsupported claim fails the whole piece. Not a score — one invented pressure rating makes a
description wrong, and averaging it against nine correct sentences hides exactly the thing worth
finding. A failed piece gets one regeneration attempt with the offending claims fed back.

Measured on BA-100-075: copy passed on the **first attempt**, 21 claims all supported, each naming
the attribute that substantiates it, at **$0.000939**. And the audit — ten deliberately fabricated
descriptions — was refused **10/10**, with honest copy passing as the control:

```
caught  inflated pressure rating           '1200 psi'
caught  plausible nearby figure            '650 psi'
caught  invented standard                  'MSS SP-110', 'ASME B16.34'
caught  wrong number on a real standard    'NSF/ANSI 372'
caught  invented alloy                     'C89833'
caught  unsupported compliance claim       'lead-free'
caught  comparative claim                  'best'
caught  promissory claim                   'guaranteed'
caught  extended temperature range         '-40 degF', '500 degF'
caught  invented stem grade                '316'
```

The audit is the point. A checker that never rejects anything is indistinguishable from no
checker, so `--audit` exists to try to get something past it on every run.

Copy is generated as part of the pipeline with `--generate-copy`, persisted into the console
bundle, and shown on the certificate page **always beside its claim check** — with each supported
claim naming the attribute that backs it. Prose without the verdict would be making a claim this
system does not support; prose shown as verified when the check failed would be worse. Failed copy
is rendered rather than hidden, because a merchandiser needs to see which sentence was rejected.

Two bugs its own tests caught, both of which would have been invisible in production:

- **Negative numbers were not captured.** `-20 degF` parsed as `20`, so every sub-zero
  temperature silently became its positive twin and then failed to match the real, negative,
  verified bound. Fixed with a sign that is distinguished from a range dash, so `18-22 ft-lb`
  still reads correctly.
- **Sentence punctuation rejected honest copy.** `ASME B16.34` legitimately contains a dot, so the
  pattern must allow one — which meant a sentence ending in `NSF/ANSI 61.` captured the full stop
  and failed to match the very standard the product holds.

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

The ablation was null at the time, which prompted a harder question — and this one found a real
bug.

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
regression. The gap reason is deliberately `NO_SOURCE_AVAILABLE` rather than
`NOT_PRESENT_IN_ANY_SOURCE` — "this datasheet is about a different product" needs a different
remedy from "this datasheet does not state that value", and collapsing them would send a buyer
chasing a supplier for data that was never missing.

The gate can be disabled for genuine series-level documents where orderable part numbers are
never printed. It is opt-in, because that is also the excuse a wrong-document bug would hide
behind.

### What these numbers do not prove

Stated plainly, because a benchmark oversold is worse than no benchmark:

- **The set is small.** 15 products, 312 comparisons, 3 datasheets. Wide confidence intervals on
  everything. This is why thresholds are chosen against a Wilson upper bound rather than the
  observed rate.
- **Author coupling.** I wrote both the source fixtures and the ground truth. That is the
  weakest part of the evaluation. Real supplier PDFs from a distributor with independently
  maintained ground truth would be a materially harder test. The mitigation is that the fixtures
  were written to be adversarial to the extractor and have repeatedly succeeded — the corpus has
  found real defects rather than confirming what was already believed.
- **Recall varies run to run.** Between two runs of the same code, recall moved 96.5% ↔ 97.8% as
  the model resolved `handle_type` differently. Precision and hallucination rate have been stable
  at ≥99% / 0% — the run-to-run failure mode is abstention, not fabrication, which is the correct
  direction for this design.
- **The corpus is what makes the guarantees measurable, and it was almost too easy.** Every
  headline result in this README that reports the trust layer *doing* something — the ablation
  delta, the entailment gate, the withdrawn-part refusal — became measurable only after the
  third datasheet was added. Before that the ablation was null and the same code looked flawless.
  A clean corpus does not validate a trust layer; it hides whether there is one.
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
│   ├── constants.yaml         # domain facts referenced by rules
│   ├── copy_policy.yaml       # banned phrases, regulated claims — for counsel, not engineers
│   └── reasoning_policy.yaml  # L6 formal policy, compiled to SMT-LIB for Bedrock
├── packages/axiom/
│   ├── core/                  # domain models, evidence, gaps, certificate builder
│   ├── ingest/                # content-addressed artifact store
│   ├── docintel/              # document parsing, quote location, span resolution
│   ├── schema/                # schema loader, integrity checks, prompt generation
│   ├── classify/              # class assignment + derived ETIM / UNSPSC
│   ├── extract/               # model cascade, evidence-bound extraction, entailment gate
│   ├── normalize/             # unit registry, datasheet value parsers, MPN cleaning
│   ├── validate/              # validation layers L0–L3 + AST rule evaluator
│   ├── confidence/            # features, calibration, Wilson risk policy
│   ├── generate/              # constrained copy generation + deterministic claim check
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
│   ├── generate_copy.py       # constrained copy + claim check; --audit tries to break it
│   ├── run_ablation.py        # same model, trust layer off — what does the gate buy?
│   ├── run_adversarial.py     # wrong-document negative control; exits non-zero on fabrication
│   ├── deploy_reasoning_policy.py # compile the L6 policy and deploy it to Bedrock
│   ├── bootstrap_lite.ps1     # CDK staging bucket without the bootstrap IAM roles
│   └── export_console_fixture.py
├── data/
│   ├── golden/                # ground truth — the highest-value directory here
│   │                          #   15 SKUs / 3 datasheets, written to be adversarial
│   ├── calibration/           # calibration set + learned priors (governs auto-accept)
│   ├── samples/               # demo datasheets
│   ├── sessions/              # review state: what humans decided
│   ├── console/               # pipeline output: what the machine produced
│   └── cache/                 # content-addressed artifact store
└── tests/                     # 863 tests
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
      - [x] Hardened golden set: a third datasheet as a PDF with DN sizing, an overriding
            footnote, reduced-port twins and a discontinued part. Turned the null ablation into
            a measurable delta and exposed the two defects below
      - [x] Entailment gate — a verified quote must also *support* its value (10 hallucinations
            → 0, precision 92.4% → 99.1%)
      - [x] Withdrawn-part targeting — refuse a part the source mentions only to discontinue it
            (12 fabrications with 12 verifiable quotes → 0)
      - [x] Automated Reasoning (L6) — formal verification via Bedrock Guardrails, deployed live
      - [x] Risk–coverage curve as an interactive dial in the console (hand-drawn SVG, no
            charting dependency)
      - [x] Variant table explosion with parent-child linkage
      - [x] Constrained copy generation with a deterministic claim-check pass
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
- The golden set is 15 SKUs against a blueprint target of 100–300 (see the caveats above).
- `handle_type` sits at 61.5% because one datasheet has a footnote that overrides the prose for
  sizes DN25 and up. Applying a size-scoped override is reasoning the extractor does not do
  reliably. `variants.py` already has size-scoped note logic for variant explosion; the fix is
  probably to share it with extraction rather than to escalate a tier.
- L4 (cross-source agreement) is still only a member of the `ValidationLayer` enum — no validator
  implements it, because it needs two independent sources per SKU and the corpus has one.
- L6 is implemented and deployed but **not yet wired into the pipeline run**. It is callable via
  `axiom.validate.ReasoningChecker` and verified end to end; hooking it onto generated copy
  automatically is the next step.
- L6 is enum-only by construction, so numeric rules stay in L2 (see above).
- HTS classification is deliberately never auto-published: published benchmarks put accuracy
  near 40% at the 10-digit level, which is not publishable at any confidence this system can
  honestly assign.
