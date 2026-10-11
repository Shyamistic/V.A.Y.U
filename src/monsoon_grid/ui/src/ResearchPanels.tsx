import { lazy, Suspense, useState } from 'react';
import { Area, AreaChart, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Battery, Check, Cpu, CircuitBoard, FlaskConical, Network, ScalingIcon, ShieldAlert, X } from 'lucide-react';
import type { Metric, MonteCarloResult, Workspace } from './types';

const HardwareScene = lazy(() => import('./HardwareScene').then(module => ({ default: module.HardwareScene })));
const HARDWARE_IMAGE = 'assets/hardware-bom.png';

const format = (value: number | null | undefined, digits = 2) => value == null ? 'Unavailable' : value.toLocaleString('en-US', { maximumFractionDigits: digits });
const clock = (slot: number) => `${String(Math.floor(slot / 4)).padStart(2, '0')}:${String(slot % 4 * 15).padStart(2, '0')}`;
function ChartAxes() {
  return <><CartesianGrid stroke="#dfe7e0" strokeDasharray="3 5" vertical={false} /><XAxis dataKey="slot" tickFormatter={clock} minTickGap={40} tick={{ fontSize: 10 }} /><YAxis tick={{ fontSize: 10 }} width={45} /><Tooltip labelFormatter={value => `${clock(Number(value))} UTC`} contentStyle={{ fontSize: 12, borderRadius: 4, borderColor: '#c3d2c7' }} /><Legend wrapperStyle={{ fontSize: 11, paddingTop: 15 }} /></>;
}

