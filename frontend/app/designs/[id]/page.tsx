"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowDownToLine, ArrowLeft, ArrowRight, Box, Check, ChevronLeft, ChevronRight, ExternalLink, FileCode2, FileSpreadsheet, LoaderCircle, Maximize2, ShoppingBag, X, Pause, Play, Square, RotateCcw, FileText } from "lucide-react";
import { apiFileUrl, controlDesign, customizeDesign, DesignDetail, getDesign, getDesignStatus, getModsCatalog, ModPartInfo, openInStudio } from "@/lib/api";
import dynamic from "next/dynamic";
import { categoryNames, viewNames } from "@/lib/showcase";
import ProductImage from "@/components/ProductImage";
import VehicleSpecifications from "@/components/VehicleSpecifications";
const AssemblyViewer = dynamic(() => import("@/components/viewer/AssemblyViewer"), { ssr:false });
const stateNames: Record<string,string> = { completed:"已验收",draft:"装配草稿",paused:"已暂停",cancelled:"已取消",failed:"生成失败",pending:"等待生成",processing:"设计生成中",passed:"已通过",unverified:"未验证" };
const checkNames:Record<string,string>={passed:"已通过",failed:"未通过",unverified:"未验证",advisory:"参考尺寸"};
const gateNames: Record<string,string> = { parts:"零件数据",transforms:"刚体变换",connections:"结构连接",collisions:"碰撞",dimensions:"尺寸参考",mechanisms:"机构运动",colors:"零件配色",appearance:"外观",feature_retention:"车型细节保留",insertion_paths:"插入路径",reference_consistency:"参考一致性",export_integrity:"导出回读",physical_geometry:"部件物理尺寸",assembly_steps:"搭建步骤" };
const detailViews=new Set(["engine_bay","engine_details","cockpit","seats","doors","hood_opening","steering","suspension","side_intakes","lights","dashboard","door_hinges","hood_hinges","underbody","rear_seats","cargo_bay"]);
type ReferenceView = { view: string; url: string; model: string; synthetic: boolean; category?: string; label?: string; purpose?: string; module?: string };

const failureKindNames: Record<string, string> = { text: "文本模型", image: "生图模型" };

function stageLabel(stage: string) {
  const [phase, section] = stage.split(":");
  if (phase === "recognition") return "识别车辆照片";
  if (phase === "reconstruction") return "多视角 3D 重建";
  if (phase === "reference_images") return `生成${viewNames[section] || "车辆"}参考图`;
  if (phase === "section" || phase === "building") return `构建${viewNames[section] || "车辆部位"}`;
  return ({ starting: "准备设计", vehicle_research:"向大模型获取车型尺寸",reference_brief:"规划参考角度",reference_consistency:"检查整车与细节一致性",planning: "规划结构", assembly:"模块装配", validation:"数字检查",repair:`局部修复 ${section || ""}`,rendering:"真实零件渲染",instructions:"生成步骤说明",exporting:"导出 Studio",acceptance:"整车验收",draft:"装配草稿",paused:"任务已暂停",cancelled:"任务已取消",completed: "设计已完成", pending: "等待生成", processing: "生成模型", failed: "生成失败" } as Record<string, string>)[phase] || "生成模型";
}

function failureCallLabel(kind?: string) {
  return kind ? (failureKindNames[kind] || kind) : "流程阶段";
}

