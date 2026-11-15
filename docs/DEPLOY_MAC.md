# Apple Silicon / 16GB 部署

推荐原生 macOS，Homebrew + Python 3.12 + Node 22.12+。CPU 解码/质量/NumPy 在本机；Ollama 运行于 macOS 宿主，利用 Metal；不在 Docker Desktop 中期待 Apple GPU。

1. `make setup`：只安装缺少的系统工具，按 uv.lock / package-lock.json 安装依赖，执行 Alembic 与前端构建。首次从空 Mac 安装需要联网。
2. `make start`：API 默认 127.0.0.1:8000，单 Worker，共享维护锁。Ollama 已运行则复用，否则在已安装命令时自动启动。在另一终端执行 `make stop` 一键停止，或 `make restart` 停止完成后重新前台启动；开发模式用 `make restart DEV=1`。Ctrl+C 同样停止本次启动的进程；外部原有 Ollama 保留。详见 [运维文档：启动与停止](OPERATIONS.md#启动与停止)。
3. 浏览器 `/setup` 自行设置账号；不写入口令到 `.env` 或源码。
4. 另一终端执行 `bash scripts/download_models.sh all`。遇到当前网络的 Ollama blob 下载限制时，可执行 `... vlm-cache` 后重试 all。
5. `make model-smoke` 真实验证。人脸/语音仍须设置页明确启用。

默认 worker 单并发，多个实例会被文件锁拒绝；VLM_CONCURRENCY、EMBED_CONCURRENCY、ASR_CONCURRENCY 只能为 1。API 不加载 torch，SigLIP2/视觉/人脸/语音使用短生命周期子进程，Ollama keep_alive=0。本机其他占用也会影响可用内存，不承诺照片推理吞吐。

## 手机局域网访问与 HTTPS

照片服务默认不暴露 LAN。显式设置 `.env`：

```dotenv
APP_HOST=127.0.0.1
TRUSTED_HOSTS=localhost,127.0.0.1,album.home.arpa,192.168.1.10
SESSION_COOKIE_SECURE=true
```

可用本机 Caddy 终止 HTTPS，反向代理环回 API。安装 `brew install caddy`，例如 Caddyfile：

```caddyfile
album.home.arpa {
    tls internal
    reverse_proxy 127.0.0.1:8000
}
```

在家庭路由器/DNS 中把域名指向 Mac 的 LAN IP，并将本机 Caddy 根证书按设备系统指引安装为受信任证书。仅在可信家庭网络开放 HTTPS 端口，不映射互联网端口。局域网 DNS、手机证书信任、麦克风权限和真实触屏属于现场验收，不能由本机 Chrome 模拟代替。

也可自行配置 HTTPS 网关；直接用 HTTP LAN IP 浏览上传可按浏览器策略工作，但麦克风通常不可用。`Secure` Cookie 必须配合 HTTPS，不能仅把 APP_HOST 改成 0.0.0.0 后暴露外网。

## macOS 版本探测结果

本次机器为 Apple M4 / arm64 / 16GB，macOS 27.2.1。已有 Homebrew 起初不识别这一系统版本，依赖安装曾失败；经显式兼容安装后 FFmpeg/Ollama/whisper.cpp 已实际可执行。setup 脚本不会擅自修改系统版本标识；缺少工具且 Homebrew 不支持当前 OS 时会退出并保留真实错误。应更新到支持当前系统的 Homebrew，或使用各项目官方原生安装包。

现有机器的安装脚本复跑与功能检查见 TEST_REPORT.md；不等同于在全新系统镜像上验收。