export function ResearchPanels({ view, workspace, slot, onSlot, theme }: { view: string; workspace: Workspace; slot: number; onSlot: (slot: number) => void; theme: 'light' | 'dark' }) {
  const [forecastId, setForecastId] = useState(workspace.forecasts[0]?.forecast_id ?? '');
  const [benchCase, setBenchCase] = useState('normal');
  const [experiment, setExperiment] = useState('full_eval');
  const current = workspace.replay.slot_states[slot];

  if (view === 'Forecasts') {
    const band = workspace.forecasts.find(item => item.forecast_id === forecastId) ?? workspace.forecasts[0];
    const forecastData = band?.p50.map((median, index) => ({ slot: index, p10: band.p10[index], p50: median, p90: band.p90[index] })) ?? [];
    const scenarioData = workspace.replay.slot_states.map(item => {
      const row: Record<string, number> = { slot: item.slot_index };
      for (const path of workspace.replay.scenario_set.paths) row[path.scenario_id] = Object.values(path.renewable_available_mw).reduce((total, series) => total + series[item.slot_index], 0);
      return row;
    });
    return <><div className="panel-controls"><label>Forecast series<select value={forecastId} onChange={event => setForecastId(event.target.value)}>{workspace.forecasts.map(item => <option key={item.forecast_id} value={item.forecast_id}>{item.asset_id ?? item.variable} / {item.variable}</option>)}</select></label><span className="meta">{band?.model_version} / {band?.unit}</span></div><div className="warning">Baseline fixture bands, not calibrated Phase 2 predictions. No empirical coverage claim. Scenario paths below are before outage/derate adjustments.</div><div className="plot"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={forecastData}><ChartAxes /><Area dataKey="p90" name="P90 baseline" fill="#e2ecdf" stroke="#9daf99" /><Area dataKey="p10" name="P10 baseline" fill="#f2f5f3" stroke="#9daf99" /><Line dataKey="p50" name="P50 baseline" stroke="#187a60" dot={false} strokeWidth={2} /><ReferenceLine x={slot} stroke="#ad493d" /></ComposedChart></ResponsiveContainer></div><div className="section-heading"><h2>Joint renewable scenario paths</h2><span className="meta">MW / NO PROBABILITY ATTESTATION</span></div><div className="plot"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={scenarioData}><ChartAxes />{workspace.replay.scenario_set.paths.map((path, index) => <Line key={path.scenario_id} dataKey={path.scenario_id} stroke={['#187a60', '#ba9634', '#38859c', '#ac5144', '#6a7364'][index % 5]} dot={false} strokeWidth={path.scenario_id === workspace.replay.realized_path_id ? 3 : 1} />)}<ReferenceLine x={slot} stroke="#ad493d" /></ComposedChart></ResponsiveContainer></div></>;
  }

  if (view === 'Storage') {
    const assets = workspace.portfolio.assets.filter(item => item.kind === 'bess');
    const series = workspace.replay.slot_states.map(item => ({ slot: item.slot_index, ...item.bess_energy_mwh }));
    return <><div className="storage-roster">{assets.map(asset => <div className="storage-item" key={asset.asset_id}><Battery size={25} /><h2>{asset.asset_id}</h2><span className={`decision-status ${workspace.asset_series.bess_available[asset.asset_id]?.[slot] ? 'accepted' : 'rejected'}`}>{workspace.asset_series.bess_available[asset.asset_id]?.[slot] ? 'AVAILABLE' : 'OUTAGE'}</span><strong>{format(current.bess_energy_mwh[asset.asset_id])} <small>MWh</small></strong><meter min="0" max="1" value={current.bess_soc_fraction[asset.asset_id]} aria-label={`${asset.asset_id} state of charge`} /><p>{format(current.bess_soc_fraction[asset.asset_id] * 100)}% SoC / {format(asset.capacity_mwh)} MWh capacity</p><div className="meta">RESERVE {format(asset.reserve_mwh)} MWh / SOFTWARE TWIN</div></div>)}</div><div className="plot"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={series}><ChartAxes />{assets.map((asset, index) => <Line key={asset.asset_id} dataKey={asset.asset_id} stroke={index ? '#38859c' : '#187a60'} dot={false} strokeWidth={2} />)}{assets.map((asset, index) => <ReferenceLine key={asset.asset_id} y={asset.reserve_mwh} stroke={index ? '#38859c' : '#187a60'} strokeDasharray="4 4" />)}<ReferenceLine x={slot} stroke="#ad493d" /></ComposedChart></ResponsiveContainer></div><div className="warning">Energy/SoC and availability are replay state. Throughput is a degradation proxy, not measured state of health or warranty certification.</div></>;
  }

  if (view === 'Timeline') return <><div className="section-heading"><h2>Weather, demand, outages and recovery</h2><span className="meta">VERSIONED SCENARIO EVENTS</span></div><div className="event-timeline">{workspace.events.map(event => <button key={event.event_id} className={`timeline-row ${event.start_slot <= slot && slot <= event.end_slot ? 'selected' : ''}`} onClick={() => onSlot(event.start_slot)}><span className="timeline-time">{clock(event.start_slot)}<small>to {clock(event.end_slot)} UTC</small></span><div><strong>{event.event_type.replaceAll('_', ' ')}</strong><span>{event.affected_ids.join(' / ') || 'Portfolio'}</span><small>{Object.entries(event.details).map(([key, value]) => `${key}: ${String(value)}`).join(' / ')}</small></div><span className="meta">SLOTS {event.start_slot}-{event.end_slot}</span></button>)}</div></>;

  if (view === 'Science') {
    const report = workspace.science.reports.find(item => item.experiment === experiment && item.experiment !== 'fetch_datasets') ?? workspace.science.reports.find(item => item.experiment !== 'fetch_datasets') ?? workspace.science.reports[0];
    const selectable = workspace.science.reports.filter(item => item.experiment !== 'fetch_datasets');
    return <><div className="science-banner"><FlaskConical size={27} /><div><h2>Research evidence, not plant validation</h2><p>Stored experiments / chronological latest run / Phase 2 not integrated into dispatch</p></div></div><div className="warning">Synthetic results validate machinery, numerical demonstrations and interfaces only. No Indian-plant or hardware validation claim.</div><div className="panel-controls"><label>Recorded experiment<select value={experiment} onChange={event => setExperiment(event.target.value)}>{selectable.map(item => <option key={item.experiment} value={item.experiment}>{item.experiment}</option>)}</select></label><span className="meta">{String(report?.result.created_at ?? 'Unavailable')}</span></div>{report ? <><div className="detail-grid"><div><span>Run ID</span><strong className="mono">{String(report.result.run_id)}</strong></div><div><span>Measured experiment duration</span><strong>{format(Number(report.result.wall_clock_seconds))} s</strong></div><div><span>Recorded runs</span><strong>{report.history_count}</strong></div></div><p className="mono">{report.artifact}</p>{report.experiment === 'monte_carlo' ? <MonteCarloView result={report.result as unknown as MonteCarloResult} /> : null}<details className="artifact-details" open><summary>Recorded result</summary><pre>{JSON.stringify(report.result, null, 2)}</pre></details><p className="hash">Artifact digest: {report.artifact_digest}</p></> : <p>Scientific artifacts unavailable in this deployment.</p>}{workspace.science.issues.map(issue => <p className="error" key={issue}>{issue}</p>)}</>;
  }

  if (view === 'Bench') {
    const frame = workspace.hardware.cases.find(item => item.case_id === benchCase)?.frame;
    return <><div className="science-banner"><Cpu size={27} /><div><h2>Optional integration bench</h2><p>{workspace.hardware.mode} / {workspace.hardware.connection} / no HIL or PHIL claim</p></div></div><div className="warning">No physical device connected. Voltage, current, timestamps and relay states below are deterministic emulator output. Cloud and browser are outside any future local safety boundary.</div><div className="segmented" role="group" aria-label="Software bench case">{workspace.hardware.cases.map(item => <button key={item.case_id} aria-pressed={benchCase === item.case_id} className={benchCase === item.case_id ? 'active' : ''} onClick={() => setBenchCase(item.case_id)}>{item.case_id.replaceAll('_', ' ')}</button>)}</div>{frame && <><div className="bench-flow"><div><Cpu size={29} /><strong>12 V model</strong><span>Declared source envelope</span></div><span className="flow-line" /><div className={frame.failsafe_tripped ? 'bench-node tripped' : 'bench-node'}><ShieldAlert size={29} /><strong>{frame.bench_state.replaceAll('_', ' ')}</strong><span>Software interlock</span></div><span className="flow-line" /><div><Battery size={29} /><strong>Virtual BESS</strong><span>No battery hardware</span></div></div><div className="detail-grid"><div><span>Simulated bus voltage</span><strong>{format(frame.bus_voltage_v)} V</strong></div><div><span>Simulated current</span><strong>{format(frame.bus_current_a, 3)} A</strong></div><div><span>Simulated power</span><strong>{format(frame.bus_power_w)} W</strong></div></div><div className="relay-states">{[['Critical load', frame.relay_critical_enabled], ['Flexible load A', frame.relay_flex_a_enabled], ['Flexible load B', frame.relay_flex_b_enabled]].map(([name, enabled]) => <div key={String(name)}>{enabled ? <Check size={18} /> : <X size={18} />}<span>{name}</span><strong>{enabled ? 'ENABLED' : 'DISCONNECTED'}</strong></div>)}</div><p className="hash">Synthetic frame digest: {frame.digest}</p></>}<div className="section-heading"><h2>Declared emulator envelope</h2></div><div className="table-scroll"><table><tbody>{Object.entries(workspace.hardware.config).map(([name, value]) => <tr key={name}><td className="mono">{name}</td><td>{value}</td><td>Configuration, not a measurement</td></tr>)}</tbody></table></div></>;
  }

  if (view === 'Scale') {
    const projection = workspace.scale_projection;
    const assetLine = (counts: Record<string, number>) => ['solar_farm', 'wind_farm', 'bess', 'load_class'].map(kind => `${counts[kind] ?? 0} ${kind.replace('_farm', '').replace('_class', '').replace('bess', 'BESS')}`).join(' / ');
    return <><div className="scale-banner"><ScalingIcon size={27} /><div><h2>One validated unit, projected to city scale</h2><p>The problem statement operates a single portfolio. A city operator runs many. This projects the real unit x {projection.fleet_units} and states exactly what holds.</p></div></div>
      <div className="warning">{projection.disclaimer}</div>
      <div className="scale-cards">
        <div className="scale-card"><h3>Operating unit</h3><span className="tag">VALIDATED REPLAY</span><div className="big">1x</div><ul>{projection.comparison.map(row => <li key={row.metric}><span>{row.metric}</span><strong>{format(row.unit_value)} {row.unit}</strong></li>)}</ul><p className="meta" style={{ marginTop: 14 }}>{assetLine(projection.unit_asset_counts)}</p></div>
        <div className="scale-connector"><Network size={26} /><span>x{projection.fleet_units}</span></div>
        <div className="scale-card city"><h3>City projection</h3><span className="tag">DECLARED LINEAR PROJECTION</span><div className="big">{projection.fleet_units}x</div><ul>{projection.comparison.map(row => <li key={row.metric}><span>{row.metric} <span className={`preserved-flag ${row.preserved_under_replication ? 'yes' : 'no'}`}>{row.preserved_under_replication ? 'scales' : 'needs study'}</span></span><strong>{format(row.city_value)} {row.unit}</strong></li>)}</ul><p className="meta" style={{ marginTop: 14 }}>{assetLine(projection.city_asset_counts)}</p></div>
      </div>
      <div className="preserve-grid">
        <div className="preserve-col keep"><h3><Check size={16} />Preserved under replication</h3><ul>{projection.preserved_properties.map(item => <li key={item}>{item}</li>)}</ul></div>
        <div className="preserve-col restudy"><h3><ShieldAlert size={16} />Requires a genuine multi-site study</h3><ul>{projection.requires_restudy.map(item => <li key={item}>{item}</li>)}</ul></div>
      </div>
      <div className="section-heading"><h2>What changes per metric at scale</h2><span className="meta">UNIT -&gt; CITY / HONEST BASIS</span></div>
      <div className="table-scroll"><table><thead><tr><th>Metric</th><th>Unit</th><th>City projection</th><th>Linear?</th><th>Why</th></tr></thead><tbody>{projection.comparison.map(row => <tr key={row.metric}><td>{row.metric}</td><td className="mono">{format(row.unit_value)} {row.unit}</td><td className="mono">{format(row.city_value)} {row.unit}</td><td><span className={`preserved-flag ${row.preserved_under_replication ? 'yes' : 'no'}`}>{row.preserved_under_replication ? 'YES' : 'NO'}</span></td><td>{row.note}</td></tr>)}</tbody></table></div>
      <div className="section-heading"><h2>Proposed hardware nodes to streamline operation</h2><span className="meta">PROPOSAL / NOT BUILT</span></div>
      <div className="node-topology">{projection.hardware_nodes.map(node => <div key={node.node_id} className={`node-card ${node.role}`}><h4>{node.node_id}</h4><span className="role">{node.role.replaceAll('_', ' ')}</span><div className="sensors">{node.sensors.map(sensor => <span key={sensor}>{sensor}</span>)}</div><div className="uplink">{node.uplink}</div><p>{node.note}</p></div>)}</div>
      <div className="warning">Edge nodes are telemetry-only by default. Approval, the independent validator, and local safety interlocks remain mandatory per site and are never delegated to the cloud or an edge node alone.</div></>;
  }

  if (view === 'Hardware') {
    return <><div className="science-banner"><CircuitBoard size={27} /><div><h2>Desk-scale hardware prototype (proposal)</h2><p>Built around the real software bench envelope. No device is connected; nothing below is a measurement.</p></div></div>
      <span className="proposal-flag"><ShieldAlert size={13} />PROPOSAL / NOT BUILT / NO HIL OR PHIL CLAIM</span>
      <div className="section-heading"><h2>Concept testboard</h2><span className="meta">AI-GENERATED CONCEPT / NOT A BUILT DEVICE</span></div>
      <img className="bom-image" src={HARDWARE_IMAGE} alt="AI-generated concept of the proposed 12 V low-voltage validation testboard, illustrative only" onError={event => { const el = event.currentTarget; el.style.display = 'none'; el.insertAdjacentHTML('afterend', '<div class="bom-image-placeholder">Drop an AI-generated concept image at ui/public/assets/hardware-bom.png. The procedural render below is always available.</div>'); }} />
      <div className="section-heading"><h2>Interactive component render</h2><span className="meta">PROCEDURAL / ILLUSTRATIVE / DRAG TO ORBIT</span></div>
      <Suspense fallback={<div className="bom-image-placeholder">Loading component render...</div>}><HardwareScene theme={theme} /></Suspense>
      <div className="section-heading"><h2>Bill of materials</h2><span className="meta">INDICATIVE / SUPERVISED LOW-VOLTAGE DEMONSTRATOR</span></div>
      <div className="table-scroll"><table><thead><tr><th>#</th><th>Component</th><th>Role in the loop</th><th>Qty</th><th>Safety note</th></tr></thead><tbody>{BOM.map((item, index) => <tr key={item.part}><td className="mono">{index + 1}</td><td>{item.part}</td><td>{item.role}</td><td className="mono">{item.qty}</td><td>{item.safety}</td></tr>)}</tbody></table></div>
      <div className="section-heading"><h2>How a hardware-in-the-loop run would work</h2><span className="meta">PROPOSED PROCEDURE</span></div>
      <div className="node-topology">{HIL_STEPS.map((step, index) => <div key={step.title} className="node-card site_edge"><h4>{index + 1}. {step.title}</h4><p>{step.body}</p></div>)}</div>
      <div className="warning">The declared envelope below drives the existing software emulator (12 V, 1 A hard current limit, 2 s watchdog). A real build must be reviewed by a qualified supervisor for components, thermal limits and wiring. Cloud and browser are outside any future local safety boundary; interlocks stay on the device.</div>
      <div className="table-scroll"><table><tbody>{Object.entries(workspace.hardware.config).map(([name, value]) => <tr key={name}><td className="mono">{name}</td><td>{value}</td><td>Declared configuration, not a measurement</td></tr>)}</tbody></table></div></>;
  }
  return null;
}

