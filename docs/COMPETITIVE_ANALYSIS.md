# VAYU — Competitive Analysis (P4 Renewable Energy Orchestrator)

> **What this is.** A deep read of **11 public GitHub repos** built for ET AI Hackathon P4,
> fetched and read README-by-README (not from summaries). Every claim attributed to a rival
> is **their own README's claim**, read on the dates below — none were executed, so treat all
> rival numbers as self-reported. GitHub search only surfaces indexed repos; more likely exist.
> Use this to position VAYU's deck/video and to pre-empt judge questions.

**Read pass:** all 11 READMEs fetched in full on this session. Content paraphrased for
compliance; rival numbers quoted only as their own claims.

---

## 1. The one-paragraph verdict

VAYU is **mid-to-upper pack on raw feature breadth** and **top of the pack on scientific
honesty and verifiable evidence**. Three rivals (Fazle-hasan, Akshadman, GridMind, Sutradhar)
match or exceed VAYU on *simulation breadth* — specifically **multi-day Monte Carlo against
baselines**, which VAYU currently shows only as a single-path recorded comparison. Two rivals
(Fazle-hasan, GridOS) add **multimodal document/radar ingestion (D3)** that VAYU does not do.
**No rival** does what VAYU uniquely does: **conformal-calibrated forecast bands feeding the
objective choice, signed offline-verifiable certificates, and publishing a loss against its own
baseline.** VAYU's "no live-control path" posture is also *safer* than GridOS's binding-SCADA
claim. The two gaps a sharp judge will probe: **(a) no multi-day Monte Carlo headline**, and
**(b) no maintenance actions** (delay/schedule/dispatch crews) — several rivals have both.

---

## 2. Scorecard — VAYU vs the field

Legend: ✅ present / strong · 🟡 partial / weaker · ❌ absent · "self" = self-reported.

| Capability | VAYU | Fazle-hasan | Akshadman | GridMind | Sutradhar | GridOS | akshay | Sandeep | ElderOsan | Jaya | akash | Yakshitha |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LLM picks criteria, solver picks MW | ✅ Gemini→MILP | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 | ❌ (D1) |
| Optimizer class | **MILP (HiGHS)** | MILP (CBC) | MILP (CBC) | MILP (CBC) | heuristic+twin | **LP** (HiGHS) | **LP** (CBC) | ranker | dispatcher | LP | ? | ❌ |
| Scenario-robust optimize | ✅ 5 paths, BESS shared | ✅ 2-stage stochastic | 🟡 stress-test | ✅ rolling | 🟡 twin | ❌ single | ❌ | 🟡 | ❌ | 🟡 10 futures | ? | ❌ |
| Reflective / re-plan loop | ✅ ≤3 iters | ✅ in-interval recovery | ✅ repair ladder | 🟡 | ✅ verify loop | ✅ arbitration | ✅ validate+rerun | 🟡 | ✅ raw→applied | 🟡 | ? | ❌ |
| Independent safety validator | ✅ sole authority | ✅ | ✅ | ✅ guardrail | ✅ | 🟡 "solver disposes" | ✅ can't override | ✅ guardrails | ✅ golden rules | ✅ | ? | 🟡 |
| **Conformal-calibrated bands** | ✅ **CQR, cited** | 🟡 quantile bands | 🟡 learned P10/50/90 | 🟡 quantile cones | 🟡 P10/50/90 | ❌ | 🟡 confidence | ❌ | ❌ | 🟡 | ? | 🟡 |
| **Signed offline certificate** | ✅ **Ed25519** | ❌ | ❌ | ❌ | ❌ | 🟡 SQLite audit | ❌ | 🟡 hash audit | 🟡 evidence/ | ❌ | ? | ❌ |
| **Publishes a loss vs baseline** | ✅ **(loses to qr)** | 🟡 CIs | 🟡 A/B honest | ❌ wins-only | 🟡 per-scenario | ❌ self A+ | ❌ | ❌ | ✅ placeholders/honest | ✅ honest | ? | ❌ |
| Multi-day Monte Carlo vs baselines | 🟡 **single path** | ✅ 30-day, 3 baselines | ✅ 100-day, 2 baselines | ✅ 30-day, 3-way | ✅ 120-day, 6 scen | 🟡 7 shocks | 🟡 24h vs dumb | ❌ | 🟡 72 scen | ✅ 15-day | ? | ❌ |
| Multimodal input (D3) | ❌ | ✅ email/PDF/radar | ❌ | ❌ | 🟡 text notes | ✅ radar vision | 🟡 text alerts | 🟡 RAG upload | 🟡 CSV/xlsx | 🟡 text alerts | ? | ❌ |
| Maintenance actions | ❌ | ✅ windows | ✅ crews+move | ❌ | ✅ delay/sched/crew | 🟡 | ❌ | ❌ | ❌ | ❌ | ? | 🟡 planned |
| Load shifting | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ shed | ✅ DR | ✅ | ✅ | ✅ | ? | ❌ |
| Human-in-the-loop approval | ✅ separate event | ✅ gated | ❌ no-HITL | ✅ critical plans | ✅ | 🟡 | ✅ pause | ✅ Clerk RBAC | ❌ | ❌ | ? | ❌ |
| 3D / rich UI | ✅ Three.js twin | 🟡 React dash | 🟡 Streamlit | 🟡 8-pg Streamlit | 🟡 React | 🟡 Streamlit 7-tab | 🟡 React | ✅ Next.js RBAC | 🟡 React | 🟡 Streamlit | ? | 🟡 Streamlit |
| Live deployed URL | ✅ Cloud Run + Gemini live | ❌ local | ❌ local | ❌ local | ✅ Render | ❌ files | ❌ local | 🟡 Render/CF | ❌ local | ❌ | ? | ❌ |
| Tests (self-reported) | **364** | 38 | 70 | 57 | 67 | 26 | 78 e2e | — | — | ~8 scripts | ? | WIP |
| Stated 9-blocker / D×F position | 🟡 implicit | ✅ F3×D3 | ✅ 9-blocker table | ✅ D2/F3 | ✅ D×F | ✅ F3×D3 | ✅ D2/F3 | 🟡 | 🟡 tiers | ✅ D2/F3 | 🟡 | 🟡 |

