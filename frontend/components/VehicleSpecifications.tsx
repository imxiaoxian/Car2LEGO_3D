"use client";

import { useState } from "react";
import { ExternalLink, LoaderCircle, Search } from "lucide-react";
import { CarResult, collectVehicleSpecifications, PhysicalSpecifications, ScaleRequirements } from "@/lib/api";

const names: Record<string, string> = {
  front_tyre_aspect: "前轮胎扁平比", rear_tyre_aspect: "后轮胎扁平比",
  front_overhang: "前悬", rear_overhang: "后悬", door_panel_height: "车门板高度",
  door_front_x: "车门前缘位置", windscreen_bottom_x: "风挡底边位置",
  windscreen_bottom_width: "风挡底边宽度", windscreen_top_width: "风挡顶边宽度",
  windscreen_slant_height: "风挡斜高", windscreen_angle_to_horizontal: "风挡倾角（相对水平面）",
  length: "车身长度", width: "车身宽度（不含后视镜）", height: "车身高度", wheelbase: "轴距",
  front_track: "前轮距", rear_track: "后轮距", front_tyre_diameter: "前轮胎外径", rear_tyre_diameter: "后轮胎外径",
  front_tyre_width: "前轮胎宽度", rear_tyre_width: "后轮胎宽度", front_rim_diameter: "前轮毂直径", rear_rim_diameter: "后轮毂直径",
  door_length: "车门长度", door_height: "车门高度（含玻璃）", door_opening_angle: "车门开启角",
  windshield_width: "前风挡宽度", windshield_height: "前风挡斜高", windshield_rake: "前风挡倾角（相对水平面）",
  seat_width: "座椅宽度", engine_length: "发动机长度", engine_width: "发动机宽度", engine_height: "发动机高度",
};

type Props = {
  car: CarResult; scale: string; data?: PhysicalSpecifications; requirements?: ScaleRequirements;
};

export default function VehicleSpecifications(props: Props) {
  const { car, scale } = props;
  return <VehicleSpecificationsView key={JSON.stringify([car.id, car.make, car.model, car.year, scale])} {...props} />;
}

