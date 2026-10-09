import React, { useState, useEffect, useRef, useCallback } from 'react';
import * as THREE from 'three';

export default function App() {
  const [slot, setSlot] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [policy, setPolicy] = useState<string>('balanced');
  const [eventScenario, setEventScenario] = useState<string>('normal');
  const [decisionState, setDecisionState] = useState<string>('PENDING');
  const [decisionDetail, setDecisionDetail] = useState<string>('Scenario path, validator digest, and human approval are required.');
  const [isApproveDisabled, setIsApproveDisabled] = useState<boolean>(false);
  const [activeAgentTab, setActiveAgentTab] = useState<string>('chief');
  const [simData, setSimData] = useState<any>(null);
  const [deliberation, setDeliberation] = useState<any>(null);

  const fetchSlotState = useCallback(async (slotIdx: number) => {
    try {
      const resp = await fetch(`/api/simulation/state?slot=${slotIdx}`);
      if (!resp.ok) return;
      const json = await resp.json();
      setSimData(json.data);
    } catch (err) {
      console.error(err);
    }
  }, []);

  const fetchDeliberation = useCallback(async (slotIdx: number, pol: string) => {
    try {
      const resp = await fetch('/api/deliberate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot_index: slotIdx, policy_mode: pol }),
      });
      if (!resp.ok) return;
      const json = await resp.json();
      setDeliberation(json.data);
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => {
    fetchSlotState(slot);
    fetchDeliberation(slot, policy);
  }, [slot, policy, fetchSlotState, fetchDeliberation]);

  useEffect(() => {
    let interval: any = null;
    if (isPlaying) {
      interval = setInterval(() => {
        setSlot(prev => (prev >= 95 ? 0 : prev + 1));
      }, 600);
    }
    return () => { if (interval) clearInterval(interval); };
  }, [isPlaying]);

  const handleScenarioSelect = (sc: string) => {
    setEventScenario(sc);
    if (sc === 'stress') {
      setSlot(36);
      setDecisionState('PENDING');
      setIsApproveDisabled(false);
    } else if (sc === 'monsoon_cloud') {
      setSlot(24);
      setDecisionState('PENDING');
      setIsApproveDisabled(false);
    } else if (sc === 'unsafe_proposal') {
      setDecisionState('REJECTED');
      setDecisionDetail('SAFETY FILTER INTERCEPT: Speculative trade blocked mathematically.');
      setIsApproveDisabled(true);
    } else {
      setDecisionState('PENDING');
      setIsApproveDisabled(false);
    }
  };

  const hour = Math.floor(slot / 4);
  const minute = (slot % 4) * 15;
  const timeStr = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} UTC`;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-6">
      <header className="flex justify-between items-center border-b border-slate-800 pb-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold">MonsoonGrid Command Center</h1>
          <p className="text-sm text-slate-400">Autonomous Renewable Portfolio Orchestrator (React + Three.js)</p>
        </div>
        <div className="flex gap-2 text-xs">
          <span className="px-3 py-1 bg-sky-950 border border-sky-500 text-sky-400 rounded font-bold">SIMULATION</span>
          <span className="px-3 py-1 bg-emerald-950 border border-emerald-500 text-emerald-400 rounded font-bold">REPLAY</span>
          <span className="px-3 py-1 bg-amber-950 border border-amber-500 text-amber-400 rounded font-bold">NO LIVE CONTROL</span>
        </div>
      </header>

      {/* Top KPI Bar */}
      <section className="grid grid-cols-2 md:grid-cols-6 gap-4 bg-slate-900 border border-slate-800 p-4 rounded-xl mb-6">
        <div>
          <div className="text-xs text-slate-400 uppercase">Slot / Time</div>
          <div className="text-xl font-bold">Slot {slot} ({timeStr})</div>
        </div>
        <div>
          <div className="text-xs text-slate-400 uppercase">Renewables</div>
          <div className="text-xl font-bold text-emerald-400">{simData ? `${simData.total_renewable_mw.toFixed(1)} MW` : '0.0 MW'}</div>
        </div>
        <div>
          <div className="text-xs text-slate-400 uppercase">Industrial Demand</div>
          <div className="text-xl font-bold">{simData ? `${simData.industrial_demand_mw.total.toFixed(1)} MW` : '64.0 MW'}</div>
        </div>
        <div>
          <div className="text-xs text-slate-400 uppercase">Market Tariff</div>
          <div className="text-xl font-bold">{simData ? `$${simData.market_price_usd.toFixed(2)}/MWh` : '$54.30'}</div>
        </div>
        <div>
          <div className="text-xs text-slate-400 uppercase">Tie Headroom</div>
          <div className="text-xl font-bold text-sky-400">{simData ? `${simData.grid_tie.headroom_mw.toFixed(1)} MW` : '80.0 MW'}</div>
        </div>
        <div>
          <div className="text-xs text-slate-400 uppercase">BESS Storage</div>
          <div className="text-xl font-bold">{simData ? `${(simData.bess_state['bess-a'].energy_mwh + simData.bess_state['bess-b'].energy_mwh).toFixed(1)} MWh` : '25.0 MWh'}</div>
        </div>
      </section>

      {/* Timeline Controls */}
      <section className="bg-slate-900 border border-slate-800 p-4 rounded-xl mb-6">
        <div className="flex flex-wrap gap-4 items-center justify-between">
          <div className="flex gap-2">
            <button className="px-4 py-2 bg-sky-500 text-slate-950 font-bold rounded" onClick={() => setIsPlaying(!isPlaying)}>
              {isPlaying ? '⏸ Pause' : '▶ Play 24h Replay'}
            </button>
            <button className="px-3 py-2 bg-slate-800 border border-slate-700 rounded" onClick={() => setSlot(p => Math.max(0, p - 1))}>⏮ Prev</button>
            <button className="px-3 py-2 bg-slate-800 border border-slate-700 rounded" onClick={() => setSlot(p => Math.min(95, p + 1))}>⏭ Next</button>
          </div>
          <div className="flex-1 max-w-xl">
            <input type="range" min="0" max="95" value={slot} onChange={e => setSlot(parseInt(e.target.value, 10))} className="w-full" />
          </div>
          <div className="flex gap-2">
            <button className={`px-3 py-1 rounded text-sm ${policy === 'reliability_first' ? 'bg-sky-600' : 'bg-slate-800'}`} onClick={() => setPolicy('reliability_first')}>Reliability</button>
            <button className={`px-3 py-1 rounded text-sm ${policy === 'balanced' ? 'bg-sky-600' : 'bg-slate-800'}`} onClick={() => setPolicy('balanced')}>Balanced</button>
            <button className={`px-3 py-1 rounded text-sm ${policy === 'cost_first' ? 'bg-sky-600' : 'bg-slate-800'}`} onClick={() => setPolicy('cost_first')}>Cost</button>
          </div>
        </div>
      </section>

      {/* Scenario buttons */}
      <section className="flex flex-wrap gap-2 mb-6">
        <button className="px-3 py-2 bg-slate-800 rounded text-sm" onClick={() => handleScenarioSelect('normal')}>☀️ Normal</button>
        <button className="px-3 py-2 bg-slate-800 rounded text-sm" onClick={() => handleScenarioSelect('monsoon_cloud')}>🌧️ Monsoon Cloudburst</button>
        <button className="px-3 py-2 bg-slate-800 rounded text-sm" onClick={() => handleScenarioSelect('stress')}>⚠️ BESS-B Outage + Congestion</button>
        <button className="px-3 py-2 bg-red-950 border border-red-500 text-red-400 font-bold rounded text-sm" onClick={() => handleScenarioSelect('unsafe_proposal')}>🛡️ Safety Refusal Demo</button>
      </section>
    </div>
  );
}