> Where a rival cell says a number, it is **their README's self-report**, not a measured result.

---

## 3. Rival-by-rival (deepest first)

### Tier A — serious rivals

**1. Fazle-hasan / Renewable-Energy-Orchestrator — the strongest overall.**
- Claude/Gemini/Grok switchable; **two-stage stochastic MILP (CBC)** with non-anticipativity,
  reserve slack, value-of-lost-load, terminal SoC value.
- **Multimodal D3:** perception agent reads emails, PDFs, CSV rosters, radar images → signals
  with confidence; six sample documents with hidden ground truth so only an agent that *reads*
  the doc anticipates the event. This is a genuine D3 story VAYU lacks.
- **In-interval recovery:** if an asset refuses a command, it re-plans within the same interval.
- **Monte Carlo evaluation** vs 3 baselines (rule/greedy/no-battery), paired 95% CIs,
  reliability-adjusted cost (net cost + unserved × VOLL). **38 tests.** HITL gates. Docker.
- **Weaknesses vs VAYU:** no signed certificate; no conformal calibration (plain quantile
  bands); no live deployed URL; doesn't publish a loss. Honesty is good but not foregrounded.
- **Threat level: HIGH.** The one repo that beats VAYU on breadth *and* has a credible agentic
  story. Our edges: calibration rigor, signed evidence, live Gemini URL, 364 tests, published loss.

**2. Akshadman / renewable-energy-orchestrator — the most rigorous "science" rival.**
- Rolling-horizon **MILP (PuLP/CBC)**; **100-future Monte Carlo stress test** per decision;
  physics validator; **online forecast-bias + band-width learning** persisted day to day.
- **Maintenance autonomy** (move flexible jobs, dispatch crews), industry-agnostic YAML config.
- **9-blocker→evidence table** mapping each failure mode to a named test — exactly the kind of
  rigor framing judges reward. **70 tests.** Honest "limitations": admits it is **not a proven
  optimum** (33-combination band search, not full stochastic optimization).
