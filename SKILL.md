---
name: video-style-editor
description: 依据用户提供的案例视频，对未剪辑素材做风格仿剪，并自动配音与烧录中文字幕。适用于用户同时给出案例/参考/对标视频和原始素材，要求照着案例的镜头节奏、字幕样式、配音语气剪片并加配音和字幕的场景。Triggers: 仿剪、照着案例剪、案例视频剪辑、参考视频风格、对标视频、加配音和字幕、视频风格迁移、自动剪辑配音一条龙、reference video style edit, style-matched video editing.
agent_created: true
---

# 案例视频风格仿剪 + 配音 + 字幕

## 目的与边界

把「案例视频 + 未剪辑素材」变成一条带配音和烧录字幕的成片：风格参数（镜头节奏、字幕样式、配音语气、画幅）从案例视频测量，画面内容取自素材视频。

必须遵守：

- **一切流程和缓存内容在 D 盘。** 根目录 `D:/VideoEdit`（由 `scripts/config.json` 的 `root` 配置）。venv、pip 缓存、模型、抽帧、切片、配音、日志、成片全部在其下。`common.check_drive()` 拒绝非 D 盘根目录，并把 `TMP/TEMP/TMPDIR/PIP_CACHE_DIR` 指向 D 盘。禁止把中间文件写到 C 盘或系统临时目录。只登记用户源视频的绝对路径，不复制、不修改源文件。
- **不搬运案例素材。** 只提取案例的风格参数与文本结构，不复用例程画面、人声音频或 BGM 音轨。需要配乐时由用户提供音频文件并填入 `edl.json -> bgm.path`。
- **音色是风格逼近，不是克隆。** 默认测量案例人声的音高、语速、明亮度、响度，映射到最接近的 edge-tts 普通话音色并调整 rate/pitch。要一模一样的音色必须接外部语音克隆模型，见 `references/voice.md`。禁止向用户宣称已复刻原声。
- **出片前必须人工确认。** `plan` 之后把剪辑方案与口播稿展示给用户并获得明确确认，才执行 `finish`。禁止跳过该关卡。

## 首次准备

执行 `python scripts/bootstrap.py`，确认输出中：

- `tools.ffmpeg.ok` 与 `tools.ffprobe.ok` 为 true（本机 ffmpeg 9.0.2 全功能版，含 libass、场景检测、whisper 滤镜）；
- `venv.edge_tts_installed` 为 true（依赖装在 `D:/VideoEdit/venv`，pip 缓存在 `D:/VideoEdit/cache/pip`）；
- `cjk_fonts` 含 `msyh.ttc`（微软雅黑）等中文字体。

缺 ffmpeg 时把绝对路径写入 `config.json` 的 `ffmpeg` / `ffprobe`；缺字体时改用 `simhei.ttf` 并同步修改 `config.json -> default_subtitle.font`。

## 工作流

所有命令以本 skill 目录为 cwd，统一走 `scripts/pipeline.py`。`SLUG` 用短横线命名，例如 `vlog-20261110`。

### 阶段 1 登记输入

```
python scripts/pipeline.py init --slug SLUG --reference "案例视频绝对路径" --source "未剪辑视频绝对路径"
```

### 阶段 2 分析案例与素材

```
python scripts/pipeline.py analyze --project SLUG
```

产物位于 `D:/VideoEdit/projects/SLUG/`：

- `reference/meta.json`、`source/meta.json` — 时长、分辨率、fps、音轨
- `reference/shots.json`、`source/shots.json` — 镜头切点、时长分布、中位数/P10/P90、快切比例
- `reference/audio.json`、`source/audio.json` — 静音区间、平均/峰值电平
- `reference/voice_profile.json` — 案例人声 F0、语速、明亮度、停顿比例、音色匹配建议
- `*_contact_sheet_*.jpg` — 每 2 秒一格的接触表；`*/keyframes/` — 每个镜头中间帧

**必须用图片查看工具实际打开接触表和关键帧**，判断画面内容、字幕外观、色调与转场；禁止只读 JSON 数字就写口播稿。判读方法见 `references/style-analysis.md`。

### 阶段 3 生成剪辑方案并等待确认

```
python scripts/pipeline.py plan --project SLUG
```

产出 `edl.json`（节奏对齐初稿）与 `script.md`（口播稿模板）。初稿自动继承案例画幅/fps，并按案例镜头时长序列在素材上铺片段；字幕样式已按案例画幅缩放为最终像素值。

完成以下编辑：

