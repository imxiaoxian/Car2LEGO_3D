"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Camera, ChevronRight, ImagePlus, LoaderCircle, X } from "lucide-react";
import { createDesign, createDesignFromImage } from "@/lib/api";
import { showcaseCars } from "@/lib/showcase";
import ProductImage from "@/components/ProductImage";
import BrandSelector from "@/components/BrandSelector";
import { findVehicleBrand } from "@/lib/vehicle-brands";

export default function HomePage() {
  return <Suspense fallback={<div className="empty-state"><LoaderCircle className="spin" /></div>}><CreatePage /></Suspense>;
}

function CreatePage() {
  const router = useRouter();
  const search = useSearchParams();
  const [make, setMake] = useState("");
  const [customMake, setCustomMake] = useState(false);
  const [model, setModel] = useState("");
  const [year, setYear] = useState("");
  const [mode, setMode] = useState<"text" | "photo">("text");
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const car = showcaseCars[selected];

  useEffect(() => {
    const previousJob = search.get("job");
    if (previousJob && /^[a-f0-9]{32}$/.test(previousJob)) {
      router.replace(`/designs/${previousJob}`);
      return;
    }
    const requestedMake = search.get("make") || "";
    const knownMake = findVehicleBrand(requestedMake);
    setMake(knownMake?.name || requestedMake); setCustomMake(Boolean(requestedMake && !knownMake));
    setModel(search.get("model") || ""); setYear(search.get("year") || "");
    const index = showcaseCars.findIndex(item => item.make.toLowerCase() === search.get("make")?.toLowerCase() && item.model.toLowerCase() === search.get("model")?.toLowerCase());
    if (index >= 0) setSelected(index);
  }, [search, router]);
  useEffect(() => {
    if (!photo) { setPreview(""); return; }
    const url = URL.createObjectURL(photo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  function chooseCar(index: number) {
    setSelected(index);
    const item = showcaseCars[index];
    setMake(item.make); setCustomMake(!findVehicleBrand(item.make)); setModel(item.model); setYear(String(item.year)); setError("");
  }

  function selectPhoto(file?: File) {
    if (!file || loading) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) { setError("请选择 JPG、PNG 或 WebP 图片。"); return; }
    if (file.size > 20 * 1024 * 1024) { setError("图片大小不能超过 20 MB。"); return; }
    setPhoto(file); setError("");
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (mode === "photo" && !photo) { setError("请选择车辆照片。"); return; }
    if (mode === "text" && (!make.trim() || !model.trim())) { setError("请选择品牌，并填写车型。"); return; }
    if (year && (!Number.isInteger(Number(year)) || Number(year) < 1900 || Number(year) > 2030)) { setError("年份应在 1900 至 2030 之间。"); return; }
    setLoading(true); setError("");
    try {
      const design = mode === "photo" && photo
        ? await createDesignFromImage(photo, { make: make.trim(), model: model.trim(), year: year ? Number(year) : undefined })
        : await createDesign(make.trim(), model.trim(), year ? Number(year) : undefined);
      router.push(`/designs/${design.id}`);
    } catch (err) { setError(err instanceof Error ? err.message : "创建失败，请重试。"); setLoading(false); }
  }

  return <>
    <div className="product-nav"><div className="content-width"><span>车辆设计工作室</span><Link href="/sets">探索车型 <ChevronRight size={14} /></Link></div></div>
    <section className="create-experience">
      <div className="experience-title"><h1>Car2LEGO</h1><p>你的下一辆，逐块成形。</p></div>
      <div className="product-stage" key={car.set}><ProductImage src={car.image} alt={`${car.make} ${car.model} LEGO 官方套装展示`} className="showcase-image" priority /></div>
      <div className="model-picker" aria-label="展示车型"><div className="model-caption"><span>{car.make} {car.model}</span><span>LEGO {car.set} · 展示车型</span></div><div className="swatches">{showcaseCars.map((item, index) => <button key={item.set} onClick={() => chooseCar(index)} title={`${item.make} ${item.model}`} aria-label={`选择 ${item.make} ${item.model}`} aria-pressed={selected === index} className={`swatch ${selected === index ? "selected" : ""}`} style={{ "--swatch": item.color } as React.CSSProperties} />)}</div></div>
      <form id="create" onSubmit={submit} className="creation-form content-width" aria-label="创建设计">
        <div className="form-heading"><h2>创建你的车型</h2><div className="segmented" role="group" aria-label="输入方式"><button type="button" aria-pressed={mode === "text"} onClick={() => { setMode("text"); setError(""); }}>车型信息</button><button type="button" aria-pressed={mode === "photo"} onClick={() => { setMode("photo"); setError(""); }}><Camera size={15} />车辆照片</button></div></div>
        {mode === "photo" && <div className="upload-row" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); selectPhoto(e.dataTransfer.files[0]); }}>
          <input ref={inputRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={e => selectPhoto(e.target.files?.[0])} hidden aria-label="上传车辆照片" />
          {photo ? <><ProductImage src={preview} alt="已选择的车辆照片" className="upload-preview" /><span className="upload-filename">{photo.name}</span><button type="button" className="icon-button" title="移除照片" aria-label="移除照片" onClick={() => { setPhoto(null); if (inputRef.current) inputRef.current.value = ""; }}><X size={18} /></button></> : <button type="button" className="upload-command" onClick={() => inputRef.current?.click()}><ImagePlus size={22} strokeWidth={1.4} /><span>选择车辆照片</span></button>}
        </div>}
        <div className="form-fields">
          <BrandSelector value={make} custom={customMake} onChange={(value, custom) => { setMake(value); setCustomMake(custom); }} optional={mode === "photo"} disabled={loading} />
          <label className="field"><span>车型{mode === "photo" ? "（可选）" : ""}</span><input autoComplete="off" value={model} onChange={e => setModel(e.target.value)} placeholder="F40" required={mode === "text"} disabled={loading} /></label>
          <label className="field"><span>年款（选填）</span><input type="number" inputMode="numeric" min={1900} max={2030} value={year} onChange={e => setYear(e.target.value)} placeholder="例如 1990" disabled={loading} /></label>
          <button type="submit" className="button primary create-button" disabled={loading}>{loading ? <><LoaderCircle size={18} className="spin" />{mode === "photo" ? "识别中" : "创建中"}</> : <>创建设计 <ArrowRight size={17} /></>}</button>
        </div>
        <div className="form-bottom"><span>BrickLink Studio .io</span></div>
        {error && <p role="alert" className="error-message">{error}</p>}
      </form>
    </section>
    <section className="collection-band" id="collection"><div className="content-width"><div className="section-heading"><h2>经典车型，各有魅力。</h2><Link href="/sets" className="text-link">查看车型目录 <ChevronRight size={16} /></Link></div><div className="collection-grid">{showcaseCars.map((item, index) => <button key={item.set} className="collection-item" onClick={() => { chooseCar(index); document.getElementById("create")?.scrollIntoView({ behavior: "smooth", block: "center" }); }}><ProductImage src={item.image} alt={`LEGO ${item.make} ${item.model}`} className="collection-image" /><div><h3>{item.make} {item.model}</h3><p>{item.subtitle}</p><span className="text-link">选择车型 <ChevronRight size={14} /></span></div></button>)}</div></div></section>
  </>;
}
