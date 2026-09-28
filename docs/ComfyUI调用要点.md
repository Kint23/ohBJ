# ComfyUI 调用要点（本机实测，2026-09-29）

> 目的：让**任何任务**都能直接复用本机的 ComfyUI 出图能力，不必再摸索。
> 同目录下 `docs/AI素材说明.md` 记录的是"帝京寻踪这次做了什么"，本文件记录的是"怎么用这套环境"。

---

## 一、环境总览

| 项 | 值 |
|---|---|
| 应用 | **ComfyUI Desktop v1.0.46**（窗口标题 `ComfyUI — Comfy Desktop v1.0.46`） |
| 启动方式 | 桌面快捷方式 / 开始菜单 **`Comfy Desktop`** |
| 可执行文件 | `%LOCALAPPDATA%\Programs\Comfy\Comfy Desktop\Comfy Desktop.exe` |
| **HTTP API** | `http://127.0.0.1:8188` — ⚠️ **端口是动态的，每次启动可能变** |
| 内核版本 | ComfyUI `0.34.5`，Python 3.13.12，PyTorch 2.10.0+cu130 |
| GPU | RTX 4090（24 GB）｜ 内存 128 GB |
| 安装根目录 | `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI`（自带 `.venv`） |
| 输出目录 | `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\output` |
| 输入目录 | `%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\input` |
| 日志目录 | `<安装根目录>\logs\comfyui*.log` |
| 模型路径配置 | `%APPDATA%\Comfy Desktop\instance-model-paths\inst-*.yaml`（内核以 `--extra-model-paths-config` 加载） |
| ffmpeg | `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\bin\ffmpeg.exe`（9.0.1，已在 PATH） |

### ⚠️ 两个容易踩的坑

1. **别启动 `D:\ComfyUI\ComfyUI.exe`**。那是旧版 v0.8.30 的另一份拷贝，userData 是 `%APPDATA%\ComfyUI`，
   它认为自己"未安装"，启动会写 `installState: "started"` 并尝试重新安装。**当前在用的是 `Comfy Desktop`。**
2. **在用的实例只看得到一套"精选"模型**，不是磁盘上所有模型。
   所以**任何任务第一步都应该先 `GET /object_info`** 确认真实可用的文件名，不要照搬 HuggingFace 上的名字。

---

## 二、怎么找到端口

```powershell
$log = Get-ChildItem "$env:LOCALAPPDATA\Comfy-Desktop\ComfyUI-Installs\ComfyUI\logs\comfyui*.log" |
       Sort-Object LastWriteTime -Desc | Select-Object -First 1
Select-String -Path $log.FullName -Pattern "To see the GUI go to" | Select-Object -Last 1
# -> To see the GUI go to: http://127.0.0.1:8188
```

探测是否就绪（就绪会返回含 `comfyui_version` 的 JSON）：

```powershell
curl.exe -sS -m 5 "http://127.0.0.1:8188/system_stats"
```

启动（会开 GUI 窗口）：

```powershell
Start-Process "$env:LOCALAPPDATA\Programs\Comfy\Comfy Desktop\Comfy Desktop.exe"
```

> 冷启动到能出图约 **1–4 分钟**（要加载 20 GB 权重 + 扫描自定义节点）。
> 模型常驻显存后，**1664×928 / 4 步约 8 秒，1920×1088 约 10 秒**。

---

## 三、实例当前可见的模型

### 图像（够用，已验证）

| 槽位 | 文件名 |
|---|---|
| `diffusion_models` | `qwen_image_2512_fp8_e4m3fn.safetensors`、`qwen_image_edit_2511_int8_convrot.safetensors` |
| `text_encoders` | `qwen_2.5_vl_7b_fp8_scaled.safetensors`、`qwen3.5_2b_bf16.safetensors`、`t5gemma_b_b_ul2.safetensors` |
| `vae` | `qwen_image_vae.safetensors`、`pixel_space` |
| `loras` | `Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors`、`Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors`、`Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` |

### 视频 —— **不可用**

