"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RotateCcw, LoaderCircle, Eye, Layers } from "lucide-react";
import { AssemblyScene, getAssemblyGeometry, getAssemblyScene } from "@/lib/api";

const palette: Record<number, string> = { 0:"#17191a",4:"#c91a09",7:"#9ba19d",14:"#f2cd37",15:"#fafafa",25:"#fe8a18",36:"#c91a09",40:"#292b2c",46:"#ff9d28",47:"#fcfcfc",71:"#a0a5a9",72:"#6c6e68" };
const mechanismNames:Record<string,string>={rolling_wheels:"车轮滚动",front_hood:"前盖开合",engine_cover:"发动机盖开合",doors:"车门开合",steering:"前轮转向",independent_suspension:"独立悬挂"};
function jointLabel(j:AssemblyScene["joints"][number]) {
  const location=j.id.startsWith("wheel_") ? `${j.id.includes("left") ? "左" : "右"}${j.id.includes("front") ? "前" : "后"}轮` : mechanismNames[j.capability] || "机构";
  return `${location} · ${j.implemented ? "待整体验收" : "规划预览"}`;
}
export default function AssemblyViewer({ id, highlightedModule = "" }: { id: string; highlightedModule?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const model = useRef<THREE.Group>();
  const data = useRef<AssemblyScene>();
  const reset = useRef<() => void>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sceneData, setSceneData] = useState<AssemblyScene>();
  const [step, setStep] = useState(0);
  const [joint, setJoint] = useState("");
  const [jointValue, setJointValue] = useState(0);
  const [wire, setWire] = useState(false);
  useEffect(() => {
    let cancelled = false, frame = 0;
    let renderer: THREE.WebGLRenderer | undefined, controls: OrbitControls | undefined, observer: ResizeObserver | undefined;
    const disposables: { dispose: () => void }[] = [];
    setLoading(true); setError("");
    void Promise.all([getAssemblyScene(id), getAssemblyGeometry(id)]).then(([assembly, geometries]) => {
      if (cancelled || !host.current) return;
      data.current = assembly; setSceneData(assembly); setStep(assembly.steps.length);
      const scene = new THREE.Scene(); scene.background = new THREE.Color("#f5f5f7");
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); renderer.outputColorSpace = THREE.SRGBColorSpace;
      host.current.appendChild(renderer.domElement);
      const camera = new THREE.PerspectiveCamera(35, 1, .1, 10000);
      controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.dampingFactor = .08;
      scene.add(new THREE.HemisphereLight(0xffffff, 0xa5a5a5, 2.8));
      const light = new THREE.DirectionalLight(0xffffff, 3); light.position.set(-300, -500, 600); scene.add(light);
      const fill = new THREE.DirectionalLight(0xffffff, 1.2); fill.position.set(500, 200, -300); scene.add(fill);
      const root = new THREE.Group(); model.current = root; scene.add(root);
      // Rotate the complete LDraw frame, preserving proper rigid part rotations.
      root.rotation.x = Math.PI;
      const geometryCache = new Map<string, THREE.BufferGeometry[]>();
      Object.entries(geometries).forEach(([name, groups]) => {
        geometryCache.set(name, groups.map(group => {
          const geometry = new THREE.BufferGeometry(); geometry.setAttribute("position", new THREE.Float32BufferAttribute(group.positions, 3));
          geometry.computeVertexNormals(); disposables.push(geometry); return geometry;
        }));
      });
      const stepMap = new Map(assembly.steps.flatMap(s => s.parts.map(p => [p, s.index] as const)));
      assembly.parts.forEach(part => {
        const group = new THREE.Group(); group.name = part.id;
        const r = part.transform.rotation, p = part.transform.position;
        group.matrix.set(r[0],r[1],r[2],p[0],r[3],r[4],r[5],p[1],r[6],r[7],r[8],p[2],0,0,0,1);
        group.matrixAutoUpdate = false; group.userData = { module:part.module, step:stepMap.get(part.id), original:group.matrix.clone() };
        (geometries[part.part_num] || []).forEach((g,index) => {
          const c = g.color_id === 16 ? part.color_id : g.color_id;
          const transparent = (c >= 32 && c <= 47);
          const material = new THREE.MeshStandardMaterial({ color:palette[c] || "#b9b9b9", roughness:.3, metalness:c === 71 ? .18 : .03,
            transparent, opacity:transparent ? c === 40 ? .75 : .45 : 1, depthWrite:!transparent, side:THREE.DoubleSide });
          disposables.push(material); group.add(new THREE.Mesh(geometryCache.get(part.part_num)![index], material));
        });
        root.add(group);
      });
      const bounds = new THREE.Box3().setFromObject(root), center = bounds.getCenter(new THREE.Vector3()), size = bounds.getSize(new THREE.Vector3());
      const radius = Math.max(size.x, size.y, size.z);
      reset.current = () => {
        const vertical=THREE.MathUtils.degToRad(camera.fov),horizontal=2*Math.atan(Math.tan(vertical/2)*camera.aspect);
        const distance=size.length()/2/Math.sin(Math.min(vertical,horizontal)/2)*1.08;
        camera.position.copy(center).add(new THREE.Vector3(-.9,.65,1.2).normalize().multiplyScalar(distance));
        controls!.target.copy(center);controls!.update();
      };
      reset.current(); controls.minDistance = radius*.25; controls.maxDistance = radius*5;
      const resize = () => { if (!host.current || !renderer) return; const {clientWidth:w,clientHeight:h} = host.current; if(!w || !h)return;renderer.setSize(w,h); camera.aspect=w/h; camera.updateProjectionMatrix();reset.current?.(); };
      observer = new ResizeObserver(resize); observer.observe(host.current); resize();
      const animate = () => { if (cancelled) return; controls!.update(); renderer!.render(scene,camera); frame=requestAnimationFrame(animate); };
      animate(); setLoading(false);
    }).catch(err => { if (!cancelled) { setError(err instanceof Error ? err.message : "模型加载失败"); setLoading(false); } });
    return () => { cancelled=true; cancelAnimationFrame(frame); observer?.disconnect(); controls?.dispose(); disposables.forEach(d => d.dispose()); renderer?.dispose(); renderer?.domElement.remove(); model.current=undefined; };
  }, [id]);
  useEffect(() => {
    const assembly=data.current;
    if (!model.current || !assembly) return;
    const selected=assembly.joints.find(j => j.id===joint);
    // Joint preview only moves its actual child module, never dependency modules.
    model.current.children.forEach(group => {
      group.visible=Number(group.userData.step)<=step;
      group.matrix.copy(group.userData.original);
      if (selected && group.userData.module===selected.child) {
        const pivot=new THREE.Vector3(...selected.pivot as [number,number,number]);
        const axis=new THREE.Vector3(...selected.axis as [number,number,number]);
        const move=new THREE.Matrix4();
        if(selected.kind==="revolute") move.makeTranslation(pivot.x,pivot.y,pivot.z).multiply(new THREE.Matrix4().makeRotationAxis(axis,THREE.MathUtils.degToRad(jointValue))).multiply(new THREE.Matrix4().makeTranslation(-pivot.x,-pivot.y,-pivot.z));
        else move.makeTranslation(axis.x*jointValue,axis.y*jointValue,axis.z*jointValue);
        group.matrix.premultiply(move);
      }
      group.traverse(object => { if(object instanceof THREE.Mesh) { const material=object.material as THREE.MeshStandardMaterial; material.wireframe=wire; material.emissive.set(group.userData.module===highlightedModule ? "#785600" : "#000000"); } });
    });
  },[step,joint,jointValue,wire,highlightedModule,sceneData]);
  const selected=sceneData?.joints.find(j => j.id===joint);
  return <div className="assembly-viewer">
    <div ref={host} className="assembly-canvas" />
    {loading && <div className="viewer-overlay"><LoaderCircle className="spin" />模型加载中</div>}
    {error && <div className="viewer-overlay error-message">{error}</div>}
    {!loading && !error && <><div className="viewer-tools"><button className="icon-button" title="恢复视角" aria-label="恢复视角" onClick={() => reset.current?.()}><RotateCcw size={18} /></button><button className="icon-button" title="线框" aria-label="线框" aria-pressed={wire} onClick={() => setWire(v => !v)}><Eye size={18} /></button></div>
      <div className="viewer-bottom"><label><Layers size={16} /><span>步骤 {step} / {sceneData?.steps.length}</span><input aria-label="搭建步骤" type="range" min={1} max={sceneData?.steps.length || 1} value={step} onChange={e => setStep(Number(e.target.value))} /></label>
        <select aria-label="机构预览" value={joint} onChange={e => {setJoint(e.target.value);setJointValue(0);}}><option value="">机构预览</option>{sceneData?.joints.map(j => <option key={j.id} value={j.id}>{jointLabel(j)}</option>)}</select>
        {selected && <input aria-label="机构位置" type="range" min={selected.limits[0]} max={selected.limits[1]} step={1} value={jointValue} onChange={e => setJointValue(Number(e.target.value))} />}
      </div></>}
  </div>;
}
