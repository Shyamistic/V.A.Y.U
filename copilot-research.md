# MonsoonGrid: problem-to-product research

Research date: 2026-10-09. Facts, inferences, and proposals are separated below.
This is a source-grounded technical/product investigation, not a market-size
study, customer validation, regulatory opinion, or proof of commercial readiness.

## 1. Why build this?

**Fact [S1]:** P4 asks for renewable portfolio orchestration, not a chatbot or
solar dashboard. Its operating example has five solar farms, three wind farms,
two BESSs, industrial consumers, grid interconnections, and market access.
Conditions change every 15 minutes. The demo must evaluate a changing situation,
select actions, and show the response. Cost, carbon, curtailment, degradation,
reliability, utilization, profit, and stability compete; the criteria must be clear.

**Inference:** The useful unit of product value is a defensible operating decision:
what changed, which feasible action bundle was selected, why other options were
excluded, which commitments are exposed, and what actually happened afterward.
An attractive visualization is valuable only if it makes this decision legible.

**Proposal:** Start with a decision-and-evidence workspace for an Indian industrial
renewable portfolio operator managing solar, wind, storage, grid purchases, and
flexible demand. Solar leads the visual experience, not the scope of optimization.

## 2. Source ledger and limitations

| Source | What was actually inspected | Appropriate conclusion |
| --- | --- | --- |
| S1: supplied `6ab20c4818adf_et_hackathon_detailed_problem_statements_branded.pdf`, pages 10-14 | Original PDF text extracted locally with pypdf 6.1.1, isolated in `runtime/research-site-packages`; 14 pages confirmed | Authoritative hackathon requirements. The feature-count text at the start of page 10 belongs to the preceding problem; P4 F3 is defined on page 14. |
| S2: https://www.iexindia.com/products/real-time-market | Official RTM product page retrieved | Half-hourly auctions, 48 sessions/day, delivery one hour after gate closure. A 15-minute planner does not imply unrestricted market orders each slot. |
| S3: https://cercind.gov.in/regulations.html | Official regulations index retrieved | CERC separates individual, consolidated, and draft regulations. Applicable current rules and amendments still require document-level legal/regulatory review. |
| S4: https://www.greenpowermonitor.com/ | Vendor product positioning retrieved | Existing products already cover multi-technology portfolio monitoring, asset views, and workflows. This is vendor evidence, not independent market-share or performance evidence. |
| S5: https://www.greenpowermonitor.com/articles/solar-and-storage-metrics-and-kpis-for-hybrid-portfolios/ | Vendor article dated 24 September 2026 retrieved | Vendor emphasizes forecast/imbalance costs, energy shifting, curtailment, battery SoC/throughput/efficiency, and prioritization of operational actions. No vendor savings number is reused as our performance claim. |

Unsuccessful fetches: an assumed CERC 2024 URL returned 404; MNRE returned 429;
SECI, NREL VPP, PowerHub, and CERC consolidated pages yielded no meaningful text;
IEX green-market page returned its own error view. They are not fresh supporting
evidence. No source here proves an applicable FDRE tariff, DSM rate, customer
willingness to pay, market size, or access to real plant telemetry.

The existing local P4 reviews supplied useful leads, but were not treated as
substitutes for the original brief or as newly verified regulatory evidence.

## 3. Narrow the niche

**Proposed first buyer:** Head of energy operations at a captive/open-access
industrial renewable portfolio with storage and material reliability obligations.
**Daily user:** Scheduling/energy operator; independent approver; audit reviewer.
**Trigger:** Updated generation forecasts, price spikes, storage outages, tie-line
derates, and load changes make the previous action bundle unreliable or costly.
**Job:** Compare feasible options, preserve critical supply/reserves, explain
shortfall exposure, approve deliberately, and retain replayable evidence.

**Wedge:** Decision accountability across the physical and commercial boundary:
priced DR rather than free load shedding; committed market revenue rather than
valuing every physical export as a sale; measured unserved energy even when an
unsafe action is not applied; and independent approval/validator evidence.