function VehicleSpecificationsView({ car, scale, data, requirements }: Props) {
  const [collected, setCollected] = useState<PhysicalSpecifications | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const specs = collected || data;
  const measurements = Object.entries(specs?.measurements || {});
  const required = requirements?.required_measurements || specs?.required_measurements;
  const research = specs?.research;
  const dialogue = specs?.acquisition_mode === "llm_dialogue";
  const queries = research?.queries || [];
  const found = measurements.filter(([, m]) => m.grounded).length;
  const vehicleName = [car.year, car.make, car.model].filter(Boolean).join(" ");

  async function collect() {
    setBusy(true); setError("");
    try { setCollected({ ...await collectVehicleSpecifications(car.make, car.model, car.year, scale), applied_to_design: false }); }
    catch (err) { setError(err instanceof Error ? err.message : "车型资料获取失败"); }
    finally { setBusy(false); }
  }

  return <section className="physical-specifications" aria-label="车型尺寸与结构资料">
    <div className="section-heading"><h2>车型尺寸与结构资料</h2><div className="physical-spec-actions">
      <span className="secondary">{busy ? "正在获取车型资料" : specs?.applied_to_design ? "已用于装配规划" : collected ? "新获取资料" : "车型资料"}</span>
      <button type="button" className="icon-button" title="向大模型获取车型尺寸与结构资料" aria-label="向大模型获取车型尺寸与结构资料" disabled={busy} onClick={() => void collect()}>
        {busy ? <LoaderCircle size={18} className="spin" /> : <Search size={18} />}
      </button></div></div>
    <p className="secondary physical-research-summary" role="status">{busy ? `${vehicleName} · 大模型对话中` : dialogue ? `大模型提供 · ${measurements.length} 项尺寸 · 未经原厂资料核实` :
      research?.skill ? `${research.status === "unavailable" ? "搜索服务暂不可用" : research.status === "searching" ? "车型检索中" : "车型检索完成"} · ${queries.filter(query => query.status === "searched").length} 组搜索 · ${research.documents_read || 0} 份原文 · ${found} 项尺寸` :
      `${vehicleName} · ${found} 项有来源的尺寸`}</p>
    {specs?.selected_variant && <p className="secondary">适用版本：{specs.selected_variant}</p>}
    {specs?.generation_status === "ambiguous" && <p className="error-message" role="status">资料涉及不同世代，尺寸暂不用于精确约束。可补充年款或世代以限定车型。</p>}
    {car.year == null && specs?.generation_status === "source_scoped" && <p className="secondary">资料适用年款：{specs.source_year_ranges?.map(range => `${range.start}–${range.end}`).join("、")}</p>}
    {error && <p className="error-message" role="alert">{error}</p>}
    {measurements.length > 0 ? <div className="parts-table-wrap"><table className="parts-table physical-spec-table">
      <thead><tr><th>项目</th><th>规划用途</th><th>实车</th><th>模型参考</th><th>资料状态</th><th>来源</th></tr></thead>
      <tbody>{measurements.map(([name, m]) => <tr key={name}><td>{names[name] || name}</td>
        <td>{required?.includes(name) ? "结构参考" : "细节参考"}</td>
        <td>{m.value.toLocaleString(undefined, { maximumFractionDigits: 2 })} {m.unit === "deg" ? "°" : m.unit}</td>
        <td>{m.unit === "deg" ? `${m.value}°` : scale === "auto" ? "按零件选型" : m.target_model_mm == null ? "待换算" : `${m.target_model_mm.toFixed(2)} mm`}</td>
        <td>{dialogue ? m.method === "derived" ? "回答换算" : "大模型提供" : m.conflicted ? "多来源差异" : m.method === "estimated" ? "估算" : m.grounded ? m.method === "derived" ? "规格换算" : "原文数据" : "适用版本待确认"}</td>
        <td>{dialogue ? <span title={m.evidence}>模型回答</span> : m.source_url ? <a className="text-link" href={m.source_url} target="_blank" rel="noreferrer" title={m.evidence}>资料<ExternalLink size={12} /></a> : "缺少来源"}</td>
      </tr>)}</tbody></table></div> : <p className="secondary">尚无可用的尺寸资料</p>}
    {!!specs?.conflicts.length && <p className="error-message">{specs.conflicts.length} 项资料存在数值冲突，未作为精确尺寸约束。</p>}
    {!!specs?.missing.length && <details className="quality-issues"><summary>尚未找到的尺寸 · {specs.missing.length} 项</summary>
      <p className="secondary physical-missing">{specs.missing.map(name => names[name] || name).join("、")}</p>
    </details>}
    {!!queries.length && <details className="quality-issues physical-sources"><summary>车型搜索记录 · {queries.length} 组</summary>
      {queries.map((query, index) => <div key={`${query.query}-${index}`}><span className="physical-search-query">{query.query}</span>
        <span className="secondary">{query.status === "searched" ? `${query.results.length} 条结果` : "搜索暂不可用"}</span></div>)}
    </details>}
    {!!specs?.sources.length && <details className="quality-issues physical-sources"><summary>原文来源 · {specs.sources.length} 份</summary>
      {specs.sources.map((source, index) => <div key={`${source.url}-${index}`}><a className="text-link" href={source.url} target="_blank" rel="noreferrer">{source.title || new URL(source.url).hostname}<ExternalLink size={12} /></a>
        <span className="secondary">{source.status === "fetched" ? `已读取 · ${source.measurement_count || 0} 项尺寸` : "暂不可用"}</span>{source.note && <span className="secondary">适用版本待确认</span>}</div>)}
    </details>}
  </section>;
}
