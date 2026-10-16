"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, ChevronDown, LoaderCircle, Search, SlidersHorizontal, X, Car } from "lucide-react";
import { CatalogVehicle, getVehicleCatalog } from "@/lib/api";
import ProductImage from "@/components/ProductImage";

const bodyNames: Record<string, string> = { sports_car: "跑车", sedan: "轿车", suv: "越野 / SUV", pickup: "皮卡", hatchback: "两厢车" };

export default function BrowsePage() {
  const [vehicles, setVehicles] = useState<CatalogVehicle[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [brand, setBrand] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    void getVehicleCatalog().then(data => { if (active) setVehicles(data.vehicles); }).catch(() => { if (active) setError("车型目录暂时无法加载。"); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [retry]);
  const brands = Array.from(new Set(vehicles.map(v => v.make))).sort();
  const filtered = vehicles.filter(v => (!brand || v.make === brand) && (!body || v.body_style === body) && `${v.make} ${v.model} ${v.year} ${v.visual_targets.join(" ")} ${v.set_num || ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const clear = () => { setQuery(""); setBrand(""); setBody(""); };
  return <div className="catalog-page content-width">
    <div className="catalog-title"><p className="eyebrow">车型目录</p><h1>车型档案。</h1><p className="secondary">{vehicles.length} 款车型 · {brands.length} 个品牌</p></div>
    <div className="catalog-toolbar"><label className="search-field"><Search size={18} /><input aria-label="搜索车型" placeholder="品牌、车型、年份或外观特征" value={query} onChange={e => setQuery(e.target.value)} />{query && <button className="icon-button" title="清空搜索" aria-label="清空搜索" onClick={() => setQuery("")}><X size={16} /></button>}</label>
      <label className="brand-filter"><SlidersHorizontal size={16} /><select aria-label="筛选品牌" value={brand} onChange={e => setBrand(e.target.value)}><option value="">全部品牌</option>{brands.map(name => <option key={name}>{name}</option>)}</select></label>
      <label className="brand-filter"><select aria-label="车身类型" value={body} onChange={e => setBody(e.target.value)}><option value="">全部车身</option>{Object.entries(bodyNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
    </div>
    {loading ? <div className="empty-state"><LoaderCircle className="spin" /><p>正在加载车型</p></div> : error ? <div className="empty-state"><p>{error}</p><button className="button" onClick={() => setRetry(r => r + 1)}>重新加载</button></div> : <>
      <div className="results-count"><span>{filtered.length} 款车型</span>{(query || brand || body) && <button className="text-link" onClick={clear}>清除筛选 <X size={14} /></button>}</div>
      <div className="catalog-grid">{filtered.map(v => <article className="catalog-item" key={v.id}>
        {v.image_url ? <ProductImage src={v.image_url} alt={`${v.make} ${v.model} 官方套件展示`} className="catalog-image" /> : <div className="catalog-no-image"><Car size={48} strokeWidth={1} /><span>{v.make}</span><strong>{v.model}</strong></div>}
        <div className="catalog-kicker"><span>{bodyNames[v.body_style]} · {v.year}</span><span className="catalog-state">实验能力</span></div>
        <h2>{v.make} {v.model}</h2><p className="catalog-description">{v.powertrain}</p>
        <p className="catalog-targets">{v.visual_targets.join(" · ")}</p>
        <div className="catalog-meta"><span>{v.recipe_available ? "车型分类配方" : "配方待实现"}</span></div>
        <details className="vehicle-specs"><summary>车型与装配档案 <ChevronDown size={14} /></summary>
          {v.specifications ? <><dl>{([['length','车长'],['width','车宽'],['height','车高'],['wheelbase','轴距']] as const).map(([key,label]) => <div key={key}><dt>{label}</dt><dd>{v.specifications![key]} mm</dd></div>)}</dl><a href={v.specifications.source} target="_blank" rel="noreferrer" className="text-link">车辆规格来源 <ArrowRight size={12} /></a></> : <p className="secondary">尺寸资料待核实</p>}
          <dl><div><dt>数字验收</dt><dd>按生成结果检查</dd></div><div><dt>Studio / 实物</dt><dd>未验证</dd></div></dl>
          {v.set_num && <p className="secondary">官方套件资料：LEGO {v.set_num.replace(/-\d+$/, "")} {v.image_url ? "· 上图为官方套件" : ""}</p>}
          {v.official_build_references?.map(guide=><p key={guide.set_num}><a className="text-link" href={guide.instructions_url} target="_blank" rel="noreferrer">官方搭建说明 · {guide.set_num}<ArrowRight size={12}/></a></p>)}
        </details>
        <Link className="text-link" href={`/?${new URLSearchParams({ make:v.make, model:v.model, year:String(v.year) })}#create`}>创建设计 <ArrowRight size={15} /></Link>
      </article>)}</div>
      {!filtered.length && <div className="empty-state"><Search size={30} /><h2>没有相关车型</h2><button className="text-link" onClick={clear}>清除筛选</button></div>}
    </>}
  </div>;
}
