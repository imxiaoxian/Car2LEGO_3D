# Car2LEGO_3D

独立探索 **生图 → 图片重建 3D → 物理规格校准 → 乐高模型 → Studio .io** 的本地研究项目。
从 [Car2LEGO](https://github.com/imxiaoxian/Car2LEGO) 的图片参考、真实零件和独立验收设计出发，将显式 3D 网格作为新的中间表示。

```text
车型 + 大模型尺寸回答 + 外观参考 → 复用 Car2LEGO GPT 生图服务
                           ↓
         同车外观锚点 + 四张正交图 + 四张补充整车图 + 十张局部特写
                           ↓
    前景与对向轮廓检查 → Hunyuan3D-2mv 联合重建
                           ↓
    尺寸校准 → 车型分类 → 官方 LEGO 结构参考配方
                           ↓
    正交特征轮廓 + 局部模块目标 + 网格细节适配 → LDraw + BOM → Studio .io
```

默认重建使用四视图 `Hunyuan3D-2mv`；原来的单图 TripoSR 仅保留为显式选择的历史实验。
`car` 策略根据车型身份、GPT 外观描述和网格选择车身类别，生成原生轮组、玻璃和曲面零件。
配方类别、官方套装来源、已检查的说明书页码及未实现范围见 [官方配方配置](docs/official-recipes.md)。
SUV / crossover 结合网格横截面、四视图实际像素的车漆边界和玻璃轮廓拟合车体，
以原生曲面零件拟合发动机盖、肩线和车门；薄灯使用平滑透明件，格栅与曲面保险杠使用侧向连接。
灯后支撑与轮腔在装配打包前预留，避免曲面填充遮挡灯组或重新占据轮胎空间。
侧窗按窗台到车顶的高度选择三板或六板高的中空面板，优化拼缝与 B 柱位置，不用透明砖填补余数。
短窗由独立横梁和隐藏纵梁承载车顶；灯带使用平滑透明瓦片和向内连接的黑色灯座，保留分体灯间隔。
前挡风件同时比较窗台高度误差、座舱宽度与原生连接网格，六宽座舱可采用缓倾斜挡风件及半格偏移。
轿跑配方保留连续车顶区间，按缩尺尺寸选择轮胎、车高和轮距，使用独立前后窗和封闭后备箱。
它是有限零件家族的近似拟合，不是通用网格到任意乐高装配的恢复算法。
`plates` 策略保留早期的车形板件雕塑路线，便于对照。
当前是装配草稿，外观、实体拼装、转向和开门机构仍需验证；数字检查按具体产物记录。
实际复现的版本、耗时、显存、结果和限制见 [复现记录](docs/reproduction.md)。

![规格约束车辆的原生零件渲染](examples/f40-physical/03-lego/native-ldraw.png)

上图来自完整实跑，`appearance_accepted=false`。早期板件样例保留在 `examples/f40/`。

## 安装（Windows）

需要 Git、[uv](https://docs.astral.sh/uv/)、Python 3.11。CUDA 运行使用 NVIDIA GPU，测试机器为 RTX 4060 Laptop 8GB。

```powershell
git clone https://github.com/imxiaoxian/Car2LEGO_3D.git
cd Car2LEGO_3D
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -Python 3.11
```

脚本创建 `.venv`，安装 PyTorch 2.5.1 CUDA 12.4 和依赖，下载并固定 Hunyuan3D 的形状重建源码。
首次运行会下载模型权重到 `models/Hunyuan3D-2mv` 和背景移除权重到 rembg 缓存，需联网及数 GB 磁盘空间。
没有全局修改 Python / Conda 环境。GPT 生图、直接对话规格和视觉特征提取复用原项目的 API 配置。
将 `.env.example` 中的 `CAR2LEGO_SOURCE_ROOT` 配置为原项目目录，必要时设置 `CAR2LEGO_SOURCE_PYTHON`。
桥接进程直接调用原来的 `ReferenceImageService.generate()`，输出写入本项目，失败不会自动换用 SD。
生图默认共 19 张：先生成一张主参考图统一外观，其余 18 张按 5、5、5、3 分组，组内并发；本组全部生成并保存成功后，才启动下一组，空出的名额不会提前发送下一组。镜头包括四张正交图、俯视及三个补充斜视图、十张灯组/门窗/过渡/轮拱/内饰特写。各图独立缓存，单张失败可续跑，成功图片不会重复请求；遇到限流会退避并降低组内并发，临时失败最多尝试 4 次。本组仍有失败时暂停后续组，续跑时复用成功缓存。
Hunyuan3D-2mv 实际接收四张正交图；其余图片没有相机标定，用于特征辨认、模块目标和渲染评审，不冒充额外重建相机。仅更新特写不重新推理未变化的网格。
Hunyuan3D-2mv 的代码、权重与许可、复现命令见 [多视角复现](docs/multiview-reconstruction.md)。
显存不足时可用 `--device cpu`，速度更慢。

如果 Git 配置了失效代理，修复本机代理，或仅在本次克隆临时使用 `git -c http.proxy= -c https.proxy= clone ...`。

## 本地生成页面与品牌选择

需要 Node.js 22.12+（本机使用 24）。完成 Python 环境安装后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-web.ps1
```

打开 http://127.0.0.1:8768；API 默认监听 8767，可用 `-Port` 和 `-ApiPort` 指定空闲端口。
桌面端直接复用 `D:\lego\frontend` 的 Next.js 页面、样式、组件和资源：创作页、车型目录、
照片识车、任务详情、参考图、装配查看器、步骤、改装副本与下载。后端适配 `/api/v1`，
生成链路仍使用本项目的 GPT 多视角及特写、Hunyuan3D-2mv 和车型配方。原项目保持只读。
品牌目录收录 396 个品牌的中文名与本地 Logo，
支持热门/全部、中文/英文/别名搜索。列表底部固定“其他”，选择后可输入自定义品牌。
目录包含现存、历史及商用品牌，不保证穷尽全球所有品牌；数据来源和更新方式见
[品牌目录](docs/vehicle-brand-catalog.md)。生图阶段以只读方式调用 `D:\lego` 的参考图服务，产物保存在本项目。

页面提交品牌、车型、可选年款或车辆照片，不再提供比例选择。默认以约 1:12 的体量和细节水平规划，
尺寸可以随外观和真实零件选型调整；后台依次向大模型获取尺寸、生成多视角及特写、重建和装配。
完成后可下载 Studio `.io`、LDraw、MPD、步骤 PDF、CSV、BrickLink XML 和检查报告。
任务参数、日志和产物保存在 `runs/web/<任务ID>/`，详情路径为 `/designs/<任务ID>`；
旧的 `/?job=<任务ID>` 链接会跳转至详情页。暂停在阶段边界生效，恢复会复用已完成且哈希匹配的输出。
重新拟合会保存旧版本并重建装配；改装会创建独立副本。同一服务每次只运行一个生成任务。
服务只监听本机地址，写入接口校验同源请求。

提交设计后直接与配置的大模型对话获取尺寸和轮胎参数，不调用网页搜索或 Codex CLI。
未填年款时由模型明确选择已知世代和配置；未知尺寸保留为空。数值标明大模型提供，不能当作已核实原厂数据。
回答、单位、置信度、所选版本和请求哈希均存档，旧网页搜索缓存不会冒充新对话结果。
装配前结合相关特写，对四张正交原图提取灯组、格栅、进气口、窗线等多边形；特写另生成带来源、置信度和矛盾标记的模块目标，内饰目标可独立于外部多边形存在。特写坐标不会直接投射到网格。
渲染评审同时接收整车、特写与座舱图，检查灯腔、门窗一体关系、前后过渡和座椅仪表。模块目标仍需由有限零件搜索和外观评审落实，生成更多图片本身不代表模型达标。
`03-lego/detail-report.json` 逐项记录对应零件或未表达原因，并绑定装配哈希。
有限比例不能保证完整表达所有细节；有覆盖记录也不代表形状保真或外观验收通过。
当前装配家族仍有尺寸上限；规划记录期望体量、实际选择和能力限制，不能把较小的草稿标成已实现 1:12。
车辆配方不保证车型外观或精确尺寸还原；品牌目录不等同于规格/配方支持目录。
桌面流程复用原项目的真实零件几何、渲染和步骤文档服务，并单独记录碰撞与连接检查。
`scripts/native_audit_worker.py` 输出装配哈希、连接分量和缺少碰撞数据的零件；覆盖不完整时，零碰撞只适用于已有数据的范围。
首页展示图沿用原项目的官方套件图片。步骤 PDF 和连通检查均不代表实际拼搭通过。

可选原生预览需要安装 `.[native]`，并配置自己的 LDraw 库、Blender 和 ImportLDraw 插件目录：

```powershell
uv pip install --python .venv/Scripts/python.exe -e ".[native]"
powershell -ExecutionPolicy Bypass -File scripts/start-web.ps1 -Port 8766 `
  -LDrawLibrary "C:/your/ldraw" -Blender "C:/your/blender.exe" -BlenderAddons "C:/your/blender-scripts"
```

CLI `run` 同样读取 `LDRAW_LIBRARY_PATH`、`LDRAW_MCP_BLENDER`、`BLENDER_USER_SCRIPTS`。
已配置的渲染失败会使流程失败并记录原因，不会把网格图片冒充积木渲染。
M4 的算法改进、同网格对照和剩余问题见 [轿跑实验](docs/coupe-fitting.md)。

前端开发：启动 `.venv/Scripts/python.exe -m car2lego3d desktop-api --port 8767`，
然后在 `frontend` 执行 `npm run dev -- --port 8768`。API 通过 Next.js 同源代理访问。
更改前端端口时，API 进程须设置 `CAR2LEGO_FRONTEND_PORT`；更改 API 端口时，构建前设置 `CAR2LEGO_API_ORIGIN`。
旧 Vite 页面源码仅作历史保留；当前 `npm run build` 构建 Next.js 桌面页面。
浏览器验收截图、测试和页面实际生成记录见 [验证记录](docs/brand-selector-validation.md)。
联网 skill 的运行条件和证据边界见 [规格搜索](docs/vehicle-spec-search.md)。

CLI 获取任意车型规格：

```powershell
.venv/Scripts/python.exe -m car2lego3d research --brand Ferrari --model F40 --output runs/f40-research
```

年款/版本为选填，可用 `--variant 1987` 补充。成功输出 `spec.json` 后，将其传给 `run --vehicle-specs`。
下文静态规格属于历史复现实例。

## 完整流程

```powershell
.venv/Scripts/python.exe -m car2lego3d run `
  --prompt "a red Ferrari F40 sports car, realistic proportions, black tires" `
  --seed 42 --steps 4 --resolution 256 --strategy car --color 4 `
  --vehicle-specs data/vehicles/ferrari_f40_1987.json --output runs/f40
```

从已生成的四视图开始：

```powershell
.venv/Scripts/python.exe -m car2lego3d run `
  --views runs/image/views.json --vehicle-specs path/to/spec.json `
  --output runs/my-car --strategy car
```

默认输入为同一辆汽车的前、左、后、右四张独立正交视图，不能将拼图直接作为一张输入。
`views.json` 记录视图标签和图片哈希；对重复图片、缺失视角和明显不一致的轮廓拒绝重建。
生图保留一张三分之四视角 overview 作为外观锚点，它不进入多视角模型的图像输入。
`run` 要求空输出目录，避免覆盖旧实验。单独阶段可复用已有图片或网格：

```powershell
.venv/Scripts/python.exe -m car2lego3d image --prompt "a red sports car" --output runs/image
.venv/Scripts/python.exe -m car2lego3d reconstruct --views runs/image/views.json --output runs/mesh --resolution 256
.venv/Scripts/python.exe -m car2lego3d lego --mesh runs/mesh/mesh.glb --output runs/lego --length-studs 24 --strategy car
.venv/Scripts/python.exe -m car2lego3d studio --ldraw runs/lego/model.ldr --output runs/studio
```

重建分辨率支持 64 / 128 / 192 / 256，默认 128；`--chunk-size 4096` 控制密度查询分块。
网格上方向默认 Z，其他模型可设置 `--up-axis y`；`--yaw` 旋转水平方向。
转换通过水平主成分方向将车身长轴放到 X。
`car` 当前支持 23–32 studs 长度和 8 / 10 / 12 studs 核心宽度，`--width-studs` 可覆盖选择。
提供规格时默认采用视觉优先规划；`--scale` 和 `--length-studs` 保留为旧接口的尺寸提示，两者互斥。
车宽、轴距、座舱和高度可调整，尺寸偏差仅作参考，不是外观交付的硬性门槛。
标准零件保持原生尺寸，不通过拉伸轮胎或玻璃伪造更大的模型。当前原生模块只能生成较小草稿，
`visual_size_preference.native_capacity_limited` 明确记录与期望约 1:12 体量的差距。
不提供规格时默认 8 studs 宽度，轴位置来自网格低侧暗色区域假设。
`--color 4` 在 `car` 中只覆盖车身颜色，在 `plates` 中覆盖全部颜色；`--no-rear-wing` 去除尾翼。
GPT 默认生成高质量图片；`--image-provider sd-turbo` 和 `--backend triposr` 仅供复查旧实验。

## 物理规格与测量

F40 的整体尺寸、轴距、轮距、前后轮胎规格已核对原厂宣传册和倍耐力资料。
规格 JSON 对每个值记录单位、来源和 `manufacturer / measured / estimated / unknown` 状态。
门长、玻璃大小和倾角尚无可靠数值，保留为空。可通过 `measure` 命令输入正交侧视图标注。
数据来源、换算公式和标注方法见 [物理参数说明](docs/physical-priors.md)。

尺寸先验用于提示词和网格边界校准，并约束乐高宽度、轴距。
轮胎/玻璃保留标准零件原有尺寸；`dimensions.json` 报告无法满足的目标，不缩放真实 LEGO 零件。

## 视觉优先与约 1:12 目标

默认要求完整门窗关系、平滑车身、分层灯组、双座座舱，以及前围、风挡、车顶、后柱和后盖的连续过渡。
本阶段优先静态外观和可靠连接，不要求开门、转向、独立悬挂或实物拼搭反馈。
约 1:12 表示默认体量和细节偏好，不是必须精确满足的比例；见 [尺寸与细节策略](docs/scale-requirements.md)。

已有比例策略仅为旧 API 和历史记录保留。运行时移除机构与实物验收要求，尺寸检查标记为 `advisory`。
当前 Countach 搜索覆盖车顶表面、灯组分段和中央控制台，使用最多 16 个模块候选、宽度 8 的束搜索，
最多 16 轮评估。每个候选先做原生资源、刚体变换、碰撞、连通和接口检查，再进行多视角渲染和视觉评审。
连续三轮提升不足 1% 且关键缺陷未减少，或候选耗尽时，保留最佳合法版本并保持未达标状态。

大尺寸车身、铰接梯形风挡、门窗整体重设计、完整的前后过渡搜索、定制件包及多车型推广尚未完成，
不能把有限模块搜索或一次成功导出称为完整质量闭环已验收。

```powershell
.venv/Scripts/python.exe -m car2lego3d requirements --vehicle-specs data/vehicles/ferrari_f40_1987.json --output runs/requirements
```

新任务保存车型目标、尺寸偏好、候选记录和质量报告。评分只用于候选排序，不能代替最终看图验收。
`examples/f40-scale-1-24/` 等旧样例保留历史结果和哈希，不自动改写为新目标或验收通过。

## 输出

| 路径 | 内容 |
| --- | --- |
| `00-vehicle/spec.json` | 本次物理规格快照（仅规格模式） |
| `00-requirements/requirements.json` | 首次模型调用前保存的视觉体量偏好、能力限制和网格规划（规格模式） |
| `00-recipe/recipe.json` | 重建前选择的车型类别和官方结构参考 |
| `01-image/reference.png`、`image.json` | GPT 外观锚点与调用来源 |
| `01-image/{front,left,back,right}.png`、`views.json` | 四个独立视角、GPT 描述与哈希 |
| `02-mesh/*-foreground.png`、`view-quality.json` | 四视图去背景和轮廓检查 |
| `02-mesh/mesh.glb`、`mesh.obj` | Hunyuan3D-2mv 无纹理联合重建网格 |
| `02-mesh/reconstruction.json` | 重建参数、面数、封闭性、耗时、显存、哈希 |
| `02-mesh/calibrated.glb`、`calibrated-mm.obj` | 长宽高先验校准网格；GLB 用米，OBJ 用毫米；原始网格保留 |
| `02-mesh/physical-calibration.json` | 独立 XYZ 缩放与校准边界记录 |
| `03-lego/model.ldr` | 标准零件 LDraw 与阶段 `0 STEP`；阶段不是已验证拼装说明书 |
| `03-lego/inventory.csv` | 零件编号、LDraw 颜色编号、数量；不是 BrickLink 颜色编号 |
| `03-lego/assembly.json`、`fit.json` | 零件放置、网格证据、拟合参数；`fit.json` 仅 `car` |
| `03-lego/recipe-selection.json` | 最终配方、官方说明书链接和模块审查记录 |
| `03-lego/dimensions.json` | 比例、轴距、轮距、轮胎等目标和当前零件偏差（规格模式） |
| `03-lego/requirements.json`、`requirements-report.json` | 视觉偏好、参考尺寸、资料、步骤和数字证据检查（规格模式） |
| `03-lego/vehicle-target.json` | 独立的几何与任务风格参考、来源置信度和约 1:12 细节偏好 |
| `03-lego/optimization/`、`quality-report.json` | 模块候选、原生检查、多视角渲染、视觉评分与未解决缺陷 |
| `03-lego/mesh-preview.png` | 重建网格静态预览 |
| `03-lego/native-ldraw.png`、`native-ldraw.json` | 可选原生零件渲染和来源哈希 |
| `03-lego/native-render.json` | 渲染完成、未配置或失败状态 |
| `03-lego/validation.json` | 当前策略的数字检查与未验证状态 |
| `04-studio/model.io`、`studio.json` | ZIP .io 与颜色/变换/步骤回读校验 |

在 BrickLink Studio 中打开 `model.io` 检查真实零件、接触和层序。
归档回读不等于 Studio GUI、原生碰撞或实物验收。
`buildability=unverified` 是当前统一状态，即使占用覆盖和连接图通过，也没有实物认证。

四视图 SUV 配方在输出 LDraw 后会检查最终零件的原生连接和碰撞数据。
断开、碰撞、碰撞数据缺失或报告哈希不匹配时会阻止流程继续导出 Studio。
该检查不覆盖插入路径、连接保持力、实际零件颜色库存或实物测试。

通过原生检查的 SUV 会再按连接图重排拼搭步骤：从底盘基础开始，后续每件必须连接到已出现的零件。
`03-lego/build-sequence.json` 记录每件的前置连接索引；重排后重新运行原生几何检查。
这不是插入路径或连接保持力认证。`scripts/prepare_build_trial.py --candidate RUN` 可准备带哈希的试拼包，
包含逐步 PDF、Studio 文件、零件清单和待反馈记录；实物验收不会由该脚本自动置为通过。

四视图 SUV 的外壳拟合优先使用长瓦片和较大的原生曲面件：肩线、发动机盖与车顶在
最大 0.85 板高的轮廓误差内减少接缝，等截面的车顶边缘和下门板采用 2×4 曲面模块。
灯座安装面、后视镜缺口与轮胎净空分别保留，不能通过拉伸零件改变轮廓。
车顶使用薄底板和横梁跨接，不再向座舱内部填充实心支撑。座舱按可用长度放置一排或两排
原生座椅，并加入方向盘、平滑仪表台及有空间时的控制台；座椅数量是比例下的简化表达。
这些零件参与同一套原生碰撞、连接和步骤检查，不代表已完成外观或实物验收。

可另外运行 `scripts/analyze_assembly.py --help` 查看 legolization 结构分析入口。
该脚本复制本模型使用的几何依赖到输出目录，使用进程内独立配置，不写入原项目。
结构分析的失败和 `unverified` 结果必须与原生几何检查分别保留。

## 验证与研究

```powershell
.venv/Scripts/python.exe -m car2lego3d doctor
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/verify_examples.py
```

测试验证坐标轴适配、非等距体素比例、颜色覆盖、stud 图、LDraw 坐标/旋转和透明预处理。
还验证规格单位与来源、轮胎外径换算、轴距约束、真实侧面连接变换和 `.io` 无损回读。
真实模型推理结果在 `examples/` 中，不能以构造测试代表模型质量。
开源方案比较及后续路线见 [调研](docs/research.md)。

安装了 ldraw-mcp、Blender、ImportLDraw 和零件库时，可用 `scripts/render_native.py`
生成真正的零件渲染和哈希记录。这个可选工具不打包第三方运行环境；参数见 `--help`。

## 许可

本项目代码 MIT；模型、依赖和第三方资产各有许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
仓库不包含模型权重、原项目密钥、Studio 零件库或 Python 环境。