**Not the initial buyer/problem:** Residential smart-home automation, solar panel
design, inverter firmware, a general asset-performance dashboard, a utility-wide
SCADA replacement, or an autonomous electricity trading account.

**Competitive inference:** Generic monitoring alone is not a persuasive wedge
against mature vendors [S4-S5]. A small team should integrate with existing
monitoring and focus on scenario decisions and evidence, not rebuild their stack.
This differentiation remains a hypothesis until prospective operators validate it.

## 4. Requirement-to-evidence matrix

| P4 requirement [S1] | Current evidence / design | Boundary or gap |
| --- | --- | --- |
| Full portfolio coordination | Exact demo roster and typed portfolio; wind, solar, BESS, industrial loads and grid/tie | India-site fixture has different IDs; do not merge it into demo geography. |
| Changes every 15 minutes | 96-slot replay with weather/demand/price scenarios and outage/derate events | Replay is synthetic, not live telemetry. |
| Choose action clusters | Planner proposal, deterministic validator, separate approval, software twin | No live command or market submission. |
| Cost and clean utilization | Metric registry, per-slot accounting, priced DR, market/physical export split | Financial inputs and carbon factor are assumptions. |
| Battery operating limits | Energy/SoC, charge/discharge limits, efficiencies, reserve bounds | Throughput proxy is not chemistry-specific SoH or validated degradation. |
| Uncertainty and options (F2) | Joint scenarios, forecast package, policy objectives, solver/fallback labels | G3 cross-scenario commitment deviation is unresolved. No claim of certified stochastic MPC. |
| Simulation over time (F3) | Executable 96-slot scenario replay, recorded slot states and decisions | A static 3D animation alone does not satisfy F3. |
| Demonstrable reliability (D2) | Current tests, blocking gates, explicit rejection, signed replay certificate | Tests establish modeled behavior, not real-grid reliability. |
| Heterogeneous inputs (D3) | Existing document/nowcast routes are prototypes | Do not claim D3 without representative input evaluation. D2/F3 is the current target. |
| Frequency/grid stability | Explicit grid/tie constraints and safety boundary | 15-minute scheduling is not primary frequency control; no dynamic network validation. |
| Maintenance actions | Maintenance/outage inputs can affect scheduling | Inspection-team dispatch and maintenance optimization are not implemented. |

## 5. UX and map-versus-3D decision

**Proposal:** Operator-grade workspace, six views: overview, assets, decisions,
economics, safety, evidence. Lead with current slot, source, constraints, and
operating outcomes. Show synthetic/unavailable/assumed status beside relevant data.
Keep historical synthetic approvals distinct from new interactive approvals.
An error must never turn into a fabricated fallback value or success message.

**Decision:** Use a full-width Three.js portfolio schematic with selectable solar
farms, wind farms, BESS, grid connection, and consumers. Output and stored energy
are bound to the canonical replay. Geometry is illustrative, not surveyed hardware
or geographic coordinates. Use a compact trend chart and an accessible asset table
as the quantitative surfaces. Preserve a readable fallback if WebGL is unavailable.

**Why not a map now:** Demo IDs do not match India-site fixture IDs and real sites
were not verified. A map would encourage false specificity. Add map mode only after
an explicit asset-to-site mapping and location provenance pass. The 3D view is a
prototype for understanding the portfolio, not a validated power-flow simulator.

## 6. Productization sequence and release gates

1. **Now: secure evidence workspace.** Reuse the existing security composition
   root for local and deployed serving; bind approval actors to verified identity;
   scope replay records/idempotency to tenant/principal; build the data-bound UI;
   make missing evidence fail explicitly; retain the G3 warning.
2. **Optimizer reliability.** Multi-period energy transitions, shared first-stage
   commitments/non-anticipativity, terminal reserves and DR deadlines. Acceptance:
   G3 becomes blocking and passes, with adversarial cross-scenario tests and
   no-worse critical-load outcomes versus a declared baseline.
3. **Customer discovery and shadow-mode pilot.** Interview operators/approvers
   about recent costly decisions; obtain licensed telemetry, forecast issue times,
   meter data, outage logs, tariffs/fees, PPA obligations, and dispatch authority.
   Replay holdout operating weeks without submitting commands or orders.
