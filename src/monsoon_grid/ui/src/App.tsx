import { lazy, Suspense, useEffect, useState } from 'react';
import { Activity, ArrowLeft, ArrowRight, Battery, BrainCircuit, CalendarClock, Check, ChevronRight, CircuitBoard, CloudSun, Cpu, Download, FileCheck2, FlaskConical, Gauge, KeyRound, Layers3, LogOut, Menu, Moon, Pause, Play, RefreshCw, ScalingIcon, Search, ShieldCheck, Sliders, Sparkles, Sun, SunMedium, Wallet, Wind, X, Zap } from 'lucide-react';
import type { Workspace, Session, Metric, Proposal, Interactive, Decision, Slot, AgentDecision } from './types';
import { parseWorkspace } from './workspaceSchema';

const Scene = lazy(() => import('./Scene').then(module => ({ default: module.Scene })));
const ResearchPanels = lazy(() => import('./ResearchPanels').then(module => ({ default: module.ResearchPanels })));
const BaselineComparison = lazy(() => import('./ResearchPanels').then(module => ({ default: module.BaselineComparison })));
const number = (value: number | null | undefined, digits = 2) => value == null ? 'Unavailable' : value.toLocaleString('en-US', { maximumFractionDigits: digits });
const time = (value: string) => new Date(value).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
const views = [{ name: 'Overview', icon: Gauge }, { name: 'Agent', icon: BrainCircuit }, { name: 'Assets', icon: Layers3 }, { name: 'Scale', icon: ScalingIcon }, { name: 'Storage', icon: Battery }, { name: 'Forecasts', icon: CloudSun }, { name: 'Timeline', icon: CalendarClock }, { name: 'Decisions', icon: Activity }, { name: 'Economics', icon: Wallet }, { name: 'Safety', icon: ShieldCheck }, { name: 'Science', icon: FlaskConical }, { name: 'Bench', icon: Cpu }, { name: 'Hardware', icon: CircuitBoard }, { name: 'Evidence', icon: FileCheck2 }];
type Envelope<T> = { status: string; data: T; error?: { message?: string }; detail?: { message?: string } | string };