export default function DesignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [design, setDesign] = useState<DesignDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [pollError, setPollError] = useState(false);
  const [activeView, setActiveView] = useState("assembly");
  const [referenceGroup,setReferenceGroup]=useState("all");
  const [expanded, setExpanded] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [modCatalog, setModCatalog] = useState<Record<string, ModPartInfo[]>>({});
  const [selectedMods, setSelectedMods] = useState<Set<string>>(new Set());
  const [activeCategory, setActiveCategory] = useState("all");
  const [customText, setCustomText] = useState("");
  const [customizing, setCustomizing] = useState(false);
  const [customError, setCustomError] = useState("");
  const [openingStudio, setOpeningStudio] = useState(false);
  const [studioResult, setStudioResult] = useState("");
  const [controlBusy, setControlBusy] = useState(false);
  const [highlightModule, setHighlightModule] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true); setError(""); setSelectedMods(new Set()); setCustomText(""); setStudioResult("");
    const poll = async () => {
      try {
        const [status, detail] = await Promise.all([getDesignStatus(id), getDesign(id)]);
        if (cancelled) return;
        setProgress(status.progress); setStage(status.stage || status.status); setDesign(detail); setPollError(false);
        if (["pending", "processing", "paused"].includes(status.status)) timer = setTimeout(poll, 3000);
      } catch { if (!cancelled) { setPollError(true); timer = setTimeout(poll, 5000); } }
    };
    const load = async () => {
      try {
        const [detail, mods] = await Promise.all([getDesign(id), getModsCatalog().catch(() => ({}))]);
        if (cancelled) return;
        setDesign(detail); setModCatalog(mods);
        if (["pending", "processing", "paused"].includes(detail.status)) void poll();
      } catch (err) { if (!cancelled) setError(err instanceof Error ? err.message : "无法加载设计。"); }
      finally { if (!cancelled) setLoading(false); }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id, refreshToken]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (expanded) dialog.showModal(); else dialog.close();
    if (!expanded) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; };
  }, [expanded]);

  async function handleControl(action:string,module?:string) {
    setControlBusy(true);setStudioResult("");
    try {
      await controlDesign(id,action,module);
      const [status, detail] = await Promise.all([getDesignStatus(id), getDesign(id)]);
      setProgress(status.progress); setStage(status.stage || status.status); setDesign(detail);
      if (action === "resume" || action === "retry") setRefreshToken(token => token + 1);
    }
    catch(err) {setStudioResult(err instanceof Error ? err.message : "任务操作失败");}
    finally {setControlBusy(false);}
  }

  async function handleOpenStudio() {
    setOpeningStudio(true); setStudioResult("");
    try {
      const result = await openInStudio(id);
      setStudioResult(result.file_opened ? "已在 Studio 中打开。" : result.studio_opened ? "Studio 已启动，请在其中打开下载的文件。" : "未能启动 Studio，请检查本机安装。" );
    } catch (err) { setStudioResult(err instanceof Error ? err.message : "无法打开 Studio。"); }
    finally { setOpeningStudio(false); }
  }

  async function handleCustomize(event: React.FormEvent) {
    event.preventDefault();
    const selected = Object.entries(modCatalog).flatMap(([category, mods]) => mods.filter(mod => selectedMods.has(mod.id)).map(mod => `${category}: ${mod.name}`));
    const text = [...selected, customText.trim()].filter(Boolean).join("; ");
    if (text.length < 3) { setCustomError("请选择改装项目或填写定制内容。"); return; }
    setCustomizing(true); setCustomError("");
    try { const response = await customizeDesign(id, text); router.push(`/designs/${response.id}`); }
    catch (err) { setCustomError(err instanceof Error ? err.message : "定制失败，请重试。"); }
    finally { setCustomizing(false); }
  }

  if (loading) return <div className="empty-state"><LoaderCircle size={28} className="spin" /><p>正在加载设计</p></div>;
  if (error || !design) return <div className="empty-state"><Box size={36} strokeWidth={1.1} /><h1>无法打开此设计</h1><p className="error-message">{error || "设计不存在。"}</p><Link href="/" className="text-link"><ArrowLeft size={16} />返回工作室</Link></div>;

  const processing = ["pending", "processing"].includes(design.status);
  const completed = design.status === "completed";
  const available = completed || design.status === "draft";
  const allReferences: ReferenceView[] = [
    ...(design.renders || []).map(r => ({view:`render_${r.view}`,url:r.url,model:"ldraw",synthetic:false})),
    ...(design.assembly_preview_url ? [{ view: "assembly", url: design.assembly_preview_url, model: "ldraw", synthetic: false }] : []),
    ...(design.assembly_preview_rear_url ? [{ view: "assembly_rear", url: design.assembly_preview_rear_url, model: "ldraw", synthetic: false }] : []),
    ...(design.external_renders || []).map(image => ({ ...image, model: "ldraw", purpose: "外部渲染辅助检查" })),
    ...(design.reference_images || []),
  ];
  const groups=[{id:"all",label:"全部视图"},{id:"exterior",label:"整车外观"},{id:"details",label:"局部与内饰"},{id:"model",label:"零件渲染"}];
  const isDetailReference = (image: ReferenceView) => image.category === "detail" || image.category === "details" || detailViews.has(image.view);
  const references=allReferences.filter(image => referenceGroup==="all" || (referenceGroup==="model" ? !image.synthetic && image.model === "ldraw" : referenceGroup==="details" ? isDetailReference(image) : image.category === "exterior" || (image.synthetic && !isDetailReference(image))));
  const reference = references.find(image => image.view === activeView) || references[0];
  const referenceIndex = references.indexOf(reference);
  const title = `${design.car?.make || ""} ${design.car?.model || "车辆设计"}`.trim();
  const visibleMods = Object.entries(modCatalog).filter(([category]) => activeCategory === "all" || category === activeCategory).flatMap(([category, mods]) => mods.map(mod => ({ ...mod, category })));
  const exports = [
    { label: "Studio", format: ".io", icon: Box, path: design.file_urls.io },
    { label: "LDraw", format: ".ldr", icon: FileCode2, path: design.file_urls.ldr },
    { label: "子模型", format: ".mpd", icon: FileCode2, path: design.file_urls.mpd },
    { label: "步骤说明", format: ".pdf", icon: FileText, path: design.file_urls.pdf },
    { label: "质量报告", format: ".json", icon: FileText, path: design.file_urls.quality },
    { label: "零件清单", format: ".csv", icon: FileSpreadsheet, path: design.file_urls.csv },
    { label: "BrickLink", format: ".xml", icon: ShoppingBag, path: design.file_urls.xml },
  ];
  const switchView = (offset: number) => setActiveView(references[(referenceIndex + offset + references.length) % references.length].view);

  return <>
    <div className="product-nav"><div className="content-width"><span className="detail-nav-title">{title}</span><div className="product-nav-links"><a href="#references">参考图</a>{available && <><a href="#parts">零件</a><a href="#customize">定制</a></>}</div></div></div>
    <section className="design-experience content-width" id="references">
      <div className="design-heading"><div><Link href="/" className="back-link"><ArrowLeft size={14} />工作室</Link><h1>{title}</h1><p className="secondary">{design.car?.year ? <>{design.car.year} <span className="dot-separator">·</span> </> : null}{design.parent_design_id ? "定制版" : "原始设计"}</p></div><span className={`status-label ${completed ? "success" : design.status === "failed" ? "failure" : ""}`}>{completed ? <Check size={15} /> : processing ? <LoaderCircle className="spin" size={15} /> : null}{stateNames[design.status] || design.status}</span></div>
      {design.scene_url && <AssemblyViewer id={id} highlightedModule={highlightModule} />}
      {!!design.official_build_references?.length && <details className="quality-issues"><summary>官方搭建参考 · {design.official_build_references.length} 套</summary>{design.official_build_references.map(guide=><div key={guide.set_num}><a className="text-link" href={guide.instructions_url} target="_blank" rel="noreferrer">{guide.name} · {guide.set_num}<ExternalLink size={14}/></a><span className="secondary">{guide.local_pdf ? "本地说明书可用" : "官方说明书"}</span></div>)}</details>}
      {design.job && <div className="task-controls">{processing && <button className="icon-button" title="暂停" aria-label="暂停任务" disabled={controlBusy} onClick={() => void handleControl("pause")}><Pause size={18} /></button>}{["paused","failed"].includes(design.status) && <button className="icon-button" title="恢复任务" aria-label="恢复任务" disabled={controlBusy} onClick={() => void handleControl("resume")}><Play size={18} /></button>}{!["completed","cancelled"].includes(design.status) && <button className="icon-button" title="取消任务" aria-label="取消任务" disabled={controlBusy} onClick={() => void handleControl("cancel")}><Square size={17} /></button>}<span className="secondary">文本 {design.job.calls.text} / {design.job.limits.text} · 生图已调用 {design.job.calls.image} 次</span></div>}
      {design.car && <VehicleSpecifications key={id} car={design.car} scale="auto" data={design.physical_specifications} requirements={design.scale_requirements} />}
      {design.quality_report && <section className="quality-section"><div className="section-heading"><h2>装配验收</h2><span className="secondary">assembly_v3</span></div><div className="quality-gates">{Object.entries(design.quality_report.gates).map(([key,value]) => <div key={key}><span>{gateNames[key] || key}</span><strong data-status={value}>{checkNames[value] || value}</strong></div>)}<div><span>Studio 检查</span><strong data-status={design.quality_report.studio.status}>{checkNames[design.quality_report.studio.status]}</strong></div><div><span>实物验证</span><strong>{checkNames[design.quality_report.physical.status]}</strong></div></div><div className="dimension-row">{Object.entries(design.actual_dimensions_mm || {}).map(([key,value]) => <span key={key}>{({length:"长",width:"宽",height:"高",wheelbase:"轴距"} as Record<string,string>)[key]} {value} mm</span>)}</div><details className="quality-issues"><summary>{design.quality_report.issues.length} 项装配问题</summary>{design.quality_report.issues.slice(0,30).map((issue,index) => <div key={index}><button className="text-link" onClick={() => setHighlightModule(issue.modules[0])}>{issue.kind} · {issue.parts.join(", ")}</button><button className="icon-button" title="重试此模块" aria-label={`重试 ${issue.modules[0]}`} disabled={controlBusy || !["draft","failed","paused"].includes(design.status)} onClick={() => void handleControl("retry",issue.modules[0])}><RotateCcw size={15} /></button></div>)}</details></section>}
      {allReferences.length>0 && <div className="reference-groups" role="tablist" aria-label="参考图分类">{groups.filter(group=>group.id==="all" || allReferences.some(image=>group.id==="model" ? !image.synthetic && image.model === "ldraw" : group.id==="details" ? isDetailReference(image) : image.category === "exterior" || (image.synthetic && !isDetailReference(image)))).map(group=><button key={group.id} role="tab" aria-selected={referenceGroup===group.id} onClick={()=>setReferenceGroup(group.id)}>{group.label}</button>)}</div>}
      <div className={`reference-stage ${!reference ? "awaiting" : ""}`}>
        {reference ? <><ProductImage src={apiFileUrl(reference.url)} alt={`${title} ${reference.label || viewNames[reference.view] || reference.view}参考图`} className="reference-main" priority /><button className="icon-button expand-image" onClick={() => setExpanded(true)} title="放大参考图" aria-label="放大参考图"><Maximize2 size={18} /></button></> : <div className="awaiting-content">{processing ? <LoaderCircle size={34} strokeWidth={1.3} className="spin" /> : <Box size={38} strokeWidth={1} />}<p>{processing ? stageLabel(stage) : "暂无车辆参考图"}</p></div>}
      </div>
      {references.length > 0 && <div className="reference-selector"><div className="view-tabs" role="tablist" aria-label="参考图视角">{references.map(image => <button role="tab" aria-selected={reference.view === image.view} key={image.view} onClick={() => setActiveView(image.view)}>{image.label || viewNames[image.view] || image.view}</button>)}</div><span className="reference-disclaimer">{reference.synthetic ? "AI 生成外观参考 · 机构待验证" : reference.purpose === "外部渲染辅助检查" ? "真实零件渲染 · 外部辅助检查" : "真实零件几何渲染"}</span></div>}
      {!!Object.keys(design.external_checks || {}).length && <details className="quality-issues external-checks"><summary>外部辅助检查 · {Object.keys(design.external_checks || {}).length} 项</summary><p className="secondary">外部工具结果仅用于发现问题，不替代数字验收、Studio 检查或实物验证。</p>{Object.entries(design.external_checks || {}).map(([key, check]) => <div key={key}><span>{check.tool || key}</span><strong data-status={check.status}>{checkNames[check.status] || check.status}</strong>{check.bom && <span className="secondary">BOM {check.bom.actual_total ?? "—"} / {check.bom.expected_total ?? "—"} · 缺失几何 {check.bom.missing_parts?.join(", ") || "无"}</span>}{check.analyzer_status && <span className="secondary">结构分析：{check.analyzer_status}</span>}{check.error && <span className="secondary">{check.error}</span>}</div>)}</details>}
      {completed && design.quality_level === "stylized_prototype" && <p className="secondary" style={{ marginTop: 20 }}>概念原型 · 实体搭建仍需在 Studio 中检查连接。</p>}
      {processing && <div className="generation-progress" role="status"><div><span>{stageLabel(stage)}</span><span>{progress}%</span></div><progress max={100} value={progress} aria-label="设计生成进度" />{pollError && <p className="secondary">连接暂时中断，正在重试。</p>}</div>}
      {design.status === "failed" && <div className="failure-message" role="alert"><h2>此次生成未能完成。</h2><p>{design.error_message || "请稍后重试。"}</p>{design.job?.failure && <p className="secondary">中断阶段：{stageLabel(design.job.failure.stage)} · {failureCallLabel(design.job.failure.call_kind)} · 尝试 {design.job.failure.attempts} 次</p>}{!!design.reference_images?.length && <p className="secondary">已保存 {design.reference_images.length} 张参考图{design.job?.resume_stage ? ` · 恢复阶段：${stageLabel(design.job.resume_stage)}` : ""}</p>}<Link className="text-link" href={`/?${new URLSearchParams({ make: design.car?.make || "", model: design.car?.model || "", year: String(design.car?.year || "") })}`}>重新创作 <ArrowRight size={15} /></Link></div>}
      {available && <div className="design-summary"><div className="design-stats"><div><strong>{design.parts_count?.toLocaleString() || "—"}</strong><span>块零件</span></div><div><strong>{({ Easy: "简单", Medium: "中等", Hard: "进阶", Expert: "专家" } as Record<string, string>)[design.difficulty || ""] || design.difficulty || "—"}</strong><span>搭建难度</span></div></div><div className="design-actions">{design.file_urls.io && <><a href={apiFileUrl(design.file_urls.io)} className="button primary"><ArrowDownToLine size={17} />下载 Studio 文件</a><button onClick={handleOpenStudio} disabled={openingStudio} className="text-link">{openingStudio ? <LoaderCircle size={15} className="spin" /> : <ExternalLink size={15} />}在 Studio 中打开</button></>}</div></div>}
      {studioResult && <p className="action-message" role="status">{studioResult}</p>}
      {design.parent_design_id && <Link href={`/designs/${design.parent_design_id}`} className="text-link parent-link">查看原始设计 <ChevronRight size={15} /></Link>}
    </section>
    {available && <>
      <section className="export-band"><div className="content-width export-layout"><h2>带走你的设计。</h2><div className="export-grid">{exports.filter(item => item.path).map(item => <a key={item.format} href={apiFileUrl(item.path)} className="export-item"><item.icon size={26} strokeWidth={1.3} /><div><strong>{item.label}</strong><span>{item.format}</span></div><ArrowDownToLine size={16} /></a>)}</div></div></section>
      <div className="detail-workspace content-width">
        <section id="parts" className="parts-section"><div className="section-heading"><h2>每一块，都有位置。</h2><span className="secondary">{design.parts.length} 种零件</span></div><div className="parts-table-wrap"><table className="parts-table"><thead><tr><th>零件编号</th><th>颜色</th><th>数量</th></tr></thead><tbody>{design.parts.map(part => <tr key={`${part.part_num}-${part.ldraw_color_id}`}><td>{part.part_num}</td><td>{part.color_name || `LDraw ${part.ldraw_color_id}`}</td><td>{part.quantity}</td></tr>)}</tbody></table>{!design.parts.length && <p className="secondary">暂无零件清单</p>}</div></section>
        <section id="customize" className="customize-section"><div className="section-heading"><h2>让经典，更像你。</h2><span className="secondary">车辆定制</span></div>
          {design.customization_request && <p className="previous-request">{design.customization_request}</p>}
          <div className="category-tabs" role="tablist" aria-label="改装类别"><button role="tab" aria-selected={activeCategory === "all"} onClick={() => setActiveCategory("all")}>全部</button>{Object.keys(modCatalog).map(category => <button role="tab" aria-selected={activeCategory === category} onClick={() => setActiveCategory(category)} key={category}>{categoryNames[category] || category}</button>)}</div>
          <div className="mod-list">{visibleMods.map(mod => <label className="mod-option" key={mod.id}><input type="checkbox" checked={selectedMods.has(mod.id)} disabled={customizing} onChange={() => setSelectedMods(previous => { const next = new Set(previous); if (next.has(mod.id)) next.delete(mod.id); else next.add(mod.id); return next; })} /><div><strong>{mod.name}</strong><p>{mod.real_world_ref}</p><span>{categoryNames[mod.category] || mod.category}</span></div><span className="mod-quantity">约 {mod.estimated_parts} 块</span></label>)}</div>
          <form onSubmit={handleCustomize} className="custom-form"><label htmlFor="custom-request">定制内容</label><textarea id="custom-request" rows={3} value={customText} onChange={e => setCustomText(e.target.value)} placeholder="车身改为黑色…" disabled={customizing} /><div className="custom-submit"><span className="secondary">已选择 {selectedMods.size} 项</span><button className="button primary" disabled={customizing || (!selectedMods.size && customText.trim().length < 3)}>{customizing ? <LoaderCircle size={16} className="spin" /> : <ArrowRight size={16} />}创建设计副本</button></div>{customError && <p className="error-message" role="alert">{customError}</p>}</form>
        </section>
      </div>
    </>}
    {reference && <dialog ref={dialogRef} className="image-dialog" onCancel={() => setExpanded(false)} onClick={event => { if (event.target === event.currentTarget) setExpanded(false); }} aria-label={`${title}参考图预览`}><div className="dialog-content"><div className="dialog-heading"><span>{title} · {reference.label || viewNames[reference.view] || reference.view}</span><button className="icon-button" title="关闭预览" aria-label="关闭预览" onClick={() => setExpanded(false)}><X size={22} /></button></div><ProductImage src={apiFileUrl(reference.url)} alt={`${title} ${reference.label || viewNames[reference.view] || reference.view}参考图`} className="dialog-image" /><div className="dialog-controls"><button className="icon-button" disabled={references.length < 2} title="上一张" aria-label="上一张" onClick={() => switchView(-1)}><ChevronLeft size={22} /></button><span>{referenceIndex + 1} / {references.length}</span><button className="icon-button" disabled={references.length < 2} title="下一张" aria-label="下一张" onClick={() => switchView(1)}><ChevronRight size={22} /></button></div></div></dialog>}
  </>;
}
