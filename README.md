# VTT 字幕翻译工具（Web）

这是一个基于 `Flask + DeepL API` 的 WebVTT 字幕翻译工具，当前仅保留 Web 端使用方式。

## 项目文件

- `vtt_translator_web.py`：Web 服务入口（上传、翻译、进度、停止、下载）
- `translate_vtt_zh_deepl_native.py`：底层翻译核心模块（供 Web 调用）

## 功能特性

- 仅翻译字幕文本，保留 VTT 时间轴和结构
- 批量请求 DeepL，支持失败重试
- 支持双语输出
- 实时进度和日志
- 任务级隔离（`job_id`），状态/下载不会互相覆盖
- DeepL 默认参数：`chunkSize=160`、`maxRetries=2`
- 参数默认值按 `provider` 配置（当前仅 `deepl`，已预留扩展接口）

## 安装

```bash
pip install -r requirements.txt
```

## 启动

```bash
python vtt_translator_web.py
```

默认地址：`http://127.0.0.1:8080`

## 可选环境变量

- `VTT_WEB_HOST`：默认 `127.0.0.1`
- `VTT_WEB_PORT`：默认 `8080`
- `VTT_WEB_DEBUG`：默认 `false`
- `VTT_WEB_ACCESS_LOG`：默认 `false`（设为 `true` 可显示每个请求日志）

示例：

```bash
VTT_WEB_HOST=127.0.0.1 VTT_WEB_PORT=8080 VTT_WEB_DEBUG=false python vtt_translator_web.py
```

## DeepL 端点说明

- 免费版：`https://api-free.deepl.com/v2/translate`
- 专业版：`https://api.deepl.com/v2/translate`

中文目标语言代码请使用 `ZH`。

## 注意事项

- 请妥善保管 API Key，不要提交到版本控制
- 免费版有字符配额限制
- 需要网络访问 DeepL API
- Web 端仅允许官方 DeepL 端点（Free/Pro）以降低安全风险
- 若部分批次请求失败，会保留原文并在状态中标记“部分批次失败”

## 测试

```bash
python3 -m unittest discover -s tests
```

## 压测（自动找最佳 chunkSize）

你可以用同一个 VTT 文件自动跑多组 `chunkSize`，输出每组耗时与失败批次，并给出推荐值：

```bash
python benchmark_deepl_chunks.py /path/to/input.vtt --key YOUR_DEEPL_KEY --chunks 40,80,120,160,220 --max-retries 1
```

可选参数：
- `--endpoint`：默认 Free 端点，可改为 Pro
- `--target`：默认 `ZH`
- `--report-json /path/report.json`：输出完整 JSON 报告
