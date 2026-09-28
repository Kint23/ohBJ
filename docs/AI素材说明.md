# AI 素材说明（Banner / 片头）

> 2026-09-29 生成。本文件记录素材是怎么来的、怎么重做，以及当时的工具环境。

## 一、一句话结论

| 素材 | 能否由 AI 自动生成 | 状态 |
|---|---|---|
| Banner（1200×675） | ✅ 可以 | **已生成**，含 3 个备选 |
| 片头视频（1080p / 13.6s） | ✅ 可以 | **已生成** |
| 3 分钟演示正片 | ❌ 不行 | 需要真人屏录 + 配音 |

原因：本机 ComfyUI **没有安装任何视频底座模型**（`diffusion_models` 里只有 Qwen-Image / Flux，Wan 只装了 LoRA 和 VAE，没有 14B 主干），
所以本地跑不了 `text_to_video_wan` 一类工作流。而 3 分钟演示片本身也应该用真实屏幕操作，
AI 生成视频无法呈现「点一下真的出路线」这件事。

## 二、工具环境（已实测）

- **ComfyUI Desktop v1.0.46**，HTTP API 在 **`http://127.0.0.1:8188`**
  - 注意：端口是**动态**的，每次启动可能变，真实端口看日志
    `C:\Users\user\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI\logs\comfyui*.log`
    里的 `To see the GUI go to: http://127.0.0.1:xxxxx`
  - 安装位置：`C:\Users\user\AppData\Local\Comfy-Desktop\ComfyUI-Installs\ComfyUI`
  - 输出目录：`C:\Users\user\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output`
  - 另有一个旧版 `D:\ComfyUI\ComfyUI.exe`（v0.8.30），**不是**当前在用的，别启动它
- **出图模型**（实例可见的三件套 + 加速 LoRA）
  - UNET `qwen_image_2512_fp8_e4m3fn.safetensors`
  - CLIP `qwen_2.5_vl_7b_fp8_scaled.safetensors`（type=`qwen_image`）
  - VAE `qwen_image_vae.safetensors`
  - LoRA `Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors`
- **速度**：RTX 4090，4 步 / cfg 1.0 / euler+simple，1664×928 约 **8 秒**，1920×1088 约 **10 秒**（模型加载后）
- **ffmpeg**：`C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\bin\ffmpeg.exe`（9.0.1）

## 三、脚本

### `tools/comfy_generate.py` — 调 ComfyUI 出图
纯标准库，节点图对标官方模板 `image_qwen_image_2512_with_2steps_lora.json`：

```
UNETLoader -> LoraLoaderModelOnly -> ModelSamplingAuraFlow(shift 3) -> KSampler
CLIPLoader -> CLIPTextEncode ->（ConditioningZeroOut 作负向）
EmptySD3LatentImage -> KSampler -> VAEDecode -> SaveImage
```

```powershell
uv run tools/comfy_generate.py --prompt-file tools/prompt_banner2.txt `
    --out banner/ai-bg-a.png --width 1664 --height 928 --steps 4 --seed 20260929
```

宽高必须是 **16 的倍数**（脚本会校验）。

### `tools/make_banner.py` — 排版成 1200×675
现在支持 AI 背景图：

```powershell
uv run tools/make_banner.py --bg banner/ai-bg-b.png --bg-mix 0.55 `
    --veil-bottom 175 --stem "帝京寻踪-banner-1200x675"
```

- `--bg` 背景图，自动 cover 裁到 1200×675
- `--bg-mix` 纸色叠加强度（0=原图，1=纯纸色），默认 0.62
- `--veil-top` / `--veil-bottom` 上下**渐变**柔化遮罩（0-255），让文字压得住画面
- 不加 `--bg` 就是纯纸色底版（`帝京寻踪-banner-纸质底版.png`）
- **文字全部由 Pillow 用楷体绘制**，所以标题、数字、坐标一定是准确的，不受 AI 出字影响

### `tools/make_film.py` — 生成 AI 影像（片头 / 氛围 / 演示）
```powershell
uv run tools/make_film.py --program intro        # 片头 4 幕（13.6s）
uv run tools/make_film.py --program atmosphere   # 氛围 10 幕（41.5s）
uv run tools/make_film.py --program demo         # Playwright 截图 → 演示片（34.9s）
uv run tools/make_film.py --program all --concat # 再拼成「开场」（55.1s）
uv run tools/make_film.py --force                # 重出静帧（换 seed 就改 SCENES / ATMO）
```
影片结构：每幕缓慢推近或拉远（缩放约 8%–17%），幕间 0.8–1.0s 交叉淡化，
标题卡 0.6s 淡入 / 4.7s 淡出，主张句在结尾 4.4s 淡入。

**运镜已修掉「震动感」**：原先用 ffmpeg `zoompan`，它的 x/y 只能取整数，
1080p 下每帧位移不足 1 像素时会在两个整数间反复跳 → 观感就是哆嗦。
现改为母版 2400×1350 + Pillow `Image.AFFINE` 浮点平移取样（BICUBIC）+ LANCZOS 降采样，
并对缩放/位移套 smoothstep 缓动，位移是连续量。