async function request<T>(token: string, path: string, body?: unknown): Promise<T> {
  const key = crypto.randomUUID();
  const response = await fetch(path, { method: body ? 'POST' : 'GET', headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json', 'Idempotency-Key': key } : {}) }, body: body ? JSON.stringify({ ...(body as object), idempotency_key: key }) : undefined });
  const envelope: Envelope<T> = await response.json();
  if (!response.ok || envelope.status === 'error') {
    const detail = typeof envelope.detail === 'string' ? envelope.detail : envelope.detail?.message;
    throw new Error(envelope.error?.message ?? detail ?? `Request failed (${response.status})`);
  }
  return envelope.data;
}

function MetricTile({ title, metric, accent }: { title: string; metric?: Metric; accent?: string }) {
  return <div className={`metric-tile ${accent ?? ''}`}><span>{title}</span><strong>{number(metric?.value)} <small>{metric?.unit ?? ''}</small></strong><div className="meta">{metric?.status ?? 'unavailable'} / {metric?.provenance.label ?? 'no evidence'}</div></div>;
}

function Sparkline({ data, slot, color = 'var(--mint)' }: { data: number[]; slot?: number; color?: string }) {
  if (!data.length) return null;
  const max = Math.max(...data, 1), min = Math.min(...data, 0), span = max - min || 1;
  const points = data.map((value, index) => `${index / (data.length - 1) * 100},${28 - (value - min) / span * 26}`).join(' ');
  return <svg className="sparkline" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true"><polyline points={points} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />{slot != null && <line x1={slot / (data.length - 1) * 100} x2={slot / (data.length - 1) * 100} y1="0" y2="28" stroke="var(--amber)" strokeWidth="1" vectorEffect="non-scaling-stroke" />}</svg>;
}

function Trend({ slots, slot, onChange }: { slots: Slot[]; slot: number; onChange: (slot: number) => void }) {
  const max = Math.max(...slots.map(item => Math.max(item.renewable_available_mw, item.demand_mw)), 1);
  return <section><div className="section-heading"><h2>24-hour operating profile</h2><div className="legend"><span className="storage-dot" />Renewable availability <span className="load-dot" />Demand <span>MW</span></div></div><div className="trend" role="group" aria-label="96-slot renewable availability and demand profile">{slots.map(item => <button key={item.slot_index} aria-label={`View slot ${item.slot_index}, ${time(item.timestamp_utc)} UTC`} title={`${time(item.timestamp_utc)} UTC | Renewable ${number(item.renewable_available_mw)} MW | Demand ${number(item.demand_mw)} MW`} className={item.slot_index === slot ? 'trend-slot active' : 'trend-slot'} onClick={() => onChange(item.slot_index)}><span className="generation-bar" style={{ height: `${item.renewable_available_mw / max * 100}%` }} /><span className="demand-mark" style={{ bottom: `${item.demand_mw / max * 100}%` }} /></button>)}</div><div className="chart-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:45 UTC</span></div></section>;
}

function LiveControl({ open, onToggle, twin, setTwin, current, price, onReset }: { open: boolean; onToggle: () => void; twin: { bess: number; dr: boolean; curtail: number; load: number }; setTwin: (next: { bess: number; dr: boolean; curtail: number; load: number }) => void; current: Slot; price: number; onReset: () => void }) {
  // Honest simulated-twin energy balance: recompute net from the real slot + operator levers.
  const baseRenewable = current.renewable_available_mw;
  const effRenewable = Math.max(0, baseRenewable - twin.curtail);
  const effDemand = Math.max(0, current.demand_mw * (1 - twin.load / 100) - (twin.dr ? 4 : 0));
  const bessContribution = twin.bess; // + discharge to grid, - charge
  const net = effDemand - effRenewable - bessContribution;
  const simImport = Math.max(0, net);
  const simExport = Math.max(0, -net);
  const simCost = (simImport * (price + 1.5) - simExport * price) * 0.25;
  return <section className="live-control">
    <div className="section-heading"><h2 className="live-h2"><Zap size={16} />Live Control <span className="sim-pill">SIMULATED TWIN</span></h2><div className="live-actions"><button className="text-button" onClick={onReset}>Reset</button><button className={open ? 'primary' : ''} onClick={onToggle}><Sliders size={15} />{open ? 'Engaged' : 'Engage controls'}</button></div></div>
    {open ? <div className="live-grid">
      <div className="live-levers">
        <label>Dispatch BESS <small>{twin.bess > 0 ? `discharge ${twin.bess.toFixed(1)} MW` : twin.bess < 0 ? `charge ${Math.abs(twin.bess).toFixed(1)} MW` : 'idle'}</small><input type="range" min="-8" max="8" step="0.5" value={twin.bess} onChange={e => setTwin({ ...twin, bess: Number(e.target.value) })} /></label>
        <label>Curtail renewables <small>{twin.curtail.toFixed(1)} MW</small><input type="range" min="0" max={Math.max(1, Math.round(baseRenewable))} step="0.5" value={twin.curtail} onChange={e => setTwin({ ...twin, curtail: Number(e.target.value) })} /></label>
        <label>Shift flexible load <small>-{twin.load}%</small><input type="range" min="0" max="30" step="1" value={twin.load} onChange={e => setTwin({ ...twin, load: Number(e.target.value) })} /></label>
        <label className="live-switch"><input type="checkbox" checked={twin.dr} onChange={e => setTwin({ ...twin, dr: e.target.checked })} />Trigger demand response (−4 MW)</label>
      </div>
      <div className="live-readout">
        <div><span>Simulated import</span><strong>{number(simImport)} <small>MW</small></strong></div>
        <div><span>Simulated export</span><strong>{number(simExport)} <small>MW</small></strong></div>
        <div><span>Effective demand</span><strong>{number(effDemand)} <small>MW</small></strong></div>
        <div className={simCost < 0 ? 'good' : ''}><span>Slot cost delta</span><strong>{number(simCost)} <small>USD</small></strong></div>
      </div>
    </div> : <p className="live-hint">Engage to drive BESS dispatch, curtailment, load-shift and demand response on the software twin and watch import/export/cost respond. This simulates the twin; it issues no real-grid command.</p>}
  </section>;
}

function CommandPalette({ query, setQuery, views, onPick, actions, onClose }: { query: string; setQuery: (q: string) => void; views: { name: string }[]; onPick: (name: string) => void; actions: { label: string; run: () => void }[]; onClose: () => void }) {
  const q = query.trim().toLowerCase();
  const matchedViews = views.filter(v => v.name.toLowerCase().includes(q));
  const matchedActions = actions.filter(a => a.label.toLowerCase().includes(q));
  return <div className="palette-scrim" onClick={onClose}>
    <div className="palette" onClick={e => e.stopPropagation()} role="dialog" aria-label="Command palette">
      <div className="palette-input"><Search size={17} /><input autoFocus placeholder="Jump to a view or run an action..." value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { if (matchedViews[0]) onPick(matchedViews[0].name); else if (matchedActions[0]) { matchedActions[0].run(); onClose(); } } }} /><kbd>Esc</kbd></div>
      <div className="palette-list">
        {matchedViews.length > 0 && <div className="palette-group">Views</div>}
        {matchedViews.map(v => <button key={v.name} onClick={() => onPick(v.name)}><Gauge size={15} />{v.name}</button>)}
        {matchedActions.length > 0 && <div className="palette-group">Actions</div>}
        {matchedActions.map(a => <button key={a.label} onClick={() => { a.run(); onClose(); }}><Zap size={15} />{a.label}</button>)}
        {matchedViews.length === 0 && matchedActions.length === 0 && <div className="palette-empty">No matches</div>}
      </div>
    </div>
  </div>;
}

const OBJ_LABEL: Record<string, string> = {
  critical_service_margin: 'Critical service', required_reliability_tier: 'Reliability tier',
  same_non_negotiable_safety_tier: 'Safety floor', reserve_margin: 'Reserve margin',
  reliability_event_count: 'Reliability events', net_cost: 'Net cost', carbon: 'Carbon',
  curtailment: 'Curtailment', bess_wear: 'Battery wear', dr_activation: 'Demand response',
};

