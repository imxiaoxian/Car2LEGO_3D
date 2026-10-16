# 视觉尺寸与细节策略

配置源为 `src/car2lego3d/requirements.py`。前端取消比例选择，默认采用约 1:12 的体量和细节偏好，
车宽、轴距、车高和座舱尺寸允许根据外观及可用原生零件调整。尺寸偏差仅作参考，不能否决外观候选。
用户的任务参考只用于该任务的比较，不写成其他车型的规则。

## 默认目标

- 完整的门板、侧窗、窗框、门槛和立柱关系。
- 连续的前围、风挡、车顶、后柱和后盖过渡。
- 平滑的大面板、楔形和弧面覆盖，减少可见小块拼贴。
- 有灯腔与透明罩层次的灯组，以及车型识别所需的格栅、进气口和轮拱。
- 双座座椅、仪表台、方向盘、中央通道和门内侧空间。
- 静态模型及可靠连接；开门、转向、悬挂机构和实物拼搭不属于本阶段验收。

## 体量与能力记录

`visual_size_preference` 保存 `denominator=12`、`exact=false`、`detail_standard=1:12` 和期望网格。
例如 F40 规格对应的期望核心网格约为 46×20 studs；这是体量参考，不是已生成或验证的尺寸。
规划先在当前模块支持范围中寻找视觉候选，实际零件仍使用原生尺寸。
Countach 当前家族支持 22–32 格长，其余现有车身家族支持 23–32 格长，核心宽度均为 8/10/12 格。
因此目前会记录 `native_capacity_limited=true` 和 `native_module_capacity` 限制，不能声称达到约 1:12 成品。
大尺寸轮组、梯形风挡、门窗和底盘模块需要实现并通过数字检查后，才能扩大这个范围。

`fit.json` 同时记录期望核心尺寸、实际选择和有效体量。轮胎与轴距按实际选择的核心尺寸匹配，
不会在更小车身上继续套用 1:12 轮胎目标；也不会缩放真实零件填补尺寸差距。
原生审计的外包尺寸才是装配的实际尺寸，核心网格不包含所有外伸件。

## 兼容与证据

旧 `--scale`、`--length-studs`、`--width-studs` 接口保留为尺寸提示。
`--scale` 与 `--length-studs` 互斥，超出当前家族范围时允许调整为可用的视觉候选。
旧四档策略保留历史资料字段和步骤上限，不能把其中的比例容差、可动机构或实物验证升级为新任务的硬要求。
`requirements-report.json` 的尺寸项使用 `enforcement=advisory`，不要求实物拼搭证据。

硬检查仍要求资源完整、变换合法、碰撞数据完整、无非法穿插、无悬空以及关键模块的预期连接。
外观评分按轮廓 35%、识别特征 30%、表面与接口 20%、内饰 10%、大致比例 5% 排序。
评分不等于看图验收，导出与 ZIP 回读也不等于 Studio GUI 打开、另存后重开验证。
历史任务保留原记录，不自动回写新验收结果。

## 命令

自动视觉尺寸，无需传入比例：

```powershell
.venv/Scripts/python.exe -m car2lego3d run `
  --views runs/image/views.json --vehicle-specs path/to/spec.json `
  --strategy car --output runs/my-car
```

查看旧接口策略及规划，不调用生图或重建模型：

```powershell
.venv/Scripts/python.exe -m car2lego3d requirements `
  --vehicle-specs data/vehicles/ferrari_f40_1987.json --output runs/requirements
```

新任务的目标和实际拟合保存在 `requirements.json`、`fit.json` 和 `vehicle-target.json`。
是否达到外观要求、数字检查和 Studio 验证分别记录；当前未验收输出继续保留 `delivery_accepted=false`。