- **Weaknesses vs VAYU:** no signed certificate; no conformal *calibration* (learned bands, not
  CQR coverage); Streamlit UI (no 3D); no live URL; no multimodal.
- **Threat level: HIGH on rigor narrative.** Our edges: conformal calibration with citations,
  signed certificates, 3D twin, live Gemini, a *provably optimal* lexicographic MILP per its order.

**3. GridMind (varunbhaskar31) — the most polished benchmark story.**
- Gemini + **PuLP MILP**, 8-hour rolling horizon, Monte Carlo tail-risk plan comparison.
- Big, specific headline table: **−44.5% net cost (₹18.2M/day), −88.7% contract shortfall,
  100% critical-load protection, 0 violations over a 30-day / 2,880-interval stress test.**
- Fictional Indian client (Deccan Renewables), 8-page Streamlit control room, YAML configs.
- **Explicitly self-declares D2/F3 and disclaims D3** (no vision).
- **Weaknesses vs VAYU:** wins-only table (no published loss); no signed evidence; no conformal
  calibration; no live URL; no HITL depth. **57 tests.**
- **Threat level: MEDIUM-HIGH on presentation.** The number "−44.5%" will land with judges.
  Counter with: VAYU's honesty (we show the loss), signed verifiable evidence, and live Gemini.

**4. Sutradhar (Harshgupta88156) — the most complete P4 action menu.**
- LLM risk judge + **digital twin that tests 9+ plans** in expected and stress worlds; guardrails
  (risk can only go up; plan must match twin within 0.2%); rule-based fallback.
- **Fullest action coverage:** charge/discharge/reserve, buy/sell/delay, curtail, DR,
  **reduce non-critical, shift load, AND maintenance (delay/schedule/dispatch crew)** — maps
  every single problem-statement action and event (12 event types) in a table.
- **120 test-days across 6 scenarios**, beats rule-based on all 6; **67 tests**; **live Render
  demo**; runs on free LLM tiers or fully offline.
- **Weaknesses vs VAYU:** heuristic plan-search, **not a MILP** (no optimality claim); no
  conformal calibration; no signed certificate; aggregate not per-asset in places.
- **Threat level: HIGH on scope/completeness.** This is the repo that makes VAYU's **missing
  maintenance actions** visible. Our edges: true MILP optimality, calibration, signed evidence,
  3D twin, Cloud Run + live Gemini.

### Tier B — credible but with a clear gap

**5. akshay21131 / energy-orchestrator — D2/F3, LP not MILP.**
- Clean "what makes this agentic" framing; LLM picks **6 objective weights + reserve**, LP
  (~20 vars, <50 ms) is a called tool; closed-loop reflection; NL operator overrides.
- Headline **~71% cost saved, 0 unmet critical load across 4 scenarios** (24h vs "dumb grid").
  **78 end-to-end checks**; **11-slide deck in the repo**; a concrete 0–30/30–90/6–12mo roadmap.
- **Gaps vs VAYU:** LP not MILP; single-day not Monte Carlo; no calibration; no signed evidence;
  "dumb-grid" baseline is weak (inflates the 71%).
- **Threat: MEDIUM.** The 71% headline is eye-catching but the baseline is soft; VAYU's honest
  single-digit forecast margins + published loss read as *more* credible to a technical judge.

**6. Sandeep1296 — product/RBAC heavy, optimization light.**
- 5 sub-agents + LangChain supervisor, **non-bypassable guardrails + prompt-guard**, HITL on
  critical plans, **Clerk email/org RBAC**, hash-stamped audit, Next.js UI, 60-second demo script,
  Cloudflare-tunnel deploy. **7-objective weighted rank** (not a MILP), 5-scenario physics sim.
- **Gaps vs VAYU:** ranking not optimization; no Monte Carlo; no calibration; no signed cert;
  no test count given.
- **Threat: MEDIUM-LOW on rigor, MEDIUM on polish/enterprise feel** (RBAC + roles look "productized").

### Tier C — honest-but-modest or incomplete

**7. ElderOsan / ET_hackathon — dual-agent + golden-rules evaluator.** Honest to a fault: its
**measured-results section is still literally `<PLACEHOLDER>`**, and it admits the live model
"frequently charges first regardless of the declared objective" and is "often close to the
deterministic baseline, not clearly ahead." Safe/Replay/Live modes. **Threat: LOW** — the
honesty is good but there's no headline result to beat.

