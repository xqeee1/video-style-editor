# ffmpeg 命令速查与 Windows 陷阱

本机环境：ffmpeg 9.0.2 full build（`C:/Users/25758/ffmpeg/bin/`），带 `libass`、`libx264`、`scdet`、`silencedetect`、`whisper` 滤镜；中文字体在 `C:/Windows/Fonts/`（`msyh.ttc`、`simhei.ttf`、`NotoSansSC-VF.ttf`）。

## 脚本里用到的关键命令

```bash
# 元数据
ffprobe -v error -show_entries format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels -of json IN

# 镜头切点：select + showinfo，从 stderr 抓 pts_time
ffmpeg -i IN -vf "select='gt(scene,0.20)',showinfo" -an -f null -
# 阈值越低越敏感；渐变/抖动会造成误检。ffmpeg 9 也可用 scdet 滤镜，但输出格式不同。

# 静音区间 / 电平
ffmpeg -i IN -af "silencedetect=noise=-35dB:d=0.25" -vn -f null -
ffmpeg -i IN -af volumedetect -vn -f null -

# 接触表（每 2 秒一格，4x4 拼图）
ffmpeg -i IN -vf "fps=1/2,scale=320:-2,tile=4x4" -frames:v 4 -q:v 4 sheet_%02d.jpg

# 按时间点抽单帧
ffmpeg -ss 3.200 -i IN -frames:v 1 -q:v 3 shot.jpg

# 人声轨提取（下混 + 语音带 + 降噪）
ffmpeg -i IN -vn -af "highpass=f=85,lowpass=f=7500,afftdn=nf=-20,alimiter=limit=0.95" -ac 1 -ar 8000 -c:a pcm_s16le voice_track.wav

# 统一画幅的切片
ffmpeg -ss START -t DUR -i IN -an -vf "scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=FPS" -c:v libx264 -pix_fmt yuv420p -r FPS clip.mp4

# 拼接（concat demuxer）
# concat_video.txt 每行：file 'D:/path/clip_001.mp4'
ffmpeg -f concat -safe 0 -i concat_video.txt -c copy video_mute.mp4

# 烧录 ASS + 混配音 + 末尾定格补齐
ffmpeg -i video_mute.mp4 -i voice.wav -map 0:v -map 1:a \
  -vf "tpad=stop_mode=clone:stop_duration=3.03,ass=main.ass" -af apad \
  -c:v libx264 -crf 20 -pix_fmt yuv420p -c:a aac -b:a 192k -t 13.38 final.mp4
```

BGM 侧链闪避（`render.py` 的 filter_complex）：把配音 `asplit` 成主路和 sidechain，用 `sidechaincompress` 压低配乐，再 `amix`。BGM 混音失败时自动回退到无 BGM 渲染并在 `report.md` 记录警告。

## Windows 陷阱

1. **滤镜里的绝对路径含盘符冒号。** `ass=D:/path/main.ass` 里的 `:` 会被 ffmpeg 当成分隔符导致解析失败。解决办法：把 `cwd` 设为字幕目录，滤镜只写相对文件名 `ass=main.ass`。`render.py` 已如此实现，不要改回绝对路径。
2. **concat list 路径。** 用 `Path.as_posix()` 写 `file 'D:/...'`，配 `-safe 0`；不要用反斜杠，也不要漏引号。
3. **`-ss` 位置。** 放在 `-i` 前是快速 seek，配合重新编码仍会精确解码到目标帧；放在 `-i` 后更慢但更保守。素材很长时保持现状。
4. **中文文件名。** 所有 subprocess 调用统一 `text=True, encoding="utf-8", errors="replace"`，避免 GBK 解码崩溃。
5. **`os.execv` 在 Windows 不是真 exec。** 它启动新进程后让当前进程立即退出，调用方会误以为任务已完成——曾导致渲染阶段读到上一轮的静音配音。改用阻塞 `subprocess.run` 转发输出并 `sys.exit(returncode)`（`tts.py -> ensure_edge_runtime`）。不要在管线里恢复 `os.execv`。
6. **AAC 静音的典型读数。** `volumedetect` 显示 `mean_volume ≈ max_volume ≈ -91 dB` 基本可判定整条音轨是静音。`render.py` 用 `peak_db()` 做前置校验。
7. **ASS 编码。** 写 UTF-8 无 BOM；颜色是 `&HAABBGGRR`（BGR）；`Alignment` 用数字键盘位置（2=底部居中）。

## 性能建议

- 冒烟测试或长素材先用 `preset=veryfast` / `ultrafast`（改 `config.json -> render.preset`）跑通，再换 `medium` 出终版。
- `voice_profile.py` 的 F0 自相关是纯 Python，最多采样 150 个有声帧；超长案例也只测前若干个均匀分布帧，不需要额外加速。
- 切片文件保留在 `build/`，重跑渲染时若 `clips` 未变化可考虑复用；当前实现每次重切，换取确定性。
- D 盘剩余空间需留足：1080p 成片每小时素材的中间切片可能占数 GB；`bootstrap.py` 不自动清理，由用户决定。
