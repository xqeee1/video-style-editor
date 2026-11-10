# 工作流详解、EDL 结构与验收清单

## 目录树（全部在 D 盘）

```
D:/VideoEdit/
├── venv/                       Python 虚拟环境（edge-tts 装在这里）
├── models/                     whisper ggml、语音克隆模型、clone_engine.py
├── cache/pip/                  pip 下载缓存
├── cache/frames/               抽帧缓存
├── tmp/                        TMP/TEMP/TMPDIR 指向此处
├── logs/
└── projects/<SLUG>/
    ├── project.json            输入登记 + 阶段状态 + 分析摘要
    ├── reference/              案例：meta/shots/audio/voice_profile.json、contact_sheet_*.jpg、keyframes/
    ├── source/                 素材：同上（无 voice_profile）
    ├── script.md               口播稿（Agent 撰写，用户确认）
    ├── edl.json                剪辑决策列表（Agent 编辑，用户确认）
    ├── voice/                  逐句 wav、句间静音、voice.wav、voice_timing.json
    ├── subtitles/              main.ass、main.srt、subtitle_meta.json
    ├── build/                  clip_*.mp4、concat_*.txt、video_mute.mp4、timeline.json
    └── output/                 final.mp4、report.md、render_meta.json
```

`common.check_drive()` 在每次命令执行前校验根目录盘符；`env()` 把 `TMP/TEMP/TMPDIR/PIP_CACHE_DIR` 改写到 `D:/VideoEdit` 下，子进程继承该环境。

## 阶段状态机

`project.json -> stages` 记录 `init / analyze / plan / voice / subtitle / render` 六个阶段。

- `init` 只登记输入路径。
- `analyze` 一次跑完 reference + source 两个角色。
- `plan` **不标记完成**：它只生成模板，`finish` 校验通过后才把 `plan` 标为 `confirmed`。
- `voice` / `subtitle` / `render` 由 `finish` 依次推进。
- `require_stage()` 强制前置阶段完成，避免用旧的配音时间轴去渲染。

`pipeline.py` 没有 `all` 命令，这是刻意设计：`plan` 与 `finish` 之间必须有人类确认。

## EDL 字段全解

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `version` | int | 必须为 1 |
| `project` | string | 工程 slug |
| `source` | string | 未剪辑视频绝对路径（`validate` 会检查存在） |
| `reference` | object | 只读参考信息：案例时长、镜头中位数 |
| `target.width/height/fps` | number | 输出画幅，默认继承案例值 |
| `clips[]` | array | 镜头列表，按数组顺序播放 |
| `clips[].id` | string | 唯一，用作切片文件名，如 `c001` |
| `clips[].src_start` / `src_end` | number | 在**素材视频**中的入点/出点（秒）；差值即成片中该镜头时长 |
| `clips[].transition` | string | 当前仅 `cut`；淡入淡出需手工在 render 里加滤镜 |
| `lines[]` | array | 口播分句，顺序对应配音段落与字幕条 |
| `lines[].id` | string | `l001`…，与 voice_timing 的 segs 一一对应 |
| `lines[].text` | string | 中文句子；标点会被保留但 TTS 会自然停顿 |
| `voice.engine` | string | `edge-tts`（默认）/ `sapi` / `clone` / `file` / `silent` |
| `voice.voice_id` | string/null | null = 用案例音色匹配建议 |
| `voice.rate` / `pitch` / `volume` | string | edge-tts 参数，如 `+8%`、`-12Hz`、`+0%` |
| `voice.source_audio` | string | `engine=file` 时用户自备的配音音频 |
| `bgm.path` | string/null | 用户提供的配乐文件；为空则不加 BGM |
| `bgm.volume_db` | number | 配乐基础增益（默认 -20） |
| `bgm.ducking` | bool | 是否用 sidechaincompress 让人声压过配乐 |
| `subtitle.font` | string | 系统字体名，如 `Microsoft YaHei`、`SimHei` |
| `subtitle.fontsize` | int | 最终像素字号（已按案例画幅缩放） |
| `subtitle.primary_color` / `outline_color` | string | ASS 颜色 `&HAABBGGRR`，注意是 **BGR** 顺序 |
| `subtitle.bold` | int | -1 粗体 / 0 常规 |
| `subtitle.outline` / `shadow` | number | 描边、阴影粗细 |
| `subtitle.alignment` | int | ASS 数字键盘位置：2=底部居中，5=正中，8=顶部居中 |
| `subtitle.margin_v` | int | 距边缘的垂直距离（像素） |
| `subtitle.max_chars_per_line` | int | 每行上限；生成时还会按 `画幅宽*0.9/字号` 自动收紧 |

