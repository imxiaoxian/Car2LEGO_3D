/** API client for the Car2LEGO backend. */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

export function apiFileUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return path.startsWith("/api/v1/")
    ? `${API_URL.replace(/\/api\/v1\/?$/, "")}${path}`
    : `${API_URL.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
}

export interface CarResult {
  id: string;
  make: string;
  model: string;
  year: number | null;
  trim?: string;
  body_style?: string;
  image_url?: string;
  created_at: string;
}

export interface MatchInfo {
  level: number;
  label: string;
  confidence: number;
  set_num?: string;
  set_name?: string;
  moc_name?: string;
  moc_author?: string;
}

export interface DesignPart {
  part_num: string;
  ldraw_color_id: number;
  color_name?: string;
  quantity: number;
  bricklink_id?: string;
  name?: string;
  image_url?: string;
}

export interface Design {
  id: string;
  car_id?: string;
  match?: MatchInfo;
  status: string;
  parts_count?: number;
  difficulty?: string;
  created_at: string;
  completed_at?: string;
  error_message?: string;
}

export interface PhysicalSpecifications {
  version: string;
  acquisition_mode?: "llm_dialogue" | "sourced";
  selected_variant?: string;
  vehicle: { make: string; model: string; year: number | null };
  scale: string;
  applied_to_design?: boolean;
  overall_grounded: boolean;
  generation_status?: "ambiguous" | "source_scoped" | "requested_year" | "unknown";
  source_year_ranges?: { start: number; end: number }[];
  measurements: Record<string, { value: number; unit: string; method: string; grounded: boolean; conflicted: boolean;
    source_url?: string; source_kind: string; source_status: string; vehicle_match: string; confidence: number;
    target_ldu?: number; target_model_mm?: number; evidence: string; definition: string; derivation?: string }>;
  missing: string[];
  required_measurements?: string[];
  recommended_measurements?: string[];
  missing_required?: string[];
  missing_recommended?: string[];
  conflicts: { name: string }[];
  sources: { url: string; status: string; title?: string; error?: string; note?: string; measurement_count?: number }[];
  research?: { skill: string; version: string; status: string; scale?: string; documents_read?: number;
    mode?: string; model?: string; dialogue_count?: number;
    started_at?: string; finished_at?: string; missing_fields?: string[];
    queries: { query: string; purpose: string; status: string; engine?: string; results: { title: string; url: string }[] }[] };
}

export interface DesignDetail extends Design {
  workflow_version?: string;
  requested_scale?: string;
  scale_requirements?: ScaleRequirements;
  scene_url?: string;
  renders?: { view: string; url: string }[];
  external_renders?: { view: string; label: string; url: string; category: string; module?: string; synthetic: boolean }[];
  external_checks?: Record<string, { status: string; tool?: string; version?: string; bom?: { status: string; expected_total?: number; actual_total?: number; missing_parts?: string[]; unknown_colors?: number[] }; analyzer_status?: string; artifact_count?: number; error?: string }>;
  actual_dimensions_mm?: Record<string, number>;
  physical_specifications?: PhysicalSpecifications;
  scale_deviation_percent?: Record<string, number>;
  mechanisms?: { id: string; capability: string; status: string; limits: number[]; reason?: string }[];
  quality_report?: { digital_status: string; precision_verified: boolean; gates: Record<string, string>; studio: { status: string }; physical: { status: string }; issues: { kind: string; parts: string[]; modules: string[] }[] };
  job?: { state: string; stage: string; progress: number; calls: Record<string, number>; limits: Record<string, number | null>; module_repairs: Record<string, number>;
    resume_stage?: string; failure?: { stage: string; call_kind?: string; exception_type: string; attempts: number; retryable: boolean } };
  assembly_preview_url?: string;
  assembly_preview_rear_url?: string;
  quality_level?: string;
  reference_images?: { view: string; url: string; model: string; synthetic: boolean; category?: string; purpose?: string;
    shot_request?: {focus: string; angles: string[]; reason: string; modules: string[]} }[];
  official_build_references?: {set_num: string; name: string; instructions_url: string; product_url: string; local_pages?: number[]; local_pdf?: boolean}[];
  car?: CarResult;
  parts: DesignPart[];
  file_urls: Record<string, string>;
  parent_design_id?: string;
  customization_request?: string;
}

export interface CustomizationResponse {
  id: string;
  status: string;
  message: string;
  parent_design_id: string;
}

export interface DesignStatus {
  id: string;
  status: string;
  progress: number;
  stage?: string;
  error_message?: string;
}

export interface LegoSetInfo {
  set_num: string;
  name: string;
  year?: number;
  brick_count?: number;
  car_make?: string;
  car_model?: string;
  image_url?: string;
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }
  return res.json();
}

/** POST /designs — submit a car and get a LEGO design */
export async function createDesign(
  make: string,
  model: string,
  year?: number | null,
  image_url?: string,
  scale: string = "auto"
): Promise<DesignDetail> {
  return fetchApi<DesignDetail>("/designs", {
    method: "POST",
    body: JSON.stringify({ make, model, year, image_url, scale }),
  });
}

export async function createDesignFromImage(
  image: File, overrides: { make?: string; model?: string; year?: number; scale?: string } = {}
): Promise<DesignDetail> {
  const form = new FormData();
  form.append("image", image);
  form.append("scale", overrides.scale || "auto");
  if (overrides.make) form.append("make", overrides.make);
  if (overrides.model) form.append("model", overrides.model);
  if (overrides.year) form.append("year", String(overrides.year));
  const response = await fetch(`${API_URL}/designs/from-image`, { method: "POST", body: form });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

/** GET /designs/:id */
export async function getDesign(id: string): Promise<DesignDetail> {
  return fetchApi<DesignDetail>(`/designs/${id}`);
}

export async function collectVehicleSpecifications(make: string, model: string, year: number | null, scale: string): Promise<PhysicalSpecifications> {
  return fetchApi("/research/physical-specifications", {
    method: "POST", body: JSON.stringify({ make, model, year, scale }),
  });
}

/** GET /designs/:id/status */
export async function getDesignStatus(id: string): Promise<DesignStatus> {
  return fetchApi<DesignStatus>(`/designs/${id}/status`);
}

/** GET /designs/:id/parts */
export async function getDesignParts(id: string): Promise<DesignPart[]> {
  return fetchApi<DesignPart[]>(`/designs/${id}/parts`);
}

/** GET /sets/known-cars */
export async function getKnownCars(): Promise<LegoSetInfo[]> {
  return fetchApi<LegoSetInfo[]>("/sets/known-cars");
}

/** GET /sets */
export async function searchSets(
  make?: string,
  model?: string
): Promise<LegoSetInfo[]> {
  const params = new URLSearchParams();
  if (make) params.set("make", make);
  if (model) params.set("model", model);
  return fetchApi<LegoSetInfo[]>(`/sets?${params.toString()}`);
}

/** GET /cars/lookup */
export async function lookupCar(make: string, model: string, year?: number | null) {
  return fetchApi<CarResult>("/cars/lookup", {
    method: "POST",
    body: JSON.stringify({ make, model, year }),
  });
}

/** POST /designs/:id/customize — customize an existing design */
export async function customizeDesign(
  designId: string,
  customizationText: string,
  scale?: string
): Promise<CustomizationResponse> {
  return fetchApi<CustomizationResponse>(`/designs/${designId}/customize`, {
    method: "POST",
    body: JSON.stringify({ customization_text: customizationText, scale }),
  });
}

/** GET /designs/:id/ldr — get raw LDraw content for 3D viewer */
export async function getDesignLdr(id: string): Promise<string> {
  const url = `${API_URL}/designs/${id}/ldr`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load LDraw: ${res.status}`);
  return res.text();
}

