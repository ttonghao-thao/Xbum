# 本地模型、固定版本与许可登记

运行时不下载权重，不调用第三方在线推理。安装器是唯一主动联网下载模型的程序；`.from_pretrained(..., local_files_only=True)`、HF_HUB_OFFLINE 和 TRANSFORMERS_OFFLINE 在推理进程生效。Ollama URL 只允许 HTTP 环回地址，启动器禁用云能力。重模型任务串行，子进程结束释放内存，Ollama keep_alive=0。

| 能力 | 固定来源/版本 | 实际文件体积 | 许可来源 |
|---|---|---|---|
| VLM | `qwen3-vl:2b-instruct`，GGUF / Q4_K_M；manifest SHA-256 `afc393e3572c07db0bc84aa460d928a16c75f3fbb9ca9e0dccc4fbd2dafb573e` | Ollama 注册 size=1,930,568,531 bytes，包含视觉 projector | [Ollama 模型页](https://ollama.com/library/qwen3-vl:2b-instruct)，安装时保留官方 license blob（Apache 2.0） |
| 图文编码 | `google/siglip2-base-patch16-224`，revision `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2`；768 维 float32 归一化 | safetensors 1,500,800,904 bytes，另含匹配 tokenizer/processor | [官方模型卡](https://huggingface.co/google/siglip2-base-patch16-224)，Apache 2.0 |
| 人脸检测 | YuNet `face_detection_yunet_2023mar.onnx`；OpenCV Zoo commit `48b10fa505aeb51fbd127fc60a5c660040e823b7` | 232,589 bytes | [YuNet LICENSE](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/LICENSE)，MIT |
| 人脸特征 | SFace `face_recognition_sface_2021dec.onnx`，同上 commit | 38,696,353 bytes | [SFace LICENSE](https://github.com/opencv/opencv_zoo/blob/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface/LICENSE)，Apache 2.0 |
| 中文语音 | whisper.cpp `ggml-small.bin` 多语言模型；weights revision `5359861c739e955e79d9a303bcbc70fb988958b1` | 487,601,967 bytes | [权重仓库](https://huggingface.co/ggerganov/whisper.cpp)、[whisper.cpp](https://github.com/ggml-org/whisper.cpp)，MIT |
| 图片文字 OCR | macOS 系统 Vision `VNRecognizeTextRequest`；本项目输出契约 `apple-vision-text-1`，Swift 设置 accurate、zh-Hans/en-US | 无独立下载权重；实际框架/识别行为由已安装 macOS 管理，不计入 MODEL_LOCK 权重体积 | Apple 系统框架；代码适配见 `scripts/ocr.swift`，不作为可独立分发的开源权重登记 |

本次安装 `MODEL_DIR` 登记文件共约 2.066GB，另有 Ollama 管理的约 1.931GB。这里是文件体积，不是运行内存承诺。逐文件 SHA-256 与确切 bytes 见 [MODEL_LOCK.json](MODEL_LOCK.json)。清单不含权重、照片、用户人脸特征或凭据。商业使用的人脸权重和训练数据授权仍需独立核查，本登记不替代该核查。

## 安装、校验和替换

先 `make start` 启动本机 Ollama，另一终端 `bash scripts/download_models.sh all`。可以单独选择 siglip/faces/whisper/vlm；缺失会明确失败，不伪装成功。大文件按固定来源做有界分片、断点续传，完成后验证 SHA；所有原始权重不提交。

本次 Ollama 原生 pull 在当前网络遇到“官方下载地址解析为非公网 IP”的拒绝。显式安装命令 `... vlm-cache` 从固定官方 registry manifest 校验并下载各 blob，再通过本机 [官方 Blob API](https://github.com/ollama/ollama/blob/main/docs/api.md) 导入缓存，最后由原生 pull 注册。没有关闭 Ollama 的地址校验，也没有手工猜写它的存储结构。该路径已实际成功，后续已安装检查无须重新下载。

VLM model blob SHA-256：`bd2179e37645d5435e0214e5399e5997408a7e9685ba527cbb60726e6a32fd99`；projector：`4dc7bc5863d7fc3ad784acf677f72f009ab5fd3758855f1996b678b621817190`。默认模型摘要与固定 manifest 不符时安装器拒绝静默升级；推理也检查登记版本。更换模型必须审查并显式更新登记/适配器，再重新分析；旧向量不能混用。

`bash scripts/download_models.sh verify` 校验 MODEL_DIR 登记文件；Ollama 缓存的 blob 在安装/上传时由 SHA 校验，并由其 manifest 摘要登记。该命令不会把 Ollama 外部目录当成本项目普通媒体扫描。

## 资源与真实验证边界

API 不导入 torch。SigLIP2 优先 MPS，执行异常回退 CPU；图文编码使用同一固定模型和归一化，保存 model/version/dimension。VLM 仅输入最长边 960 的 JPEG，严格 Pydantic JSON，不采纳姓名、关系或地点为事实。人脸默认关闭，姓名和关系必须人工标注；关闭后不新增，授权删除后不留机器特征。

真实运行证据见 [model-smoke.json](validation/model-smoke.json) 和 [real-pipeline.json](validation/real-pipeline.json)。这些测试使用几何合成图与本地 Tingting TTS：证明加载、推理、JSON/向量、队列、转写和释放路径，**不证明家庭场景标签、事件或正样本人脸识别准确率**。

记录包含 RSS 采样、MPS driver allocation、Ollama size_vram 和任务结束后的加载列表；这些指标不应相加视为统一内存总峰值。每个模型结束后 Worker 子进程为零，Ollama 加载列表为空。真实 1000/3000 张全模型连续推理的吞吐和内存长稳性尚未验收。

## 2026-11-16 OCR 与增强运行边界

新增 OCR provider 仅在既有隔离推理子进程内调用 `/usr/bin/swift scripts/ocr.swift`，使用 macOS Vision，运行时不下载独立模型、不请求第三方 API。需要 macOS 和可执行 Swift；状态检测到 Swift 不等于实际识别已成功。ocr-cache 位于受保护 DATA_DIR，只缓存 Swift 模块，不存入 Git。

OCR 使用原图以保留小字，严格限制输出文本/区域数量，bbox 为相对图片的归一化坐标。历史 family 照片由 Worker 按 apple-vision-text-1 排缺失/过期任务，人工校对独立保留。该契约版本是本项目适配版本，不是假定系统 Vision 模型权重在不同 macOS 上完全一致。

显式真实 OCR 冒烟命令为 `.venv/bin/python scripts/ocr_smoke.py`，输入是程序生成的中英文文字图片；实际执行结果见 TEST_REPORT.md。它不代表家庭照片、手写字或模糊文字准确率。原 make model-smoke 对既有模型的检查与 OCR 冒烟分别报告。

本轮故事编排不增加 LLM provider：规则选片、日期章节和事实模板保持 generated_by_ai=false。图片质量继续使用已有清晰度/曝光/分辨率，不宣称审美或最佳表情模型。后台可配置的资源检查是可用内存、磁盘和小时窗口，未实现温控或抢占。模型单并发及任务后释放约束保持不变。