1. 调整 `edl.json -> clips`，让镜头时长向 `reference/shots.json` 的中位数靠拢，保留案例的快慢段落分布；删除无关、抖动、过曝的片段。
2. 结合接触表画面内容把口播稿写入 `script.md`：一句一意、每句 12–18 字、总时长约等于案例时长。
3. 把每句按顺序写入 `edl.json -> lines[].text`，`id` 保持 `l001`、`l002`…。句数决定配音段落数与字幕条数。
4. 依据案例字幕外观调整 `edl.json -> subtitle`（字体、字号、颜色、描边、位置、每行字数）。
5. 按需设置 `voice.voice_id`（留空则用案例音色匹配建议）、`bgm.path`、`target`。

**向用户展示并等待确认**：成片时长、镜头数、口播稿全文、配音音色、字幕样式。得到明确确认后才继续；修改意见回写 `edl.json` 与 `script.md`。字段全解见 `references/workflow.md`。

### 阶段 4 配音、字幕、渲染

```
python scripts/pipeline.py finish --project SLUG
```

依次执行：

- `tts.py` — 逐句合成中文配音（默认 edge-tts，音色/语速/音高来自案例画像），测量每句真实时长并回写 `voice/voice_timing.json`；句间停顿由 `config.json -> line_gap_ms` 控制。
- `subtitle.py` — 按真实配音时间轴生成 `subtitles/main.ass`（烧录用）与 `main.srt`（校对用），自动按画幅限制每行字数、优先在标点处断行。
- `render.py` — 按 EDL 切片 → 统一画幅/fps → 拼接 → 混入配音（可选 BGM 侧链闪避）→ 烧录 ASS → 输出 `output/final.mp4` 与 `output/report.md`。

**配音降级链**：edge-tts → 离线 Windows SAPI → 静音占位。`voice_timing.json -> warnings` 非空时必须告知用户；出现「静音占位」警告时不得当作成品交付。渲染前会检测配音音轨电平，近静音直接报错，防止产出无声成片。

### 阶段 5 交付

用结果展示工具打开 `output/final.mp4`，并汇报 `report.md` 的时长对齐提示与警告。`delta_sec` 绝对值超过 1 秒时（配音比画面长会定格末帧，比画面短会截断尾部），建议用户增删镜头或调整口播稿后重跑。

## 复跑与排错

- 只改画面或字幕样式 → 重跑 `finish`，或单独执行 `subtitle.py` + `render.py`；中间切片在 `build/` 可复用。
- 只改口播稿 → 回填 `lines` 后**必须重跑 `finish`**：配音时长变化会导致字幕时间轴失效。
- 换音色 → 改 `edl.json -> voice.voice_id` / `rate` / `pitch` 后重跑 `finish`。
- 镜头检测偏少 → 调低 `config.json -> scene_probability`（默认 0.20，越低越敏感）后重跑 `init` + `analyze`。
- 查看进度 → `python scripts/pipeline.py status --project SLUG`。
- 更多排错见 `references/workflow.md` 与 `references/ffmpeg-notes.md`。

## 脚本职责

| 文件 | 作用 |
| --- | --- |
| `scripts/config.json` | D 盘根目录、ffmpeg 路径、字幕/渲染默认参数、场景阈值、句间停顿 |
| `scripts/common.py` | D 盘路径铁律、ffmpeg/ffprobe 封装、阶段状态机、音轨电平校验 |
| `scripts/bootstrap.py` | 建目录树、建 venv、装 edge-tts、环境自检 |
| `scripts/analyze.py` | 镜头切分、静音/电平、接触表、关键帧 |
| `scripts/voice_profile.py` | 案例人声 F0/语速/明亮度测量 + 音色映射（纯标准库） |
| `scripts/edl.py` | EDL 模板、校验、时间轴计算 |
| `scripts/tts.py` | 配音合成、音色匹配、降级链、克隆接口 |
| `scripts/subtitle.py` | ASS / SRT 生成 |
| `scripts/render.py` | 切片、拼接、混音、字幕烧录、报告 |
| `scripts/pipeline.py` | 阶段编排（唯一入口） |

## 参考文档

- `references/workflow.md` — EDL 字段全解、阶段状态机、复跑策略、验收清单
- `references/style-analysis.md` — 从案例提取节奏/字幕/语气/BGM 风格并写进 EDL
- `references/voice.md` — 音色模仿原理与局限、edge-tts 音色表、真语音克隆升级路径
- `references/ffmpeg-notes.md` — 关键 ffmpeg 命令与 Windows 路径陷阱
