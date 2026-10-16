"""Package a versioned, explicitly unaccepted physical build trial."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

from car2lego3d.common import digest, write_json
from car2lego3d.studio import verify_archive


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--candidate",type=Path,required=True)
    args=parser.parse_args()
    root=args.candidate.resolve()
    lego=root / "03-lego"
    assembly=lego / "assembly.json"
    rows=json.loads(assembly.read_text(encoding="utf-8"))["parts"]
    audit=json.loads((lego / "native-audit.json").read_text(encoding="utf-8"))
    sequence=json.loads((lego / "build-sequence.json").read_text(encoding="utf-8"))
    if audit["assembly_sha256"] != digest(assembly) or sequence["assembly_sha256"] != digest(assembly):
        raise ValueError("Build trial requires matching final audit and sequence")
    presentation=json.loads((root / "05-presentation/presentation.json").read_text(encoding="utf-8"))
    if presentation["assembly_sha256"] != digest(assembly):
        raise ValueError("Build instructions do not match the final assembly")
    verify_archive(root / "04-studio/model.io",lego / "model.ldr")
    target=root / "06-build-trial"
    target.mkdir(exist_ok=True)
    files={"model.io":root / "04-studio/model.io", "instructions.pdf":root / "05-presentation/instructions.pdf",
           "model.ldr":lego / "model.ldr", "assembly.json":assembly,
           "native-audit.json":lego / "native-audit.json", "build-sequence.json":lego / "build-sequence.json"}
    for name,source in files.items():
        shutil.copyfile(source,target / name)
    for source in (root / "05-presentation").glob("render_*.png"):
        shutil.copyfile(source,target / source.name)
    colors={0:"Black",14:"Yellow",25:"Orange",36:"Trans Red",40:"Trans Black",47:"Trans Clear"}
    counts=Counter((p["part"],p["color"]) for p in rows)
    with (target / "parts.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(["part","ldraw_color","color_name","quantity","description"])
        writer.writerows((part,color,colors.get(color,str(color)),qty,audit["catalog"][part+".dat"]["description"])
            for (part,color),qty in sorted(counts.items()))
    with (target / "steps.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(["step","part_index","part","ldraw_color","module","previous_attachment_index"])
        writer.writerows((p["step"],i,p["part"],p["color"],p["module"],
            sequence["attachments"][i]["parent_index"]) for i,p in enumerate(rows))
    wheel_end=max(p["step"] for p in rows if p["module"] in {"tyres","axles","axle mounting","wheel rims"})
    glass_start=min(p["step"] for p in rows if "windscreen" in p["module"] or p["module"]=="side window glass")
    checkpoints=[dict(id="parts",through_step=0),dict(id="rolling_chassis",through_step=wheel_end),
                 dict(id="body_before_glass",through_step=glass_start-1),
                 dict(id="complete",through_step=max(p["step"] for p in rows))]
    feedback=target / "physical-feedback.json"
    if feedback.exists() and json.loads(feedback.read_text(encoding="utf-8"))["assembly_sha256"] != digest(assembly):
        raise ValueError("Existing physical feedback belongs to another model; use a new candidate directory")
    if not feedback.exists():
        write_json(feedback,dict(assembly_sha256=digest(assembly),physical_verified=False,
            checkpoints=[dict(**c,status="pending",notes="",photos=[]) for c in checkpoints]))
    (target / "START-HERE.md").write_text(f"""# 莲花 For Me 试拼版

本版为 {len(rows)} 件、{max(p['step'] for p in rows)} 步。尚未达到交付验收。
当前只请求分段试拼；外观、插入路径、零件颜色可获取性及实物保持力仍需反馈。

## 文件

- `model.io`：Studio 模型，按 STEP 逐步显示。
- `instructions.pdf`：每页对应一步，新件用黄色高亮。
- `parts.csv`：零件号、LDraw 颜色、数量及零件名称。颜色编号不是 BrickLink 编号。
- `steps.csv`：每件零件的步骤和已存在的连接件索引，索引从 0 开始。
- `physical-feedback.json`：分段反馈记录，不会自动标记实物通过。

## 先试拼底盘

1. 按 `parts.csv` 清点。缺件请记录零件号、颜色、数量，先不要自行改形状。
2. 按 PDF 从第 1 步拼到第 {wheel_end} 步，核查两根 11L 轴、四个轮毂和轮胎。
3. 检查轴是否完全进入轮毂、四轮是否落地、在平桌面前后滚动 20 cm 是否卡住或擦轮拱。
4. 双手托住底盘，离桌约 2 cm 再放回；记录松动或脱落的零件和对应步骤。
5. 有卡死、需要掰弯、强压或拆开已完成结构才可插入的步骤就停在该步并反馈。

底盘通过后再拼到第 {glass_start-1} 步检查侧面曲面与灯组，最后安装玻璃和车顶。
整车检查前后及侧面相似度、玻璃与车顶接缝、灯组、格栅、四轮滚动和拿取保持力。
不要以完成拼接代替外观验收，也不要通过胶水、削切或强压来掩盖不合法连接。

## 反馈格式

版本：`{digest(assembly)[:16]}`
已完成至第 __ 步；缺件 __；卡住步骤 __；松动/脱落零件 __；
滚动是否顺畅 __；与 For Me 不像的部位 __。
请在当前聊天反馈，并附底盘上下、正前、正后、侧面及问题部位照片。

原生碰撞/连接通过只表示数字检查结果。Studio 检查和独立结构分析结果单独保存。
实物拼搭和外观未被接受前，physical_verified 和 delivery_accepted 均保持 false。
""",encoding="utf-8")
    evidence=root / "04-studio/studio-check.json"
    if evidence.exists():
        shutil.copyfile(evidence,target / evidence.name)
    for name in ("studio-saved.io","saved-roundtrip.json"):
        source=root / "04-studio" / name
        if source.exists():
            shutil.copyfile(source,target / name)
    for source in (root / "04-studio").glob("*.png"):
        shutil.copyfile(source,target / source.name)
    analysis=root / "structure-final/diagnostics/report.json"
    if analysis.exists():
        shutil.copyfile(analysis,target / "independent-structure-report.json")
    manifest=dict(assembly_sha256=digest(assembly),physical_verified=False,delivery_accepted=False,
        files={p.name:digest(p) for p in target.iterdir() if p.is_file() and p.name!="manifest.json"})
    write_json(target / "manifest.json",manifest)
    archive=root / "lotus-for-me-build-trial.zip"
    with ZipFile(archive,"w",ZIP_DEFLATED) as stream:
        for file in sorted(target.iterdir()):
            if file.is_file():
                stream.write(file,file.name)
    print(json.dumps(dict(directory=str(target),zip=str(archive),part_count=len(rows),checkpoints=checkpoints)))


if __name__=="__main__":
    main()
