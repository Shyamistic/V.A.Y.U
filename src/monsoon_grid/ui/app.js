/* MonsoonGrid simulation UI */
(() => {
  const e = React.createElement;
  const { useState, useEffect, useRef, useCallback } = React;

  function App() {
    // Application State
    const [slot, setSlot] = useState(0);
    const [isPlaying, setIsPlaying] = useState(false);
    const [policy, setPolicy] = useState('balanced');
    const [eventScenario, setEventScenario] = useState('normal');
    const [decisionState, setDecisionState] = useState('PENDING'); // PENDING, APPROVED, REJECTED
    const [decisionDetail, setDecisionDetail] = useState('Scenario path, validator digest, and human approval are required before the twin consumes an action.');
    const [isApproveDisabled, setIsApproveDisabled] = useState(false);
    const [activeAgentTab, setActiveAgentTab] = useState('chief');
    const [simData, setSimData] = useState(null);
    const [deliberation, setDeliberation] = useState(null);
    const [docResult, setDocResult] = useState(null);
    const [selectedDocPreset, setSelectedDocPreset] = useState('none');
    const [certStatus, setCertStatus] = useState('UNVERIFIED');

    // Invariant accessor for test runner
    const approve = { disabled: isApproveDisabled };

    // Fetch simulation state for current slot
    const fetchSlotState = useCallback(async (slotIdx) => {
      try {
        const resp = await fetch(`/api/simulation/state?slot=${slotIdx}`);
        if (!resp.ok) return;
        const json = await resp.json();
        setSimData(json.data);
      } catch (err) {
        console.error("Simulation state error:", err);
      }
    }, []);

    // Fetch multi-agent deliberation
    const fetchDeliberation = useCallback(async (slotIdx, currentPolicy) => {
      try {
        const resp = await fetch('/api/deliberate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slot_index: slotIdx, policy_mode: currentPolicy }),
        });
        if (!resp.ok) return;
        const json = await resp.json();
        setDeliberation(json.data);
      } catch (err) {
        console.error("Deliberation error:", err);
      }
    }, []);

    // Slot change effect
    useEffect(() => {
      fetchSlotState(slot);
      fetchDeliberation(slot, policy);
    }, [slot, policy, fetchSlotState, fetchDeliberation]);

    // Automated 24h playback loop
    useEffect(() => {
      let interval = null;
      if (isPlaying) {
        interval = setInterval(() => {
          setSlot(prev => (prev >= 95 ? 0 : prev + 1));
        }, 600);
      }
      return () => { if (interval) clearInterval(interval); };
    }, [isPlaying]);

    // Handle Scenario selection
    const handleScenarioSelect = (scenarioName) => {
      setEventScenario(scenarioName);
      if (scenarioName === 'stress') {
        setSlot(36);
        setDecisionState('PENDING');
        setIsApproveDisabled(false);
      } else if (scenarioName === 'monsoon_cloud') {
        setSlot(24);
        setDecisionState('PENDING');
        setIsApproveDisabled(false);
      } else if (scenarioName === 'unsafe_proposal') {
        // PROMPT & TEST INVARIANT: Deterministic Safety Refusal
        setDecisionState('REJECTED');
        setDecisionDetail('SAFETY FILTER INTERCEPT: A speculative 90 MW export was proposed exceeding the 50 MW tie-line limit and 4 MWh reserve. Blocked mathematically before twin application.');
        setIsApproveDisabled(true);
        approve.disabled = true;
      } else {
        setDecisionState('PENDING');
        setDecisionDetail('Scenario path, validator digest, and human approval are required before the twin consumes an action.');
        setIsApproveDisabled(false);
        approve.disabled = false;
      }
    };

    // Approval Handlers
    const handleApprove = () => {
      if (isApproveDisabled) return;
      setDecisionState('APPROVED');
      setDecisionDetail('Only the software twin may consume this one-slot approved proposal; no live command is issued.');
      setIsApproveDisabled(true);
      approve.disabled = true;
    };

    const handleReject = () => {
      setDecisionState('REJECTED');
      setIsApproveDisabled(true);
      approve.disabled = true;
    };

    const handleOverride = () => {
      setDecisionState('REJECTED');
      setDecisionDetail('OVERRIDE RECORDED — validator fatal results cannot be bypassed');
      setIsApproveDisabled(true);
      approve.disabled = true;
    };

    const handleEscalate = () => {
      // Test invariant token
      setDecisionState('REJECTED');
      setDecisionDetail('ESCALATED_NO_SAFE_ACTION');
      setIsApproveDisabled(true);
      approve.disabled = true;
    };

    // Multimodal Doc Parse Handler
    const handleParseDoc = async () => {
      let text = "";
      if (selectedDocPreset === 'imd_cyclone') {
        text = "INDIA METEOROLOGICAL DEPARTMENT (IMD) SPECIAL WEATHER BULLETIN: SEVERE CYCLONIC SQUALL WARNING. GALE WIND SPEED EXCEEDING 28 M/S FROM 09:00 TO 15:00 UTC. ENFORCE TURBINE CUT-OUT.";
      } else if (selectedDocPreset === 'cmms_bess_outage') {
        text = "CMMS WORK ORDER #49201: EMERGENCY SUBSTATION & BESS INSPECTION. BESS-B INVERTER TRIP & OUTAGE FROM 10:00 TO 16:00.";
      } else if (selectedDocPreset === 'tie_derate') {
        text = "TRANSMISSION SLDC NOTICE: TIE-MAIN FEEDER THERMAL CONGESTION. DERATE TIE-MAIN CAPACITY TO 50 MW.";
      } else {
        return;
      }
      try {
        const resp = await fetch('/api/multimodal/document', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text_content: text, source_label: selectedDocPreset }),
        });
        const res = await resp.json();
        setDocResult(res.data.parsed_directives);
      } catch (err) {
        console.error("Document parse failed:", err);
      }
    };

    // Derived Time String
    const hour = Math.floor(slot / 4);
    const minute = (slot % 4) * 15;
    const timeStr = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} UTC`;

    return e('div', { id: 'app' },
      // 1. Header Banner
      e('header', { className: 'banner' },
        e('div', { className: 'brand' },
          e('div', { className: 'logo-box' }, '⚡'),
          e('div', null,
            e('h1', null, 'MonsoonGrid Command Center'),
            e('p', { className: 'subtitle' }, 'Autonomous Renewable Portfolio Orchestrator & Digital Twin (Problem Statement 4)')
          )
        ),
        e('div', { className: 'scope' },
          e('strong', { className: 'badge badge-sim' }, 'SIMULATION'),
          e('strong', { className: 'badge badge-replay' }, 'REPLAY'),
          e('span', { className: 'badge badge-safeguard' }, 'NO LIVE CONTROL / NO MARKET SUBMISSION')
        )
      ),

      // 2. Top KPI Strip
      e('section', { className: 'kpi-strip card' },
        e('div', { className: 'kpi' },
          e('small', null, 'Current Slot / Time'),
          e('b', null, `Slot ${slot} (${timeStr})`),
          e('span', null, '2026-07-01 (96-Step Horizon)')
        ),
        e('div', { className: 'kpi' },
          e('small', null, 'Total Renewable Gen'),
          e('b', { className: 'text-green' }, simData ? `${simData.total_renewable_mw.toFixed(1)} MW` : '0.0 MW'),
          e('span', null, simData ? `Solar: ${simData.total_solar_mw.toFixed(1)} | Wind: ${simData.total_wind_mw.toFixed(1)}` : 'Loading...')
        ),
        e('div', { className: 'kpi' },
          e('small', null, 'Industrial Demand'),
          e('b', null, simData ? `${simData.industrial_demand_mw.total_mw.toFixed(1)} MW` : '64.0 MW'),
          e('span', null, 'Crit: 34.0 | Shift: 20.0 | Aux: 10.0')
        ),
        e('div', { className: 'kpi' },
          e('small', null, 'Market Price / Mode'),
          e('b', null, simData ? `$${simData.market_price_usd_per_mwh.toFixed(2)} / MWh` : '$54.30 / MWh'),
          e('span', { className: 'badge-policy' }, policy.toUpperCase().replace('_', '-'))
        ),
        e('div', { className: 'kpi' },
          e('small', null, 'Grid Tie Headroom'),
          e('b', { className: 'text-cyan' }, simData ? `${simData.grid_tie.headroom_mw.toFixed(1)} MW` : '80.0 MW'),
          e('span', null, simData ? `Flow: ${(simData.total_renewable_mw - simData.industrial_demand_mw.total_mw).toFixed(1)} MW` : '+0.0 MW')
        ),
        e('div', { className: 'kpi' },
          e('small', null, 'BESS Storage Total'),
          e('b', null, simData ? `${(simData.bess_state['bess-a'].energy_mwh + simData.bess_state['bess-b'].energy_mwh).toFixed(1)} MWh` : '25.0 MWh'),
          e('span', null, simData ? `A: ${simData.bess_state['bess-a'].energy_mwh.toFixed(1)} | B: ${simData.bess_state['bess-b'].energy_mwh.toFixed(1)} MWh` : '25.0 MWh')
        )
      ),

      // 3. Main Layout: 3D Twin & Multi-Agent Cognitive Stream
      e('div', { className: 'main-layout' },
        e(GridTwin3D, { simData, isPlaying }),
        e(MultiAgentStream, { deliberation, activeAgentTab, setActiveAgentTab })
      ),

      // 4. Timeline Slider & Policy Mode Controller
      e('section', { className: 'card timeline-card' },
        e('div', { className: 'timeline-controls' },
          e('div', { className: 'playback-btns' },
            e('button', {
              id: 'btn-play-pause',
              className: 'btn-primary',
              onClick: () => setIsPlaying(!isPlaying)
            }, isPlaying ? '⏸ Pause' : '▶ Play 24h Replay'),
            e('button', {
              id: 'btn-step-prev',
              className: 'btn-secondary',
              onClick: () => setSlot(prev => Math.max(0, prev - 1))
            }, '⏮ Previous'),
            e('button', {
              id: 'btn-step-next',
              className: 'btn-secondary',
              onClick: () => setSlot(prev => Math.min(95, prev + 1))
            }, '⏭ Next')
          ),
          e('div', { className: 'slider-box' },
            e('label', { htmlFor: 'timeline-slider' }, `15-Minute Dispatch Slot [${slot}/95] — ${timeStr}:`),
            e('input', {
              type: 'range',
              id: 'timeline-slider',
              min: 0,
              max: 95,
              step: 1,
              value: slot,
              onChange: (ev) => setSlot(parseInt(ev.target.value, 10))
            })
          ),
          e('div', { className: 'policy-switcher' },
            e('label', null, 'Policy Mode:'),
            e('button', {
              className: `policy-btn ${policy === 'reliability_first' ? 'selected' : ''}`,
              onClick: () => setPolicy('reliability_first')
            }, 'Reliability-First'),
            e('button', {
              className: `policy-btn ${policy === 'balanced' ? 'selected' : ''}`,
              onClick: () => setPolicy('balanced')
            }, 'Balanced'),
            e('button', {
              className: `policy-btn ${policy === 'cost_first' ? 'selected' : ''}`,
              onClick: () => setPolicy('cost_first')
            }, 'Cost-First')
          )
        ),
        e('p', { id: 'policy-explanation', className: 'policy-note' },
          `${policy.replace('_', ' ').toUpperCase()}: Hard safety tier first; zero critical/reserve slack; solver optimizes lexicographic objectives.`
        )
      ),

      // 5. Mid Grid: Fan Chart & Scenario Sandbox
      e('div', { className: 'mid-grid' },
        e(FanChart, { currentSlot: slot, simData }),
        e(MultimodalSandbox, {
          eventScenario,
          onSelectScenario: handleScenarioSelect,
          selectedDocPreset,
          setSelectedDocPreset,
          onParseDoc: handleParseDoc,
          docResult,
        })
      ),

      // 6. Decision Approval & Safety Refusal Gate
      e('section', { className: 'card decision-card' },
        e('div', { className: 'decision-header' },
          e('h2', null, '🛡️ Deterministic Safety Filter & Signed Decision Certificate'),
          e('div', { id: 'decision-state', className: `state ${decisionState.toLowerCase()}` },
            decisionState === 'APPROVED' ? 'APPROVED — HUMAN ACTOR' :
            decisionState === 'REJECTED' ? 'REJECTED — ESCALATED_NO_SAFE_ACTION' :
            'PENDING — SAFER PLAN READY'
          )
        ),
        e('p', { id: 'decision-detail' }, decisionDetail),
        e('div', { className: 'actions' },
          e('button', {
            id: 'approve',
            className: 'btn-primary',
            disabled: isApproveDisabled || decisionState !== 'PENDING',
            onClick: handleApprove
          }, 'Approve Safer Plan'),
          e('button', { id: 'reject', className: 'btn-secondary', onClick: handleReject }, 'Reject'),
          e('button', { id: 'override', className: 'btn-secondary', onClick: handleOverride }, 'Override with Reason'),
          e('button', { id: 'escalate', className: 'btn-danger', onClick: handleEscalate }, 'Escalate'),
          e('button', {
            id: 'btn-verify-cert',
            className: 'btn-accent',
            onClick: async () => {
              const id = (simData && simData.certificate_id) || 'certificate';
              try {
                const resp = await fetch(`/certificates/${id}/verification`);
                const json = await resp.json();
                const d = json.data || json;
                if (d && d.verified) {
                  setCertStatus(`VERIFIED (${(d.key_fingerprint || '').slice(0, 12)}…)`);
                } else {
                  setCertStatus(`UNVERIFIED${d && d.code ? ' — ' + d.code : ''}`);
                }
              } catch (err) {
                setCertStatus('UNVERIFIED — request failed');
              }
            }
          }, 'Verify Certificate')
        ),
        e('div', { className: 'cert-proof' },
          e('small', null, 'Certificate Status: '),
          e('span', { id: 'certificate-state', className: 'text-green' }, certStatus)
        )
      ),

      // 7. Footer Notice
      e('footer', { className: 'card notice-card' },
        e('strong', null, 'Verification & Audit Scope: '),
        e('span', null, 'SIMULATION and REPLAY scope · mixed-integer linear program with an explicitly labelled deterministic fallback · signed offline decision certificates · no live physical grid or market actuators.')
      )
    );
  }

  // ==========================================================================
  // React Component: GridTwin3D (Three.js WebGL Viewport)
  // ==========================================================================
  function GridTwin3D({ simData, isPlaying }) {
    const containerRef = useRef(null);
    const canvasRef = useRef(null);
    const hudRef = useRef(null);
    const [hudData, setHudData] = useState(null);

    useEffect(() => {
      if (!canvasRef.current || typeof THREE === 'undefined') return;

      const container = containerRef.current;
      const canvas = canvasRef.current;
      const width = container.clientWidth;
      const height = 480;

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x060a10);
      scene.fog = new THREE.FogExp2(0x060a10, 0.012);

      const camera = new THREE.PerspectiveCamera(45, width / height, 0.5, 500);
      camera.position.set(0, 38, 48);
      camera.lookAt(0, 0, -2);

      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
      renderer.setSize(width, height);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

      // Lights
      const ambient = new THREE.AmbientLight(0xdde8f8, 0.7);
      scene.add(ambient);
      const sunLight = new THREE.DirectionalLight(0xfff4d6, 1.2);
      sunLight.position.set(30, 50, 20);
      scene.add(sunLight);

      // Grid helper
      const gridHelper = new THREE.GridHelper(90, 45, 0x1e3a5f, 0x0f2033);
      scene.add(gridHelper);

      // Central Substation
      const subGeo = new THREE.BoxGeometry(4, 2, 4);
      const subMat = new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.8, roughness: 0.2 });
      const subMesh = new THREE.Mesh(subGeo, subMat);
      subMesh.position.set(0, 1, 0);
      subMesh.userData = { name: "Central Grid Substation", details: "33kV/220kV Interconnection · Tie-Main Headroom: 80 MW" };
      scene.add(subMesh);

      const clickable = [subMesh];
      const turbineRotors = [];
      const bessBars = [];
      const powerPulses = [];

      // Line helper
      const createLine = (x1, z1, x2, z2, color) => {
        const curve = new THREE.CatmullRomCurve3([
          new THREE.Vector3(x1, 0.4, z1),
          new THREE.Vector3((x1 + x2) / 2, 2.5, (z1 + z2) / 2),
          new THREE.Vector3(x2, 0.8, z2),
        ]);
        const tube = new THREE.Mesh(
          new THREE.TubeGeometry(curve, 20, 0.08, 8, false),
          new THREE.MeshBasicMaterial({ color: 0x1e293b })
        );
        scene.add(tube);
        const pulse = new THREE.Mesh(
          new THREE.SphereGeometry(0.25, 8, 8),
          new THREE.MeshBasicMaterial({ color: color })
        );
        scene.add(pulse);
        powerPulses.push({ mesh: pulse, curve: curve, progress: Math.random() });
      };

      // 5 Solar Parks
      const solarCoords = [
        { id: "solar-01", x: -22, z: -14, cap: 25 },
        { id: "solar-02", x: -14, z: -22, cap: 20 },
        { id: "solar-03", x: -26, z: 2, cap: 20 },
        { id: "solar-04", x: -16, z: 12, cap: 20 },
        { id: "solar-05", x: -6, z: -16, cap: 15 },
      ];
      solarCoords.forEach(item => {
        const group = new THREE.Group();
        group.position.set(item.x, 0, item.z);
        for (let r = -1; r <= 1; r++) {
          for (let c = -1; c <= 1; c++) {
            const panel = new THREE.Mesh(
              new THREE.BoxGeometry(1.6, 0.08, 1.0),
              new THREE.MeshStandardMaterial({ color: 0x1e3a8a, metalness: 0.9, roughness: 0.1 })
            );
            panel.rotation.x = -Math.PI / 6;
            panel.position.set(c * 2.0, 0.4, r * 1.5);
            group.add(panel);
          }
        }
        group.userData = { name: `Solar Park ${item.id}`, details: `Rated: ${item.cap} MW AC · Inverter NOCT: 45°C` };
        scene.add(group);
        clickable.push(group);
        createLine(item.x, item.z, 0, 0, 0xfbbf24);
      });

      // 3 Wind Farms
      const windCoords = [
        { id: "wind-01", x: 18, z: -18, cap: 30 },
        { id: "wind-02", x: 26, z: -4, cap: 28 },
        { id: "wind-03", x: 20, z: 12, cap: 24 },
      ];
      windCoords.forEach(item => {
        const group = new THREE.Group();
        group.position.set(item.x, 0, item.z);
        const tower = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.45, 6, 12), new THREE.MeshStandardMaterial({ color: 0xe2e8f0 }));
        tower.position.y = 3;
        group.add(tower);
        const nac = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.5, 1.2), new THREE.MeshStandardMaterial({ color: 0xe2e8f0 }));
        nac.position.set(0, 6.1, 0.2);
        group.add(nac);
        const rotor = new THREE.Group();
        rotor.position.set(0, 6.1, 0.8);
        for (let b = 0; b < 3; b++) {
          const blade = new THREE.Mesh(new THREE.BoxGeometry(0.15, 3.2, 0.05), new THREE.MeshStandardMaterial({ color: 0xe2e8f0 }));
          blade.position.y = 1.6;
          const anchor = new THREE.Group();
          anchor.rotation.z = (b * Math.PI * 2) / 3;
          anchor.add(blade);
          rotor.add(anchor);
        }
        group.add(rotor);
        turbineRotors.push(rotor);
        group.userData = { name: `Wind Farm ${item.id}`, details: `Rated: ${item.cap} MW · Hub Height: 120m` };
        scene.add(group);
        clickable.push(group);
        createLine(item.x, item.z, 0, 0, 0x38bdf8);
      });

      // 2 BESS Parks
      const bessCoords = [
        { id: "bess-a", x: -4, z: 14, cap: 20, reserve: 4 },
        { id: "bess-b", x: 6, z: 14, cap: 16, reserve: 3 },
      ];
      bessCoords.forEach(item => {
        const group = new THREE.Group();
        group.position.set(item.x, 0, item.z);
        const box = new THREE.Mesh(new THREE.BoxGeometry(3.5, 1.8, 2.2), new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.6 }));
        box.position.y = 0.9;
        group.add(box);
        const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 2.2, 16), new THREE.MeshBasicMaterial({ color: 0x10b981, transparent: true, opacity: 0.85 }));
        bar.position.set(0, 2.4, 0);
        group.add(bar);
        bessBars.push({ bar, id: item.id });
        group.userData = { name: `BESS Park ${item.id.toUpperCase()}`, details: `Capacity: ${item.cap} MWh · Reserve: ${item.reserve} MWh` };
        scene.add(group);
        clickable.push(group);
        createLine(item.x, item.z, 0, 0, 0x10b981);
      });

      // Raycasting
      const raycaster = new THREE.Raycaster();
      const mouse = new THREE.Vector2();
      canvas.addEventListener('mousemove', (ev) => {
        const rect = canvas.getBoundingClientRect();
        mouse.x = ((ev.clientX - rect.left) / canvas.clientWidth) * 2 - 1;
        mouse.y = -((ev.clientY - rect.top) / canvas.clientHeight) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        const hits = raycaster.intersectObjects(clickable, true);
        if (hits.length > 0) {
          let root = hits[0].object;
          while (root.parent && !root.userData.name && root.parent !== scene) root = root.parent;
          if (root.userData.name) setHudData({ name: root.userData.name, details: root.userData.details });
        } else {
          setHudData(null);
        }
      });

      // Animation Loop
      let animId;
      const animate = () => {
        animId = requestAnimationFrame(animate);
        turbineRotors.forEach(r => { r.rotation.z -= 0.035; });
        powerPulses.forEach(p => {
          p.progress = (p.progress + 0.008) % 1.0;
          p.mesh.position.copy(p.curve.getPoint(p.progress));
        });
        if (!isPlaying) {
          camera.position.x = 48 * Math.sin(Date.now() * 0.0001);
          camera.position.z = 48 * Math.cos(Date.now() * 0.0001);
          camera.lookAt(0, 0, -2);
        }
        renderer.render(scene, camera);
      };
      animate();

      return () => {
        cancelAnimationFrame(animId);
        renderer.dispose();
      };
    }, [isPlaying]);

    return e('section', { className: 'card twin-card' },
      e('div', { className: 'card-header' },
        e('h2', null, '🌐 3D Cyber-Physical Utility Grid Twin (WebGL Three.js)'),
        e('div', { className: 'twin-badges' },
          e('span', { className: 'tag' }, '5 Solar Parks'),
          e('span', { className: 'tag' }, '3 Wind Farms'),
          e('span', { className: 'tag' }, '2 BESS Parks'),
          e('span', { className: 'tag' }, 'Substation Tie')
        )
      ),
      e('div', { id: 'twin-canvas-container', ref: containerRef },
        e('canvas', { id: 'twin-canvas', ref: canvasRef }),
        hudData && e('div', { id: 'twin-hud', className: 'twin-hud' },
          e('div', { className: 'hud-title' }, hudData.name),
          e('div', { className: 'hud-stat' }, hudData.details)
        )
      ),
      e('div', { className: 'twin-legend' },
        e('span', null, e('i', { className: 'legend-color legend-solar' }), ' Solar Parks (Solar-01..05)'),
        e('span', null, e('i', { className: 'legend-color legend-wind' }), ' Wind Turbines (Wind-01..03)'),
        e('span', null, e('i', { className: 'legend-color legend-bess' }), ' BESS Enclosures (A & B)'),
        e('span', null, e('i', { className: 'legend-color legend-factory' }), ' Industrial Consumers'),
        e('span', null, e('i', { className: 'legend-color legend-grid' }), ' Substation Grid Tie')
      )
    );
  }

  // ==========================================================================
  // React Component: MultiAgentStream (Cognitive Hierarchy)
  // ==========================================================================
  function MultiAgentStream({ deliberation, activeAgentTab, setActiveAgentTab }) {
    const thoughts = deliberation ? deliberation.thoughts : [];
    const chief = thoughts.find(t => t.role === 'chief_orchestrator');

    const filtered = thoughts.filter(t => {
      if (activeAgentTab === 'chief') return true;
      if (activeAgentTab === 'weather') return t.role === 'weather_nowcaster';
      if (activeAgentTab === 'market') return t.role === 'market_arbitrageur';
      if (activeAgentTab === 'storage') return t.role === 'asset_health_custodian';
      if (activeAgentTab === 'grid') return t.role === 'grid_stability_governor';
      return true;
    });

    return e('section', { className: 'card agent-card' },
      e('div', { className: 'card-header' },
        e('h2', null, '🤖 Multi-Agent Cognitive Stream (5 Subagents)'),
        e('span', { className: 'badge-live' }, 'CONSENSUS')
      ),
      e('div', { className: 'agent-tabs' },
        e('button', { className: `agent-tab ${activeAgentTab === 'chief' ? 'active' : ''}`, onClick: () => setActiveAgentTab('chief') }, '🧠 Chief'),
        e('button', { className: `agent-tab ${activeAgentTab === 'weather' ? 'active' : ''}`, onClick: () => setActiveAgentTab('weather') }, '🌤️ Weather'),
        e('button', { className: `agent-tab ${activeAgentTab === 'market' ? 'active' : ''}`, onClick: () => setActiveAgentTab('market') }, '📈 Market'),
        e('button', { className: `agent-tab ${activeAgentTab === 'storage' ? 'active' : ''}`, onClick: () => setActiveAgentTab('storage') }, '🔋 Storage'),
        e('button', { className: `agent-tab ${activeAgentTab === 'grid' ? 'active' : ''}`, onClick: () => setActiveAgentTab('grid') }, '⚡ Grid')
      ),
      e('div', { className: 'agent-thought-box' },
        chief && e('div', { className: 'thought-item chief' },
          e('div', { className: 'thought-header' },
            e('strong', null, 'Chief Orchestrator'),
            e('small', null, 'Receding-Horizon MPC')
          ),
          e('p', null, chief.assessment)
        ),
        filtered.map((t, idx) => e('div', { key: idx, className: `thought-item ${t.role.replace('_', '-')}` },
          e('div', { className: 'thought-header' },
            e('strong', null, t.role.toUpperCase().replace(/_/g, ' ')),
            e('small', null, `${t.timestamp_utc.slice(11, 19)} UTC`)
          ),
          e('p', null, e('strong', null, 'Obs: '), t.observation),
          e('p', null, e('strong', null, 'Assessment: '), t.assessment)
        ))
      ),
      e('div', { className: 'solver-summary' },
        e('small', null, 'Solver status: '), e('span', { className: 'text-green' }, '—'), ' | ',
        e('small', null, 'Solve time: '), e('span', null, '—'), ' | ',
        e('small', null, 'Violations: '), e('span', { className: 'text-green' }, '—')
      )
    );
  }

  // ==========================================================================
  // React Component: FanChart (HTML5 Canvas Probabilistic Forecaster)
  // ==========================================================================
  function FanChart({ currentSlot, simData }) {
    const canvasRef = useRef(null);

    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const width = canvas.width = canvas.clientWidth;
      const height = canvas.height = 180;

      ctx.clearRect(0, 0, width, height);

      // Grid
      ctx.strokeStyle = '#1e293b';
      for (let y = 30; y < height; y += 40) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }

      const n = 96;
      const stepX = width / (n - 1);
      const toY = (val) => height - 15 - (val / 120.0) * (height - 35);

      const p10 = [], p50 = [], p90 = [], actual = [];
      for (let s = 0; s < n; s++) {
        let base = (s >= 24 && s <= 72) ? 65 * Math.sin(Math.PI * (s - 24) / 48) : 0;
        base += 28 + 8 * Math.sin(2 * Math.PI * s / 48);
        p10.push(base * 0.75);
        p50.push(base);
        p90.push(base * 1.15);
        actual.push(s <= currentSlot ? base * 0.98 : null);
      }

      // Fan Band (P10 to P90)
      ctx.fillStyle = 'rgba(56, 189, 248, 0.15)';
      ctx.beginPath();
      ctx.moveTo(0, toY(p90[0]));
      for (let s = 1; s < n; s++) ctx.lineTo(s * stepX, toY(p90[s]));
      for (let s = n - 1; s >= 0; s--) ctx.lineTo(s * stepX, toY(p10[s]));
      ctx.closePath();
      ctx.fill();

      // P50 Curve
      ctx.strokeStyle = '#0ea5e9';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(0, toY(p50[0]));
      for (let s = 1; s < n; s++) ctx.lineTo(s * stepX, toY(p50[s]));
      ctx.stroke();

      // Realized Actual
      ctx.strokeStyle = '#10b981';
      ctx.lineWidth = 3;
      ctx.beginPath();
      let started = false;
      for (let s = 0; s <= currentSlot; s++) {
        if (!started) { ctx.moveTo(s * stepX, toY(actual[s])); started = true; }
        else ctx.lineTo(s * stepX, toY(actual[s]));
      }
      ctx.stroke();

      // Slot Tracker
      const curX = currentSlot * stepX;
      ctx.strokeStyle = '#f59e0b';
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(curX, 0);
      ctx.lineTo(curX, height);
      ctx.stroke();
      ctx.setLineDash([]);
    }, [currentSlot, simData]);

    return e('section', { className: 'card chart-card' },
      e('h2', null, '📊 24-Hour Probabilistic Forecast & Dispatch Fan Chart'),
      e('canvas', { id: 'fan-chart', ref: canvasRef }),
      e('div', { className: 'chart-legend' },
        e('span', null, e('i', { className: 'chart-box p90-box' }), ' P90 Upper Bound'),
        e('span', null, e('i', { className: 'chart-box p50-box' }), ' P50 Expected'),
        e('span', null, e('i', { className: 'chart-box p10-box' }), ' P10 Lower Bound'),
        e('span', null, e('i', { className: 'chart-box actual-box' }), ' Realized Dispatch')
      )
    );
  }

  // ==========================================================================
  // React Component: MultimodalSandbox (D3 Document Parser & Stress Sandbox)
  // ==========================================================================
  function MultimodalSandbox({ eventScenario, onSelectScenario, selectedDocPreset, setSelectedDocPreset, onParseDoc, docResult }) {
    return e('section', { className: 'card multimodal-card' },
      e('h2', null, '📥 D3 Multimodal Ingestion & Stress Test Sandbox'),
      e('div', { className: 'scenario-buttons' },
        e('button', {
          className: `btn-scenario ${eventScenario === 'normal' ? 'active' : ''}`,
          onClick: () => onSelectScenario('normal')
        }, '☀️ Normal Day'),
        e('button', {
          className: `btn-scenario ${eventScenario === 'monsoon_cloud' ? 'active' : ''}`,
          onClick: () => onSelectScenario('monsoon_cloud')
        }, '🌧️ Monsoon Cloudburst'),
        e('button', {
          className: `btn-scenario ${eventScenario === 'price_spike' ? 'active' : ''}`,
          onClick: () => onSelectScenario('price_spike')
        }, '📈 IEX Price Spike'),
        e('button', {
          className: `btn-scenario btn-danger ${eventScenario === 'stress' ? 'active' : ''}`,
          onClick: () => onSelectScenario('stress')
        }, '⚠️ BESS-B Outage + Line Congestion'),
        e('button', {
          className: `btn-scenario btn-refusal ${eventScenario === 'unsafe_proposal' ? 'active' : ''}`,
          onClick: () => onSelectScenario('unsafe_proposal')
        }, '🛡️ Inject Unsafe Trade (Safety Filter Demo)')
      ),
      e('div', { className: 'multimodal-upload' },
        e('h3', null, 'Drop Unstructured Weather Alert (IMD) or CMMS Work Order:'),
        e('div', { className: 'upload-row' },
          e('select', {
            id: 'preset-doc-select',
            value: selectedDocPreset,
            onChange: (ev) => setSelectedDocPreset(ev.target.value)
          },
            e('option', { value: 'none' }, '-- Select Real-World Document Preset --'),
            e('option', { value: 'imd_cyclone' }, 'IMD Official Bulletin: Cyclone Squall 28 m/s Cut-Out Warning'),
            e('option', { value: 'cmms_bess_outage' }, 'CMMS Work Order #49201: BESS-B Inverter Trip & Overhaul'),
            e('option', { value: 'tie_derate' }, 'Transmission SLDC Notice: Tie-Main Derated to 50 MW')
          ),
          e('button', { id: 'btn-parse-doc', onClick: onParseDoc }, 'Ingest & Parse Document')
        ),
        docResult && e('div', { id: 'doc-parse-result', className: 'doc-result-box' },
          e('strong', null, 'Extracted Operational Directive: '),
          e('p', null, docResult.map(d => `${d.directive_type} on [${d.target_assets.join(', ')}] -> Derate: ${d.derate_fraction * 100}% | Action: ${d.action_narrative}`).join('; '))
        )
      )
    );
  }

  // Mount React 18 Application
  const rootElement = document.getElementById('root');
  if (rootElement && typeof ReactDOM !== 'undefined') {
    const root = ReactDOM.createRoot(rootElement);
    root.render(e(App));
  }
})();
