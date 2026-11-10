# 配音：模仿案例音色

## 默认路径：声学特征逼近（开箱可用）

`voice_profile.py` 完全用 Python 标准库实现，不下载任何模型：

1. **提取案例人声轨**：ffmpeg 下混单声道 → `highpass=85` / `lowpass=7500` 保留语音带 → `afftdn` 轻度降噪 → 8 kHz / 16-bit wav，落在 `reference/_voice_work/voice_track.wav`。
2. **测量**：
   - F0：100 ms 一帧，自相关法（lag 20–114），取有声帧中位数；
   - 语速代理：20 ms 能量包络的峰值数 / 时长，近似"音节每秒"；
   - 明亮度：过零率 ZCR 映射到 0–1；
   - 电平：RMS 折算 dBFS；
   - `voiced_ratio` / `pause_ratio`：有声帧占比，反映停顿密度。
3. **映射**：与 `VOICE_TABLE` 比较音高差 + 明亮度差，标准普通话音色优先（方言/台湾腔加 0.9 惩罚作为 tie-breaker），输出 `selected` 及前 5 个候选；`rate` 按语速算 ±25%，`pitch` 按 F0 差算 ±40 Hz。
4. **合成**：`tts.py` 逐句调用 edge-tts，把 mp3 转成 48 kHz 单声道 wav，按实际时长拼接，句间插入 `config.json -> line_gap_ms` 静音。

输出 `voice/voice_timing.json` 记录每句真实起止时间，字幕与成片长度都以它为准。

### 内置音色表（`scripts/voice_profile.py -> VOICE_TABLE`）

| voice id | 基准 F0 | 气质 |
| --- | --- | --- |
| `zh-CN-YunjianNeural` | 108 Hz | 男声 浑厚 纪录片/解说 |
| `zh-CN-YunyangNeural` | 116 Hz | 男声 新闻播报 沉稳 |
| `zh-CN-YunxiNeural` | 126 Hz | 男声 青年 自然口语 |
| `zh-CN-YunxiaNeural` | 148 Hz | 男声 少年感 轻快 |
| `zh-CN-XiaoxiaoNeural` | 198 Hz | 女声 温柔 通用 |
| `zh-CN-XiaoyiNeural` | 212 Hz | 女声 活泼 年轻 |
| `zh-CN-liaoning-XiaobeiNeural` | 190 Hz | 女声 东北 生活化 |
| `zh-CN-shaanxi-XiaoniNeural` | 194 Hz | 女声 陕西 亲和 |
| `zh-TW-HsiaoChenNeural` | 200 Hz | 女声 台湾腔 温和 |
| `zh-CN-YunfengNeural` | 120 Hz | 男声 低沉 成熟 |

覆盖方式：在 `edl.json -> voice` 填 `voice_id`、`rate`（如 `+8%`）、`pitch`（如 `-10Hz`）、`volume`。留空则使用 `reference/voice_profile.json -> match` 的建议。

## 局限（必须如实告知用户）

- **不是音色克隆。** 输出是"音高/语速/明亮度最接近的现成神经音色"，说话人的音色质感、口音、咬字习惯不会复现。
- 案例音频里 BGM 很响、多人对话、方言或歌唱时，F0 与语速估计会失真，映射结果可能明显跑偏；此时让用户直接指定 `voice_id`。
- F0 只能粗分男女声区，150–170 Hz 是模糊带，不要据此推断性别。
- 案例视频没有音轨时，`voice_profile.valid` 为 false，退化为默认音色 `zh-CN-XiaoxiaoNeural`。

## 降级链

| 顺序 | engine | 触发条件 |
| --- | --- | --- |
| 1 | `edge-tts` | 默认，需联网访问微软语音服务；venv 在 `D:/VideoEdit/venv` |
| 2 | `sapi` | edge-tts 不可用或合成异常；Windows 本地 COM，离线，音色少且机械 |
| 3 | `silent` | SAPI 也失败；生成静音占位，**必须警告用户且不得当作成品交付** |

渲染前 `common.peak_db()` 会检测 `voice.wav` 峰值，低于 -45 dB 且 engine 非 `silent` 时直接报错，避免产出无声成片。

另有 `engine=file`：用户提供现成配音音频，填 `voice.source_audio`，脚本按字数比例把整段音频切成与 `lines` 对应的段落。

## 真·语音克隆（可选升级路径）

若要复刻案例说话人音色，需要本地语音克隆模型。可选：

- **CosyVoice 2**（阿里，中文零样本克隆效果较好）
- **GPT-SoVITS**（社区常用，少样本微调）
- **IndexTTS 2**（中文，支持时长控制）

模型权重通常 2–8 GB，CPU 推理慢、GPU 更合适。全部文件放在 `D:/VideoEdit/models/`，禁止装到 C 盘用户目录。

### 接入契约

1. 把推理封装脚本放到 `D:/VideoEdit/models/clone_engine.py`。
2. 脚本必须接受：
   ```
   python clone_engine.py --reference-wav <案例人声wav> --texts <texts.json> --out-dir <voice目录>
   ```
   `texts.json` 是字符串数组，顺序对应 `edl.json -> lines`。
3. 必须输出 `line_001.wav`、`line_002.wav` …（数量与文本条数一致，48 kHz 单声道 16-bit PCM 最佳）。
4. 在 `edl.json` 设置 `"voice": {"engine": "clone"}` 后执行 `finish`。`tts.py` 会自动把 `reference/_voice_work/voice_track.wav` 作为参考音频传入，并沿用后续拼接、时间轴、字幕、渲染流程。

克隆质量取决于案例人声轨的纯净度：若 BGM 干扰严重，先用 demucs / UVR 做人声分离（模型同样放 `D:/VideoEdit/models/`），再把分离后的人声覆盖到 `voice_track.wav`。

### 法律与伦理

克隆他人声音前必须取得声音本人授权；不得用于冒充、诈骗或误导性内容。案例视频来自第三方时，默认只做风格参数提取，不启用克隆。