function MonteCarloView({ result }: { result: MonteCarloResult }) {
  // Reads only top-level fields: experiment_reports strips 'config'/'environment'
  // before the UI sees the report, so n_days/seed/day_seeds/per_operator_means/
  // paired_comparisons/orchestrator_loses_on are all consumed from the top level.
  const operators = result.operators ?? [];
  const metrics = result.metrics ?? [];
  const baselines = operators.filter(op => op !== 'orchestrator');
  const losses = result.orchestrator_loses_on ?? [];
  return <>
    <div className="warning">{result.note}</div>
    <div className="detail-grid"><div><span>Seeded days</span><strong>{result.n_days}</strong></div><div><span>Config seed</span><strong className="mono">{result.seed}</strong></div><div><span>Policy mode</span><strong>{result.policy_mode}</strong></div></div>
    <div className="section-heading"><h2>Per-operator mean KPIs</h2><span className="meta">MEAN OVER {result.n_days} SEEDED DAYS</span></div>
    <div className="table-scroll"><table><thead><tr><th>Metric</th>{operators.map(op => <th key={op}>{op}</th>)}</tr></thead><tbody>{metrics.map(metric => <tr key={metric}><td className="mono">{metric}</td>{operators.map(op => <td key={op}>{format(result.per_operator_means?.[op]?.[metric] ?? null, 3)}</td>)}</tr>)}</tbody></table></div>
    <div className="section-heading"><h2>Paired mean difference vs orchestrator</h2><span className="meta">BASELINE - ORCHESTRATOR / 95% CI / LOWER IS BETTER</span></div>
    <div className="table-scroll"><table><thead><tr><th>Baseline</th><th>Metric</th><th>Mean diff</th><th>95% CI</th><th>p-value</th><th>Orchestrator wins</th></tr></thead><tbody>{baselines.flatMap(baseline => metrics.map(metric => { const c = result.paired_comparisons?.[baseline]?.[metric]; return c ? <tr key={`${baseline}:${metric}`}><td>{baseline}</td><td className="mono">{metric}</td><td>{format(c.mean_difference, 3)}</td><td className="mono">[{format(c.ci95_low, 2)}, {format(c.ci95_high, 2)}]</td><td>{format(c.p_value, 4)}</td><td><span className={`preserved-flag ${c.orchestrator_wins ? 'yes' : 'no'}`}>{c.orchestrator_wins ? 'YES' : 'NO'}</span></td></tr> : null; }))}</tbody></table></div>
    <div className="section-heading"><h2>Where a baseline beats the orchestrator</h2><span className="meta">REPORTED, NEVER SUPPRESSED</span></div>
    {losses.length ? <div className="table-scroll"><table><thead><tr><th>Baseline</th><th>Metric</th></tr></thead><tbody>{losses.map(([baseline, metric]) => <tr key={`${baseline}:${metric}`}><td>{baseline}</td><td className="mono">{metric}</td></tr>)}</tbody></table></div> : <p>No baseline beats the orchestrator on any reported metric.</p>}
  </>;
}