## 四、产物清单

| 文件 | 说明 |
|---|---|
| `banner/帝京寻踪-banner-1200x675.png` / `.jpg` | **正式 Banner**（AI 背景 + 报刊式排版） |
| `banner/帝京寻踪-banner-纸质底版.png` / `.jpg` | 纯纸色底版（不依赖 AI 的保底方案） |
| `banner/候选A.png` / `候选B.png` / `候选C.png` | 三张 AI 背景的成品对比 |
| `banner/ai-bg-a.png` / `ai-bg-b.png` / `ai-bg-c.png` | AI 背景原图（1664×928） |
| `banner/film-*.png` | 片头 4 幕 + 氛围 10 幕静帧（2400×1344，原生超采样） |
| `banner/demo/01..07-*.png` | Playwright 截的真实页面状态图（1920×1080） |
| `video/片头-帝京寻踪.mp4` | **AI 片头**（13.6s / 1080p） |
| `video/氛围-帝京寻踪.mp4` | **氛围铺垫 10 幕**（41.5s，每幕带「明清名 → 今名」字幕） |
| `video/开场-帝京寻踪.mp4` | 片头 + 氛围拼接（55.1s） |
| `video/演示-帝京寻踪.mp4` | 真实页面截图合成演示片（34.9s，7 个操作状态） |
| `video/正片-帝京寻踪.mp4` | **3 分钟正片**（181.4s / 1080p / 烧字幕 / 原创配乐，无配音） |
| `video/正片字幕.srt` | 与正片时间轴对齐的字幕 |

`video/` **不会**被 GitHub Pages 发布（workflow 只发布 `app/`、`data/places.json`、`data/routes.json`、`docs/`）。

## 五、3 分钟正片（已生成，无配音）

三条脚本串成一条流水线：

### `tools/make_music.py` —— 原创古风配乐（无版权顾虑）
```powershell
uv run tools/make_music.py --seconds 186 --out video/music.wav
```
numpy 加法合成：拨弦音色（谐波叠加 + 各谐波不同衰减）+ 低音衬底 + 轻木击，
尾部多抽头扩散当混响，D 宫五声 / 92 BPM。
**自己合成就不存在版权问题**，可以放心上传 B 站。

### `tools/record_demo.py` —— 真屏幕录像（约 180s）
```powershell
uv run tools/record_demo.py            # headful；地图走 WebGL，无头容易拿不到渲染
uv run tools/record_demo.py --list-only
```
用 Playwright 的 `record_video_dir` 上下文录制（**不是截图拼接**），
脚本按绝对时间点驱动：全景平移 → 拉远看全城 → 三重筛选 → 点开憫忠寺 →
逐段阅读详情 → 搜索 → 智能排线 3 方案 → 应用方案 + 混合模式 + 生成真实路线 → AI 导游 → 落版。
同时写出 `video/正片字幕.srt` 与 `video/_timeline.json`（含片头加载耗时 `trim_start`）。

### `tools/make_subs.py` —— SRT → ASS（修正字号）
```powershell
uv run tools/make_subs.py --srt video/正片字幕.srt --out video/subs.ass --size 46
```
**这一步不能省**：libass 读 SRT 时会套用 ASS 的默认 `PlayResY=288`，
于是 `force_style` 里的 FontSize 会被放大 1080/288 ≈ 3.75 倍（字大得离谱）。
自己写 ASS 显式声明 `PlayResX/Y = 1920/1080`，字号就是像素。

### 最后合成
```powershell
ffmpeg -y -ss 4.2 -i video/_raw.webm -i video/music.wav -filter_complex `
 "[0:v]ass=video/subs.ass[v];[1:a]volume=0.72,afade=t=in:st=0:d=1.5,afade=t=out:st=176:d=5[a]" `
 -map "[v]" -map "[a]" -c:v libx264 -crf 19 -preset slow -r 25 -pix_fmt yuv420p `
 -c:a aac -b:a 192k -shortest "video/正片-帝京寻踪.mp4"
```
`-ss 4.2` = 剪掉地图加载那几秒（具体值见 `video/_timeline.json` 的 `trim_start`）。
成品：181.4s / 1920×1080 / 25fps / 44 MB，音量 mean −17.2 dB、max −3.9 dB（纯音乐场景合适）。

### 还要配人声时
直接用 `video/正片字幕.srt` 当配音稿（`docs/配音稿.txt` 是同内容的纯文本版），
人声进来后把配乐压到 −18 ~ −22 dB。

## 六、可复现性

```powershell
uv run tools/comfy_generate.py --prompt-file tools/prompt_banner2.txt --out banner/ai-bg-b.png `
    --width 1664 --height 928 --steps 4 --seed 778899
uv run tools/make_banner.py --bg banner/ai-bg-b.png --bg-mix 0.55 --veil-bottom 175
uv run tools/make_intro.py --force
```

`make_intro.py` 的 seed 写死在 `SCENES` 里，所以片头静帧是可复现的；
`comfy_generate.py` 不传 `--seed` 时会随机，成品 banner 用的 seed 见上面命令。
