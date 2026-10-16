# Project isolation

| Project | Modelling method | Local checkout | UI / API |
| --- | --- | --- | --- |
| Car2LEGO_3D | GPT references -> Hunyuan3D-2mv -> mesh -> LEGO | `D:/Car2LEGO_3D` | 8768 / 8767 |
| Car2LGEO_3D_LLM | GPT references -> LLM + Blender MCP -> mesh -> LEGO | `D:/Car2LGEO_3D_LLM` | 8778 / 8777 |

Each project has its own Git database, main branch, Python environment, frontend dependencies, configuration, build output and `runs/` directory. Desktop tasks reject the other project's modelling backend. CLI historical backends remain explicit research options for replay.

`D:/lego` remains a read-only provider of existing image-generation and presentation services. Do not change its source, database or original uncommitted changes. Outputs from either project must remain outside that directory. Secrets, downloaded models, generated runs and local environments are excluded from Git.

The open-source optimization snapshot is `archive/open-source-before-isolation-20261016`. The original Blender worktree is retained as a source archive until migration is verified; do not use it as the active LLM checkout.

Repository: https://github.com/imxiaoxian/Car2LEGO_3D
Companion repository: https://github.com/imxiaoxian/Car2LGEO_3D_LLM
