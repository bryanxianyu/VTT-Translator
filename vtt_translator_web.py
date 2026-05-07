#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VTT字幕翻译工具 - Web版本

基于Flask的Web界面版本，避免桌面GUI的兼容性问题。
详细使用说明请参考README.md文件。
"""

from flask import Flask, request, jsonify, send_file
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from werkzeug.utils import secure_filename
from uuid import uuid4

# 导入核心翻译功能
from translate_vtt_zh_deepl_native import (
    DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
    DEEPSEEK_DEFAULT_MODEL,
    GEMINI_BASE_URL,
    GEMINI_DEFAULT_MODEL,
    OPENAI_DEFAULT_MODEL,
    OPENAI_RESPONSES_ENDPOINT,
    normalize_gemini_endpoint,
    normalize_openai_compatible_endpoint,
    read_text,
    should_translate,
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()

MAX_LOG_MESSAGES = 50
MAX_CHUNK_SIZE = 1000
JOB_TTL_SECONDS = 6 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 5 * 60

ALLOWED_ENDPOINTS = {
    "https://api-free.deepl.com/v2/translate",
    "https://api.deepl.com/v2/translate",
}
ALLOWED_OPENAI_ENDPOINTS = {OPENAI_RESPONSES_ENDPOINT}
ALLOWED_DEEPSEEK_ENDPOINT_PREFIX = "https://api.deepseek.com"
ALLOWED_GEMINI_ENDPOINT_PREFIX = "https://generativelanguage.googleapis.com"
ALLOWED_TARGET_LANGS = {"ZH", "EN", "JA", "KO", "FR", "DE", "ES", "IT", "PT", "RU"}
ALLOWED_OPENAI_MODELS = {
    "gpt-5-nano",
    "gpt-5.4-nano",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
}
ALLOWED_DEEPSEEK_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}
ALLOWED_GEMINI_MODELS = {"gemini-2.5-flash-lite"}

TRANSLATOR_PROFILES = {
    "deepl": {
        "default_chunk_size": 160,
        "default_concurrency": 2,
        "default_max_retries": 2,
        "default_endpoint": "https://api-free.deepl.com/v2/translate",
        "default_model": "",
    },
    "openai": {
        "default_chunk_size": 5,
        "default_concurrency": 96,
        "default_max_retries": 1,
        "default_endpoint": OPENAI_RESPONSES_ENDPOINT,
        "default_model": "gpt-5.4-nano",
    },
    "deepseek": {
        "default_chunk_size": 5,
        "default_concurrency": 96,
        "default_max_retries": 1,
        "default_endpoint": DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
        "default_model": DEEPSEEK_DEFAULT_MODEL,
    },
    "gemini": {
        "default_chunk_size": 5,
        "default_concurrency": 96,
        "default_max_retries": 1,
        "default_endpoint": GEMINI_BASE_URL,
        "default_model": "gemini-2.5-flash-lite",
    }
}

# 按任务(job_id)维护状态，避免任务之间互相覆盖
jobs = {}
jobs_lock = threading.Lock()
last_cleanup_at = 0.0

def allowed_file(filename):
    """检查文件类型是否允许"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'vtt'


def get_translator_profile(provider: str) -> dict | None:
    return TRANSLATOR_PROFILES.get((provider or "").strip().lower())

def create_job(job_id: str, input_file: str, output_file: str, original_filename: str) -> None:
    with jobs_lock:
        jobs[job_id] = {
            'job_id': job_id,
            'is_running': False,
            'stop_requested': False,
            'progress': 0,
            'total': 0,
            'current': 0,
            'status': '就绪',
            'log_messages': [],
            'error': None,
            'input_file': input_file,
            'output_file': output_file,
            'original_filename': original_filename,
            'created_at': time.time(),
            'updated_at': time.time(),
            'failed_batches': 0,
        }


def get_job(job_id: str):
    with jobs_lock:
        return jobs.get(job_id)


def update_job(job_id: str, **kwargs):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return False
        job.update(kwargs)
        job['updated_at'] = time.time()
        return True