4. **Economic/regulatory validation.** Pin relevant CERC/SERC rules and effective
   dates; verify participant category, SLDC/RLDC permissions, settlement/loss/fee
   treatment, Indian time-zone windows, and FDRE contract terms with practitioners.
   Do not relabel synthetic USD examples as verified INR settlement.
5. **Production operational readiness.** Persist decision authors and tenant-scoped
   replay state transactionally, distributed idempotency/rate limiting, migration
   tests, authenticated UI hosting, service-role separation, key rotation, recovery
   drills, dependency/vulnerability/image checks, load tests, and budget controls.
   Existing in-memory author history fails closed after restart; it is not HA.
6. **Only later: controlled integrations.** Licensed market access, validated
   plant/network models, explicit operator procedures and interlocks, and a separate
   hardware/OT assurance program. No current module authorizes this step.

**Proposed pilot scorecard:** Forecast pinball/coverage and time-aware leakage
checks; physical balance/reserve invariants; critical/noncritical unserved energy;
DR cost and rebound completeness; committed market shortfall; throughput and
curtailment; end-to-end measured latency/fallback frequency; reviewer decision time;
and replay/certificate reproducibility. Savings are a holdout comparison under
matching inputs, not a selected best-case replay. Reliability precedes economics.

**Commercial hypothesis, not a validated price:** Subscription per portfolio with
paid onboarding/data integration, initially advisory/shadow mode. Discover whether
procurement belongs with energy operations, plant operations, or finance before
selecting pricing. No TAM, ROI, or willingness-to-pay number has been invented.

## 7. Five-minute proof, not theatre

Open the real 96-slot artifact; inspect the solar/wind/load trend; jump to an actual
rejected slot; show the recorded violation and unchanged action-application state;
show per-slot unserved accounting and priced DR; distinguish physical exports from
market revenue; verify the certificate; show the remaining G3 warning. New human
approvals use the authenticated API, never browser-only toggles. No fabricated
telemetry, hardcoded performance wins, regulatory approval, or grid-control claim.

## 8. Expansion audit and fresh source review (2026-10-09)

The login white-screen cause was an API/UI contract mismatch: replay artifacts
store decisions as a slot-keyed object; the UI assumed an array. The API now
validates and sorts decisions, and the browser validates its workspace payload.
This is a correctness failure, not a reason to bypass authentication.

### Current capability map

| Surface | Actual owning code/evidence | What the frontend may honestly show |
| --- | --- | --- |
| Dispatch and state | `planner.py`, `validator.py`, `twin.py`, replay slot states | Synthetic dispatch, constraints, fallback/rejection, energy and SoC; not utility control. |
| Forecasts | `forecasts.py` supplies baseline bands; `forecasting/` is a separate research subsystem | Baseline P10/P50/P90 bands and raw joint scenarios. Phase 2 integration is not complete. |
| Asset events | `dispatch.py`, fixture events | Event-adjusted generation/demand, BESS outage and tie derates. No geographic site mapping is inferred. |
| Economics | Metric registry and baseline artifact | Priced DR, unserved energy, cost, throughput, committed-market versus physical export. Include baseline losses, not only wins. |
| Obligations/DSM | `obligations.py`, `dsm.py`, assumed fixtures | Report-only contracts and assumed rates; not verified Indian settlement or optimizer-integrated obligations. |
| Science | `experiments/results/` | Latest run by recorded timestamp, run/code/content digests, measured experiment duration, acquisition status and withheld conclusions. Stored artifacts are not freshly rerun validation. |
| Hardware | `software_bench_validation.py` | Deterministic software-only normal/reduced-load/watchdog/disconnect frames. No connected ESP32, measured electrical values, CHIL, PHIL or HIL. |
| Cloud | Existing deployment templates | Prepared infrastructure design. Postgres adapter, durable distributed replay and authenticated production browser hosting remain unresolved. |