**8. Jaya-hash — well-scoped, honest, but stand-in-LLM numbers.** Declares **D2/F3, explicitly
not D3**; LP with 8h look-ahead; everything-as-dollar-cost objective; clear "what's covered /
not covered" table that **openly lists missing maintenance + non-critical-load reduction**.
Headline agent numbers use a **stand-in LLM**; real-LLM numbers are "TODO". **Threat: LOW-MEDIUM.**

**9. akash-8991 — pointer README only.** Root is a signpost to `platform/` (Docker Compose,
Postgres, Redis, OpenRouter, 8-doc client spec, Terraform mentioned elsewhere). Can't assess
rigor/metrics/tests from the indexed root; has a Drive demo video. **Threat: UNKNOWN** (not
assessable from the public root).

**10. Yakshitha123-wq — Day-1 only.** Simulator + dashboard + baseline controllers; **optimizer
and agent explicitly not built yet.** Nice ISA-101 HMI design note. **Threat: NONE** currently.

**11. GridOS (ManojKumar7676) — biggest claims, thinnest proof.** Claims **15 binding SCADA
actuators** (incl. breaker trips, black-start, islanding), **"Grade A+ 98.4/100" self-scored**
scorecard, NOAA radar vision, 4+1 agents, F3×D3. But only **26 tests**, the scorecard is
**self-graded**, and the **binding-physical-control claim is a liability** (safety/overclaim).
**Threat: MEDIUM on surface dazzle, LOW on substance** — and VAYU's "no live control" posture is
the direct, defensible contrast. *Do not* imitate its overclaiming.

---

## 4. VAYU's defensible moats (say these out loud)

1. **Conformal-calibrated forecast bands that drive the objective choice.** No rival calibrates
   with a cited distribution-free method (CQR / EnbPI / ACI) *and* wires the band width into the
   agent's `uncertainty_ratio`. Rivals have "quantile bands"; VAYU has calibration with a
   leakage-admissibility invariant. **Unique.**
2. **Signed, offline-verifiable Ed25519 certificate per decision**, content-addressed store. The
   closest rivals have a SQLite/hash audit log; none sign evidence you can verify offline.
   **Unique.**
3. **We publish a loss against our own baseline** (`qr_uncalibrated` beats our forecaster,
   DM p=3.6e-4) and label the run Tier-C synthetic. Only ElderOsan and Jaya-hash are comparably
   candid, and neither has a headline result. **Rare and high-trust.**
4. **A true lexicographic MILP** (provably optimal for the agent-chosen order), not a weighted
   sum, ranker, or heuristic plan-search. Fazle/Akshadman/GridMind have MILP too; Sutradhar/
   Sandeep/akshay/Jaya do not.
5. **"No live-control path" as an explicit safety stance**, labelled on every surface — the clean
   contrast to GridOS's binding-SCADA claim.
6. **Live on Cloud Run with Gemini live via Vertex AI** + the richest UI (Three.js 3D twin).
   Only Sutradhar and (partly) Sandeep have a live URL; none pair it with a 3D twin + live Gemini.
7. **364 tests** — the highest self-reported count in the field.

---

## 5. Where rivals lead VAYU (fix or frame)

| Gap | Who has it | Recommended response |
|---|---|---|
| **Multi-day Monte Carlo vs baselines** | Fazle, Akshadman (100d), GridMind (30d), Sutradhar (120d), Jaya (15d) | **Frame now, build later.** Say on the scorecard: *"evaluated on the same realized path against a recorded baseline; multi-day Monte Carlo is on the roadmap."* If time permits before judging, run the existing engine over N seeded days and report paired deltas — the pieces exist. |
| **Maintenance actions** (delay/schedule/dispatch crew) | Fazle, Akshadman, Sutradhar, (Yakshitha planned) | **Name it on the roadmap slide** as a one-liner: *"maintenance scheduling + crew dispatch — modelled, not yet in the action set."* Pre-empts the judge question. |
| **Multimodal D3** (email/PDF/radar) | Fazle, GridOS | **Don't fake it.** State scope honestly as D2 and point to VAYU's depth elsewhere (calibration, signed evidence). D3 is optional for P4; overclaiming is riskier than a clean D2. |
| **Big headline % savings** | GridMind (−44.5%), akshay (−71%) | **Counter with credibility, not a bigger number.** Those use soft baselines; VAYU reports honest margins + a published loss. A technical judge trusts the honest deck more. |
| **Non-critical-load reduction as a distinct action** | Sutradhar, Fazle | Minor; fold into the DR/load-shift story or name on roadmap. |