const BOM = [
  { part: 'ESP32-WROOM edge node', role: 'Per-site sensing + relay command; mirrors the software bench interlocks', qty: '1 / site', safety: 'Low-voltage logic only; no mains contact' },
  { part: 'Mini-PV cell + light sensor', role: 'Irradiance proxy feeding the generation estimate', qty: '1', safety: 'Isolated sensing channel' },
  { part: '3-channel relay board', role: 'Switch critical / flexible-A / flexible-B loads under priced-DR rules', qty: '1', safety: 'Critical feeder relay is physically non-sheddable' },
  { part: 'Fused bus bars + inline fuses', role: 'Current path with a hard over-current cutout', qty: '2', safety: 'Fuse rating below the 1 A emulator limit' },
  { part: 'Current / voltage sense module', role: 'Bus voltage and current telemetry', qty: '1', safety: 'Calibration evidence retained' },
  { part: 'Fused low-power resistive loads', role: 'Stand-in industrial consumers', qty: '3', safety: 'Thermally rated, enclosed' },
  { part: 'Raspberry-Pi class gateway', role: 'Validates device identity, units, ordering, freshness before export', qty: '1', safety: 'Authenticated batch uplink only' },
  { part: 'Manual master disconnect', role: 'Operator emergency open-all', qty: '1', safety: 'Independent of software and cloud' },
  { part: 'Isolated 12 V bench supply', role: 'Declared source envelope', qty: '1', safety: 'Current-limited, supervised' },
  { part: 'Virtual BESS (software)', role: 'Storage modelled in the twin, not a physical pack', qty: '1', safety: 'No rechargeable chemistry on the bench' },
];