function AgentView({ decision, busy, error, slot }: { decision: AgentDecision | null; busy: boolean; error: string; slot: number }) {
  if (error) return <div className="error" role="alert">{error}</div>;
  if (busy && !decision) return <div className="agent-skeleton"><div className="skl-hero" /><div className="skl-row" /><div className="skl-row" /></div>;
  if (!decision) return <p role="status">Select the Agent view to run autonomous objective selection.</p>;
  const d = decision;
  const safe = d.chosen_objective_order[0];
  const costBetter = d.cost_regret_usd < -0.01, costWorse = d.cost_regret_usd > 0.01;
  const f = d.features;
  const feat = (label: string, value: string, hot = false) => <div className={`agent-feat ${hot ? 'hot' : ''}`}><span>{label}</span><strong>{value}</strong></div>;
  return <>
    <div className="agent-hero">
      <div className="agent-hero-main">
        <div className="agent-badge"><Sparkles size={14} />{d.brain === 'gemini' ? 'GEMINI (Vertex AI)' : 'DETERMINISTIC BRAIN'}<span className="agent-conf">confidence {(d.confidence * 100).toFixed(0)}%</span></div>
        <h2>The agent chose the optimizer's criteria for slot {slot}.</h2>
        <p className="agent-rationale">{d.rationale}</p>
        <div className="agent-order">{d.chosen_objective_order.map((name, index) => <span key={name} className={`agent-chip ${index === 0 ? 'lead' : ''}`}>{index + 1}. {OBJ_LABEL[name] ?? name}</span>)}</div>
      </div>
      <div className="agent-hero-side">
        <div className="agent-stat"><span>Lead objective</span><strong>{OBJ_LABEL[safe] ?? safe}</strong></div>
        <div className="agent-stat"><span>Reflection steps</span><strong>{d.steps.length}</strong></div>
        <div className="agent-stat"><span>Preset baseline</span><strong>{d.preset_policy}</strong></div>
      </div>
    </div>

    <div className="section-heading"><h2>Why — the situation it reasoned over</h2><span className="meta">LIVE FEATURES / CONFORMAL BAND</span></div>
    <div className="agent-features">
      {feat('Renewable', `${number(f.renewable_available_mw)} MW`)}
      {feat('Demand', `${number(f.demand_mw)} MW`)}
      {feat('Net margin', `${number(f.net_margin_mw)} MW`, f.net_margin_mw < 0)}
      {feat('Price', `$${number(f.price_usd_per_mwh)}`)}
      {feat('Forecast band (P90−P10)', `${number(f.forecast_band_width_mw)} MW`)}
      {feat('Uncertainty ratio', f.uncertainty_ratio.toFixed(2), f.uncertainty_ratio >= 0.46)}
      {feat('Stored SoC', `${number(f.bess_soc_fraction * 100)}%`)}
      {feat('Contingencies', [f.bess_outage && 'BESS outage', f.tie_derated && 'tie derate', f.demand_spike && 'demand spike', f.weather_event && 'cloud'].filter(Boolean).join(', ') || 'none', f.bess_outage || f.tie_derated || f.demand_spike)}
    </div>

    <div className="section-heading"><h2>Reflective loop</h2><span className="meta">PROPOSE → OPTIMIZE → VALIDATE → CRITIQUE</span></div>
    <div className="agent-steps">
      {d.steps.map(step => <div key={step.iteration} className={`agent-step ${step.accepted ? 'ok' : 'revise'}`}>
        <div className="agent-step-head"><strong>Iteration {step.iteration}</strong><span className={`decision-status ${step.accepted ? 'accepted' : 'rejected'}`}>{step.validator_status}</span></div>
        <p>{step.rationale}</p>
        <div className="agent-step-meta"><span>lead: {OBJ_LABEL[step.objective_order[0]] ?? step.objective_order[0]}</span><span>critical unserved: {number(step.critical_unserved_mwh)} MWh</span><span>net cost: ${number(step.net_cost_usd)}</span></div>
        {step.revised_reason && <div className="agent-feedback">↻ revised after: {step.revised_reason}</div>}
      </div>)}
    </div>

    <div className="section-heading"><h2>Actions the agent took</h2><span className="meta">REALIZED DISPATCH / SIMULATED TWIN</span></div>
    <div className="agent-conflict"><Zap size={15} />{d.conflict_note}</div>
    <div className="agent-actions">{d.actions.map((a, i) => {
      const icon = a.category === 'battery' ? <Battery size={18} /> : a.category === 'market' ? <Wallet size={18} /> : a.category === 'renewable' ? <Sun size={18} /> : <Activity size={18} />;
      const idle = a.magnitude_mw <= 0.05;
      return <div key={i} className={`agent-action ${a.category} ${idle ? 'idle' : ''}`}>
        <div className="agent-action-head">{icon}<strong>{a.action}</strong>{!idle && <span className="agent-action-mag">{number(a.magnitude_mw)} {a.unit}</span>}</div>
        <div className="agent-action-target">{a.target}</div>
        <p>{a.note}</p>
        <div className="agent-action-driver">driven by: {OBJ_LABEL[a.driver_objective] ?? a.driver_objective}</div>
      </div>;
    })}</div>

    <div className="section-heading"><h2>Regret vs the fixed preset</h2><span className="meta">SAME SITUATION / HONEST COUNTERFACTUAL</span></div>
    <div className="agent-regret">
      <div className="regret-col"><h4>Agent (autonomous)</h4><div className="regret-num">${number(d.agent_net_cost_usd)}</div><span>net cost</span><div className="regret-sub">{number(d.agent_critical_unserved_mwh)} MWh critical unserved</div></div>
      <div className="regret-vs">vs</div>
      <div className="regret-col"><h4>Preset ({d.preset_policy})</h4><div className="regret-num">${number(d.preset_net_cost_usd)}</div><span>net cost</span><div className="regret-sub">{number(d.preset_critical_unserved_mwh)} MWh critical unserved</div></div>
    </div>
    <div className={`agent-verdict ${costBetter ? 'win' : costWorse ? 'premium' : 'tie'}`}>
      {d.reliability_regret_mwh <= 1e-6 ? <><Check size={16} />Never worse on critical reliability than the preset.</> : <><ShieldCheck size={16} />Reliability trade-off flagged for review.</>}
      {' '}{costBetter ? `Cost ${number(-d.cost_regret_usd)} USD lower than the preset this slot.` : costWorse ? `Pays a ${number(d.cost_regret_usd)} USD reliability premium this slot — a deliberate, disclosed trade-off.` : 'Matches the preset cost this slot.'}
    </div>

    <div className="warning">The agent chooses only the objective <strong>ordering</strong>. The deterministic validator remains the sole safety authority; the agent cannot approve, override a rejection, write a setpoint, or submit a market order. Blocked tools: {d.forbidden_tools_blocked.join(', ')}.</div>
    <p className="hash">Decision digest: {d.digest}</p>
  </>;
}

