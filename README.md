# video-style-editor

一个 WorkBuddy Skill：给一段**案例视频**和一段**未剪辑素材**，自动仿照案例的镜头节奏、画幅、字幕样式与配音语气剪片，AI 撰写口播稿，配音并烧录中文字幕，输出成片。

> 一切流程和缓存内容默认都在 D 盘（`D:/VideoEdit`），包括虚拟环境、模型、抽帧、切片、配音、日志和成片。

## 功能

- **风格测量**：镜头切点与时长分布（中位数 / P10 / P90 / 快切比例）、静音与电平、画幅与 fps、接触表与关键帧、案例人声的 F0 / 语速 / 明亮度 / 停顿密度。
- **节奏仿剪**：按案例的镜头时长序列在素材上铺片段，自动继承案例画幅与 fps。
- **AI 口播稿**：结合抽帧画面内容撰写，一句一意；`plan` 之后必须经用户确认才允许出片。
- **配音**：edge-tts 逐句合成，音色/语速/音高由案例人声画像匹配；每句真实时长驱动后续时间轴。降级链 edge-tts → Windows SAPI → 静音占位（静音会告警且拒绝当作成品）。
- **字幕**：按配音时间轴生成 ASS（烧录）与 SRT（校对），自动按画幅限制每行字数、优先在标点断行。
- **渲染**：切片 → 统一画幅 → 拼接 → 混入配音（可选 BGM 侧链闪避）→ 烧录 ASS → 成片 + 报告。

## 环境要求

- Windows（路径与 D 盘约定面向 Windows；`config.json` 可改根目录）
- ffmpeg / ffprobe（建议 full build，需 libass、libx264）
- Python 3.13+
- 中文字体（微软雅黑 / 黑体）
- 联网以使用 edge-tts（依赖装在 `D:/VideoEdit/venv`）

## 安装

把本目录放到 WorkBuddy 的 skills 目录：

```
<workspace>/.workbuddy/skills/video-style-editor/     # 项目级
~/.workbuddy/skills/video-style-editor/               # 用户级
```

首次执行：

```bash
python scripts/bootstrap.py
```

会创建 `D:/VideoEdit` 目录树、虚拟环境并安装 edge-tts，同时检查 ffmpeg 与中文字体。

## 使用

在 skill 目录下执行（`SLUG` 为工程名）：

```bash
python scripts/pipeline.py init --slug SLUG --reference "案例视频绝对路径" --source "未剪辑视频绝对路径"
python scripts/pipeline.py analyze --project SLUG
python scripts/pipeline.py plan --project SLUG      # 生成 EDL 初稿 + 口播稿模板，等待确认
# 用户确认方案后：
python scripts/pipeline.py finish --project SLUG     # 配音 → 字幕 → 渲染
python scripts/pipeline.py status --project SLUG
```

成片位于 `D:/VideoEdit/projects/SLUG/output/final.mp4`，验收要点见 `output/report.md`。

## 目录结构

```
SKILL.md                 技能说明（WorkBuddy 读取的入口）
README.md                本文件
scripts/
  config.json            D 盘根目录、字幕/渲染默认参数
  common.py              路径铁律、ffmpeg 封装、阶段状态机、音轨电平校验
  bootstrap.py           环境自检与依赖安装
  analyze.py             镜头/音频分析、接触表、关键帧
  voice_profile.py       案例人声声学画像 + 音色映射（纯标准库）
  edl.py                 EDL 模板、校验、时间轴计算
  tts.py                 配音合成、音色匹配、降级链、克隆接口
  subtitle.py            ASS / SRT 生成
  render.py              切片、拼接、混音、字幕烧录、报告
  pipeline.py            阶段编排（唯一入口）
references/
  workflow.md            EDL 字段全解、复跑矩阵、验收清单
  style-analysis.md      从案例提取风格参数的判读指南
  voice.md               音色模仿原理与局限、真语音克隆升级路径
  ffmpeg-notes.md        ffmpeg 命令速查与 Windows 路径陷阱
```

## 边界与限制

- **音色是声学特征逼近，不是原声克隆。** 若需复刻说话人音色，需接入 CosyVoice / GPT-SoVITS 等外部模型，接口契约见 `references/voice.md`；克隆他人声音前须取得授权。
- 不复用案例视频的画面、人声或音乐，只提取风格参数；BGM 需用户提供文件。
- 字幕样式、转场、调色依赖对案例抽帧的判读，属于近似还原；转场与调色滤镜需手工追加。
- 案例有声但 BGM 很响、多人对话或歌唱时，音高/语速估计会失真，建议手工指定音色。