def get_job_snapshot(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return None
        return {
            'job_id': job['job_id'],
            'is_running': job['is_running'],
            'progress': job['progress'],
            'total': job['total'],
            'current': job['current'],
            'status': job['status'],
            'log_messages': list(job['log_messages']),
            'error': job['error'],
            'stop_requested': job['stop_requested'],
            'created_at': job['created_at'],
            'updated_at': job['updated_at'],
            'failed_batches': int(job.get('failed_batches', 0)),
        }


def log_message(job_id: str, message: str, level="INFO"):
    """添加任务日志消息"""
    timestamp = time.strftime("%H:%M:%S")
    log_entry = f"[{timestamp}] {level}: {message}"
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job['log_messages'].append(log_entry)
        if len(job['log_messages']) > MAX_LOG_MESSAGES:
            job['log_messages'] = job['log_messages'][-MAX_LOG_MESSAGES:]
        job['updated_at'] = time.time()


def increment_failed_batches(job_id: str) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job['failed_batches'] = int(job.get('failed_batches', 0)) + 1
        job['updated_at'] = time.time()


def should_continue(job_id: str) -> bool:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return False
        return not job.get('stop_requested', False)


def safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except Exception:
        pass


def cleanup_old_jobs(force: bool = False) -> None:
    global last_cleanup_at
    now = time.time()
    if not force and (now - last_cleanup_at) < CLEANUP_INTERVAL_SECONDS:
        return

    stale_jobs = []
    with jobs_lock:
        if not force and (now - last_cleanup_at) < CLEANUP_INTERVAL_SECONDS:
            return
        for job_id, job in list(jobs.items()):
            if now - float(job.get('updated_at', now)) > JOB_TTL_SECONDS:
                stale_jobs.append((job_id, job.get('input_file'), job.get('output_file')))
                del jobs[job_id]
        last_cleanup_at = now

    for _, input_file, output_file in stale_jobs:
        safe_unlink(input_file)
        safe_unlink(output_file)


def translate_worker(
    job_id,
    input_file,
    output_file,
    provider,
    api_key,
    endpoint,
    target_lang,
    model,
    bilingual,
    chunk_size,
    concurrency,
    max_retries,
    max_chars,
    max_paragraphs,
    rps,
    request_timeout,
    fallback_mode,
    repair_concurrency,
    openai_reasoning_effort,
):
    """翻译工作线程"""
    try:
        update_job(
            job_id,
            is_running=True,
            stop_requested=False,
            error=None,
            progress=0,
            current=0,
            total=0,
            status='准备中',
            log_messages=[],
            failed_batches=0,
        )
        log_message(job_id, f"开始翻译任务 provider={provider}")

        # 读取文件
        log_message(job_id, f"读取文件: {input_file}")
        lines = read_text(Path(input_file))
        translatable_count = sum(1 for line in lines if should_translate(line))

        log_message(job_id, f"找到 {translatable_count} 行需要翻译的文本")

        if translatable_count == 0:
            update_job(job_id, error="没有找到需要翻译的文本", status="翻译失败")
            return
        update_job(job_id, total=translatable_count, current=0, progress=0)

        # 执行翻译
        log_message(job_id, "开始翻译...")

        # 修改翻译函数以支持进度回调
        translated_lines = translate_with_progress(
            job_id,
            lines,
            provider,
            api_key,
            endpoint,
            target_lang,
            model,
            bilingual,
            chunk_size,
            concurrency,
            max_retries,
            max_chars,
            max_paragraphs,
            rps,
            request_timeout,
            fallback_mode,
            repair_concurrency,
            openai_reasoning_effort,
        )

        if not should_continue(job_id):
            update_job(job_id, status="用户停止")
            log_message(job_id, "翻译任务已停止，未生成输出文件", "WARNING")
            return

        # 保存结果
        log_message(job_id, f"保存文件: {output_file}")
        Path(output_file).write_text("\n".join(translated_lines), encoding="utf-8")

        failed_batches = 0
        snapshot = get_job_snapshot(job_id)
        if snapshot:
            failed_batches = int(snapshot.get('failed_batches', 0))

        if failed_batches > 0:
            warning_msg = f"翻译完成，但有 {failed_batches} 个批次失败并保留原文"
            log_message(job_id, warning_msg, "WARNING")
            update_job(
                job_id,
                status="翻译完成（部分批次失败，已保留原文）",
                progress=100,
                current=translatable_count,
            )
        else:
            log_message(job_id, "翻译完成！", "SUCCESS")
            update_job(job_id, status="翻译完成", progress=100, current=translatable_count)

    except Exception as e:
        log_message(job_id, f"翻译失败: {str(e)}", "ERROR")
        update_job(job_id, error=str(e), status="翻译失败")
    finally:
        safe_unlink(input_file)
        update_job(job_id, input_file=None)
        update_job(job_id, is_running=False)


def translate_with_progress(
    job_id,
    lines,
    provider,
    api_key,
    endpoint,
    target_lang,
    model,
    bilingual,
    chunk_size,
    concurrency,
    max_retries,
    max_chars,
    max_paragraphs,
    rps,
    request_timeout,
    fallback_mode,
    repair_concurrency,
    openai_reasoning_effort,
):
    """带进度显示的翻译函数，底层复用核心并发逻辑"""
    from translate_vtt_zh_deepl_native import should_translate, translate_lines_native

    total = sum(1 for ln in lines if should_translate(ln))

    def progress_callback(current, total_count):
        progress = (current / total_count) * 100 if total_count > 0 else 0
        update_job(
            job_id,
            current=current,
            progress=progress,
            status=f"翻译进度: {current}/{total_count} ({progress:.1f}%)",
        )

    def batch_error_callback(start, end, error_text):
        increment_failed_batches(job_id)
        log_message(job_id, f"批次 {start}-{end} 翻译失败，已保留原文: {error_text}", "WARNING")

    return translate_lines_native(
        lines,
        api_key=api_key,
        provider=provider,
        endpoint=endpoint,
        target_lang=target_lang,
        model=model,
        bilingual=bilingual,
        every=max(1, min(10, chunk_size)),
        chunk=max(1, chunk_size),
        concurrency=max(1, concurrency),
        max_retries=max_retries,
        max_chars=max_chars,
        max_paragraphs=max_paragraphs,
        rps=rps,
        progress_callback=progress_callback if total > 0 else None,
        stop_check=lambda: should_continue(job_id),
        batch_error_callback=batch_error_callback,
        log_progress=False,
        request_timeout=request_timeout,
        fallback_mode=fallback_mode,
        repair_concurrency=repair_concurrency,
        openai_reasoning_effort=openai_reasoning_effort,
    )


def allowed_openai_reasoning_efforts(model: str) -> set[str]:
    model_lower = (model or "").strip().lower()
    # gpt-5.4 family supports none/low/medium/high/xhigh.
    if model_lower.startswith("gpt-5.4"):
        return {"none", "low", "medium", "high", "xhigh"}
    # gpt-5-nano family supports minimal/low/medium/high.
    if model_lower.startswith("gpt-5"):
        return {"minimal", "low", "medium", "high"}
    # non GPT-5 models: reasoning effort is not exposed in this tool.
    return {"low"}


@app.route('/')
def index():
    """主页面"""
    return '''
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>VTT字幕翻译工具</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #333;
                text-align: center;
                margin-bottom: 30px;
            }
            .form-group {
                margin-bottom: 20px;
            }
            label {
                display: block;
                margin-bottom: 5px;
                font-weight: bold;
                color: #555;
            }
            input, select, textarea {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 14px;
            }
            input[type="file"] {
                padding: 5px;
            }
            input[type="checkbox"] {
                width: auto;
                margin-right: 10px;
            }
            .checkbox-group {
                display: flex;
                align-items: center;
            }
            .number-input {
                width: 80px;
                display: inline-block;
            }
            button {
                background-color: #007AFF;
                color: white;
                padding: 12px 24px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
                margin-right: 10px;
            }
            button:hover {
                background-color: #0056CC;
            }
            button:disabled {
                background-color: #ccc;
                cursor: not-allowed;
            }
            .stop-btn {
                background-color: #FF3B30;
            }
            .stop-btn:hover {
                background-color: #CC2E26;
            }
            .progress-container {
                margin: 20px 0;
            }
            .progress-bar {
                width: 100%;
                height: 20px;
                background-color: #e0e0e0;
                border-radius: 10px;
                overflow: hidden;
            }
            .progress-fill {
                height: 100%;
                background-color: #007AFF;
                transition: width 0.3s ease;
                width: 0%;
            }
            .status {
                margin-top: 10px;
                font-weight: bold;
            }
            .log-container {
                margin-top: 20px;
                max-height: 300px;
                overflow-y: auto;
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 10px;
                background-color: #f9f9f9;
                font-family: monospace;
                font-size: 12px;
            }
            .log-entry {
                margin-bottom: 5px;
            }
            .log-error {
                color: #FF3B30;
            }
            .log-warning {
                color: #FF9500;
            }
            .log-success {
                color: #34C759;
            }
            .hidden {
                display: none;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎬 VTT字幕翻译工具</h1>
            
            <form id="translateForm" enctype="multipart/form-data">
                <div class="form-group">
                    <label for="inputFile">输入VTT文件:</label>
                    <input type="file" id="inputFile" name="inputFile" accept=".vtt" required>
                </div>
                
                <div class="form-group">
                    <label for="provider">翻译服务:</label>
                    <select id="provider" name="provider">
                        <option value="openai">OpenAI</option>
                        <option value="deepl">DeepL</option>
                        <option value="deepseek">DeepSeek</option>
                        <option value="gemini">Gemini</option>
                    </select>
                </div>

                <div class="form-group">
                    <label for="apiKey" id="apiKeyLabel">DeepL API密钥:</label>
                    <input type="password" id="apiKey" name="apiKey" placeholder="输入您的DeepL API密钥" required>
                </div>
                
                <div class="form-group">
                    <label for="endpoint">API端点:</label>
                    <select id="endpoint" name="endpoint">
                        <option value="https://api-free.deepl.com/v2/translate">免费版 (api-free.deepl.com)</option>
                        <option value="https://api.deepl.com/v2/translate">专业版 (api.deepl.com)</option>
                    </select>
                </div>

                <div class="form-group" id="modelGroup">
                    <label for="model">模型:</label>
                    <select id="model" name="model">
                        <option value="gpt-5-nano">gpt-5-nano</option>
                        <option value="gpt-5.4-nano">gpt-5.4-nano</option>
                        <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                        <option value="gpt-5.4">gpt-5.4</option>
                        <option value="gpt-4.1-nano">gpt-4.1-nano</option>
                        <option value="gpt-4.1-mini">gpt-4.1-mini</option>
                        <option value="gpt-4.1">gpt-4.1</option>
                        <option value="gpt-4o-mini">gpt-4o-mini</option>
                        <option value="deepseek-v4-flash">deepseek-v4-flash</option>
                        <option value="deepseek-v4-pro">deepseek-v4-pro</option>
                        <option value="gemini-2.5-flash-lite">gemini-2.5-flash-lite</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label for="targetLang">目标语言:</label>
                    <select id="targetLang" name="targetLang">
                        <option value="ZH">中文 (ZH)</option>
                        <option value="EN">英语 (EN)</option>
                        <option value="JA">日语 (JA)</option>
                        <option value="KO">韩语 (KO)</option>
                        <option value="FR">法语 (FR)</option>
                        <option value="DE">德语 (DE)</option>
                        <option value="ES">西班牙语 (ES)</option>
                        <option value="IT">意大利语 (IT)</option>
                        <option value="PT">葡萄牙语 (PT)</option>
                        <option value="RU">俄语 (RU)</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <div class="checkbox-group">
                        <input type="checkbox" id="bilingual" name="bilingual">
                        <label for="bilingual">双语输出（保留原文和译文）</label>
                    </div>
                </div>
                
                <div class="form-group">
                    <label>chunk(兜底): <input type="number" id="chunkSize" name="chunkSize" value="5" min="1" max="1000" class="number-input"></label>
                    <label style="margin-left: 20px;">并发数: <input type="number" id="concurrency" name="concurrency" value="80" min="1" max="200" class="number-input"></label>
                    <label style="margin-left: 20px;">最大重试次数: <input type="number" id="maxRetries" name="maxRetries" value="2" min="0" max="10" class="number-input"></label>
                </div>

                <div class="form-group">
                    <label>自适应分批: max_chars <input type="number" id="maxChars" name="maxChars" value="1800" min="0" max="20000" class="number-input"></label>
                    <label style="margin-left: 20px;">max_paragraphs <input type="number" id="maxParagraphs" name="maxParagraphs" value="6" min="0" max="200" class="number-input"></label>
                    <label style="margin-left: 20px;">RPS <input type="number" id="rps" name="rps" value="0" min="0" max="100" step="0.1" class="number-input"></label>
                </div>
                <div class="form-group">
                    <small style="color:#666;">说明：当 max_chars 或 max_paragraphs 大于 0 时，chunk 仅作为兜底值，不是主分批策略。</small>
                </div>

                <div class="form-group">
                    <label>超时(s) <input type="number" id="requestTimeout" name="requestTimeout" value="10" min="1" max="300" class="number-input"></label>
                    <label style="margin-left: 20px;" id="openaiReasoningGroup">OpenAI reasoning
                        <select id="openaiReasoningEffort" name="openaiReasoningEffort" class="number-input" style="width: 140px;">
                            <option value="low" selected>low</option>
                            <option value="minimal">minimal</option>
                            <option value="none">none</option>
                            <option value="medium">medium</option>
                            <option value="high">high</option>
                            <option value="xhigh">xhigh</option>
                        </select>
                    </label>
                    <label style="margin-left: 20px;">fallback模式
                        <select id="fallbackMode" name="fallbackMode" class="number-input" style="width: 150px;">
                            <option value="immediate">immediate</option>
                            <option value="deferred">deferred</option>
                            <option value="deferred-fastpath">deferred-fastpath</option>
                        </select>
                    </label>
                    <label style="margin-left: 20px;">repair并发 <input type="number" id="repairConcurrency" name="repairConcurrency" value="1" min="1" max="20" class="number-input"></label>
                </div>
                
                <div class="form-group">
                    <button type="submit" id="translateBtn">开始翻译</button>
                    <button type="button" id="stopBtn" class="stop-btn hidden" onclick="stopTranslation()">停止翻译</button>
                </div>
            </form>
            
            <div class="progress-container">
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill"></div>
                </div>
                <div class="status" id="status">就绪</div>
            </div>
            
            <div class="log-container" id="logContainer">
                <div class="log-entry">等待开始翻译...</div>
            </div>
        </div>
        
        <script>
            let isTranslating = false;
            let currentJobId = null;
            const providerDefaults = {
                deepl: {
                    apiKeyLabel: 'DeepL API密钥:',
                    apiKeyPlaceholder: '输入您的DeepL API密钥',
                    endpoints: [
                        { value: 'https://api-free.deepl.com/v2/translate', label: '免费版 (api-free.deepl.com)' },
                        { value: 'https://api.deepl.com/v2/translate', label: '专业版 (api.deepl.com)' }
                    ],
                    endpoint: 'https://api-free.deepl.com/v2/translate',
                    chunkSize: 160,
                    concurrency: 2,
                    maxRetries: 2,
                    maxChars: 0,
                    maxParagraphs: 0,
                    modelDisabled: true,
                    model: '',
                    models: [],
                },
                openai: {
                    apiKeyLabel: 'OpenAI API密钥:',
                    apiKeyPlaceholder: '输入您的OpenAI API密钥',
                    endpoints: [
                        { value: 'https://api.openai.com/v1/responses', label: 'Responses API (api.openai.com/v1/responses)' }
                    ],
                    endpoint: 'https://api.openai.com/v1/responses',
                    chunkSize: 5,
                    concurrency: 96,
                    maxRetries: 1,
                    maxChars: 1200,
                    maxParagraphs: 6,
                    modelDisabled: false,
                    model: 'gpt-5.4-nano',
                    models: ['gpt-5-nano', 'gpt-5.4-nano', 'gpt-5.4-mini', 'gpt-5.4', 'gpt-4.1-nano', 'gpt-4.1-mini', 'gpt-4.1', 'gpt-4o-mini'],
                    openaiReasoningEffort: 'low',
                    reasoningByModel: {
                        'gpt-5-nano': ['minimal', 'low', 'medium', 'high'],
                        'gpt-5.4-nano': ['none', 'low', 'medium', 'high', 'xhigh'],
                        'gpt-5.4-mini': ['none', 'low', 'medium', 'high', 'xhigh'],
                        'gpt-5.4': ['none', 'low', 'medium', 'high', 'xhigh'],
                        'gpt-4.1-nano': ['low'],
                        'gpt-4.1-mini': ['low'],
                        'gpt-4.1': ['low'],
                        'gpt-4o-mini': ['low'],
                    },
                },
                deepseek: {
                    apiKeyLabel: 'DeepSeek API密钥:',
                    apiKeyPlaceholder: '输入您的DeepSeek API密钥',
                    endpoints: [
                        { value: 'https://api.deepseek.com/chat/completions', label: 'DeepSeek Chat Completions (官方)' }
                    ],
                    endpoint: 'https://api.deepseek.com/chat/completions',
                    chunkSize: 5,
                    concurrency: 96,
                    maxRetries: 1,
                    maxChars: 1200,
                    maxParagraphs: 6,
                    modelDisabled: false,
                    model: 'deepseek-v4-flash',
                    models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
                    openaiReasoningEffort: 'low',
                    reasoningByModel: {},
                },
                gemini: {
                    apiKeyLabel: 'Gemini API密钥:',
                    apiKeyPlaceholder: '输入您的Gemini API密钥',
                    endpoints: [
                        { value: 'https://generativelanguage.googleapis.com/v1beta', label: 'Generative Language API (v1beta)' }
                    ],
                    endpoint: 'https://generativelanguage.googleapis.com/v1beta',
                    chunkSize: 5,
                    concurrency: 96,
                    maxRetries: 1,
                    maxChars: 1200,
                    maxParagraphs: 6,
                    modelDisabled: false,
                    model: 'gemini-2.5-flash-lite',
                    models: ['gemini-2.5-flash-lite'],
                    openaiReasoningEffort: 'low',
                    reasoningByModel: {},
                },
            };
            
            document.getElementById('translateForm').addEventListener('submit', function(e) {
                e.preventDefault();
                startTranslation();
            });
            document.getElementById('provider').addEventListener('change', function() {
                applyProviderDefaults(this.value);
            });
            document.getElementById('model').addEventListener('change', function() {
                refreshOpenAIReasoningOptions();
            });
            applyProviderDefaults('openai');

            function refreshOpenAIReasoningOptions() {
                const provider = document.getElementById('provider').value;
                const p = providerDefaults[provider] || providerDefaults.deepl;
                const model = document.getElementById('model').value || '';
                const sel = document.getElementById('openaiReasoningEffort');
                if (!sel) return;

                const allowed = (p.reasoningByModel && p.reasoningByModel[model]) || ['low'];
                const current = sel.value || p.openaiReasoningEffort || 'low';
                sel.innerHTML = '';
                allowed.forEach(v => {
                    const option = document.createElement('option');
                    option.value = v;
                    option.textContent = v;
                    sel.appendChild(option);
                });
                const next = allowed.includes(current) ? current : (allowed.includes('low') ? 'low' : allowed[0]);
                sel.value = next || 'low';
                sel.disabled = provider !== 'openai' || allowed.length === 1;
            }

            function applyProviderDefaults(provider) {
                const p = providerDefaults[provider] || providerDefaults.deepl;
                const endpoint = document.getElementById('endpoint');
                const model = document.getElementById('model');
                const modelGroup = document.getElementById('modelGroup');
                const openaiReasoningGroup = document.getElementById('openaiReasoningGroup');
                const openaiReasoningEffort = document.getElementById('openaiReasoningEffort');
                const apiKeyLabel = document.getElementById('apiKeyLabel');
                const apiKey = document.getElementById('apiKey');
                endpoint.innerHTML = '';
                p.endpoints.forEach(item => {
                    const option = document.createElement('option');
                    option.value = item.value;
                    option.textContent = item.label;
                    endpoint.appendChild(option);
                });
                endpoint.value = p.endpoint;
                document.getElementById('chunkSize').value = p.chunkSize;
                document.getElementById('concurrency').value = p.concurrency;
                document.getElementById('maxRetries').value = p.maxRetries;
                if (typeof p.maxChars !== 'undefined') {
                    document.getElementById('maxChars').value = p.maxChars;
                }
                if (typeof p.maxParagraphs !== 'undefined') {
                    document.getElementById('maxParagraphs').value = p.maxParagraphs;
                }
                model.innerHTML = '';
                (p.models || []).forEach(m => {
                    const option = document.createElement('option');
                    option.value = m;
                    option.textContent = m;
                    model.appendChild(option);
                });
                model.value = p.model || '';
                model.disabled = p.modelDisabled;
                modelGroup.style.display = p.modelDisabled ? 'none' : 'block';
                if (openaiReasoningGroup && openaiReasoningEffort) {
                    openaiReasoningGroup.style.display = provider === 'openai' ? 'inline-flex' : 'none';
                    openaiReasoningEffort.value = p.openaiReasoningEffort || 'low';
                }
                refreshOpenAIReasoningOptions();
                apiKeyLabel.textContent = p.apiKeyLabel;
                apiKey.placeholder = p.apiKeyPlaceholder || '';
            }
            
            function startTranslation() {
                if (isTranslating) return;
                
                const formData = new FormData();
                const inputFile = document.getElementById('inputFile').files[0];
                if (!inputFile) {
                    alert('请选择输入文件');
                    return;
                }
                
                formData.append('inputFile', inputFile);
                formData.append('provider', document.getElementById('provider').value);
                formData.append('apiKey', document.getElementById('apiKey').value);
                formData.append('endpoint', document.getElementById('endpoint').value);
                formData.append('targetLang', document.getElementById('targetLang').value);
                formData.append('model', document.getElementById('model').value);
                formData.append('bilingual', document.getElementById('bilingual').checked);
                formData.append('chunkSize', document.getElementById('chunkSize').value);
                formData.append('concurrency', document.getElementById('concurrency').value);
                formData.append('maxRetries', document.getElementById('maxRetries').value);
                formData.append('maxChars', document.getElementById('maxChars').value);
                formData.append('maxParagraphs', document.getElementById('maxParagraphs').value);
                formData.append('rps', document.getElementById('rps').value);
                formData.append('requestTimeout', document.getElementById('requestTimeout').value);
                formData.append('openaiReasoningEffort', document.getElementById('openaiReasoningEffort').value);
                formData.append('fallbackMode', document.getElementById('fallbackMode').value);
                formData.append('repairConcurrency', document.getElementById('repairConcurrency').value);
                
                isTranslating = true;
                document.getElementById('translateBtn').disabled = true;
                document.getElementById('translateBtn').classList.add('hidden');
                document.getElementById('stopBtn').classList.remove('hidden');
                document.getElementById('logContainer').innerHTML = '';
                
                fetch('/translate', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        currentJobId = data.job_id;
                        // 开始轮询状态
                        pollStatus();
                    } else {
                        alert('翻译启动失败: ' + data.error);
                        resetUI();
                    }
                })
                .catch(error => {
                    alert('请求失败: ' + error);
                    resetUI();
                });
            }
            
            function stopTranslation() {
                if (!currentJobId) return;
                fetch('/stop', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_id: currentJobId })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        addLog('用户停止了翻译', 'warning');
                    }
                });
                resetUI();
            }
            
            function resetUI() {
                isTranslating = false;
                document.getElementById('translateBtn').disabled = false;
                document.getElementById('translateBtn').classList.remove('hidden');
                document.getElementById('stopBtn').classList.add('hidden');
                currentJobId = null;
            }
            
            function pollStatus() {
                if (!isTranslating || !currentJobId) return;
                
                fetch('/status?job_id=' + encodeURIComponent(currentJobId))
                .then(response => response.json())
                .then(data => {
                    if (!data.success) {
                        addLog('状态查询失败: ' + data.error, 'error');
                        resetUI();
                        return;
                    }
                    updateProgress(data.progress);
                    updateStatus(data.status);
                    updateLogs(data.log_messages);
                    
                    if (data.is_running) {
                        setTimeout(pollStatus, 1000);
                    } else {
                        if (data.error) {
                            addLog('翻译失败: ' + data.error, 'error');
                        } else {
                            addLog('翻译完成！', 'success');
                            addDownloadLink(currentJobId);
                        }
                        resetUI();
                    }
                })
                .catch(error => {
                    addLog('状态查询失败: ' + error, 'error');
                    resetUI();
                });
            }
            
            function updateProgress(progress) {
                document.getElementById('progressFill').style.width = progress + '%';
            }
            
            function updateStatus(status) {
                document.getElementById('status').textContent = status;
            }
            
            function updateLogs(logs) {
                const container = document.getElementById('logContainer');
                container.innerHTML = '';
                logs.forEach(log => {
                    addLog(log, 'info');
                });
            }
            
            function addLog(message, type = 'info') {
                const container = document.getElementById('logContainer');
                const entry = document.createElement('div');
                entry.className = 'log-entry log-' + type;
                entry.textContent = message;
                container.appendChild(entry);
                container.scrollTop = container.scrollHeight;
            }

            function addDownloadLink(jobId) {
                const container = document.getElementById('logContainer');
                const entry = document.createElement('div');
                entry.className = 'log-entry log-success';
                const link = document.createElement('a');
                link.href = '/download?job_id=' + encodeURIComponent(jobId);
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = '点击下载翻译后的文件';
                entry.appendChild(link);
                container.appendChild(entry);
                container.scrollTop = container.scrollHeight;
            }
        </script>
    </body>
    </html>
    '''

@app.route('/translate', methods=['POST'])
def translate():
    """开始翻译"""
    try:
        cleanup_old_jobs()
        # 获取上传的文件
        if 'inputFile' not in request.files:
            return jsonify({'success': False, 'error': '没有选择文件'})
        
        file = request.files['inputFile']
        if file.filename == '':
            return jsonify({'success': False, 'error': '没有选择文件'})
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': '文件类型不支持，请选择.vtt文件'})
        
        # 保存上传的文件
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'success': False, 'error': '非法文件名'})

        job_id = uuid4().hex
        input_filename = f"{job_id}_{filename}"
        output_filename = f"{job_id}_translated_{filename}"
        input_path = os.path.join(app.config['UPLOAD_FOLDER'], input_filename)
        file.save(input_path)
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
        
        # 获取参数
        provider = (request.form.get('provider') or 'openai').strip().lower()
        if provider not in {"openai", "deepl", "deepseek", "gemini"}:
            return jsonify({'success': False, 'error': '当前Web端仅开放 OpenAI / DeepL / DeepSeek / Gemini'})
        profile = get_translator_profile(provider)
        if not profile:
            return jsonify({'success': False, 'error': '不支持的 provider'})

        api_key = (request.form.get('apiKey') or '').strip()
        endpoint = (request.form.get('endpoint') or profile["default_endpoint"]).strip()
        target_lang = (request.form.get('targetLang') or '').strip().upper()
        model = (request.form.get('model') or profile["default_model"]).strip()
        bilingual = request.form.get('bilingual') == 'true'
        try:
            chunk_size = int(request.form.get('chunkSize', profile["default_chunk_size"]))
            concurrency = int(request.form.get('concurrency', profile["default_concurrency"]))
            max_retries = int(request.form.get('maxRetries', profile["default_max_retries"]))
            max_chars = int(request.form.get('maxChars', 0))
            max_paragraphs = int(request.form.get('maxParagraphs', 6))
            repair_concurrency = int(request.form.get('repairConcurrency', 1))
        except ValueError:
            return jsonify({'success': False, 'error': '参数格式错误：数值参数必须是数字'})

        try:
            rps = float(request.form.get('rps', 0))
            request_timeout = float(request.form.get('requestTimeout', 10))
        except ValueError:
            return jsonify({'success': False, 'error': '参数格式错误：rps/requestTimeout 必须是数字'})

        fallback_mode = (request.form.get('fallbackMode') or 'immediate').strip().lower()
        openai_reasoning_effort = (request.form.get('openaiReasoningEffort') or 'low').strip().lower()

        # 后端强制限幅，避免前端限制被绕过导致超大请求
        chunk_size = max(1, min(MAX_CHUNK_SIZE, chunk_size))
        concurrency = max(1, min(200, concurrency))
        max_retries = max(0, min(10, max_retries))
        max_chars = max(0, min(20000, max_chars))
        max_paragraphs = max(0, min(500, max_paragraphs))
        rps = max(0.0, min(100.0, rps))
        request_timeout = max(1.0, min(300.0, request_timeout))
        repair_concurrency = max(1, min(20, repair_concurrency))
        if fallback_mode not in {"immediate", "deferred", "deferred-fastpath"}:
            return jsonify({'success': False, 'error': '非法 fallbackMode'})
        if openai_reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            return jsonify({'success': False, 'error': '非法 openaiReasoningEffort'})

        if not api_key:
            return jsonify({'success': False, 'error': 'API Key 不能为空'})
        if target_lang not in ALLOWED_TARGET_LANGS:
            return jsonify({'success': False, 'error': '非法 targetLang'})
        if provider == "deepl":
            if endpoint not in ALLOWED_ENDPOINTS:
                return jsonify({'success': False, 'error': '非法 endpoint'})
        elif provider == "openai":
            if endpoint not in ALLOWED_OPENAI_ENDPOINTS:
                return jsonify({'success': False, 'error': '非法 endpoint'})
            if model not in ALLOWED_OPENAI_MODELS:
                return jsonify({'success': False, 'error': '非法 model'})
            allowed_efforts = allowed_openai_reasoning_efforts(model)
            if openai_reasoning_effort not in allowed_efforts:
                return jsonify({'success': False, 'error': f'模型 {model} 不支持 reasoning={openai_reasoning_effort}'})
        elif provider == "deepseek":
            if not endpoint.startswith(ALLOWED_DEEPSEEK_ENDPOINT_PREFIX):
                return jsonify({'success': False, 'error': '非法 endpoint'})
            endpoint = normalize_openai_compatible_endpoint(endpoint, provider)
            if model not in ALLOWED_DEEPSEEK_MODELS:
                return jsonify({'success': False, 'error': '非法 model'})
        elif provider == "gemini":
            if not endpoint.startswith(ALLOWED_GEMINI_ENDPOINT_PREFIX):
                return jsonify({'success': False, 'error': '非法 endpoint'})
            if model not in ALLOWED_GEMINI_MODELS:
                return jsonify({'success': False, 'error': '非法 model'})
            endpoint = normalize_gemini_endpoint(endpoint, model)
        else:
            return jsonify({'success': False, 'error': '不支持的 provider'})

        create_job(job_id, input_path, output_path, filename)

        # 启动翻译线程
        thread = threading.Thread(
            target=translate_worker,
            args=(job_id, input_path, output_path, provider, api_key, endpoint, target_lang,
                  model, bilingual, chunk_size, concurrency, max_retries,
                  max_chars, max_paragraphs, rps, request_timeout, fallback_mode, repair_concurrency, openai_reasoning_effort),
            daemon=True
        )
        thread.start()

        return jsonify({'success': True, 'job_id': job_id})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/status')
