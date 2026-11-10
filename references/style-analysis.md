# 从案例视频提取风格参数

案例视频的作用是**风格标尺**，不是内容模板。分析阶段产出客观数据，Agent 必须用图片查看工具打开接触表与关键帧做主观判读，再把结论写进 `edl.json` 与 `script.md`。

## 1. 剪辑节奏与镜头时长

读取 `reference/shots.json`：

- `median_sec` — 镜头时长中位数，是 `clips` 时长的基准。
- `p10_sec` / `p90_sec` — 快切与长镜头的边界。短视频常见 1.2s / 4.5s，讲解类常见 3s / 8s。
- `fast_shot_ratio` — 短于中位数 60% 的镜头占比，反映整体"急促感"。
- `durations[]` — 完整时长序列。`plan` 生成的初稿就是按这个序列循环套到素材上的。

判读要点：

- 看 `durations[]` 是否有**成组的快切段**（连续 3–5 个短镜头）与**收尾长镜头**。若存在，在 `clips` 中复现同样的分组，不要把所有镜头都压成中位数。
- 结合 `reference/audio.json -> silences` 判断旁白停顿是否落在切点上；若是，让长句对应的镜头稍微拉长。
- 素材的 `source/shots.json` 给出可用边界。优先让 `src_start` / `src_end` 贴近素材自带的切点，避免在镜头中间硬切。
- 检测镜头数明显偏少时（例如 30 秒视频只检出 2 个镜头），调低 `config.json -> scene_probability`（0.20 → 0.12）后重跑 `init` + `analyze`；数值过低会把渐变误判为切点。

## 2. 字幕样式与位置

打开 `reference/contact_sheet_*.jpg` 与 `reference/keyframes/`，逐项判断并写入 `edl.json -> subtitle`：

| 观察 | 对应字段 |
| --- | --- |
| 字幕在底部 / 正中 / 顶部 | `alignment`：2 / 5 / 8 |
| 字高约占画面高度的 3%–6% | `fontsize` = 画幅高 × 该比例；模板已按案例画幅缩放，按观感微调 |
| 白字黑描边 / 黄字 / 无描边 | `primary_color`、`outline_color`、`outline` |
| 是否粗体 | `bold`：-1 / 0 |
| 每行约 10–16 字、最多两行 | `max_chars_per_line`；生成时会再按 `宽*0.9/字号` 自动收紧 |
| 字幕距底边约 8% 画幅高 | `margin_v` |
| 字体观感（黑体/圆体/宋体） | `font`：`Microsoft YaHei` / `SimHei` / `SimSun` / `Noto Sans SC` |

ASS 颜色是 `&HAABBGGRR`（**BGR，不是 RGB**）：不透明白 `&H00FFFFFF`，纯黑描边 `&H00000000`，黄色 `&H0000FFFF`。

案例字幕若是逐字高亮（卡拉 OK 式）或带弹跳动画，当前脚本只生成整句常显字幕；向用户说明这一差异，不要假装支持。需要逐字效果时，在 `subtitles/main.ass` 里手工加 `{\k<时长>}` 标签后再单独跑 `render.py`。

## 3. 配音语气与语速

读取 `reference/voice_profile.json`：

- `f0_median_hz` — 音高区间，决定男声/女声组与 `pitch` 偏移。
- `speech_rate_syl_per_sec` — 语速代理，决定 `rate`（每秒 4 音节为 0%，更快加正百分比）。
- `brightness` / `pause_ratio` — 明亮活泼 vs 低沉沉稳，影响音色候选排序与句式长短。
- `match.candidates` — 前五个候选音色，用户不满意时从这里换。

写口播稿时复刻案例的**句式结构**：短句密集的快节奏解说用 8–12 字短句；纪录片式长旁白用 16–20 字长句，并让 `line_gap_ms` 稍大。若案例有 ASR 文本可作为结构参考，但不得照搬内容。

## 4. BGM / 转场 / 调色

- **BGM**：`reference/audio.json` 只给电平，不分离音轨。向用户询问配乐文件或让其确认无 BGM；把文件绝对路径填到 `bgm.path`。`render.py` 用 `sidechaincompress` 做人声闪避，配乐循环铺满。禁止直接从案例视频抽取音频当 BGM。
- **转场**：从关键帧判断硬切为主还是带淡入淡出/闪白。当前渲染只做硬切；若有明显转场，在 `clips[].transition` 标注需求，并在 `render.py` 的切片阶段手工加 `xfade` 或 `fade` 滤镜。
- **调色**：从关键帧判断冷暖、对比度、是否低饱和。脚本**不自动套用调色**，避免把案例色调硬套到不同素材上造成偏色。需要时由 Agent 在 `render.py -> cut_clips()` 的 `vf` 里追加 `eq=contrast=...:saturation=...:gamma=...` 或 `colorbalance`，并把参数记录到 `report.md`。

## 5. 画幅与输出

`edl.json -> target` 默认继承案例的宽高与 fps（`inherited_from_reference: true`）。若用户要发到不同平台（例如案例是 9:16，成片要 16:9），改 `target`，渲染会用 `scale + pad` 做等比留黑边——注意这会裁掉/压缩素材构图，先在接触表上确认主体位置。

## 必须向用户说明的边界

1. 不复用案例视频的画面、人声、音乐，只复用风格参数。
2. 字幕样式、转场、调色依赖 Agent 对抽帧的主观判读，属于近似还原，不是像素级复刻。
3. 音色是声学特征逼近，不是原声克隆。