`diffusion_models` 里**没有任何视频主干**（Wan 系列只装了 LoRA 和 VAE，没有 14B 主干）。
所以 `video_wan2_2_14B_t2v` / `image_to_video_wan` / `ltxv_*` / `video_hunyuan_video_1.5_*` 等模板**都跑不起来**。

要跑视频，需要额外下载主干（任选其一）：

- Wan 2.2 T2V 14B high+low noise（配已有的 `wan2.2_t2v_lightx2v_4steps_lora_v1.1_*`，4 步即可）
- 或 LTX-Video / HunyuanVideo 主干

模板文件（**只含图，不含权重**）在：
```
%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\.venv\Lib\site-packages\comfyui_workflow_templates_json\templates\
```
按需 `Get-ChildItem ... -Filter "*wan2_2*"` 查看。

> 另一处 `D:\CFmodel\models` 里有 Flux / SD3.5 / Wan VAE / 各类 LoRA，但**当前实例看不到**；
> 要纳入，编辑 `%APPDATA%\Comfy Desktop\instance-model-paths\inst-*.yaml` 后重启。

---

## 四、API 三步走

| 步骤 | 请求 | 说明 |
|---|---|---|
| 1. 排队 | `POST /prompt`，body `{"prompt": {节点图}, "client_id": "任意"}` | 返回 `prompt_id`；图有错会返回 `node_errors` |
| 2. 取结果 | `GET /history/{prompt_id}` | 出现该 key 即完成；`status.status_str == "error"` 表示失败 |
| 3. 下图 | `GET /view?filename=&subfolder=&type=output` | 图片实际存在 ComfyUI 的 output 目录，需要用这个接口取回 |

其他有用的：`GET /system_stats`（版本/显卡/显存）、`GET /object_info/<节点名>`（**查真实可用文件名**）、`GET /queue`。

**节点图是 API 格式**（不是 UI 格式）：`{ "节点id": {"class_type": "...", "inputs": {...}} }`，
节点间用 `["上游节点id", 输出槽序号]` 表示连接。UI 里保存的 workflow JSON 不能直接 POST。

---

## 五、Qwen-Image 文生图标准图（已实测可用）

对标官方模板 `image_qwen_image_2512_with_2steps_lora.json`，**全部是内核自带节点**，不依赖任何自定义节点：

```
105 UNETLoader(qwen_image_2512_fp8_e4m3fn, weight_dtype=default)
      └→ 114 LoraLoaderModelOnly(Qwen-Image-2512-Lightning-4steps, strength=1)
              └→ 110 ModelSamplingAuraFlow(shift=3)
                      └→ 106 KSampler.model
104 CLIPLoader(qwen_2.5_vl_7b_fp8_scaled, type=qwen_image, device=default)
      └→ 108 CLIPTextEncode(text=正向提示词)
              ├→ 106 KSampler.positive
              └→ 128 ConditioningZeroOut → 106 KSampler.negative
107 EmptySD3LatentImage(width, height, batch_size)
      └→ 106 KSampler.latent_image
106 KSampler(steps=4, cfg=1.0, sampler_name=euler, scheduler=simple, denoise=1.0)
      └→ 109 VAEDecode(vae=103 VAELoader) └→ 123 SaveImage(filename_prefix)
```

**参数要点**

- `steps=4` + `cfg=1.0`：配合 Lightning 4 步 LoRA。**cfg 必须是 1.0**，否则 LoRA 蒸馏效果会被破坏
  （cfg=1 时负向条件实际不起作用，所以 `ConditioningZeroOut` 只是走个形式）
- `shift=3`：Qwen-Image 官方推荐值
- **宽高必须是 16 的倍数**，否则 `EmptySD3LatentImage` 会报错
- 推荐尺寸：`1664×928`（16:9）、`1328×1328`（1:1）、`928×1664`（9:16）、`1920×1088`（近 1080p）
- 负向提示词统一用 `ConditioningZeroOut`（模板做法），不要在 cfg=1 下期待它生效
- 提示词写**正向约束**更有效：`no text, no letters, no watermark, no people, no modern buildings`

