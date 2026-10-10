import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RotateCcw } from 'lucide-react';
import type { Asset, Slot } from './types';

type Scale = 'unit' | 'city';
type Theme = 'light' | 'dark';
type Props = {
  assets: Asset[]; slot: Slot; generation: Record<string, number[]>; selected: string | null;
  onSelect: (id: string) => void; availability: Record<string, boolean[]>;
  scale: Scale; fleetUnits: number; theme: Theme;
};

export function Scene({ assets, slot, generation, selected, onSelect, availability, scale, fleetUnits, theme }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const reset = useRef<() => void>(() => {});
  const updates = useRef<((slot: Slot, selected: string | null) => void) | null>(null);
  const selectRef = useRef(onSelect);
  const [error, setError] = useState(false);
  selectRef.current = onSelect;

  useEffect(() => {
    const element = host.current!;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); }
    catch { setError(true); return; }
    const pal = theme === 'dark'
      ? { bg: '#070b10', ground: scale === 'city' ? '#0d161c' : '#101c22', spine: '#15323c', spineEmissive: '#0b2730', base: '#182530', hemiSky: '#9fc5ff', hemiGround: '#101820', hemiInt: 1.5, sunColor: '#ffe6b0', sunInt: 2.8, rim: '#4cc9e8', rimInt: 0.6, labelBg: 'rgba(18,28,38,.9)', labelInk: '#e6eef5', labelSub: '#9fb2c2', cityTower: '#141f2a' }
      : { bg: '#e8edf1', ground: scale === 'city' ? '#d3dce2' : '#dde4e9', spine: '#aebfc8', spineEmissive: '#000000', base: '#c3ced7', hemiSky: '#eaf2fb', hemiGround: '#c3ccd2', hemiInt: 2.1, sunColor: '#fff3da', sunInt: 2.4, rim: '#8fb7c6', rimInt: 0.4, labelBg: 'rgba(255,255,255,.92)', labelInk: '#16202b', labelSub: '#4e5e6c', cityTower: '#b7c3cc' };
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(pal.bg);
    scene.fog = new THREE.Fog(pal.bg, scale === 'city' ? 70 : 90, scale === 'city' ? 170 : 160);
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 400);
    const baseCam = scale === 'city' ? new THREE.Vector3(46, 44, 50) : new THREE.Vector3(23, 24, 25);
    camera.position.copy(baseCam);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.setAttribute('aria-label', scale === 'city' ? 'City-scale projected portfolio landscape schematic' : 'Solar, wind, storage, grid and industrial portfolio schematic');
    renderer.domElement.setAttribute('role', 'img');
    renderer.domElement.dataset.scene = scale;
    element.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0, 0); controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI / 2.15;
    controls.minDistance = scale === 'city' ? 28 : 15;
    controls.maxDistance = scale === 'city' ? 150 : 65;
    reset.current = () => { camera.position.copy(baseCam).multiplyScalar(camera.aspect < 1.4 ? 1.3 : 1); controls.target.set(0, 0, 0); controls.update(); renderer.render(scene, camera); };

    scene.add(new THREE.HemisphereLight(pal.hemiSky, pal.hemiGround, pal.hemiInt));
    const sun = new THREE.DirectionalLight(pal.sunColor, pal.sunInt);
    sun.position.set(-18, 34, 14); sun.castShadow = true;
    const shadowSpan = scale === 'city' ? 60 : 24;
    sun.shadow.camera.left = -shadowSpan; sun.shadow.camera.right = shadowSpan;
    sun.shadow.camera.top = shadowSpan; sun.shadow.camera.bottom = -shadowSpan;
    sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0004; scene.add(sun);
    const rim = new THREE.DirectionalLight(pal.rim, pal.rimInt); rim.position.set(20, 12, -22); scene.add(rim);

    const materials: THREE.Material[] = [], geometries: THREE.BufferGeometry[] = [], textures: THREE.Texture[] = [];
    // Solar-cell panel texture.
    const panelCanvas = document.createElement('canvas'); panelCanvas.width = 256; panelCanvas.height = 256;
    const pc = panelCanvas.getContext('2d')!;
    pc.fillStyle = '#0f2d3c'; pc.fillRect(0, 0, 256, 256);
    pc.strokeStyle = '#2f6b7f'; pc.lineWidth = 2;
    for (let cell = 0; cell <= 8; cell++) { pc.beginPath(); pc.moveTo(cell * 32, 0); pc.lineTo(cell * 32, 256); pc.stroke(); pc.beginPath(); pc.moveTo(0, cell * 32); pc.lineTo(256, cell * 32); pc.stroke(); }
    const panelTexture = new THREE.CanvasTexture(panelCanvas); panelTexture.colorSpace = THREE.SRGBColorSpace; textures.push(panelTexture);

    function mat(color: string, opts: Partial<THREE.MeshStandardMaterialParameters> = {}) {
      const m = new THREE.MeshStandardMaterial({ color, roughness: 0.72, metalness: 0.16, ...opts }); materials.push(m); return m;
    }
    function box(parent: THREE.Object3D, size: [number, number, number], position: [number, number, number], color: string, opts: Partial<THREE.MeshStandardMaterialParameters> = {}) {
      const geometry = new THREE.BoxGeometry(...size); geometries.push(geometry);
      const mesh = new THREE.Mesh(geometry, mat(color, opts));
      mesh.position.set(...position); mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
    }

    // Ground.
    const groundSize = scale === 'city' ? 170 : 34;
    const ground = box(scene, [groundSize, 0.3, groundSize * 0.78], [0, -0.3, 0], pal.ground);
    (ground.material as THREE.MeshStandardMaterial).roughness = 1;
    // Bus corridor (the grid spine) — connector motif.
    const spineZ = scale === 'city' ? 6 : 4;
    box(scene, [groundSize * 0.92, 0.05, 1.1], [0, 0.02, spineZ], pal.spine, { emissive: new THREE.Color(pal.spineEmissive), emissiveIntensity: theme === 'dark' ? 0.6 : 0 });

    type ObjRec = { group: THREE.Group; base: THREE.Mesh; rotors: THREE.Group[]; fills: THREE.Mesh[]; panels: THREE.Mesh[]; label: THREE.Sprite; labelCanvas: HTMLCanvasElement; labelTexture: THREE.CanvasTexture; kind: string; glow?: THREE.Mesh };
    const objects = new Map<string, ObjRec>();
    const flowMarkers: { mesh: THREE.Mesh; assetId: string; start: THREE.Vector3; corner: THREE.Vector3; end: THREE.Vector3; phase: number }[] = [];
    const spread = scale === 'city' ? 2.15 : 1;

    function buildAsset(parent: THREE.Object3D, asset: Asset, position: THREE.Vector3): ObjRec {
      const group = new THREE.Group(); group.userData.assetId = asset.asset_id; group.position.copy(position);
      const rotors: THREE.Group[] = [], fills: THREE.Mesh[] = [], panels: THREE.Mesh[] = [];
      const base = box(group, [4.5, 0.16, 4], [0, 0.02, 0], pal.base);
      let glow: THREE.Mesh | undefined;
      if (asset.kind === 'solar_farm') {
        for (let row = 0; row < 3; row++) for (let column = 0; column < 4; column++) {
          box(group, [0.06, 0.6, 0.06], [-1.5 + column, 0.3, -1 + row], '#2a3a44');
          const panel = box(group, [0.9, 0.08, 0.88], [-1.5 + column, 0.7, -1 + row], '#13425a', { map: panelTexture, metalness: 0.6, roughness: 0.28 });
          panel.rotation.x = -0.26; panels.push(panel);
        }
      } else if (asset.kind === 'wind_farm') {
        box(group, [0.2, 3.6, 0.2], [0, 1.9, 0], '#d7e3ea');
        box(group, [0.68, 0.32, 0.42], [0, 3.7, 0], '#eef4f8');
        const rotor = new THREE.Group(); rotor.position.set(0, 3.7, 0.34);
        for (let blade = 0; blade < 3; blade++) { const arm = new THREE.Group(); arm.rotation.z = blade * Math.PI * 2 / 3; box(arm, [0.13, 1.8, 0.07], [0, 0.85, 0], '#f4f8fb'); rotor.add(arm); }
        group.add(rotor); rotors.push(rotor);
      } else if (asset.kind === 'bess') {
        box(group, [3.2, 1.45, 1.9], [0, 0.85, 0], '#1b2731');
        for (let stripe = 0; stripe < 5; stripe++) box(group, [0.06, 0.95, 0.03], [-1.15 + stripe * 0.48, 0.83, 0.97], '#2c3f4b');
        glow = box(group, [2.8, 0.14, 0.2], [0, 1.64, 0.6], '#35d6a4', { emissive: new THREE.Color('#35d6a4'), emissiveIntensity: 0.9 });
        fills.push(glow);
      } else if (asset.kind === 'load_class') {
        box(group, [3.1, 1.6, 2.3], [0, 0.88, 0], '#2a2440');
        box(group, [0.3, 2.6, 0.3], [1, 1.4, -0.7], '#3a3356');
        for (let window = 0; window < 4; window++) box(group, [0.38, 0.4, 0.04], [-1 + window * 0.67, 0.95, 1.17], '#b98cff', { emissive: new THREE.Color('#7b5fcf'), emissiveIntensity: 0.5 });
      } else {
        // grid / tie — substation feel.
        box(group, [2.0, 1.05, 1.2], [0, 0.6, -0.9], '#20303a');
        for (let rail = 0; rail < 5; rail++) box(group, [0.07, 0.9, 1.25], [-0.7 + rail * 0.35, 0.6, -0.9], '#38505c');
        box(group, [0.15, 3.4, 0.15], [0, 1.8, 0], '#3a525e');
        for (let height = 1; height < 4; height++) box(group, [2.2 - height * 0.25, 0.1, 0.1], [0, height, 0], '#3a525e');
      }
      const labelCanvas = document.createElement('canvas'); labelCanvas.width = 256; labelCanvas.height = 110;
      const labelTexture = new THREE.CanvasTexture(labelCanvas); labelTexture.colorSpace = THREE.SRGBColorSpace; textures.push(labelTexture);
      const labelMaterial = new THREE.SpriteMaterial({ map: labelTexture, transparent: true, depthTest: false }); materials.push(labelMaterial);
      const label = new THREE.Sprite(labelMaterial);
      label.scale.set(4.7, 1.01, 1); label.position.set(0, asset.kind === 'wind_farm' ? 6.1 : 2.6, 0);
      group.add(label);
      parent.add(group);
      return { group, base, rotors, fills, panels, label, labelCanvas, labelTexture, kind: asset.kind, glow };
    }

    // Place assets. Unit scale: original compact layout. City scale: replicated clusters + a consumer city block.
    const counters: Record<string, number> = {};
    function unitPosition(kind: string, count: number): THREE.Vector3 {
      if (kind === 'solar_farm') return new THREE.Vector3(-10 + count % 3 * 5.4, 0, -7 + Math.floor(count / 3) * 5.4);
      if (kind === 'wind_farm') return new THREE.Vector3(-10 + count * 6, 0, 8.3);
      if (kind === 'bess') return new THREE.Vector3(7 + count * 4.3, 0, -7);
      if (kind === 'load_class') return new THREE.Vector3(7 + count % 2 * 4.6, 0, count < 2 ? 0 : 7.5);
      return new THREE.Vector3(kind === 'grid_interface' ? 11.5 : 6.8, 0, 8.5);
    }
    for (const asset of assets) {
      const count = counters[asset.kind] ?? 0; counters[asset.kind] = count + 1;
      const basePos = unitPosition(asset.kind, count);
      const pos = scale === 'city' ? basePos.clone().multiplyScalar(spread) : basePos;
      const rec = buildAsset(scene, asset, pos);
      objects.set(asset.asset_id, rec);
      const start = pos.clone(); start.y = 0.3;
      const corner = new THREE.Vector3(start.x, 0.3, spineZ);
      const end = new THREE.Vector3(0, 0.3, spineZ);
      const conduitGeometry = new THREE.BufferGeometry().setFromPoints([start, corner, end]); geometries.push(conduitGeometry);
      const conduitMaterial = new THREE.LineBasicMaterial({ color: '#2d4e5a', transparent: true, opacity: 0.7 }); materials.push(conduitMaterial);
      scene.add(new THREE.Line(conduitGeometry, conduitMaterial));
      const marker = box(scene, [0.14, 0.12, 0.14], [start.x, 0.4, start.z], '#4cc9e8', { emissive: new THREE.Color('#4cc9e8'), emissiveIntensity: 1 });
      flowMarkers.push({ mesh: marker, assetId: asset.asset_id, start, corner, end, phase: (count % 5) / 5 });
    }

    // City-scale extras: a consumer city cluster + ghosted replicated fleet + substation emphasis.
    if (scale === 'city') {
      const cityCenter = new THREE.Vector3(0, 0, -34);
      const rand = (seed: number) => { const x = Math.sin(seed * 127.1) * 43758.5453; return x - Math.floor(x); };
      for (let i = 0; i < 54; i++) {
        const gx = (i % 9 - 4) * 3.4 + (rand(i) - 0.5) * 1.2;
        const gz = Math.floor(i / 9) * 3.4 + (rand(i + 50) - 0.5) * 1.2;
        const h = 2 + rand(i + 100) * 7;
        const tower = box(scene, [1.8 + rand(i + 7), h, 1.8 + rand(i + 11)], [cityCenter.x + gx, h / 2, cityCenter.z + gz], pal.cityTower);
        if (rand(i + 200) > 0.45) { const litMat = tower.material as THREE.MeshStandardMaterial; litMat.emissive = new THREE.Color('#b98cff'); litMat.emissiveIntensity = 0.12 + rand(i + 300) * 0.18; }
      }
      // Consumer-city label.
      const cityLabelCanvas = document.createElement('canvas'); cityLabelCanvas.width = 256; cityLabelCanvas.height = 80;
      const clc = cityLabelCanvas.getContext('2d')!; clc.fillStyle = 'rgba(16,24,34,.85)'; clc.fillRect(0, 0, 256, 80);
      clc.fillStyle = '#b98cff'; clc.font = '600 30px sans-serif'; clc.textAlign = 'center'; clc.fillText('CONSUMER CITY', 128, 38);
      clc.fillStyle = '#9fb2c2'; clc.font = '20px sans-serif'; clc.fillText('illustrative demand cluster', 128, 66);
      const cityTexture = new THREE.CanvasTexture(cityLabelCanvas); cityTexture.colorSpace = THREE.SRGBColorSpace; textures.push(cityTexture);
      const cityMat = new THREE.SpriteMaterial({ map: cityTexture, transparent: true, depthTest: false }); materials.push(cityMat);
      const citySprite = new THREE.Sprite(cityMat); citySprite.scale.set(11, 3.4, 1); citySprite.position.set(cityCenter.x, 11, cityCenter.z); scene.add(citySprite);
      // Faint replicated fleet rows to convey "many units".
      for (let unit = 1; unit < Math.min(fleetUnits, 10); unit++) {
        const row = Math.floor(unit / 3), col = unit % 3;
        for (let s = 0; s < 2; s++) {
          const ghost = box(scene, [3.4, 0.3, 3], [-56 + col * 10 + s * 4, 0.15, 30 + row * 9], '#0f1a22', { emissive: new THREE.Color('#13425a'), emissiveIntensity: 0.15 });
          (ghost.material as THREE.MeshStandardMaterial).transparent = true; (ghost.material as THREE.MeshStandardMaterial).opacity = 0.5;
        }
      }
    }

    let currentSlot = slot, selectedId = selected;
    updates.current = (nextSlot, nextSelected) => {
      currentSlot = nextSlot; selectedId = nextSelected;
      for (const [id, object] of objects) {
        object.label.visible = element.clientWidth >= 620 || id === nextSelected;
        const unavailable = availability[id]?.[nextSlot.slot_index] === false;
        (object.base.material as THREE.MeshStandardMaterial).color.set(id === nextSelected ? '#f6b64b' : unavailable ? (theme === 'dark' ? '#5e2420' : '#d99b90') : pal.base);
        const output = generation[id]?.[nextSlot.slot_index] ?? 0;
        for (const panel of object.panels) (panel.material as THREE.MeshStandardMaterial).emissive.set(output > 0 ? '#1a3a4a' : '#000000');
        for (const fill of object.fills) { fill.scale.x = Math.max(0.02, nextSlot.bess_soc_fraction[id] ?? 0); (fill.material as THREE.MeshStandardMaterial).color.set(unavailable ? '#ff6b5e' : '#35d6a4'); }
        const ctx = object.labelCanvas.getContext('2d')!; ctx.clearRect(0, 0, 256, 110);
        ctx.fillStyle = id === nextSelected ? 'rgba(246,182,75,.95)' : pal.labelBg; ctx.fillRect(0, 0, 256, 110);
        ctx.fillStyle = id === nextSelected ? '#1a1206' : pal.labelInk; ctx.font = '600 31px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(id, 128, 42);
        ctx.font = '24px sans-serif'; ctx.fillStyle = id === nextSelected ? '#2a2008' : pal.labelSub;
        const value = object.kind === 'bess' ? (unavailable ? 'OUTAGE' : `${((nextSlot.bess_soc_fraction[id] ?? 0) * 100).toFixed(1)}% SoC`) : generation[id] ? `${output.toFixed(1)} MW` : object.kind === 'load_class' ? 'consumer' : `${nextSlot.flow_mw.toFixed(1)} MW tie`;
        ctx.fillText(value, 128, 83); object.labelTexture.needsUpdate = true;
      }
    };
    updates.current(slot, selected);

    const raycaster = new THREE.Raycaster(); let pointerStart = [0, 0];
    const down = (event: PointerEvent) => { pointerStart = [event.clientX, event.clientY]; };
    const up = (event: PointerEvent) => {
      if (Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
      for (const hit of raycaster.intersectObjects(scene.children, true)) {
        let object: THREE.Object3D | null = hit.object;
        while (object) { if (object.userData.assetId) { selectRef.current(object.userData.assetId); return; } object = object.parent; }
      }
    };
    renderer.domElement.addEventListener('pointerdown', down); renderer.domElement.addEventListener('pointerup', up);
    const renderScene = () => renderer.render(scene, camera);
    controls.addEventListener('change', renderScene);
    const resize = new ResizeObserver(() => {
      renderer.setSize(element.clientWidth, element.clientHeight);
      camera.aspect = element.clientWidth / element.clientHeight; camera.updateProjectionMatrix();
      camera.position.copy(baseCam).multiplyScalar(camera.aspect < 1.4 ? 1.3 : 1);
      updates.current?.(currentSlot, selectedId); controls.update(); renderScene();
    });
    resize.observe(element);

    let animation = 0, lastTime = 0;
    function render(time: number) {
      const delta = Math.min((time - lastTime) / 1000, 0.05); lastTime = time;
      for (const [id, object] of objects) for (const rotor of object.rotors) rotor.rotation.z += delta * Math.min((generation[id]?.[currentSlot.slot_index] ?? 0) / 10, 2);
      for (const marker of flowMarkers) {
        const power = generation[marker.assetId]?.[currentSlot.slot_index] ?? (marker.assetId === 'grid-main' ? Math.abs(currentSlot.flow_mw) : 0);
        marker.mesh.visible = power > 0;
        marker.phase = (marker.phase + delta * Math.min(power / 60, 0.8)) % 1;
        marker.mesh.position.copy(marker.phase < 0.5 ? marker.start.clone().lerp(marker.corner, marker.phase * 2) : marker.corner.clone().lerp(marker.end, (marker.phase - 0.5) * 2));
      }
      controls.update(); renderer.render(scene, camera); animation = requestAnimationFrame(render);
    }
    animation = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(animation); resize.disconnect(); controls.dispose();
      controls.removeEventListener('change', renderScene);
      renderer.domElement.removeEventListener('pointerdown', down); renderer.domElement.removeEventListener('pointerup', up);
      geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose()); textures.forEach(t => t.dispose());
      renderer.dispose(); renderer.domElement.remove(); updates.current = null;
    };
  }, [assets, generation, availability, scale, fleetUnits, theme]);

  useEffect(() => { updates.current?.(slot, selected); }, [slot, selected]);

  return <div className="scene-shell">
    <div className="scene-label"><span className="status-dot" />{scale === 'city' ? 'CITY-SCALE PROJECTION' : 'UNIT SCHEMATIC'} <span>NOT GEOGRAPHIC</span></div>
    <div ref={host} className="scene" />
    {error && <div className="scene-fallback">3D rendering unavailable. Asset and replay values remain available below.</div>}
    <button className="icon-button scene-reset" title="Reset 3D view" aria-label="Reset 3D view" onClick={() => reset.current()}><RotateCcw size={18} /></button>
    <div className="scene-key"><span><span className="solar-dot" />Solar</span><span><span className="wind-dot" />Wind</span><span><span className="storage-dot" />Storage</span><span><span className="load-dot" />Consumers</span></div>
  </div>;
}