Historical dataset manifests must not be pooled as successful acquisitions. The
latest `fetch_datasets` run at 2026-10-09T11:09:36Z rejects the EMSx landing page
and zero-length Open-Meteo response; GEFCom2014 has a recorded 126,360,077-byte
payload and hash. The latest stored full evaluation still withholds real-data
conclusions. Obtaining an archive is not evaluating a model on it.

### Scientific findings and concrete engineering consequences

- Xu and Xie, *Conformal prediction for time series*, arXiv:2010.09107v15:
   https://arxiv.org/abs/2010.09107. Abstract inspected; full HTML could not be
   extracted. EnbPI addresses sequential intervals and coverage gaps under stated
   assumptions. Do not promise conditional coverage, arbitrary-drift robustness,
   or Indian-plant accuracy merely because a conformal implementation exists.
- Gibbs and Candes, *Adaptive Conformal Inference Under Distribution Shift*,
   arXiv:2106.00170v3: https://arxiv.org/abs/2106.00170. Abstract inspected; full
   HTML extraction unavailable. Long-time coverage-frequency control is distinct
   from every-regime/per-slot coverage. Product consequence: rolling coverage,
   interval width and sample count must travel together with horizon/regime and
   issue-time provenance, rather than a single confidence score.
- Pu et al., *A Hybrid Strategy for Probabilistic Forecasting and Trading of
   Aggregated Wind-Solar Power*, arXiv:2505.10367v2:
   https://arxiv.org/abs/2505.10367. Abstract inspected. The competition solution
   combines NWP forecasts, solar post-processing under capacity shift, hybrid
   quantile aggregation and stochastic day-ahead trading. Evaluate forecasting
   and downstream decision loss separately; competition trading performance is
   not evidence of Indian RTM access or MonsoonGrid revenue. Full-paper methods
   and reproducible competition experiments were not independently rerun here.
- Pamshetti et al., *Enhanced Battery Degradation-Aware Scheduling for Distribution
   Network with Electric Vehicle Load*, arXiv:2407.06857:
   https://arxiv.org/abs/2407.06857. Abstract inspected. Its multi-objective study
   uses an IEEE 33-bus benchmark and incorporates purchase/degradation costs and
   network losses/voltage deviation. Product consequence: chemistry-specific
   battery-wear models and voltage claims require explicit calibrated inputs;
   throughput and the current aggregate tie constraint are only proxies.
- Achiam et al., *Constrained Policy Optimization*, PMLR 70, 2017:
   https://proceedings.mlr.press/v70/achiam17a.html. Abstract inspected. Near-
   constraint-satisfaction guarantees in a modeled learning problem do not
   establish physical plant safety. Do not replace the deterministic validator
   or local device interlocks with an RL/LLM policy without a separate assurance
   and out-of-sample validation program.
- GPM Horizon official product page:
   https://www.greenpowermonitor.com/solutions/cloud-unified-management-of-multiple-renewable-plants/gpm-horizon/.
   Retrieved vendor capabilities include alarms, forecasts, planning, revenues,
   reports, tickets and logbooks across solar/wind/storage. Generic dashboard
   breadth is not defensible differentiation. Prove constrained action selection,
   economic accounting and decision evidence instead; vendor claims are not our
   independent performance evidence.
- NIST SP 800-82r3 final page:
   https://csrc.nist.gov/pubs/sp/800/82/r3/final. Retrieved guidance explicitly
   includes OT performance, reliability and safety concerns. Rev. 4 is a draft
   as of the page's September 2026 note, not a replacement final standard.
   Product consequence: cloud/browser authorization is never a physical interlock.
- Google connected-device architectures:
   https://docs.cloud.google.com/architecture/connected-devices. Retrieved
   documentation compares MQTT broker, IoT platform and direct Pub/Sub approaches.
   Pub/Sub is not itself an MQTT broker. Start with a local authenticated gateway
   and telemetry-only export; device fleet management is a separate capability.
- Google Cloud Run overview:
   https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run. Retrieved
   documentation describes stateless autoscaling services and disposable local
   filesystems, as well as jobs for finite batch work. Therefore SQLite/process
   replay state cannot be promoted to durable production by adding a Cloud SQL
   resource without an adapter and transactional migration tests.