---

## 六、已封装脚本

### `tools/comfy_generate.py` —— 纯标准库，可直接复制到别的任务

```powershell
uv run tools/comfy_generate.py `
    --prompt-file tools/prompt_banner2.txt `
    --out banner/out.png `
    --width 1664 --height 928 --steps 4 --seed 20260929
```

也可作为模块调用（`make_intro.py` 就是这么做）：

```python
import sys; sys.path.insert(0, "tools")
import comfy_generate
comfy_generate.generate(prompt, "banner/x.png", width=1920, height=1088, steps=4, seed=1101)
```

其他参数：`--server`（换端口）、`--shift`、`--lora`、`--lora-strength`、`--batch`、`--prefix`、`--timeout`。

### `tools/make_banner.py` —— 排版（AI 背景 + 楷体文字）

```powershell
uv run tools/make_banner.py --bg banner/ai-bg-b.png --bg-mix 0.55 --veil-bottom 175
```
`--bg` 背景图（自动 cover 到 1200×675）、`--bg-mix` 纸色叠加强度、`--veil-top/--veil-bottom` 上下**渐变**柔化遮罩、`--stem` 改输出名。

### `tools/make_film.py` —— AI 静帧 → 视频（亚像素运镜）

```powershell
uv run tools/make_film.py --program intro        # 片头 4 幕（标题卡 + 主张句）
uv run tools/make_film.py --program atmosphere   # 氛围 10 幕（每幕「明清名 → 今名」字幕）
uv run tools/make_film.py --program demo         # 用 Playwright 截图合成演示片
uv run tools/make_film.py --program all --concat # 两条都出，再拼成「开场」
uv run tools/make_film.py --concat-only          # 只拼接，不渲染
```

**为什么不用 ffmpeg 的 `zoompan`：** 它的 `x`/`y` 只能取整数，1080p 下每帧位移不足 1 像素时
会在两个整数间反复跳，观感就是「震动/哆嗦」。本脚本改为：母版 2400×1350，
用 Pillow `Image.AFFINE` 做**纯平移的浮点取样**（BICUBIC 插值），再用 LANCZOS 降采样到 1920×1080，
并给缩放与位移套 `smoothstep` 缓动 —— 运动才是连续的。
（注意：`Image.AFFINE` 只接受 NEAREST/BILINEAR/BICUBIC，**不接受 LANCZOS**，所以必须分两步。）

### `tools/capture_demo.py` —— Playwright 驱动真实页面截图

```powershell
uv run tools/capture_demo.py            # 默认 headful，WebGL 才能出图
uv run tools/capture_demo.py --headless # 无头（百度地图 GL 可能拿不到渲染）
```

会逐个操作状态截 1920×1080 图，并写 `banner/demo/manifest.json`；
之后 `make_film.py --program demo` 就能把它合成带字幕的演示片。
默认复用 `%LOCALAPPDATA%\ms-playwright\chromium-*` 里已有的 Chromium，不额外下载。

---

## 七、其他任务直接照抄的清单

1. 先 `GET /system_stats` 确认在跑；不在就 `Start-Process` 那个 exe，等 1–4 分钟。
2. 用 `GET /object_info/<节点>` **核实文件名**，别猜。
3. 复制 `tools/comfy_generate.py`（零依赖，只需标准库 + `urllib`）。
4. 宽高记得取 16 的倍数；`steps=4 / cfg=1.0 / shift=3`。
5. 想要更多模型 → 改 `instance-model-paths` 的 yaml → 重启 Comfy Desktop。
6. **写 PEP 723 脚本时，凡是 `import PIL` 就一定要在头部写 `dependencies = ["pillow"]`**，
   否则 `uv run` 起的环境里没有 Pillow（本人踩过）。
7. Windows 控制台是 GBK，脚本里别打印 `✔` 之类的符号（会 `UnicodeEncodeError` 直接让命令返回非零），
   加 `sys.stdout.reconfigure(errors="replace")` 并改用 `[OK]`。