/** POST /designs/:id/studio-open — open design in BrickLink Studio */
export async function openInStudio(designId: string): Promise<{
  success: boolean;
  io_path: string;
  studio_opened: boolean;
  file_opened: boolean;
}> {
  return fetchApi(`/designs/${designId}/studio-open`, { method: "POST" });
}

/** GET /mods — get all modification parts catalog */
export interface ModPartInfo {
  id: string;
  name: string;
  category: string;
  real_world_ref: string;
  description: string;
  difficulty: string;
  estimated_parts: number;
  visual_change: string;
  lego_parts_count: number;
}

export async function getModsCatalog(): Promise<Record<string, ModPartInfo[]>> {
  return fetchApi<Record<string, ModPartInfo[]>>("/mods");
}

export interface ScaleRequirements {
  version: string; scale: string; label: string; description: string; applied_to_design?: boolean;
  visual_requirements: { id: string; label: string; detail: string }[];
  mechanisms: { id: string; label: string; minimum_instances: number }[];
  reference_views: string[]; required_measurements: string[]; recommended_measurements: string[];
  dimension_tolerances_percent: Record<string, number>;
  component_tolerances_percent: { tyre_diameter: number; tyre_width: number };
  physical_checks: string[]; motion_sample_count: number; max_step_parts: number;
}
export interface ScaleCapability { ratio: string; mechanisms: string[]; status: string; recipe_available: boolean; precision_supported: boolean; requirements?: ScaleRequirements }
export interface CatalogVehicle {
  id: string; make: string; model: string; year: number; body_style: string; powertrain: string;
  visual_targets: string[]; set_num?: string; image_url?: string; image_kind: string;
  specifications?: { length: number; width: number; height: number; wheelbase: number; source: string };
  official_build_references?: {set_num: string; name: string; instructions_url: string; product_url: string}[];
  scale_capabilities: ScaleCapability[]; recipe_available: boolean; precision_verified: boolean;
}
export async function getVehicleCatalog(): Promise<{ vehicles: CatalogVehicle[] }> { return fetchApi("/sets/vehicles"); }
export async function getScales(): Promise<{ scales: ScaleCapability[]; default: string }> { return fetchApi("/designs/scales"); }
export async function controlDesign(id: string, action: string, module?: string) {
  return fetchApi<DesignStatus>(`/designs/${id}/control/${action}${module ? `?module=${encodeURIComponent(module)}` : ""}`, { method: "POST" });
}
export interface ScenePart { id: string; part_num: string; color_id: number; module: string; feature: string; transform: { position: number[]; rotation: number[] } }
export interface AssemblyScene { version: string; parts: ScenePart[]; modules: { id: string; depends_on: string[] }[]; steps: { index: number; module: string; parts: string[] }[]; joints: { id: string; child: string; parent: string; axis: number[]; pivot: number[]; limits: number[]; kind: string; capability: string; implemented: boolean }[] }
export function getAssemblyScene(id: string): Promise<AssemblyScene> { return fetchApi(`/designs/${id}/scene`); }
export function getAssemblyGeometry(id: string): Promise<Record<string, { color_id: number; positions: number[] }[]>> { return fetchApi(`/designs/${id}/geometry`); }
