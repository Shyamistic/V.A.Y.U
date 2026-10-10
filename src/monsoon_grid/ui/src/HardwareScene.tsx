import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RotateCcw } from 'lucide-react';

/* Procedural 3D render of the PROPOSED desk-scale bench components.
   This is an illustrative concept render, not a photo of a built device and not
   a measurement. Geometry approximates the BOM: PCB, ESP32 module, relay board,
   mini-PV cell, fused bus bars and screw-terminal connectors. */

export function HardwareScene({ theme = 'light' }: { theme?: 'light' | 'dark' }) {
  const host = useRef<HTMLDivElement>(null);
  const reset = useRef<() => void>(() => {});
  const [error, setError] = useState(false);

  useEffect(() => {
    const element = host.current!;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true }); }
    catch { setError(true); return; }
    const scene = new THREE.Scene();
    const bg = theme === 'dark' ? '#060a0e' : '#e6ebef';
    const deskColor = theme === 'dark' ? '#0c1218' : '#c7d0d7';
    scene.background = new THREE.Color(bg);
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
    const baseCam = new THREE.Vector3(9, 8, 11);
    camera.position.copy(baseCam);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.setAttribute('role', 'img');
    renderer.domElement.setAttribute('aria-label', 'Proposed desk-scale hardware bench component render, not a built device');
    element.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.4, 0); controls.enableDamping = true;
    controls.minDistance = 7; controls.maxDistance = 26; controls.maxPolarAngle = Math.PI / 2.1;
    reset.current = () => { camera.position.copy(baseCam); controls.target.set(0, 0.4, 0); controls.update(); renderer.render(scene, camera); };

    scene.add(new THREE.HemisphereLight(theme === 'dark' ? '#bcd4ff' : '#eef4fb', theme === 'dark' ? '#0a0f14' : '#c3ccd2', theme === 'dark' ? 1.6 : 2.2));
    const key = new THREE.DirectionalLight('#ffffff', 2.6); key.position.set(-6, 12, 8); key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024); key.shadow.camera.left = -8; key.shadow.camera.right = 8; key.shadow.camera.top = 8; key.shadow.camera.bottom = -8; scene.add(key);
    const fill = new THREE.DirectionalLight(theme === 'dark' ? '#4cc9e8' : '#9fb7c6', 0.5); fill.position.set(6, 4, -6); scene.add(fill);

    const materials: THREE.Material[] = [], geometries: THREE.BufferGeometry[] = [], textures: THREE.Texture[] = [];
    const labels: { sprite: THREE.Sprite }[] = [];
    function mat(color: string, opts: Partial<THREE.MeshStandardMaterialParameters> = {}) { const m = new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.2, ...opts }); materials.push(m); return m; }
    function box(parent: THREE.Object3D, size: [number, number, number], pos: [number, number, number], color: string, opts: Partial<THREE.MeshStandardMaterialParameters> = {}) {
      const g = new THREE.BoxGeometry(...size); geometries.push(g);
      const mesh = new THREE.Mesh(g, mat(color, opts)); mesh.position.set(...pos); mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
    }
    function cyl(parent: THREE.Object3D, r: number, h: number, pos: [number, number, number], color: string, opts: Partial<THREE.MeshStandardMaterialParameters> = {}) {
      const g = new THREE.CylinderGeometry(r, r, h, 20); geometries.push(g);
      const mesh = new THREE.Mesh(g, mat(color, opts)); mesh.position.set(...pos); mesh.castShadow = true; parent.add(mesh); return mesh;
    }
    function label(text: string, pos: [number, number, number], color = '#4cc9e8') {
      const c = document.createElement('canvas'); c.width = 256; c.height = 64;
      const g = c.getContext('2d')!; g.fillStyle = 'rgba(8,13,18,.82)'; g.fillRect(0, 0, 256, 64);
      g.fillStyle = color; g.font = '600 30px sans-serif'; g.textAlign = 'center'; g.fillText(text, 128, 42);
      const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; textures.push(tex);
      const m = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }); materials.push(m);
      const sprite = new THREE.Sprite(m); sprite.scale.set(3, 0.75, 1); sprite.position.set(...pos); scene.add(sprite); labels.push({ sprite });
    }

    // Desk surface.
    const desk = box(scene, [16, 0.3, 12], [0, -0.15, 0], deskColor); (desk.material as THREE.MeshStandardMaterial).roughness = 1;

    // Main PCB.
    const pcb = box(scene, [8, 0.18, 5.4], [0, 0.1, 0], '#0f3b2e', { roughness: 0.5 });
    // copper traces hint
    for (let i = 0; i < 6; i++) box(pcb, [7, 0.02, 0.05], [0, 0.1, -2.2 + i * 0.85], '#1f7a5c', { emissive: new THREE.Color('#0d3326'), emissiveIntensity: 0.4 });

    // ESP32 module.
    box(scene, [2.1, 0.35, 1.5], [-2.4, 0.37, -1.2], '#111820');
    box(scene, [1.1, 0.1, 0.95], [-2.4, 0.57, -1.2], '#c9ccd2', { metalness: 0.7, roughness: 0.3 }); // RF shield
    for (let i = 0; i < 8; i++) box(scene, [0.06, 0.12, 0.06], [-3.3 + i * 0.12, 0.3, -0.4], '#d9b24a', { metalness: 0.8 }); // pins
    label('ESP32 NODE', [-2.4, 1.3, -1.2]);

    // Relay board (3-channel).
    const relay = box(scene, [2.6, 0.3, 1.8], [2.5, 0.35, -1.1], '#15202a');
    for (let i = 0; i < 3; i++) { box(relay, [0.7, 0.75, 0.7], [-0.8 + i * 0.8, 0.5, 0], '#1b3a6b'); cyl(relay, 0.05, 0.5, [-0.8 + i * 0.8, 0.95, 0], '#4cc9e8', { emissive: new THREE.Color('#4cc9e8'), emissiveIntensity: 0.6 }); }
    label('3-CH RELAY', [2.5, 1.5, -1.1], '#f6b64b');

    // Mini-PV cell.
    const pv = box(scene, [2.4, 0.08, 1.6], [-2.5, 0.5, 1.6], '#0e2d3e', { metalness: 0.6, roughness: 0.25 });
    pv.rotation.x = -0.3;
    for (let r = 0; r < 3; r++) for (let col = 0; col < 4; col++) box(pv, [0.5, 0.02, 0.42], [-0.9 + col * 0.6, 0.06, -0.5 + r * 0.5], '#14425a', { emissive: new THREE.Color('#0c2a3a'), emissiveIntensity: 0.5 });
    label('MINI-PV SENSE', [-2.5, 1.3, 1.6]);

    // Fused bus bars (connector motif).
    box(scene, [3.2, 0.12, 0.2], [1.4, 0.4, 1.4], '#b0b6bd', { metalness: 0.85, roughness: 0.25 });
    cyl(scene, 0.18, 0.7, [0.2, 0.5, 1.4], '#d9b24a', { metalness: 0.6 }); // fuse holder
    cyl(scene, 0.18, 0.7, [2.6, 0.5, 1.4], '#d9b24a', { metalness: 0.6 });
    label('FUSED BUS', [1.4, 1.2, 1.4], '#35d6a4');

    // Screw-terminal connectors along the front edge (plus-sign/connector language).
    for (let i = 0; i < 6; i++) { const t = box(scene, [0.55, 0.4, 0.6], [-2.7 + i * 1.08, 0.3, 2.4], '#1b6ae0'); cyl(t, 0.12, 0.18, [0, 0.3, 0], '#c9ccd2', { metalness: 0.8 }); }

    // Virtual BESS indicator block.
    box(scene, [1.6, 1.0, 1.2], [3.1, 0.6, 1.6], '#13231c', { emissive: new THREE.Color('#35d6a4'), emissiveIntensity: 0.25 });
    label('VIRTUAL BESS', [3.1, 1.5, 1.6], '#35d6a4');

    const resize = new ResizeObserver(() => {
      renderer.setSize(element.clientWidth, element.clientHeight);
      camera.aspect = element.clientWidth / element.clientHeight; camera.updateProjectionMatrix();
      controls.update(); renderer.render(scene, camera);
    });
    resize.observe(element);
    const renderScene = () => renderer.render(scene, camera);
    controls.addEventListener('change', renderScene);
    let animation = 0;
    const render = () => { controls.update(); renderer.render(scene, camera); animation = requestAnimationFrame(render); };
    animation = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(animation); resize.disconnect(); controls.dispose(); controls.removeEventListener('change', renderScene);
      geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose()); textures.forEach(t => t.dispose());
      renderer.dispose(); renderer.domElement.remove();
    };
  }, [theme]);

  return <div className="bom-render">
    <div className="scene-label" style={{ position: 'absolute', top: 12, left: 12, zIndex: 2 }}><span className="status-dot" />COMPONENT CONCEPT <span>NOT A BUILT DEVICE</span></div>
    <div ref={host} className="scene" />
    {error && <div className="scene-fallback">3D rendering unavailable.</div>}
    <button className="icon-button scene-reset" title="Reset component view" aria-label="Reset component view" onClick={() => reset.current()}><RotateCcw size={18} /></button>
  </div>;
}