---

## 6. Positioning one-liners for the deck / video / Q&A

- **Tagline that no rival can copy:** *"The agent chooses what matters; a verified optimizer
  chooses the actions; and every decision is signed so you can check it offline."*
- **Against GridMind/akshay's big percentages:** *"We could quote a 70% number against a naive
  grid too — we don't, because our baseline is honest and we even publish the one baseline that
  beats us. Trust is the product."*
- **Against GridOS's SCADA/A+ claims:** *"We deliberately have no live-control path and we don't
  grade our own homework — an independent validator and a signed certificate do."*
- **Against Fazle/Akshadman's Monte Carlo:** *"They run multi-day Monte Carlo; so can our engine,
  and it's on the roadmap. What they don't have is conformal-calibrated uncertainty driving the
  objective, or signed offline-verifiable evidence."*
- **On the maintenance gap (pre-empt):** *"Maintenance scheduling and crew dispatch are the next
  actions on the roadmap; today the agent owns dispatch, storage, market, curtailment and demand
  response, each with a safety-checked, signed decision."*

---

## 7. Two concrete, no-new-slide edits recommended

1. **Slide 12 (roadmap) — add one scope line:** *"Action set today: battery, market, curtailment,
   demand response, load shift. Next: maintenance scheduling + crew dispatch, and multi-day Monte
   Carlo evaluation."* (Pre-empts the two gaps judges will probe.)
2. **Slide 10 (validation scorecard) — add one line:** *"Evaluated on the same realized path
   against a recorded baseline (net cost −434.91 USD); multi-day Monte Carlo on the roadmap."*
   (Turns the single-path comparison from a weakness into a stated, honest method.)

---

## 8. Source list (all fetched this session; claims are each repo's own)

| Repo | Branch read | Headline self-claim |
|---|---|---|
| Fazle-hasan/Renewable-Energy-Orchestrator | main | Claude/Gemini/Grok, stochastic MILP, D3 multimodal, 38 tests, MC vs 3 baselines |
| Akshadman/renewable-energy-orchestrator | main | MILP + 100-future MC, learning, 9-blocker table, 70 tests, −23% cost (100 days) |
| varunbhaskar31/gridmind | main | Gemini+PuLP MILP, −44.5% cost, 30-day/2880-interval, D2/F3, 57 tests |
| Harshgupta88156/renewable-energy-orchestrator (Sutradhar) | main | twin plan-test, full action menu + maintenance, 120 days, 67 tests, live Render |
| ManojKumar7676/GridOs | main | 15 SCADA actuators, radar vision, self-scored A+ 98.4, 26 tests, F3×D3 |
| akshay21131/energy-orchestrator | main | LLM weights + LP, reflection, ~71% saved, 78 e2e, 11-slide deck, D2/F3 |
| Sandeep1296/renewable-energy-orchestrator | main | 5 sub-agents + LangChain, Clerk RBAC, hash audit, Next.js, 60-sec demo |
| ElderOsan/ET_hackathon | main | Gemini + golden-rules evaluator; results still placeholder; honest limits |
| Jaya-hash/renewable-energy-orchestrator | main | LP, D2/F3 (not D3), stand-in-LLM numbers, 15-day, honest coverage table |
| akash-8991/renewable_energy_orchestrator | master (root) | pointer to platform/; Docker Compose, Postgres/Redis, 8-doc spec, Drive video |
| Yakshitha123-wq/renewable-energy-orchestrator1 | main | Day-1 only: simulator + dashboard + baselines; optimizer/agent WIP |

*Content was rephrased for compliance with licensing restrictions; rival figures are
self-reported README claims, not independently verified.*
