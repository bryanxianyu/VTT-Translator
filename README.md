# VTT字幕翻译工具

这是一个使用DeepL原生API翻译WebVTT字幕文件的Python脚本。支持批量翻译，保持VTT格式结构，并提供进度显示和双语输出选项。

## 脚本说明

`translate_vtt_zh_deepl_native.py` - 直接调用DeepL API（免费版/专业版），无需deep_translator库。

### 核心特性
- 使用端点：https://api-free.deepl.com/v2/translate（免费版默认）
- 批量处理多行文本，使用重复的'text'参数
- 保持VTT结构，仅翻译对话行
- 显示进度，支持双语输出
- 遇到临时错误时使用指数退避重试

### 重要说明
- DeepL API免费版**必须**使用端点：https://api-free.deepl.com/v2/translate
- 中文目标语言：使用"ZH"（DeepL原生API），不是zh-CN

## 功能特点

- 🚀 **直接调用DeepL API**：无需第三方翻译库，直接使用DeepL原生API
- 📝 **保持VTT格式**：只翻译对话内容，保留时间码、索引等格式信息
- 🔄 **批量处理**：支持批量翻译，提高效率
- 📊 **进度显示**：实时显示翻译进度
- 🌐 **双语输出**：可选择保留原文和译文
- 🔁 **错误重试**：支持指数退避重试机制
- 💰 **支持免费版**：兼容DeepL API免费版和专业版

## 安装要求

### Python依赖
```bash
pip install requests
```

### DeepL API密钥
1. 访问 [DeepL API](https://www.deepl.com/pro-api) 注册账户
2. 获取API密钥（免费版每月50万字符限制）

## 使用方法

### 基本用法
```bash
python translate_vtt_zh_deepl_native.py input.vtt --out output.vtt --key YOUR_API_KEY
```

### 完整参数示例
```bash
python translate_vtt_zh_deepl_native.py input.vtt \
  --out output.vtt \
  --key YOUR_API_KEY \
  --bilingual \
  --every 10 \
  --chunk 20 \
  --endpoint https://api-free.deepl.com/v2/translate \
  --target ZH
```

## 参数说明

| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `input` | ✅ | - | 输入VTT文件路径 |
| `--out` | ✅ | - | 输出VTT文件路径 |
| `--key` | ✅ | - | DeepL API密钥 |
| `--endpoint` | ❌ | `https://api-free.deepl.com/v2/translate` | DeepL API端点 |
| `--target` | ❌ | `ZH` | 目标语言代码（中文=ZH） |
| `--bilingual` | ❌ | `False` | 是否保留原文和译文 |
| `--every` | ❌ | `10` | 每N行显示一次进度 |
| `--chunk` | ❌ | `20` | 每次API请求的行数 |
| `--max-retries` | ❌ | `4` | 最大重试次数 |

## API端点说明

### 免费版
- 端点：`https://api-free.deepl.com/v2/translate`
- 限制：每月50万字符
- 注意：**必须**使用此端点

### 专业版
- 端点：`https://api.deepl.com/v2/translate`
- 限制：根据订阅计划
- 需要专业版API密钥

## 使用示例

### 1. 基本翻译
```bash
python translate_vtt_zh_deepl_native.py movie.vtt --out movie_zh.vtt --key YOUR_API_KEY
```

### 2. 双语输出
```bash
python translate_vtt_zh_deepl_native.py movie.vtt --out movie_bilingual.vtt --key YOUR_API_KEY --bilingual
```

### 3. 调整批处理大小
```bash
python translate_vtt_zh_deepl_native.py movie.vtt --out movie_zh.vtt --key YOUR_API_KEY --chunk 50
```

### 4. 使用专业版API
```bash
python translate_vtt_zh_deepl_native.py movie.vtt --out movie_zh.vtt --key YOUR_PRO_API_KEY --endpoint https://api.deepl.com/v2/translate
```

## 注意事项

1. **API密钥安全**：请妥善保管您的API密钥，不要将其提交到版本控制系统
2. **字符限制**：免费版每月有50万字符限制，请注意使用量
3. **网络连接**：需要稳定的网络连接访问DeepL API
4. **文件编码**：脚本会自动检测文件编码，支持UTF-8和其他常见编码
5. **错误处理**：如果某个批次翻译失败，脚本会保留原文并继续处理

## 故障排除

### 常见错误

1. **HTTP 403错误**
   - 检查API密钥是否正确
   - 确认使用的是正确的API端点

2. **HTTP 429错误**
   - 已达到API使用限制
   - 等待一段时间后重试

3. **编码错误**
   - 脚本会自动尝试检测编码
   - 如果仍有问题，请确保VTT文件使用UTF-8编码

### 性能优化

- 增加`--chunk`参数值可以提高批处理效率
- 减少`--every`参数值可以减少进度输出频率
- 使用专业版API可以获得更高的请求限制


## 更新日志

- v1.0.0: 初始版本，支持基本的VTT翻译功能
- 支持DeepL原生API调用
- 支持批量处理和错误重试
- 支持双语输出选项