### 最小可用示例

```json
{
  "version": 1,
  "project": "demo",
  "source": "D:/videos/raw.mp4",
  "target": { "width": 1080, "height": 1920, "fps": 30 },
  "clips": [
    { "id": "c001", "src_start": 3.2, "src_end": 5.6, "transition": "cut" },
    { "id": "c002", "src_start": 12.0, "src_end": 13.8, "transition": "cut" }
  ],
  "lines": [
    { "id": "l001", "text": "清晨的光落在窗台上" },
    { "id": "l002", "text": "我们从一个安静的镜头开始" }
  ],
  "voice": { "engine": "edge-tts", "voice_id": null, "rate": null, "pitch": null },
  "bgm": { "path": null, "volume_db": -20, "ducking": true },
  "subtitle": {
    "font": "Microsoft YaHei", "fontsize": 72,
    "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
    "bold": -1, "outline": 3, "shadow": 1,
    "alignment": 2, "margin_v": 140, "max_chars_per_line": 18
  }
}
```

## 复跑矩阵

| 改动 | 需要重跑 |
| --- | --- |
| 只换 BGM / 只改字幕样式 | `subtitle.py`（若改样式）+ `render.py`，或直接 `finish` |
| 增删镜头（clips） | `render.py`（配音时间轴不变）或 `finish` |
| 改口播文案（lines） | **必须 `finish`**，配音时长变了，字幕轴要重算 |
| 换音色 / 改语速 | `finish` |
| 重新分析案例（改场景阈值） | `init` → `analyze` → `plan` → 重新编辑 EDL → `finish` |
| 想换案例但保留口播稿 | 新 SLUG 重跑全流程，再复制 `lines` 过去 |

所有中间产物保留在 `build/`，不自动清理；磁盘紧张时手动删除该工程目录即可（**不要**删除 `D:/VideoEdit/venv` 与 `models`）。

## 验收清单

1. `output/final.mp4` 存在，ffprobe 显示视频 + 音频两条流。
2. 用 volumedetect 检查音轨：`max_volume` 应高于 -45 dB（近静音说明 TTS 失败或复用了旧文件）。
3. 抽查 1–2 帧：字幕在预期位置、无溢出、无乱码方块。
4. `output/report.md` 的 `delta_sec` 绝对值小于 1 秒；否则提示用户调整。
5. `voice/voice_timing.json -> warnings` 为空；非空必须逐条告知用户。
6. 把 `final.mp4` 与 `report.md` 展示给用户，说明音色是风格逼近而非克隆。

## 常见错误

| 现象 | 原因 / 处理 |
| --- | --- |
| `root must be on D: drive` | `config.json -> root` 被改到别的盘，或 `drive_required` 被改 |
| `run these stages first: ...` | 前置阶段未完成，按顺序执行；`plan` 只有 `finish` 校验后才算完成 |
| `voice.wav is near-silent` | TTS 未正常结束 / 复用了旧占位音轨；重跑 `finish`，检查 `voice_timing.json -> engine` |
| `voice segments (N) != EDL lines (M)` | 改了 lines 但没重跑 voice；重跑 `finish` |
| `final render failed` + ASS 报错 | 字体名不存在或 ASS 编码异常；改 `subtitle.font` 为 `SimHei` 重试 |
| 字幕跑到画面中央 | `margin_v` 相对画幅过大；调小该值（模板已自动缩放，通常是手工改坏） |
| 成片末尾画面定格数秒 | 配音长于画面；缩短口播稿或增加 clips，详见 `report.md` |
