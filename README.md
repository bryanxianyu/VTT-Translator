# VTT 字幕翻译工具（Web）

这是一个基于 `Flask` 的 WebVTT 字幕翻译工具，支持 `DeepL`、`OpenAI`、`DeepSeek`、`Gemini`（Web 端）。

## 项目文件

- `vtt_translator_web.py`：Web 服务入口（上传、翻译、进度、停止、下载）
- `translate_vtt_zh_deepl_native.py`：底层翻译核心模块（供 Web 调用）

## 功能特性

- 仅翻译字幕文本，保留 VTT 时间轴和结构
- 批量请求 DeepL，支持失败重试
- 支持 OpenAI Responses API 批量翻译（provider 独立参数）
- 支持 DeepSeek（OpenAI 兼容接口，支持 `base_url=https://api.deepseek.com`）
- 支持 Gemini generateContent API
- 支持双语输出
- 实时进度和日志
- 任务级隔离（`job_id`），状态/下载不会互相覆盖
- DeepL 默认参数：`chunkSize=160`、`concurrency=2`、`maxRetries=2`、`max_chars=0`、`max_paragraphs=0`
- OpenAI 默认参数：`chunkSize=5`、`concurrency=12`、`maxRetries=2`、`max_chars=1800`、`max_paragraphs=12`、`model=gpt-5.4-nano`
- DeepSeek 默认参数：`chunkSize=5`、`concurrency=12`、`maxRetries=2`、`max_chars=1800`、`max_paragraphs=12`、`model=deepseek-v4-flash`（默认关闭思考模式）
- Gemini 默认参数：`chunkSize=5`、`concurrency=12`、`maxRetries=1`、`max_chars=1800`、`max_paragraphs=12`、`model=gemini-2.5-flash-lite`
- 参数默认值按 `provider` 隔离配置

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

## OpenAI 端点说明

- `https://api.openai.com/v1/responses`
- 默认模型：`gpt-5.4-nano`
- Web 端当前内置可选：`gpt-5.4-nano` / `gpt-5.4-mini` / `gpt-5.4` / `gpt-4.1-mini` / `gpt-4.1` / `gpt-4o-mini`

## DeepSeek 端点说明（OpenAI-compatible）

- Web 端端点固定为：`https://api.deepseek.com/chat/completions`
- 当前内置可选模型：`deepseek-v4-flash` / `deepseek-v4-pro`

## Gemini 端点说明

- 默认 base URL：`https://generativelanguage.googleapis.com/v1beta`
- 后端会自动补全到 `.../models/{model}:generateContent`
- 当前内置可选模型：`gemini-2.5-flash-lite` / `gemini-2.5-flash` / `gemini-3.1-flash-lite-preview`

## 注意事项

- 请妥善保管 API Key，不要提交到版本控制
- 免费版有字符配额限制
- 需要网络访问 DeepL/OpenAI API
- Web 端仅允许官方内置端点（DeepL Free/Pro、OpenAI Responses、DeepSeek 官方端点、Gemini 官方端点）以降低安全风险
- 若部分批次请求失败，会保留原文并在状态中标记“部分批次失败”