export function App() {
  const [token, setToken] = useState(''), [inputToken, setInputToken] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [view, setView] = useState('Overview'), [slot, setSlot] = useState(0), [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [sceneScale, setSceneScale] = useState<'unit' | 'city'>('unit');
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState('');
  const [toasts, setToasts] = useState<{ id: number; text: string; kind: 'ok' | 'warn' | 'err' }[]>([]);
  const [liveControl, setLiveControl] = useState(false);
  const [twin, setTwin] = useState<{ bess: number; dr: boolean; curtail: number; load: number }>({ bess: 0, dr: false, curtail: 0, load: 0 });
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [verification, setVerification] = useState<{ verified: boolean; persistence_consistent: boolean; unsigned_digest: string } | null>(null);
  const [interactive, setInteractive] = useState<Interactive | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [interactiveDecision, setInteractiveDecision] = useState<Decision | null>(null);
  const [approvalToken, setApprovalToken] = useState<string | null>(null);
  const [policy, setPolicy] = useState('balanced'), [reason, setReason] = useState('');
  const [demoStep, setDemoStep] = useState<number | null>(null);
  const [assetQuery, setAssetQuery] = useState(''), [assetKind, setAssetKind] = useState('all');
  const [agent, setAgent] = useState<AgentDecision | null>(null), [agentBusy, setAgentBusy] = useState(false), [agentError, setAgentError] = useState('');

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setSlot(current => { if (current >= 95) { setPlaying(false); return 95; } return current + 1; }), 1200);
    return () => window.clearInterval(timer);
  }, [playing]);

  useEffect(() => { document.documentElement.dataset.theme = theme; document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0b1016' : '#f4f6f8'); }, [theme]);

  useEffect(() => {
    if (view !== 'Agent' || !token) return;
    let cancelled = false;
    setAgentBusy(true); setAgentError('');
    request<AgentDecision>(token, `/api/agent/deliberate?slot=${slot}&policy=${policy}`)
      .then(data => { if (!cancelled) { setAgent(data); setAgentBusy(false); } })
      .catch(failure => { if (!cancelled) { setAgentError(failure instanceof Error ? failure.message : 'Agent request failed'); setAgentBusy(false); } });
    return () => { cancelled = true; };
  }, [view, slot, policy, token]);

  function toast(text: string, kind: 'ok' | 'warn' | 'err' = 'ok') {
    const id = Date.now() + Math.random();
    setToasts(list => [...list, { id, text, kind }]);
    window.setTimeout(() => setToasts(list => list.filter(item => item.id !== id)), 3600);
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPaletteOpen(open => !open); return; }
      if (event.key === 'Escape') { setPaletteOpen(false); setDrawerOpen(false); return; }
      if (!workspace || paletteOpen) return;
      const target = event.target as HTMLElement;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (event.key === 'ArrowRight') setSlot(value => Math.min(95, value + 1));
      else if (event.key === 'ArrowLeft') setSlot(value => Math.max(0, value - 1));
      else if (event.key === ' ') { event.preventDefault(); setPlaying(value => !value); }
      else if (event.key.toLowerCase() === 't') setTheme(value => value === 'dark' ? 'light' : 'dark');
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [workspace, paletteOpen]);

  async function connect(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const identity = await request<Session>(inputToken, '/api/session');
      const data = parseWorkspace(await request<unknown>(inputToken, '/api/workspace'));
      setSession(identity); setWorkspace(data); setToken(inputToken); setInputToken(''); setVerification(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Connection failed'); }
    finally { setBusy(false); }
  }

  async function action(run: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await run(); } catch (failure) { setError(failure instanceof Error ? failure.message : 'Action failed'); }
    finally { setBusy(false); }
  }

  function disconnect() {
    setToken(''); setSession(null); setWorkspace(null); setPlaying(false); setVerification(null);
    setInteractive(null); setProposal(null); setInteractiveDecision(null); setApprovalToken(null); setNotice(''); setError('');
  }

  const metric = (name: string) => workspace?.metrics.values.find(item => item.name === name);
  const current = workspace?.replay.slot_states[slot];
  const scenarioPath = workspace?.replay.scenario_set.paths.find(item => item.scenario_id === workspace.replay.realized_path_id);
  const path = scenarioPath && workspace ? { ...scenarioPath, renewable_available_mw: workspace.asset_series.generation_mw, demand_mw: workspace.asset_series.demand_mw } : undefined;
  const decision = workspace?.replay.decisions.find(item => item.slot_index === slot);
  const rejected = workspace?.replay.decisions.filter(item => item.status !== 'ACCEPTED') ?? [];
  const selectedAsset = workspace?.portfolio.assets.find(asset => asset.asset_id === selected);
  const canApprove = interactiveDecision?.status === 'ACCEPTED' && session?.roles.includes('approver') && reason.trim().length > 0 && !busy && !approvalToken;
  const demoChapters = workspace ? [
    { title: 'The portfolio', slot: 0, view: 'Overview' },
    ...workspace.events.filter(event => ['monsoon_cloud', 'bess_outage', 'stale_telemetry'].includes(event.event_type)).map(event => ({ title: event.event_type.replaceAll('_', ' '), slot: event.start_slot, view: event.event_type === 'bess_outage' ? 'Storage' : 'Overview' })),
    ...rejected.slice(0, 1).map(item => ({ title: 'Unsafe action rejected', slot: item.slot_index, view: 'Safety' })),
    { title: 'Cost and reliability', slot: 95, view: 'Economics' },
    { title: 'Scientific boundary', slot: 95, view: 'Science' },
    { title: 'Signed evidence', slot: 95, view: 'Evidence' },
  ] : [];

  function showChapter(index: number) {
    const chapter = demoChapters[index];
    if (!chapter) return;
    setPlaying(false); setDemoStep(index); setSlot(chapter.slot); setView(chapter.view);
  }

  async function createInteractive() {
    if (!workspace) return;
    const state = await request<Interactive>(token, '/replays', { seed: workspace.replay.seed, policy_mode: policy, fixture_set_hash: workspace.replay.fixture_set_hash, adapter_state: 'DISABLED' });
    setInteractive(state); setProposal(null); setInteractiveDecision(null); setApprovalToken(null);
    setNotice('Interactive replay created at slot 0. No action applied.');
  }

  async function planInteractive() {
    if (!interactive) return;
    const result = await request<{ proposal: Proposal }>(token, '/planning', { replay_id: interactive.replay_id, slot_index: interactive.slot_index, expected_state_digest: interactive.state_digest, policy_mode: policy });
    const validation = await request<Decision>(token, '/validate', { replay_id: interactive.replay_id, expected_state_digest: interactive.state_digest, proposal: result.proposal });
    setProposal(result.proposal); setInteractiveDecision(validation); setApprovalToken(null);
    setNotice(validation.status === 'ACCEPTED' ? 'Validator accepted. Independent approval still required.' : 'Validator rejected. No action applied.');
  }

  async function approveInteractive(approve: boolean) {
    if (!interactive || !proposal || !session) return;
    const result = await request<{ approval_event?: { record_id: string }; state: string }>(token, '/approvals', { replay_id: interactive.replay_id, decision_id: proposal.decision_id, expected_state_digest: interactive.state_digest, actor_id: session.actor_id, action: approve ? 'approve' : 'reject', reason });
    setInteractive(await request<Interactive>(token, `/replays/${encodeURIComponent(interactive.replay_id)}`));
    setApprovalToken(result.approval_event?.record_id ?? null); setNotice(`API approval state: ${result.state}. No live command issued.`);
  }

  async function applyInteractive() {
    if (!interactive || !proposal || !approvalToken) return;
    const state = await request<Interactive>(token, `/replays/${encodeURIComponent(interactive.replay_id)}/advance`, { replay_id: interactive.replay_id, expected_slot_index: interactive.slot_index, expected_state_digest: interactive.state_digest, proposal, approval_token: approvalToken });
    setInteractive(state); setProposal(null); setInteractiveDecision(null); setApprovalToken(null);
    setNotice(`Replay advanced to slot ${state.slot_index}. Software simulation only.`);
  }

  function download() {
    if (!workspace) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({ replay_id: workspace.replay.replay_id, metrics: workspace.metrics, certificate: workspace.certificate }, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'vayu-evidence.json'; anchor.click(); URL.revokeObjectURL(url);
  }

  return <div className="app-shell">
    <header className="header"><div className="brand">{workspace && <button className="icon-button menu-button" onClick={() => setDrawerOpen(true)} title="Open menu" aria-label="Open navigation menu"><Menu size={20} /></button>}<div className="brand-symbol"><Wind size={24} /></div><div className="brand-text"><h1>VAYU</h1><span>Variability-Aware Yield &amp; Utilization orchestrator</span></div></div><div className="header-right">{workspace && <button className="icon-button palette-trigger" onClick={() => setPaletteOpen(true)} title="Command palette (Ctrl+K)" aria-label="Open command palette"><Search size={17} /><kbd>Ctrl K</kbd></button>}<button className="icon-button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'} aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>{theme === 'dark' ? <SunMedium size={18} /> : <Moon size={18} />}</button>{session && <><span className="tenant">{session.tenant_id}</span><button className="icon-button" onClick={disconnect} title="Disconnect session" aria-label="Disconnect session"><LogOut size={18} /></button></>}</div></header>
    {!workspace || !current || !path ? <main className="connection"><div className="connection-mark"><KeyRound size={30} /></div><h2>Connect to the decision desk</h2><form onSubmit={connect}><label htmlFor="bearer">Bearer token</label><input id="bearer" type="password" autoComplete="off" value={inputToken} onChange={event => setInputToken(event.target.value)} required /><button className="primary" type="submit" disabled={busy}><KeyRound size={17} />{busy ? 'Connecting...' : 'Connect'}</button></form>{error && <p className="error" role="alert">{error}</p>}<div className="connection-status"><span className="status-dot" />Authenticated replay workspace</div></main> : <>
      {drawerOpen && <div className="drawer-scrim" onClick={() => setDrawerOpen(false)} />}
      <nav className={`drawer ${drawerOpen ? 'open' : ''}`} aria-label="Workspace views" aria-hidden={!drawerOpen}>
        <div className="drawer-head"><div className="brand-symbol"><Wind size={20} /></div><div><strong>VAYU</strong><span>Renewable orchestrator</span></div><button className="icon-button" onClick={() => setDrawerOpen(false)} title="Close menu" aria-label="Close menu"><X size={18} /></button></div>
        <div className="drawer-list" role="tablist">{views.map(item => <button role="tab" aria-selected={view === item.name} className={view === item.name ? 'active' : ''} key={item.name} onClick={() => { setView(item.name); setDrawerOpen(false); }}><item.icon size={18} /><span>{item.name}</span>{view === item.name && <ChevronRight size={15} className="drawer-caret" />}</button>)}</div>
        <div className="drawer-foot"><span className="meta">SIMULATION / REPLAY</span><span className="meta">NO LIVE CONTROL / NO MARKET SUBMISSION</span></div>
      </nav>
      <div className="view-strip"><div className="view-strip-current">{(() => { const v = views.find(item => item.name === view); return v ? <><v.icon size={16} />{v.name}</> : view; })()}</div><button className="text-button view-strip-browse" onClick={() => setDrawerOpen(true)}>Browse views <ChevronRight size={15} /></button></div>
      <main className="workspace"><div className="workspace-heading"><div><div className="eyebrow">REPLAY / {workspace.replay.realized_path_id} / 01 JUL 2026 UTC</div><h2>{view === 'Overview' ? 'One portfolio. Every trade-off.' : view}</h2></div><div className="replay-id">{workspace.replay.replay_id}<span>{workspace.replay.slot_states.length} slots / 15-minute cadence / {workspace.summary.policy_mode}</span></div></div>
        {error && <div className="error alert" role="alert">{error}<button className="icon-button" title="Dismiss error" aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}{notice && <div className="notice" role="status">{notice}</div>}
        <div className="demo-strip"><button className="text-button" onClick={() => showChapter(0)}><Play size={15} />Demo sequence</button>{demoStep !== null && <><button className="icon-button" title="Previous demo chapter" disabled={demoStep === 0} onClick={() => showChapter(demoStep - 1)}><ArrowLeft size={15} /></button><strong>{demoStep + 1}/{demoChapters.length} / {demoChapters[demoStep]?.title}</strong><button className="icon-button" title="Next demo chapter" disabled={demoStep >= demoChapters.length - 1} onClick={() => showChapter(demoStep + 1)}><ArrowRight size={15} /></button><button className="icon-button" title="Close demo sequence" onClick={() => setDemoStep(null)}><X size={15} /></button></>}<span className="meta">{workspace.events.filter(event => event.start_slot <= slot && slot <= event.end_slot).map(event => event.event_type.replaceAll('_', ' ')).join(' / ')}</span></div>
        <div className="replay-toolbar"><div className="slot-time"><strong>{time(current.timestamp_utc)}</strong><span>UTC / SLOT {String(slot).padStart(2, '0')}</span></div><div className="playback-controls"><button className="icon-button" title="Previous slot" aria-label="Previous slot" disabled={slot === 0} onClick={() => { setPlaying(false); setSlot(slot - 1); }}><ArrowLeft size={17} /></button><button className="icon-button play-button" title={playing ? 'Pause replay' : 'Play replay'} aria-label={playing ? 'Pause replay' : 'Play replay'} onClick={() => setPlaying(!playing)}>{playing ? <Pause size={18} /> : <Play size={18} />}</button><button className="icon-button" title="Next slot" aria-label="Next slot" disabled={slot === 95} onClick={() => { setPlaying(false); setSlot(slot + 1); }}><ArrowRight size={17} /></button></div><input type="range" aria-label="Replay slot" min="0" max="95" value={slot} onChange={event => { setPlaying(false); setSlot(Number(event.target.value)); }} /><span className={`decision-status ${current.action_applied ? 'accepted' : 'rejected'}`}>{current.action_applied ? 'REPLAY ACTION APPLIED' : 'ACTION NOT APPLIED'}</span></div>
        {view === 'Overview' && <>
          <div className="slot-metrics"><div><Sun size={19} /><span>Renewable available</span><strong>{number(current.renewable_available_mw)} <small>MW</small></strong><Sparkline data={workspace.replay.slot_states.map(s => s.renewable_available_mw)} slot={slot} /></div><div><Activity size={19} /><span>Industrial demand</span><strong>{number(current.demand_mw)} <small>MW</small></strong><Sparkline data={workspace.replay.slot_states.map(s => s.demand_mw)} slot={slot} color="var(--cyan)" /></div><div><Battery size={19} /><span>Stored energy</span><strong>{number(Object.values(current.bess_energy_mwh).reduce((total, value) => total + value, 0))} <small>MWh</small></strong><Sparkline data={workspace.replay.slot_states.map(s => Object.values(s.bess_energy_mwh).reduce((t, v) => t + v, 0))} slot={slot} /></div><div><Wallet size={19} /><span>Scenario price</span><strong>{number(path.price_usd_per_mwh[slot])} <small>USD/MWh</small></strong><Sparkline data={path.price_usd_per_mwh} slot={slot} color="var(--amber)" /></div></div>
          <div style={{ position: 'relative' }}>
            <div className="scene-scale-toggle" role="group" aria-label="3D scale"><button className={sceneScale === 'unit' ? 'active' : ''} aria-pressed={sceneScale === 'unit'} onClick={() => setSceneScale('unit')}>Unit</button><button className={sceneScale === 'city' ? 'active' : ''} aria-pressed={sceneScale === 'city'} onClick={() => setSceneScale('city')}>City</button></div>
            <Suspense fallback={<div className="scene-loading">Loading portfolio geometry...</div>}><Scene assets={workspace.portfolio.assets} slot={current} generation={workspace.asset_series.generation_mw} selected={selected} onSelect={setSelected} availability={workspace.asset_series.bess_available} scale={sceneScale} fleetUnits={workspace.scale_projection.fleet_units} theme={theme} /></Suspense>
          </div>
          <div className="asset-selection">{selectedAsset ? <><strong>{selectedAsset.asset_id}</strong><span>{selectedAsset.kind.replaceAll('_', ' ')}</span><span>{selectedAsset.kind === 'bess' ? `${number(current.bess_energy_mwh[selectedAsset.asset_id])} MWh / ${number((current.bess_soc_fraction[selectedAsset.asset_id] ?? 0) * 100)}% SoC` : selectedAsset.kind === 'load_class' ? `${number(path.demand_mw[selectedAsset.asset_id]?.[slot])} MW demand` : path.renewable_available_mw[selectedAsset.asset_id] ? `${number(path.renewable_available_mw[selectedAsset.asset_id][slot])} MW available` : `${number(current.flow_mw)} MW tie flow`}</span></> : <><strong>Portfolio state</strong><span>Import {number(current.import_mw)} MW</span><span>Export {number(current.export_mw)} MW</span><span>Tie capacity {number(workspace.asset_series.tie_capacity_mw[slot])} MW</span><span>Curtailment {number(current.curtailment_mwh)} MWh</span></>}</div>
          <LiveControl open={liveControl} onToggle={() => { setLiveControl(v => !v); if (!liveControl) toast('Live control engaged — driving the simulated twin', 'ok'); }} twin={twin} setTwin={setTwin} current={current} price={path.price_usd_per_mwh[slot]} onReset={() => { setTwin({ bess: 0, dr: false, curtail: 0, load: 0 }); toast('Twin reset to replay baseline'); }} />
          <Trend slots={workspace.replay.slot_states} slot={slot} onChange={setSlot} />
          <div className="overview-bottom"><section><div className="section-heading"><h2>Safety exceptions</h2><button className="text-button" onClick={() => setView('Safety')}>All findings <ChevronRight size={16} /></button></div>{rejected.map(item => <button className="exception-row" key={item.decision_id} onClick={() => { setSlot(item.slot_index); setView('Safety'); }}><ShieldCheck size={17} /><span>Slot {item.slot_index}<small>{item.status.replaceAll('_', ' ')}</small></span><span>{item.violations.length} findings</span><ChevronRight size={16} /></button>)}</section><section><div className="section-heading"><h2>Whole-replay outcomes</h2><span className="meta">SYNTHETIC / DERIVED</span></div><div className="outcome-row"><span>Net cost</span><strong>{number(metric('net_cost_usd')?.value)} USD</strong></div><div className="outcome-row"><span>Critical unserved</span><strong>{number(metric('critical_unserved_mwh')?.value)} MWh</strong></div><div className="outcome-row"><span>Priced demand response</span><strong>{number(metric('dr_cost_usd')?.value)} USD</strong></div><div className="warning">G3 / cross-scenario commitment gap remains open</div></section></div>
        </>}
        {view === 'Assets' && (() => { const kinds = ['all', ...Array.from(new Set(workspace.portfolio.assets.map(a => a.kind)))]; const filtered = workspace.portfolio.assets.filter(a => (assetKind === 'all' || a.kind === assetKind) && a.asset_id.toLowerCase().includes(assetQuery.trim().toLowerCase())); return <section><div className="section-heading"><h2>Generation, storage and consumers</h2><span className="meta">CANONICAL DEMO ROSTER</span></div><div className="asset-filter"><div className="asset-search"><Search size={15} /><input placeholder="Search assets..." value={assetQuery} onChange={e => setAssetQuery(e.target.value)} aria-label="Search assets" /></div><div className="segmented">{kinds.map(k => <button key={k} className={assetKind === k ? 'active' : ''} aria-pressed={assetKind === k} onClick={() => setAssetKind(k)}>{k === 'all' ? 'All' : k.replaceAll('_', ' ')}</button>)}</div><span className="meta">{filtered.length} / {workspace.portfolio.assets.length}</span></div><div className="table-scroll"><table><thead><tr><th>Asset</th><th>Type</th><th>Capacity</th><th>Slot value</th><th>Source</th></tr></thead><tbody>{filtered.map(asset => <tr key={asset.asset_id}><td><button className="text-button" onClick={() => { setSelected(asset.asset_id); setView('Overview'); }}>{asset.asset_id}</button></td><td>{asset.kind.replaceAll('_', ' ')}</td><td>{number(asset.kind === 'bess' ? asset.capacity_mwh : asset.capacity_mw)} {asset.kind === 'bess' ? 'MWh' : 'MW'}</td><td>{asset.kind === 'bess' ? `${number(current.bess_energy_mwh[asset.asset_id])} MWh` : path.renewable_available_mw[asset.asset_id] ? `${number(path.renewable_available_mw[asset.asset_id][slot])} MW available` : path.demand_mw[asset.asset_id] ? `${number(path.demand_mw[asset.asset_id][slot])} MW demand` : `${number(current.flow_mw)} MW tie flow`}</td><td><span className="meta">SYNTHETIC REPLAY</span></td></tr>)}{filtered.length === 0 && <tr><td colSpan={5} className="table-empty">No assets match the filter.</td></tr>}</tbody></table></div></section>; })()}
        {view === 'Decisions' && <><section className="decision-record"><div className="section-heading"><h2>Recorded slot decision</h2><span className={`decision-status ${decision?.status === 'ACCEPTED' ? 'accepted' : 'rejected'}`}>{decision?.status ?? 'UNAVAILABLE'}</span></div><p className="mono">{decision?.decision_id}</p><div className="detail-grid"><div><span>Applied in software replay</span><strong>{current.action_applied ? 'Yes / synthetic approval' : 'No'}</strong></div><div><span>Critical unserved</span><strong>{number(current.critical_unserved_mwh)} MWh</strong></div><div><span>Recorded events</span><strong>{current.event_ids.length}</strong></div></div>{decision?.violations.map((item, index) => <div className="finding" key={index}><strong>{item.code}</strong><span>{item.message}</span><small>{item.severity}</small></div>)}</section><section><div className="section-heading"><h2>Interactive proposal desk</h2><span className="meta">{session?.actor_id}</span></div><div className="desk-controls"><label>Policy<select value={policy} onChange={event => setPolicy(event.target.value)} disabled={busy}><option value="balanced">Balanced</option><option value="reliability_first">Reliability first</option><option value="cost_first">Cost first</option></select></label><button className="primary" disabled={busy || !session?.roles.includes('operator')} onClick={() => action(createInteractive)}><RefreshCw size={16} />Create replay</button><button disabled={busy || !interactive || !session?.roles.includes('operator') || interactive.slot_index >= 96} onClick={() => action(planInteractive)}><Activity size={16} />Plan and validate</button></div>{interactive && <p className="mono">{interactive.replay_id} / interactive slot {interactive.slot_index} / {interactiveDecision?.status ?? 'PENDING'}</p>}<label className="reason-label">Approval reason<textarea value={reason} onChange={event => setReason(event.target.value)} maxLength={512} rows={2} /></label><div className="desk-actions"><button className="primary" disabled={!canApprove} onClick={() => action(() => approveInteractive(true))}><Check size={16} />Approve</button><button disabled={!proposal || !reason.trim() || busy || !session?.roles.includes('approver')} onClick={() => action(() => approveInteractive(false))}><X size={16} />Reject</button><button disabled={!approvalToken || busy || !session?.roles.includes('operator')} onClick={() => action(applyInteractive)}><ArrowRight size={16} />Advance approved slot</button>{busy && <span role="status">Engine request running...</span>}</div><div className="warning">Independent approver required. Self-approval is rejected by the API.</div></section></>}
        {view === 'Economics' && <><div className="economic-metrics"><MetricTile title="Net replay cost" metric={metric('net_cost_usd')} /><MetricTile title="Committed market revenue" metric={metric('market_revenue_usd')} accent="mint" /><MetricTile title="Demand-response cost" metric={metric('dr_cost_usd')} /><MetricTile title="Critical unserved energy" metric={metric('critical_unserved_mwh')} accent="red" /></div><div className="warning">Prices, DSM rates, carbon factor and contracts are scenario assumptions. Physical export is not automatically market revenue.</div><div className="table-scroll"><table><thead><tr><th>Metric</th><th>Value</th><th>Unit</th><th>Status / provenance</th></tr></thead><tbody>{workspace.metrics.values.map(item => <tr key={item.name}><td className="mono">{item.name}</td><td>{number(item.value, 6)}</td><td>{item.unit ?? 'Not provided'}</td><td>{item.status} / {item.provenance.label}</td></tr>)}</tbody></table></div></>}
        {view === 'Safety' && <><div className="safety-heading"><ShieldCheck size={30} /><div><h2>Rejection is an operating outcome.</h2><p>{rejected.length} recorded unsafe decisions / {number(metric('critical_unserved_mwh')?.value)} MWh critical unserved</p></div></div><div className="warning">G3 non-anticipativity warning: committed decisions can deviate across scenarios. Multi-period optimizer work remains required.</div><section><div className="section-heading"><h2>Recorded rejected slots</h2></div>{rejected.map(item => <button key={item.decision_id} className={`exception-row ${slot === item.slot_index ? 'selected' : ''}`} onClick={() => { setPlaying(false); setSlot(item.slot_index); }}><ShieldCheck size={17} /><span>Slot {item.slot_index}<small>{item.status}</small></span><span>{item.violations.length} findings</span><ChevronRight size={16} /></button>)}</section><section className="decision-record"><div className="section-heading"><h2>Slot {slot} / validator evidence</h2><span className="decision-status">{decision?.status ?? 'UNAVAILABLE'}</span></div>{decision?.violations.length ? decision.violations.map((item, index) => <div className="finding" key={index}><strong>{item.code}</strong><span>{item.message}</span><small>{item.severity}</small></div>) : <p>No validator findings recorded for this slot.</p>}<div className="detail-grid"><div><span>Action applied</span><strong>{current.action_applied ? 'Yes' : 'No'}</strong></div><div><span>Critical unserved</span><strong>{number(current.critical_unserved_mwh)} MWh</strong></div><div><span>Noncritical unserved</span><strong>{number(current.noncritical_unserved_mwh)} MWh</strong></div></div>{decision?.recovery_options.map(option => <p key={option}>{option}</p>)}</section></>}
        {view === 'Evidence' && <><div className="evidence-toolbar"><h2>Replay certificate and provenance</h2><button onClick={() => action(async () => { setVerification(await request<typeof verification>(token, `/certificates/${encodeURIComponent(workspace.certificate.certificate_id)}/verification`)); })} disabled={busy}><FileCheck2 size={17} />Verify certificate</button><button onClick={download}><Download size={17} />Export evidence</button></div><div className={`verification-state ${verification?.verified ? 'verified' : ''}`}><FileCheck2 size={30} /><div><strong>{verification?.verified ? 'Certificate verification passed' : 'Certificate not verified in this session'}</strong><span>{verification ? `Persistence consistent: ${verification.persistence_consistent}` : 'Verification status is not inferred from a signature.'}</span></div></div><dl className="evidence-list"><dt>Replay ID</dt><dd>{workspace.replay.replay_id}</dd><dt>Certificate ID</dt><dd>{workspace.certificate.certificate_id}</dd><dt>Unsigned digest</dt><dd>{workspace.certificate.unsigned_digest}</dd><dt>Fixture set hash</dt><dd>{workspace.replay.fixture_set_hash}</dd><dt>Input scope</dt><dd>{workspace.scope}</dd><dt>Layout</dt><dd>{workspace.layout}</dd><dt>Known limitations</dt><dd>{workspace.limitations.join(' / ')}</dd></dl></>}
        {view === 'Agent' && <AgentView decision={agent} busy={agentBusy} error={agentError} slot={slot} />}
        {['Storage', 'Forecasts', 'Timeline', 'Science', 'Bench', 'Scale', 'Hardware'].includes(view) && <Suspense fallback={<p role="status">Loading evidence view...</p>}><ResearchPanels view={view} workspace={workspace} slot={slot} onSlot={value => { setPlaying(false); setSlot(value); }} theme={theme} /></Suspense>}
        {view === 'Economics' && <Suspense fallback={<p role="status">Loading baseline comparison...</p>}><BaselineComparison workspace={workspace} /></Suspense>}
      </main><footer><span><Sun size={14} />5 solar</span><span><Wind size={14} />3 wind</span><span><Battery size={14} />2 BESS</span><span className="footer-scope">Signed replay evidence · simulation / replay · no live control / no market submission</span></footer>
      {paletteOpen && <CommandPalette query={paletteQuery} setQuery={setPaletteQuery} views={views} onPick={name => { setView(name); setPaletteOpen(false); setPaletteQuery(''); }} actions={[
        { label: playing ? 'Pause replay' : 'Play replay', run: () => setPlaying(p => !p) },
        { label: 'Jump to first rejected slot', run: () => { const r = rejected[0]; if (r) { setSlot(r.slot_index); setView('Safety'); } else toast('No rejected slots in this replay', 'warn'); } },
        { label: 'Reset slot to 00:00', run: () => setSlot(0) },
        { label: theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme', run: () => setTheme(t => t === 'dark' ? 'light' : 'dark') },
        { label: sceneScale === 'unit' ? 'Switch 3D to city scale' : 'Switch 3D to unit scale', run: () => setSceneScale(s => s === 'unit' ? 'city' : 'unit') },
        { label: 'Export evidence JSON', run: download },
      ]} onClose={() => { setPaletteOpen(false); setPaletteQuery(''); }} />}
      <div className="toast-stack" role="status" aria-live="polite">{toasts.map(item => <div key={item.id} className={`toast ${item.kind}`}>{item.kind === 'ok' ? <Check size={15} /> : item.kind === 'warn' ? <ShieldCheck size={15} /> : <X size={15} />}{item.text}</div>)}</div>
    </>}
  </div>;
}