const HIL_STEPS = [
  { title: 'Telemetry capture', body: 'Edge node streams bus voltage, current and the mini-PV proxy to the gateway. No command path is enabled yet.' },
  { title: 'Gateway validation', body: 'The gateway checks device identity, units, sequence, age and duplicates. Bad frames are rejected, never silently patched.' },
  { title: 'Software twin in the loop', body: 'The validated software planner/validator/twin consumes the gateway stream alongside the replay; the engine stays authoritative.' },
  { title: 'Interlock exercise', body: 'Over-current, watchdog timeout, stale/replayed frames and sensor disagreement are injected; the device must trip locally.' },
  { title: 'Approval + evidence', body: 'Any relay change still requires the independent validator and a separate approval; every frame is signed and replayable.' },
];

export function BaselineComparison({ workspace }: { workspace: Workspace }) {
  const baseline = (workspace.summary as unknown as { baseline_metrics?: { values: Metric[] } }).baseline_metrics?.values ?? [];
  const names = ['net_cost_usd', 'critical_unserved_mwh', 'renewable_curtailment_mwh', 'bess_throughput_mwh', 'dr_cost_usd'];
  const series = workspace.replay.slot_states.map(item => ({ slot: item.slot_index, cost: item.cost_usd }));
  return <><div className="section-heading"><h2>Recorded baseline comparison</h2><span className="meta">SAME SYNTHETIC REALIZED PATH / NOT A SAVINGS GUARANTEE</span></div><div className="table-scroll"><table><thead><tr><th>Metric</th><th>Recorded baseline</th><th>Planner replay</th><th>Difference</th></tr></thead><tbody>{names.map(name => { const reference = baseline.find(item => item.name === name), current = workspace.metrics.values.find(item => item.name === name); return <tr key={name}><td className="mono">{name}</td><td>{format(reference?.value)} {reference?.unit}</td><td>{format(current?.value)} {current?.unit}</td><td>{reference?.value != null && current?.value != null ? format(current.value - reference.value) : 'Unavailable'}</td></tr>; })}</tbody></table></div><section><div className="section-heading"><h2>Per-slot cost</h2><span className="meta">USD / DERIVED</span></div><div className="plot compact"><ResponsiveContainer width="100%" height="100%"><AreaChart data={series}><ChartAxes /><Area dataKey="cost" name="Slot cost USD" fill="#dae9df" stroke="#187a60" /></AreaChart></ResponsiveContainer></div></section><div className="section-heading"><h2>Declared delivery obligations</h2><span className="meta">REPORT-ONLY / ASSUMED CONTRACT TERMS</span></div><div className="table-scroll"><table><thead><tr><th>Obligation</th><th>Type</th><th>Commitment</th><th>Penalty assumption</th></tr></thead><tbody>{workspace.obligations.map(item => <tr key={item.obligation_id}><td>{item.obligation_id}</td><td>{item.obligation_type}</td><td>{item.committed_value} {item.committed_unit}</td><td>{item.shortfall_penalty_usd_per_mwh} USD/MWh</td></tr>)}</tbody></table></div></>;
}