- Google Cloud budgets:
   https://docs.cloud.google.com/billing/docs/how-to/budgets. Retrieved page warns
   that alerts-only budgets do not cap spending and reporting is delayed. It also
   describes spend-cap budgets for eligible services; eligibility must be checked,
   not assumed for the whole architecture. Monitor gross spend excluding credits
   and rehearse the expiry/teardown plan before the promotional period ends.

The PHIL review at https://www.mdpi.com/2079-9292/9/3/382 was blocked by an
interstitial; the attempted Typhoon CHIL page returned 404. These were not read
as fresh evidence. Existing desk-scale research is background, not proof that
this project's hardware was validated. No literature benchmark percentage is
relabelled as a MonsoonGrid result.

### Optional hardware validation plan

**Proposal, not implemented/validated:** a supervised, electrically isolated,
current-limited low-voltage desk demonstrator with a mini-PV sensing channel,
ESP32 node, optional Pi gateway, fused low-power loads, manual disconnect and
virtual BESS. No mains/grid connection or raw rechargeable pack is needed.
Have a qualified supervisor review components, thermal limits and wiring.

Begin with telemetry-only capture. A gateway validates device identity, units,
sequence, sensor quality, age and duplicate frames; cloud telemetry never replaces
authoritative dispatch inputs by default. Retain raw/calibration/clock evidence.
Test dropout, stale/replayed frames, invalid units, sensor disagreement and
watchdog response. Measured acceptance thresholds must be declared before tests.
Only a documented real-time model/controller closed loop can support CHIL; PHIL
additionally requires a characterized power interface and stability evidence.

### GCloud tiers and the credits-expiry plan

**Demo tier:** Cloud Build + Artifact Registry for reproducible images; a private
Cloud Run service; Secret Manager for actual secrets; Cloud Logging/Monitoring;
Cloud Storage for durable immutable replay evidence. Browser auth/static hosting
must be completed deliberately (IAP/BFF or a separately authenticated hosting
architecture), not by enabling the local bearer mode in production. Retain the
current production UI denial until that design is implemented and verified.

**Pilot tier:** transactional Cloud SQL/Postgres adapter for approvals, authors,
idempotency and replay state; migration/restore testing; service-role separation;
Cloud Scheduler only after durable state exists; Cloud Run Jobs for independent
evaluation/backtesting. The current instance template is a plan, not a release.

**Data/AI tier when justified:** Pub/Sub for telemetry fan-out and explicit
dead-letter handling; partitioned BigQuery with query/retention limits for fleet
analytics; Vertex AI experiments/model registry for trained forecast governance.
Gemini may narrate signed decisions or retrieve approved manuals with citations
and bounded spend, never calculate setpoints, authorize actions or override safety.
GPU clusters, GKE, always-on brokers and extra databases require a measured need.

Before any push/provisioning, confirm project ID, region, IAM principals, gross
monthly cap, credit expiry and teardown owner. Set instance/query/retention limits,
keep scheduler jobs paused, label demo resources and export recoverable evidence.
Review gross post-credit costs and teardown deliberately; a bill alert alone is
not a kill switch. No project was selected or mutated in this implementation.

### Release roadmap and demo value

The implemented frontend expansion is eleven data-bound views plus guided
chapters selected from actual fixture events and rejected decisions. The 3D scene
is a physically recognizable schematic, not a geographically correct grid or
validated electromechanical simulator. More code or 3D detail does not establish
million-dollar product value; operator adoption and reduced decision error do.

Next gates, in order: resolve G3 with multi-period/shared first-stage commitments;
wire the forecast research interface with issue-time/probability/coverage contracts;
validate on licensed measured plant and market data; persist distributed state;
complete browser auth; run a paid shadow-mode pilot; then evaluate OT integration.
Customer interviews and a holdout operating scorecard determine which adjacent
features deserve investment. Predictive maintenance, ticketing, warranty models
and real-time frequency control are not silently claimed as implemented here.