def status():
    """获取翻译状态"""
    cleanup_old_jobs()
    job_id = request.args.get('job_id', '').strip()
    if not job_id:
        return jsonify({'success': False, 'error': '缺少job_id'}), 400
    snapshot = get_job_snapshot(job_id)
    if not snapshot:
        return jsonify({'success': False, 'error': '任务不存在'}), 404
    return jsonify({'success': True, **snapshot})

@app.route('/stop', methods=['POST'])
def stop():
    """停止翻译"""
    cleanup_old_jobs()
    payload = request.get_json(silent=True) or {}
    job_id = (payload.get('job_id') or request.form.get('job_id') or '').strip()
    if not job_id:
        return jsonify({'success': False, 'error': '缺少job_id'}), 400
    if not get_job(job_id):
        return jsonify({'success': False, 'error': '任务不存在'}), 404

    update_job(job_id, stop_requested=True, status='正在停止...')
    log_message(job_id, '用户请求停止翻译', 'WARNING')
    return jsonify({'success': True})

@app.route('/download')
def download():
    """下载翻译后的文件"""
    try:
        cleanup_old_jobs()
        job_id = request.args.get('job_id', '').strip()
        if not job_id:
            return "缺少job_id", 400

        if not get_job(job_id):
            return "任务不存在", 404

        with jobs_lock:
            output_file = jobs[job_id].get('output_file')
            original_filename = jobs[job_id].get('original_filename') or 'translated.vtt'
        if not output_file or not Path(output_file).exists():
            return "没有找到翻译文件", 404

        return send_file(
            str(output_file),
            as_attachment=True,
            download_name=f"translated_{original_filename}",
        )

    except Exception as e:
        return f"下载失败: {str(e)}", 500

if __name__ == '__main__':
    print("启动VTT字幕翻译工具 - Web版本")
    print("访问地址: http://localhost:8080")
    print("按 Ctrl+C 停止服务器")
    host = os.environ.get("VTT_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("VTT_WEB_PORT", "8080"))
    debug = os.environ.get("VTT_WEB_DEBUG", "false").lower() in ("1", "true", "yes", "on")
    access_log = os.environ.get("VTT_WEB_ACCESS_LOG", "false").lower() in ("1", "true", "yes", "on")
    if not access_log:
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(debug=debug, host=host, port=port)
