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

### `tools/make_intro.py` — 生成 AI 片头（4 幕 + 交叉淡化 + 标题）
```powershell
uv run tools/make_intro.py            # 生成缺失静帧并合成
uv run tools/make_intro.py --no-gen   # 只重新合成
uv run tools/make_intro.py --force    # 重出静帧（换 seed 就在 SCENES 里改）
```
影片结构：每幕 4.0s 缓慢推近（zoompan），幕间 0.8s 交叉淡化，
标题卡 0.6s 淡入 / 4.7s 淡出，主张句在结尾 4.4s 淡入。总长 **13.6s**，1920×1080 / 25fps / H.264。

## 四、产物清单

| 文件 | 说明 |
|---|---|
| `banner/帝京寻踪-banner-1200x675.png` / `.jpg` | **正式 Banner**（AI 背景 + 报刊式排版） |
| `banner/帝京寻踪-banner-纸质底版.png` / `.jpg` | 纯纸色底版（不依赖 AI 的保底方案） |
| `banner/候选A.png` / `候选B.png` / `候选C.png` | 三张 AI 背景的成品对比 |
| `banner/ai-bg-a.png` / `ai-bg-b.png` / `ai-bg-c.png` | AI 背景原图（1664×928） |
| `banner/intro-scene-1..4.png` | 片头 4 幕静帧（1920×1088） |
| `banner/intro-title.png` / `intro-claim.png` | 片头标题层 / 主张层（RGBA，可用 `--no-gen` 快速重排） |
| `video/片头-帝京寻踪.mp4` | **AI 片头正片**（13.6s / 1080p / 13.8 MB） |

`video/` **不会**被 GitHub Pages 发布（workflow 只发布 `app/`、`data/places.json`、`data/routes.json`、`docs/`）。

## 五、片头怎么接进正片

片头是**无声**的（配乐版权建议你自己选，见 `docs/提交材料.md` 的音乐指引）：

1. 剪映新建 1920×1080 / 25fps 工程
2. 导入 `video/片头-帝京寻踪.mp4` → 放在时间轴最前
3. 接你的屏录（100% 缩放、F11、关通知）
4. 「导入字幕」选 `docs/视频字幕.srt`；「文本朗读」用 `docs/配音稿.txt`
5. 片头段配乐起，到屏录段把音乐压到约 −18 dB 给人声让位
6. 导出 1080p / H.264 → 传 B 站

## 六、可复现性

```powershell
uv run tools/comfy_generate.py --prompt-file tools/prompt_banner2.txt --out banner/ai-bg-b.png `
    --width 1664 --height 928 --steps 4 --seed 778899
uv run tools/make_banner.py --bg banner/ai-bg-b.png --bg-mix 0.55 --veil-bottom 175
uv run tools/make_intro.py --force
```

`make_intro.py` 的 seed 写死在 `SCENES` 里，所以片头静帧是可复现的；
`comfy_generate.py` 不传 `--seed` 时会随机，成品 banner 用的 seed 见上面命令。
