export const showcaseCars = [
  { make: "Ferrari", model: "F40", year: 1990, image: "/images/ferrari-f40.webp", color: "#c72826", set: "76934", subtitle: "经典红色。永恒轮廓。" },
  { make: "Porsche", model: "911", year: 1989, image: "/images/porsche-911.webp", color: "#d9d9d7", set: "10295", subtitle: "一眼经典，每一块亦然。" },
  { make: "McLaren", model: "P1", year: 2015, image: "/images/mclaren-p1.webp", color: "#e9ae28", set: "42172", subtitle: "性能之美，逐块呈现。" },
];

export const viewNames: Record<string, string> = {
  left:"左侧",right:"右侧",
  render_front:"零件前视",render_rear:"零件后视",render_left:"零件左视",render_right:"零件右视",render_top:"零件顶视",render_bottom:"零件底视",
  assembly: "装配模型",
  assembly_rear: "模型后视",
  overview: "整车", chassis: "底盘", front: "前部", body: "车身", rear: "尾部", wheels: "车轮",
  engine_bay: "发动机舱", engine_details: "发动机细节", cockpit: "仪表台与内饰", seats: "座椅细节",
  doors: "车门开合", hood_opening: "前盖与后盖开合", steering: "转向细节", suspension: "悬挂与转向",
  dashboard: "仪表台特写", door_hinges: "车门铰链与间隙", hood_hinges: "前后盖铰链与间隙",
  underbody: "底盘结构特写", rear_seats: "后排座舱", cargo_bay: "储物与检修空间",
  side_intakes: "侧进气口", lights: "灯组细节",
  top: "整车俯视", front_right: "右前整车", rear_left: "左后整车", rear_right: "右后整车",
  front_lights: "前灯与前脸", rear_lights: "尾灯与后脸", door_windows: "车门与侧窗",
  windscreen_cowl: "前挡风与前围", rear_transition: "座舱与后盖过渡", wheel_arches: "轮毂与轮拱",
};

export const categoryNames: Record<string, string> = {
  aerodynamics: "空气动力", wheels_stance: "轮毂与姿态", exhaust: "排气",
  body_kits: "车身套件", interior: "内饰", lighting: "灯光", paint_finish: "配色", performance: "性能",
};
