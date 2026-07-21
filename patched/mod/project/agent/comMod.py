# coding: utf-8
# -------------------------------------------------------------------
# aapanel
# -------------------------------------------------------------------
# Copyright (c) 2015-2099 aapanel(http://www.aapanel.com) All rights reserved.
# -------------------------------------------------------------------
# Author: aapanel
# -------------------------------------------------------------------

import ast
import datetime
import json
import logging
import os
import random
import re
import shutil
import tarfile
import threading
import time
import zipfile
from urllib.parse import quote

import requests
import yaml
from flask import stream_with_context, Response as Resp

import public

try:
    from public.hook_import import hook_import

    hook_import()
except:
    pass

from public import lang

# 尝试引入openai和numpy 如果没安装则执行命令安装
try:
    import openai
except ImportError:
    public.ExecShell("btpip install pydantic==2.5.3 openai==1.39.0")
    import openai

try:
    import numpy  # 提前为retrieval安装numpy
except ImportError:
    public.ExecShell("btpip install numpy==1.21.6")
    import numpy

from mod.project.agent.chat_client.tools import registry
from mod.project.agent.chat_client.skills import skill_manager
from mod.project.agent.chat_client.agent import Agent
from mod.project.agent.chat_client.single_agent import SingleAgent
from mod.project.agent.dynamic import _Dynamic_Pompts

# suppress verbose logging from libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

panelPath = public.get_panel_path()

APP_Num = 'aa_app_001'
APP_PATH = f'{panelPath}/mod/project/agent'
APP_DATA_PATH = f'{panelPath}/data/agent'

OFFICIAL = "aapanel.com"
AA_BASE_URL = "https://aa.maxcdn.top/aap_agent"

# RunCommand 默认继承 os
# Unsplash
UNSPLASH_CLIENT_ID = os.environ.setdefault("UNSPLASH_CLIENT_ID", "cOojVdJ5hFx_fn8FVOjUyIokoyj8GClePKxHgZ7d-T0")
# fallback PEXELS
PEXELS_API_KEY = os.environ.setdefault("PEXELS_API_KEY", "METErTSABjs8ZbC0hdWmbEDlcqbqegKSTaRnUoHGN0BkAQGqZd276tO6")

# OFFICIAL = "192.168.66.189"
# AA_BASE_URL = "http://192.168.66.189:61218"

# 文件完整性检查开关, 开发时可设置为 False 跳过验证
ENABLE_INTEGRITY_CHECK = False

# 关键文件的基准SHA256
BASELINE_FILE_HASHES = {
    'chat_client/simple_agent.py': 'df4272e7fe305a98451502ce5f7169ba293b074d468cf3700e25ae223cfdb2fb',
    'chat_client/single_agent.py': '858dea8206c3f20e257f578be8b3e58a751aa442d4f1ec3f34f367764a124ab6',
    'social/engine.py': '93ab1e2b7d9b2b5e2a4402fcf4c99cdd132c8d82889e765ecf04618b7c911108'
}


def return_response(status, msg):
    """统一返回格式"""
    if status:
        return public.success_v2(msg)
    return public.fail_v2(msg)


class main:
    # 类级别共享状态缓存 has_quota/is_pro/remote_model_version, 避免每次请求新建实例时状态丢失
    _shared_state = {
        "has_quota": False,
        "is_pro": False,
        "remote_model_version": "",
    }

    DEFAULT_CONFIG = {
        "has_quota": False,  # 初始化默认没额度
        "is_pro": False,  # 初始化非pro
        "default_headers": {
            "uid": "",
            "access-key": "",
            "appid": ""
        },

        "system_prompt": """# Role
        You are a professional Linux operations engineer within aaPanel. Proficient in Ubuntu, CentOS, Debian command syntax and
        operational scenarios. Provide reliable, precise, secure answers.""",

        # user
        "api_usage_url": f"{AA_BASE_URL}/api/usage",
        "api_usage_record_url": f"{AA_BASE_URL}/api/usage-records",

        # openai模型配置
        "api_base_url": "https://api-gpt.luce.moe/v3",
        # ai终端等场景
        "api_simple_url": "https://api-gpt.luce.moe/v3",
        # 睡梦(AutoDream) fast model 端点 (RAG 弃用后复用原知识库快速通道)
        "api_sleep_model_url": "https://api-gpt.luce.moe/v3",
        # 外部知识库查询 (RAG 已禁用, 死配置保留注释)
        # "api_klg_retrieve_url": f"{AA_BASE_URL}/knowledge-base/retrieve",

        # "api_embedding_url": "https://api-gpt.luce.moe/v3",  # RAG 已禁用

        "api_key": "--",
        "default_model": "",  # 暂时先不用
        "models": {
            "default": [
                {"name": "qwen3.5-flash", "auth": 0, "active": True},
                {"name": "qwen3.5-plus", "auth": 0, "active": True},
                {"name": "qwen3-max-2026-01-23", "auth": 0, "active": True},
                {"name": "qwen-plus", "auth": 0, "active": True},
                {"name": "doubao-seed-code-preview-251028", "auth": 0, "active": True},
            ]
        },
        "model_config": {
            # "qwen3.5-plus": {
            #     "max_context_tokens": 262144,
            #     "auto_compact_threshold": 0.75  # 模型特有的自动压缩阈值（可选，覆盖全局配置）
            # },
            # "qwen3-max-2026-01-23": {},
        },

        # 嵌入模型配置 (RAG 已禁用, 死配置保留注释)
        # "embedding": {
        #     "embedding_api_key": "--",
        #     "embedding_base_url": "https://api-gpt.luce.moe/v3",
        #     "embedding_model_name": "text-embedding-v4",
        # },
        # "rag": {
        #     "rag_retrieval_count": 10,  # RAG搜索数量 从向量库中获取出n条
        #     "rag_final_count": 5  # RAG搜索数量 最终会拼接在Message
        # },
        "agent": {
            "max_tool_iterations": 30,  # 最大工具调用次数  防止无限循环调用
            "temperature": 0.9,  # 温度参数 控制回复的随机性
            "top_p": 0.8,  # top_p参数 控制回复的多样性
        },
        "max_context_tokens": 128 * 1024,  # 主流模型较高能力范围(128k), 用于前端展示和上下文判断, 不传递给 OpenAI 接口
        # 自动压缩配置（当前默认关闭，预留代码位置）
        # "auto_compact": {
        #     "enabled": False,                  # 是否启用自动压缩
        #     "threshold_ratio": 0.75,           # 触发自动压缩的阈值比例（已使用token / 最大token）
        #     "preserve_rounds": 3,              # 压缩后保留的最近对话轮数
        #     "circuit_breaker_max_failures": 3, # 连续失败次数上限，超过后暂停自动压缩
        # }
    }

    # 受限场景的 prompt_id 集合 (social_chat 等)
    RESTRICTED_PROMPT_IDS = {'social_chat'}

    # AI 建站设计风格列表 (中性抽象风格文件名)
    awesome_style = [
        "Brand-Marketing.md",
        "Editorial-Workflow.md",
        "Museum-Gallery.md",
        "Crypto-Finance.md",
        "Corporate-Auto.md",
        "Motorsport-Engineering.md",
        "Luxury-Automotive.md",
        "Friendly-SaaS.md",
        "Warm-Editorial.md",
        "Vibrant-Data.md",
        "Dark-Database.md",
        "Refined-AI.md",
        "Institutional-Fintech.md",
        "Dark-Devtool.md",
        "Editorial-Devtool.md",
        "Retro-Web.md",
        "Voice-Magazine.md",
        "Developer-Platform.md",
        "Cinematic-Luxury.md",
        "Studio-Monochrome.md",
        "Dark-Builder.md",
        "AI-Developer.md",
        "Tech-Corporate.md",
        "Enterprise.md",
        "Customer-SaaS.md",
        "Dark-Crypto.md",
        "Supercar-Luxury.md",
        "Software-Craft.md",
        "Warm-Devtool.md",
        "Financial-Brand.md",
        "Product-Commerce.md",
        "Gradient-AI.md",
        "Docs-Platform.md",
        "Playful-Workspace.md",
        "Sunset-AI.md",
        "Dual-Database.md",
        "E-Commerce.md",
        "Retro-Gaming.md",
        "Illustrated-Workspace.md",
        "Engineering-Tech.md",
        "Minimal-Docs.md",
        "Terminal-Mono.md",
        "Visual-Discovery.md",
        "Console-Marketing.md",
        "Playful-Devtool.md",
        "App-Showcase.md",
        "Auto-Editorial.md",
        "AI-Lab.md",
        "Mono-Serif.md",
        "Fintech-Brochure.md",
        "Generative-Studio.md",
        "Content-Platform.md",
        "Midnight-Devtool.md",
        "Cinematic-Commerce.md",
        "Workplace-Messaging.md",
        "Aerospace-Mission.md",
        "Music-Streaming.md",
        "Retail-Lifestyle.md",
        "Gradient-Finance.md",
        "Emerald-Devtool.md",
        "Productivity-Editorial.md",
        "Minimal-Tech.md",
        "Tech-Magazine.md",
        "Gradient-Infra.md",
        "Mobility-Mono.md",
        "Gradient-Devtool.md",
        "Telecom-Editorial.md",
        "Agent-Engineering.md",
        "Terminal-Devtool.md",
        "Visual-Builder.md",
        "Magazine-Editorial.md",
        "Fintech-Editorial.md",
        "Frontier-AI.md",
        "Workflow-Warm.md",
    ]

    # 初始化
    def __init__(self):
        self.APP_PATH = APP_PATH
        self.data_path = APP_DATA_PATH

        if not os.path.exists(self.data_path):
            os.makedirs(self.data_path)

        self.config_path = os.path.join(self.data_path, 'config.json')

        # 1. 以默认配置为基准 (深拷贝)
        self.config = json.loads(json.dumps(self.DEFAULT_CONFIG))

        # 2. 加载用户配置并逐 key 判断合并 (文件中有有效值用文件的,否则用默认的)
        user_config = self._load_config()
        self._merge_config_with_rules(self.config, user_config)

        # 3. 更新动态配置 (用户信息)
        self._refresh_default_headers()

        # 从类级别缓存读取 has_quota/is_pro (避免每次请求新建实例时状态丢失)
        self.config['has_quota'] = self._shared_state['has_quota']
        self.config['is_pro'] = self._shared_state['is_pro']
        self._pro_auth(None)

        # 加载检查是否有默认模型配置信息
        try:
            need_refresh = False
            # 配置文件不存在或为空
            if not os.path.exists(self.config_path) or not user_config:
                need_refresh = True
            # 检查 default 模型配置是否为空或格式错误
            elif not need_refresh:
                default_models = self.config.get('models', {}).get('default', [])
                if not default_models or not isinstance(default_models, list) or len(default_models) == 0:
                    need_refresh = True
                else:
                    for m in default_models:
                        if not isinstance(m, dict) or not m.get('name') or not m.get('base_url'):
                            need_refresh = True
                            break

            if need_refresh:
                self.__refresh_default()
        except Exception as e:
            public.print_log(f"Error refreshing default model config: {str(e)}")

    def __refresh_default(self) -> bool:
        """刷新 default 账号模型配置

        Returns:
            bool: 刷新是否成功 (status == 0 表示成功)
        """
        status = self.get_models(public.to_dict_obj({
            "account_name": "default",
            "base_url": self.DEFAULT_CONFIG["api_base_url"],
            "key": "--",
            "force": True
        })).get("status", -1)
        return status == 0

    def __generate_session_title(self, session_dir, user_text):
        """异步生成会话标题并写入 meta.json, 失败时用用户输入截断兜底"""
        title = None
        try:
            single_agent = SingleAgent(
                api_key='--',
                base_url=self.DEFAULT_CONFIG['api_simple_url'],
                model_name='qwen3.5-flash',
                default_headers=self.config['default_headers'],
                temperature=0.3
            )

            result = single_agent.chat(
                prompt="You are a title generation assistant. Generate a short, "
                       "accurate chat title (max 20 characters) based on user input."
                       " Return only the title, nothing else.",
                input_text=user_text
            )
            single_agent.close()

            if result.get('success') and result.get('response'):
                title = result['response'].strip()
        except:
            pass

        # 失败时, 用用户输入截断前5字符兜底
        if not title:
            title = (user_text or '')[:5].replace('\n', ' ').strip() or 'question'

        from mod.project.agent.chat_client.tools.base import atomic_update_json
        try:
            meta_file = os.path.join(session_dir, 'meta.json')
            if os.path.exists(meta_file):
                atomic_update_json(meta_file, lambda d: {**d, 'ai_title': title})
        except:
            pass

    def _async_generate_title(self, session_dir: str, user_input: str):
        # 提取用户输入文本
        is_new_session = False
        meta_file = os.path.join(session_dir, 'meta.json')

        if not os.path.exists(meta_file):
            is_new_session = True
        else:
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                # 如果没有 ai_title 字段，说明是首次
                if 'ai_title' not in meta:
                    is_new_session = True
                    # 初始化 ai_title 为空
                    meta['ai_title'] = ''
                    with open(meta_file, 'w', encoding='utf-8') as f:
                        json.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception as e:
                public.print_log(f"[ERROR] Failed to read/write meta file : {meta_file}, {str(e)}")
                return

        if not is_new_session:
            return

        # 如果是首次创建，生成标题
        user_text = ''
        if isinstance(user_input, str):
            user_text = user_input
        elif isinstance(user_input, list):
            for item in user_input:
                if isinstance(item, dict) and item.get('type') == 'text':
                    user_text = item.get('text', '')
                    break

        if user_text:
            # 异步生成标题
            # self.__generate_session_title(session_dir, user_text)
            threading.Thread(
                target=self.__generate_session_title,
                args=(session_dir, user_text),
                daemon=True
            ).start()

    def _merge_custom_headers(self, get, template_config):
        """
        合并自定义 headers，支持前端传递和模板定义
        优先级：前端传递 > 模板定义
        逻辑：追加到现有 headers，不直接覆盖
        注意：对包含非 ASCII 字符（如中文）的值进行 URL 编码
        """
        custom_headers = {}

        # 1. 从模板配置中获取 custom_headers
        template_custom_headers = template_config.get('custom_headers', {})
        if isinstance(template_custom_headers, str):
            try:
                template_custom_headers = json.loads(template_custom_headers)
            except:
                template_custom_headers = {}
        if isinstance(template_custom_headers, dict):
            custom_headers.update(template_custom_headers)

        # 2. 从前端参数中获取 custom_headers（优先级更高）
        frontend_custom_headers = get.get('custom_headers', '')
        if frontend_custom_headers:
            if isinstance(frontend_custom_headers, str):
                try:
                    frontend_custom_headers = json.loads(frontend_custom_headers)
                except:
                    frontend_custom_headers = {}
            if isinstance(frontend_custom_headers, dict):
                custom_headers.update(frontend_custom_headers)

        # 3. 对包含非 ASCII 字符（如中文）的值进行 URL 编码
        encoded_headers = {}
        for key, value in custom_headers.items():
            if isinstance(value, str):
                # 检查是否包含非 ASCII 字符
                try:
                    value.encode('ascii')
                    # 纯 ASCII，不需要编码
                    encoded_headers[key] = value
                except UnicodeEncodeError:
                    # 包含非 ASCII 字符（如中文），进行 URL 编码
                    encoded_headers[key] = quote(value, safe='')
            else:
                # 非字符串值直接保留
                encoded_headers[key] = value

        return encoded_headers

    def _fetch_remote_info(self, refresh_headers: bool = True) -> dict:
        """获取远端用户信息并更新缓存状态

        功能：
        - 仅在使用官方 API 时请求远端
        - 获取用户额度、model_version 等信息
        - 自动刷新模型配置（model_version 变化时）
        - 更新类级别缓存（has_quota、is_pro、remote_model_version）

        Returns:
            dict: 远端返回的 data 部分，失败时返回空 dict
        """
        data = {}
        if refresh_headers:
            self._refresh_default_headers()
        if self.config.get('api_base_url') != self.DEFAULT_CONFIG['api_base_url']:
            return data  # 非官方 API，不请求远端

        try:
            response = requests.get(self.config['api_usage_url'], headers=self.config['default_headers'])
            if response.status_code != 200:
                return data

            res = response.json()
            if not res.get("status"):
                return data

            data = res.get("data", {})

            # 远端 model_version 与类变量缓存对比，不一致则刷新
            try:
                remote_model_version = data.get("model_version", "")
                cached_version = main._shared_state.get('remote_model_version', '')
                if remote_model_version and remote_model_version != cached_version:
                    self.__refresh_default()
                    # 刷新后重新加载配置以获取最新的模型列表
                    self.config['models'] = self._load_config().get('models', {})
                    # 更新缓存
                    main._shared_state['remote_model_version'] = remote_model_version
            except Exception as e:
                public.print_log(f"Model version check error: {str(e)}")

            # 更新额度状态
            common_packages = data.get("common_packages",
                                       {"remaining_count": 0, "total_count": 0, "used_count": 0, "packages": []})
            remaining = data.get("remaining", 0) + common_packages.get("remaining_count", 0)
            self.config['has_quota'] = True if remaining > 0 else False
            main._shared_state['has_quota'] = self.config['has_quota']

            # 更新 pro 状态
            self._pro_auth(None)

        except Exception as e:
            public.print_log(f"Fetch remote info error: {str(e)}")

        return data

    def _log_chat_stats(self, log_type: str, model: str, total_tokens: int, is_official: bool):
        """封装聊天统计日志记录"""
        # 均为 mod 前缀
        # log_type -> prompts id -> agent_aics.md
        # 记录 ai agent 调用入口, 不再记录model, token等细节.
        public.set_module_logs("mod_agent_api_call", log_type,1)
        # 记录官方/非官方 URL 调用
        if is_official:
            public.set_module_logs("mod_agent_api_source", "official", 1)
        else:
            public.set_module_logs("mod_agent_api_source", "custom", 1)

    def _load_template_config(self, template_name, smart_mode="0") -> tuple[str, dict]:
        """
        加载模板配置, 统一由 _Dynamic_Pompts 动态生成
        Returns:
            (system_prompt, config_dict)
        """
        dp = _Dynamic_Pompts()
        return dp.get_prompts(prompts_name=template_name, smart_mode=smart_mode), dp.frontmatter(template_name)

    def _load_prompt_config(self, prompt_id, system_prompt=None):
        """
        加载 Prompt 配置，处理变量替换和参数提取
        返回 (system_prompt, config_dict)
        """
        prompt_config = {}
        final_system_prompt = system_prompt

        # 1. 如果指定了 prompt_id 且没有外部传入 system_prompt，则从文件加载
        if prompt_id and not final_system_prompt:
            prompts_dir = os.path.join(self.APP_PATH, 'prompts')
            if os.path.exists(prompts_dir):
                for ext in ['.md', '.txt']:
                    prompt_file_path = os.path.join(prompts_dir, f"{prompt_id}{ext}")
                    if os.path.exists(prompt_file_path):
                        try:
                            with open(prompt_file_path, 'r', encoding='utf-8') as f:
                                content = f.read()

                            # Frontmatter 解析
                            if content.startswith('---'):
                                try:
                                    import re
                                    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
                                    if match:
                                        frontmatter_str = match.group(1)
                                        final_system_prompt = match.group(2).strip()

                                        # 解析 Key-Value using PyYAML
                                        try:
                                            parsed_config = yaml.safe_load(frontmatter_str)
                                            if isinstance(parsed_config, dict):
                                                prompt_config.update(parsed_config)
                                        except Exception:
                                            pass
                                    else:
                                        final_system_prompt = content
                                except:
                                    final_system_prompt = content
                            else:
                                final_system_prompt = content
                            break  # 找到文件后退出循环
                        except Exception:
                            pass

        # 2. 变量替换
        if final_system_prompt:
            try:
                os_version = public.get_os_version()
                final_system_prompt = final_system_prompt.replace('{{OS_VERSION}}', os_version)
            except Exception:
                pass

        return final_system_prompt, prompt_config

    def _refresh_default_headers(self) -> dict:
        """刷新官方 API 请求所需的动态 headers"""
        user_info = public.get_user_info(jwt=True)
        headers = {
            "env": json.dumps(public.fetch_env_info()),
            "uid": str(user_info.get("uid", "")),
            "access-key": user_info.get("jwt", ""),
            "appid": APP_Num,
        }
        self.DEFAULT_CONFIG['default_headers']['uid'] = headers['uid']
        self.DEFAULT_CONFIG['default_headers']['access-key'] = headers['access-key']
        self.DEFAULT_CONFIG['default_headers']['appid'] = headers['appid']
        self.config['default_headers'] = headers
        return headers

    def _calculate_file_hash(self, file_path: str) -> str:
        """计算文件的 SHA256 哈希值"""
        try:
            import hashlib
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return ""

    def _verify_file_integrity(self):
        """检查关键文件的完整性，失败时返回 SSE 错误响应"""
        # 检查开关，开发时可跳过
        if not ENABLE_INTEGRITY_CHECK:
            return None

        # 验证文件完整性
        for file_rel_path, expected_hash in BASELINE_FILE_HASHES.items():
            file_full_path = os.path.join(self.APP_PATH, file_rel_path)

            if not os.path.exists(file_full_path):
                return self._integrity_error_response()

            current_hash = self._calculate_file_hash(file_full_path)
            if not current_hash:
                return self._integrity_error_response()

            if current_hash != expected_hash:
                return self._integrity_error_response()

        return None

    def _integrity_error_response(self):
        """生成文件完整性检查失败的 SSE 错误响应"""
        sse_headers = {
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }

        def error_wrapper():
            yield self.sse_pack(event="error", data={"msg": lang('文件损坏，请修复面板')})

        return Resp(
            stream_with_context(error_wrapper()),
            mimetype="text/event-stream",
            headers=sse_headers
        )

    def _normalize_base_url(self, base_url: str) -> str:
        """归一化前端展示用的官方地址别名"""
        if base_url == "official":
            return self.DEFAULT_CONFIG.get('api_base_url', '')
        return base_url

    def _filter_default_values(self, current, defaults):
        """递归过滤掉与默认值相同的配置项"""
        filtered = {}
        for k, v in current.items():
            if k not in defaults:
                # 默认配置中不存在的 key，直接保留
                filtered[k] = v
            elif isinstance(v, dict) and isinstance(defaults[k], dict):
                # 递归处理嵌套字典
                nested_filtered = self._filter_default_values(v, defaults[k])
                if nested_filtered:  # 只保留有内容的嵌套字典
                    filtered[k] = nested_filtered
            elif v != defaults[k]:
                # 值与默认值不同，保留
                filtered[k] = v
            # 值与默认值相同，跳过不保存
        return filtered

    def _filter_models_value_by_whitelist(self, submitted_models: dict, existing_models: dict):
        """保护 models 使用白名单机制，只允许修改白名单字段，不允许增删模型"""
        WHITELIST = [
            "active",  # 是否启用
        ]
        result = {}
        for account_name, model_list in submitted_models.items():
            if not isinstance(model_list, list):
                continue
            existing_list = existing_models.get(account_name, [])
            if not existing_list:
                continue
            # 按 name 建立提交模型的映射
            name_to_submitted = {}
            for item in model_list:
                if isinstance(item, dict) and item.get("name"):
                    name_to_submitted[item["name"]] = item
            # 以原有模型为基础，更新白名单字段
            updated_list = []
            for original in existing_list:
                new_item = dict(original)
                submitted = name_to_submitted.get(original.get("name"))
                if submitted:
                    for field in WHITELIST:
                        if field in submitted:
                            new_item[field] = submitted[field]
                updated_list.append(new_item)
            result[account_name] = updated_list
        return result

    def _merge_config_with_rules(self, base, update):
        """递归合并配置，空值使用默认值"""
        for k, v in update.items():
            # 处理字符串 strip
            if isinstance(v, str):
                v = v.strip()

            # 判断是否为空 (None, "", [])
            is_empty = (v is None) or (v == "") or (isinstance(v, list) and len(v) == 0)

            if is_empty:
                continue

            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._merge_config_with_rules(base[k], v)
            else:
                base[k] = v

    def _get_model_max_context_tokens(self):
        """获取最大上下文 token 数, 取主流模型较高能力范围(128K)"""
        return self.DEFAULT_CONFIG.get('max_context_tokens', 131072)

    def _get_history_session_platform(self, session_id):
        """从 session_id 解析平台名称, 转小写, 无白名单限制"""
        if session_id.startswith('social:'):
            parts = session_id.split(':')
            if len(parts) >= 3 and parts[1]:
                return parts[1].lower()
            return ''
        return 'panel'

    def _resolve_history_sessions_dirs(self, sessions_dir_param):
        """解析并规范化 sessions_dir 列表, 始终追加 social_sessions"""
        SOCIAL_SESSIONS = 'social_sessions'

        if not sessions_dir_param:
            return ['sessions', SOCIAL_SESSIONS]

        # 解析输入为列表
        if isinstance(sessions_dir_param, str):
            try:
                parsed = ast.literal_eval(sessions_dir_param)
                if isinstance(parsed, list):
                    raw_dirs = parsed
                else:
                    raw_dirs = [sessions_dir_param]
            except Exception:
                raw_dirs = [d.strip() for d in sessions_dir_param.split(',') if d.strip()]
        elif isinstance(sessions_dir_param, list):
            raw_dirs = sessions_dir_param
        else:
            raw_dirs = [sessions_dir_param]

        # 去重(保持首次出现顺序)
        seen = set()
        dirs = []
        for d in raw_dirs:
            if d not in seen:
                seen.add(d)
                dirs.append(d)

        # 始终追加 social_sessions
        if SOCIAL_SESSIONS not in dirs:
            dirs.append(SOCIAL_SESSIONS)

        return dirs

    def _resolve_enabled_tools(self, smart_mode: str, prompt_id: str, request_tools: list, template_tools) -> list:
        """
        根据场景解析最终启用的工具列表.

        规则:
        - smart_mode=1: 全开所有可见工具
        - smart_mode=0 + 受限场景: 仅安全只读工具
        - smart_mode=0 + 普通场景: 请求参数 + 模板配置合并去重
        """
        # 智能模式火力全开
        if smart_mode == '1':
            final_tools = [meta["id"] for meta in registry.get_all_tools_info()]

        # 特殊受限场景
        elif prompt_id in main.RESTRICTED_PROMPT_IDS:
            final_tools = sorted(
                t["id"] for t in registry.get_all_tools_info() if t.get("risk_level") == "low"
            )

        # 否则正常合并请求参数和模板配置的工具列表
        else:
            final_tools = list(request_tools) if request_tools else []
            if isinstance(template_tools, str):
                try:
                    template_tools = ast.literal_eval(template_tools)
                except Exception:
                    template_tools = []
            if isinstance(template_tools, list):
                for tool in template_tools:
                    if tool not in final_tools:
                        final_tools.append(tool)

        # Skills 工具常驻: 实际可用内容由 skill 启用状态决定, 全关则为空 (见 tools/skill.py:_get_skill_doc)
        if "Skills" not in final_tools:
            final_tools.append("Skills")

        # 过滤 subagent_only 工具 (主代理不可见, 仅子代理可用, 来自 registry 声明)
        _subagent_only_ids = {t["id"] for t in registry.get_all_tools_info() if t.get("subagent_only")}
        final_tools = [t for t in final_tools if t not in _subagent_only_ids]

        # internal 工具常驻(强开启; 列表来自 registry 声明)
        for _it in registry.get_internal_tools():
            if _it not in final_tools:
                final_tools.append(_it)

        return final_tools

    def _sync_model_config_to_storage(self, models_data: list, accoutn_name: str, base_url: str, key: str):
        """
        内部方法：将模型数据同步到 config
        """
        try:
            existing_model_config = self._load_config().get('models', {})
            if not isinstance(existing_model_config, dict):
                existing_model_config = {}

            is_official = OFFICIAL in base_url
            accoutn_name = "default" if is_official else accoutn_name
            updates = []
            for model_data in models_data:
                model_id = model_data.get('id', '')
                if not model_id:
                    continue
                auth = 0  # 免费
                usage_cost = model_data.get("usage_cost", 1)
                if model_data.get('pro', False) and isinstance(model_data.get('pro'), bool):
                    auth = 1  # pro
                if not is_official:
                    auth = 2  # 自定义
                    usage_cost = 0

                record = {
                    "name": model_id,
                    "auth": auth,
                    "active": model_data.get("active", False),  # 使用远端返回的 active, 如果有的话
                    "base_url": base_url,
                    "key": key,
                    "usage_cost": usage_cost,
                }
                # 仅官方账号写入真实上下文上限; 非官方不存, 取值时 get 不到即走 128k
                if is_official:
                    record["max_context_tokens"] = model_data.get("max_context_tokens", 131072)
                updates.append(record)

            # 保留 active 状态
            old_models = existing_model_config.get(accoutn_name, [])
            old_model_map = {
                m.get('name'): m for m in old_models if isinstance(m, dict)
            }
            for model in updates:
                model_name = model.get('name')
                if model_name in old_model_map:
                    # 存在的模型保留旧的 active 状态
                    model['active'] = old_model_map[model_name].get('active', False)

            existing_model_config[accoutn_name] = updates
            self.config['models'] = existing_model_config
            self._save_config()
        except:
            # 静默失败，不影响主流程
            pass

    def _get_priority_value(self, key, get, prompt_config, default=None):
        """
        获取配置参数，优先级: URL参数 > Prompt配置 > 默认值
        """
        # 1. URL 参数
        val = get.get(key)
        if val:
            return val

        # 2. Prompt 配置
        val = prompt_config.get(key)
        if val:
            return val

        # 3. 默认值
        return default

    @staticmethod
    def _filter_internal_tool_msgs(history):
        """剥离 internal=True 工具在历史中的痕迹: assistant.tool_calls + role=tool 结果双侧清理"""
        _internal = frozenset(t for t in registry.get_internal_tools() if isinstance(t, str))
        # 第一遍: 按 name 收集 internal 工具的 tool_call_id
        _internal_ids = set()
        if _internal:
            for msg in history:
                if msg.get('role') == 'assistant':
                    for tc in (msg.get('tool_calls') or []):
                        if ((tc or {}).get('function') or {}).get('name') in _internal and tc.get('id'):
                            _internal_ids.add(tc['id'])
        filtered = []
        for msg in history:
            role = msg.get('role')
            # role=tool: internal 结果整条跳过
            if role == 'tool' and msg.get('tool_call_id') in _internal_ids:
                continue
            # role=assistant: 剥离 internal 的 tool_calls; 剥光且无文本内容则丢空壳
            if role == 'assistant' and msg.get('tool_calls'):
                kept = [tc for tc in msg['tool_calls']
                        if ((tc or {}).get('function') or {}).get('name') not in _internal]
                if len(kept) != len(msg['tool_calls']):
                    if kept:
                        msg['tool_calls'] = kept
                    else:
                        msg.pop('tool_calls', None)  # tool_calls 全是 internal -> 删字段
                        _c = msg.get('content')
                        if not (isinstance(_c, str) and _c.strip()):
                            continue  # 且无文本内容 -> 丢空壳
            # role=tool 的 list content -> text 规范化 (非 internal 结果, 供前端展示)
            if role == 'tool':
                c = msg.get('content')
                if isinstance(c, list) and c and isinstance(c[0], dict) and c[0].get('type') == 'text':
                    msg['content'] = c[0].get('text', '')
            filtered.append(msg)
        return filtered

    def _pro_auth(self, get):  # noqa
        try:
            import PluginLoader  # noqa
            self.config['is_pro'] = True if PluginLoader.get_auth_state() > 0 else False
            main._shared_state['is_pro'] = self.config['is_pro']
        except ImportError:
            import sys
            if not "class/" in sys.path:
                sys.path.insert(0, "class/")
            try:
                import PluginLoader  # noqa
                self.config['is_pro'] = True if PluginLoader.get_auth_state() > 0 else False
                main._shared_state['is_pro'] = self.config['is_pro']
            except Exception as e:
                public.print_log(f"Failed to load: {str(e)}")

    def _load_config(self):
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    def _save_config(self):
        """保存配置到 config.json，过滤掉与默认值相同的配置及保护字段"""
        try:
            # 过滤掉与默认值相同的配置
            filtered_config = self._filter_default_values(self.config, self.DEFAULT_CONFIG)
            # 过滤掉保护字段 (动态字段，不应持久化)
            PROTECTED_FIELDS = ['has_quota', 'is_pro']
            for field in PROTECTED_FIELDS:
                if field in filtered_config:
                    del filtered_config[field]
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(filtered_config, f, indent=4, ensure_ascii=False)
            return True, lang('Saved successfully')
        except Exception as e:
            return False, lang(f'Save failed: {str(e)}')

    def _sse_response(self, generator_func, error_prefix):
        """
        通用的 SSE 响应处理方法

        Args:
            generator_func: 生成器函数，无参数调用
            error_prefix: 错误消息前缀 (如 "Chat error", "Simple chat error")

        Returns:
            Flask Response 对象，配置为 SSE 流式响应
        """
        sse_headers = {
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }

        def wrapper():
            try:
                for chunk in generator_func():
                    yield chunk
            except GeneratorExit:
                # 客户端终止生成, 断开连接, 正常
                public.print_log(f"GeneratorExit")
                pass
            except Exception as e:
                import traceback
                err = traceback.format_exc()
                public.print_log(f"[SSE ERROR] {err}")
                yield self.sse_pack(event="error", data={"msg": f"{error_prefix}: {str(e)}"})

        try:
            response = Resp(
                stream_with_context(wrapper()),
                mimetype="text/event-stream",
                headers=sse_headers
            )
            return response
        except Exception as e:
            import traceback
            public.print_log(f"[RESPONSE CREATE ERROR] {traceback.format_exc()}")

            # 出错返回一个 Response 对象，包含错误信息
            def error_wrapper():
                yield self.sse_pack(event="error", data={"msg": f"Response creation failed: {str(e)}"})

            return Resp(
                stream_with_context(error_wrapper()),
                mimetype="text/event-stream",
                headers=sse_headers
            )

    def _chat_generator(self, get):
        """
        聊天接口 (SSE)
        支持通过 prompt_id 选定对应的 prompt，也支持直接传递 system_prompt
        Args:
            - message: 用户输入
            - session_id: 会话 ID (可选)
            - model: 模型名称 (可选，优先级高于 Prompt 配置)
            - system_prompt: 系统提示词 (可选，优先级高于 Prompt)
            - prompt_id: Prompt 配置 ID (可选) 从 prompts 目录加载对应的配置
            - tools: 工具列表 (可选，优先级高于 Prompt 配置)
            - sessions_dir: 会话目录 (可选，支持 prompt 模板配置)
            - appid: 应用 ID (可选，用于覆盖 headers 中的 appid)
            - custom_headers: 自定义请求头 (可选，JSON字符串格式，会追加到默认headers中，不会覆盖)
            - smart_mode: 智能模式开关 (可选，默认为 '0'), 开启后启开启所有工具, 智能选择, 忽略 tools 参数
        """
        # 初始化 token 累积
        total_usage = {
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0
        }

        user_input = get.get('message', '')
        if isinstance(user_input, str):
            try:
                parsed_input = json.loads(user_input)
                if isinstance(parsed_input, list):
                    user_input = parsed_input
            except json.JSONDecodeError:
                pass

        session_id = get.get('session_id', 'default_session')
        account_name = get.get('account_name', '') or 'default'  # 默认使用 default
        model = get.get('model', '').strip()
        prompt_id = get.get('prompt_id', '')
        tools = get.get('tools', '[]')
        tools = ast.literal_eval(tools)
        thinking = get.get('thinking', 'true').lower() == 'true'
        web_search = get.get('web_search', 'false').lower() == 'true'
        smart_mode = get.get('smartMode', '0')
        chat_type = get.get('chat_type', 'chat')

        if not user_input:
            yield self.sse_pack(event="error", data={"msg": lang("Please enter content")})
            return

        # 加载模板配置 (统一走 dynamic.py)
        # website 会话: 强制走 website 专属 prompt(不改前端传参; chat_type 见 L1019)
        if chat_type == "website":
            prompt_id = "website"
        final_system_prompt, template_config = self._load_template_config(prompt_id, smart_mode)
        if not final_system_prompt:
            yield self.sse_pack(
                event="error", data={"msg": lang("Assistant configuration not found, please try updating the plugin")}
            )
            return
        # 确定 model 参数（优先级：URL > 模板配置 > 默认）
        if not model:
            model = template_config.get('model_name') or template_config.get('model')
        if not model:
            yield self.sse_pack(event="error", data={"msg": lang("Please select a model")})
            return

        # 从 models[account_name] 中查找当前模型的 base_url 和 key（作为 fallback）
        account_models = self.config.get('models', {}).get(account_name, [])
        fallback_base_url = None
        fallback_key = None
        model_auth = None  # 模型的 auth 状态 (0=免费, 1=付费)
        model_max_context = None

        for m in account_models:
            if m.get('name') == model:
                fallback_base_url = m.get('base_url')
                fallback_key = m.get('key')
                model_auth = m.get('auth', 0)  # 获取 auth 状态
                model_max_context = m.get('max_context_tokens')  # 官方真实值, 非官方忽略走 128k
                break

        # 客户端校验 model auth 与用户 is_pro 状态
        is_pro = self.config.get('is_pro', False)
        if model_auth == 1 and not is_pro:
            yield self.sse_pack(event="error", data={
                "msg": lang(f"😥 Model '{model}' requires Pro permission. Please upgrade to Pro to use this model. ")
            })
            return

        if not fallback_base_url:
            fallback_base_url = self.DEFAULT_CONFIG.get('api_base_url', '')
        if not fallback_key:
            fallback_key = self.DEFAULT_CONFIG.get('api_key', '')

        # 通过 _get_priority_value 确定最终的 api_key 和 base_url
        final_api_key = self._get_priority_value('api_key', get, template_config, fallback_key)
        final_base_url = self._normalize_base_url(
            self._get_priority_value('base_url', get, template_config, fallback_base_url)
        )

        # 根据最终 base_url 判断是否为官方地址，确定 headers
        is_official = OFFICIAL in final_base_url
        if is_official:
            headers = self.config['default_headers'].copy()
            appid = self._get_priority_value('appid', get, template_config, headers.get('appid', ''))
            if appid:
                headers['appid'] = appid
        else:
            headers = {}

        # 合并自定义 headers（前端传递 + 模板定义，追加不覆盖）
        custom_headers = self._merge_custom_headers(get, template_config)
        headers.update(custom_headers)

        # 获取 sessions_dir
        default_sessions = 'sessions'
        sessions_dir = self._get_priority_value('sessions_dir', get, template_config, default_sessions)
        # 工具选择
        final_tools = self._resolve_enabled_tools(smart_mode, prompt_id, tools, template_config.get('tools', []))

        api_simple_url = self.DEFAULT_CONFIG['api_simple_url'] if main._shared_state['has_quota'] else ''
        # 构造配置
        agent_config = {
            # OpenAI / Chat config
            "is_pro": self.config.get('is_pro', False),
            "app_id": APP_Num,
            "api_key": final_api_key,
            "base_url": final_base_url,
            "model_name": model,
            "small_model_name": '',

            "default_headers": headers,

            # Embedding config (RAG 已禁用, 配置注入保留注释)
            # "embedding_api_key": self.config['embedding'].get('embedding_api_key', ''),
            # "embedding_base_url": self.config['embedding'].get('embedding_base_url', ''),
            # "embedding_model_name": self.config['embedding'].get('embedding_model_name', ''),

            # 前置has_quota, 用于 fast model
            "api_simple_url": api_simple_url,

            # RAG config (RAG 已禁用, 配置注入保留注释)
            # "rag_retrieval_count": self.config['rag'].get('retrieval_count', 10),
            # "rag_final_count": self.config['rag'].get('final_count', 5),

            # Agent config
            "max_tool_iterations": self._get_priority_value(
                'max_tool_iterations', get, template_config, self.config['agent'].get('max_tool_iterations', 10)
            ),
            "tools": final_tools,
            "system_prompt": final_system_prompt,
            "temperature": float(self._get_priority_value(
                'temperature', get, template_config, self.config['agent'].get('temperature', 1.0)
            )),
            "top_p": float(
                self._get_priority_value('top_p', get, template_config, self.config['agent'].get('top_p', 1.0))
            ),
            "thinking": thinking,
            "web_search": web_search,
            # 真实上下文上限 (仅官方), 打0.9余量
            "max_context_tokens": int((model_max_context or self._get_model_max_context_tokens()) * 0.9),

            # Paths
            "sessions_dir": os.path.join(self.data_path, sessions_dir),
            "chat_type": chat_type
        }

        yield self.sse_pack(event="connection_ready", data={"session_id": session_id})
        # 实例化Agent
        try:
            agent = Agent(session_id=session_id, config=agent_config)
        except Exception as e:
            public.print_log(f"[ERROR] Agent init failed: {str(e)}")
            yield self.sse_pack(event="error", data={"msg": lang(f"Agent initialization failed: {str(e)}")})
            return

        # 首次创建会话，异步生成标题
        session_dir = os.path.join(self.data_path, sessions_dir, session_id)
        # 写入会话分类到 meta.json (纯标签, 与记忆/上下文无关, 不走 MemoryManager)
        from mod.project.agent.chat_client.tools.base import atomic_update_json
        try:
            meta_file = os.path.join(session_dir, 'meta.json')
            if os.path.exists(meta_file):
                atomic_update_json(meta_file, lambda d: {**d, 'chat_type': chat_type})
        except Exception as e:
            public.print_log(f"[ERROR] Failed to write chat_type to meta: {str(e)}")
        self._async_generate_title(session_dir, user_input)
        try:
            # 流式
            chunk = None  # 初始化，防止break时未定义
            for chunk in agent.chat(user_input):
                if chunk.get("type") == "content":
                    yield self.sse_pack(event="message", data=chunk.get("response", ""))
                elif chunk.get("type") == "reasoning":
                    yield self.sse_pack(event="message_think", data=chunk.get("response", ""))
                elif chunk.get("type") == "error":
                    # error类型时发送错误并中断对话
                    yield self.sse_pack(event="error", data={"msg": chunk.get("data", "")})
                    break  # 中断循环，不再发送后续内容
                elif chunk.get("type") == "stop":
                    usage = chunk.get("usage", {})
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    total_usage["input_tokens"] += usage.get("input_tokens", 0)
                    total_usage["output_tokens"] += usage.get("output_tokens", 0)
                    yield self.sse_pack(event="usage", data={"usage": usage})
                elif chunk.get("type") == "meta_info":
                    current_user_id = chunk.get("user_msg_id")
                    current_ai_id = chunk.get("ai_msg_id")
                    last_loop_tokens = chunk.get("last_loop_tokens", {})
                    yield self.sse_pack(event="meta_info",
                                        data={
                                            "user_msg_id": current_user_id,
                                            "ai_msg_id": current_ai_id,
                                            "last_loop_tokens": last_loop_tokens
                                        })
                else:
                    yield self.sse_pack(event=chunk.get("type"), data=chunk)

            # 只有正常结束时才发送 message_end
            if chunk.get("type") != "error":
                yield self.sse_pack(event="message_end")

        except Exception as e:
            yield self.sse_pack(event="error", data={"msg": lang(f"Chat error: {str(e)}")})

        finally:
            agent.close()
            log_type = f'{prompt_id}' if prompt_id else 'run_chat'
            self._log_chat_stats(log_type, model, total_usage["total_tokens"], is_official)

    def sse_pack(self, event=None, id=None, data=None, retry=None):
        """
        通用 SSE 打包器
        - event: 事件类型 (message / message_end / error / progress 等)
        - data: 任意 dict，前端直接拿来用
        - id: 可选事件 ID
        """
        lines = []
        if id is not None:
            lines.append(f"id: {id}")
        if event is not None:
            lines.append(f"event: {event}")
        if retry is not None:
            lines.append(f"retry: {retry}")
        if data is not None:
            if isinstance(data, str):
                # 转义换行符，确保SSE格式正确，同时保留换行符给前端
                data = data.replace('\n', '\\n')
                lines.append(f"data: {data}")
            else:
                lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")

        return "\n".join(lines) + "\n\n"

    def chat(self, get):
        """
        聊天接口 (SSE)
        返回 Flask Response 对象包装 SSE 流
        """
        # 文件完整性检查
        check = self._verify_file_integrity()
        if check is not None:
            return check
        return self._sse_response(
            lambda: self._chat_generator(get),
            "Chat error"
        )

    def refresh_runtime_state(self, fetch_remote: bool = True) -> dict:
        """刷新运行时状态, 供 get_config 和长驻进程复用"""
        self._refresh_default_headers()
        self.config['has_quota'] = main._shared_state.get('has_quota', False)
        self.config['is_pro'] = main._shared_state.get('is_pro', False)
        self._pro_auth(None)
        if not fetch_remote:
            return {}
        if self.config.get('api_base_url') != self.DEFAULT_CONFIG['api_base_url']:
            return {}
        return self._fetch_remote_info(refresh_headers=False)

    def get_config(self, get):
        """获取插件配置信息

        功能：
        - 验证资源包和 pro 状态
        - 检查 remote_model_version，不一致时自动刷新 default 账号模型
        - 返回可用模型列表、额度和推荐问题

        """
        data = self.refresh_runtime_state()

        q_type = get.get('type', '')
        if q_type == 'aics':
            questions = [
                {"question": lang("Nginx service cannot start"), "tools": ["RunCommand"]},
                {"question": lang("View server resource usage"), "tools": ["GetSystemResources"]},
                {"question": lang("Query server IP address"), "tools": ["RunCommand"]},
                {"question": lang("What should I do if CPU and disk load are too high?"),
                 "tools": ["GetSystemResources", "GetTopProcesses"]},
                {"question": lang("MySQL service status is abnormal"), "tools": ["RunCommand"]},
                {"question": lang("Run a server health check"), "tools": ["GetSystemResources", "GetTopProcesses"]},
                {"question": lang("Generate a website traffic report"), "tools": ["SiteList", "GetSiteOverview"]},
                {"question": lang("What is aaPanel WAF?"), "tools": []},
                {"question": lang("What features does aaPanel website monitoring have?"), "tools": []},
                {"question": lang("How to enable two-factor authentication in aaPanel?"), "tools": []},
                {"question": lang("What practical security tools are available in aaPanel?"), "tools": []},
                {"question": lang("What free website analysis tools are available in aaPanel?"), "tools": []}
            ]
            questions = random.sample(questions, 5)
        else:
            questions = [
                {"question": lang("Nginx service cannot start"), "tools": ["get_service_status"]},
                {"question": lang("Check Docker running status"),
                 "tools": ["RunCommand"]},
                {"question": lang("View server resource usage"), "tools": ["GetSystemResources"]},
                {"question": lang("Query server IP address"), "tools": ["RunCommand"]},
                {"question": lang("What should I do if CPU and disk load are too high?"),
                 "tools": ["GetSystemResources", "GetTopProcesses"]},
                {"question": lang("Docker MySQL container status is abnormal"),
                 "tools": ["RunCommand"]},
                {"question": lang("Panel MySQL database connection failed"),
                 "tools": ["get_mysql_list", "RunCommand"]},
                {"question": lang("MySQL service status is abnormal"), "tools": ["get_service_status"]},
                {"question": lang("Run a server health check"), "tools": ["GetSystemResources", "GetTopProcesses"]},
                {"question": lang("Generate a website traffic report"), "tools": ["SiteList", "GetSiteOverview"]},
            ]
            questions = random.sample(questions, 9)

        models = self.config.get('models', {})
        # 过滤名字中带 embed 的模型，按 auth、name 倒序排序
        sorted_models = {}
        for key, model_list in models.items():
            filtered = [
                {
                    "name": m.get("name", ""),
                    "auth": m.get("auth", False),
                    "active": m.get("active", False),
                    "usage_cost": m.get("usage_cost", 1),
                    "base_url": m.get("base_url", "") if OFFICIAL not in m.get("base_url", "") else "official"
                } for m in model_list if 'embedding' not in m.get('name', '').lower()
            ]
            sorted_models[key] = sorted(
                filtered, key=lambda m: (-m.get('auth', 0), m.get('name', ''))
            )

        # 构造配置返回
        common_packages = data.get(
            "common_packages", {"remaining_count": 0, "total_count": 0, "used_count": 0, "packages": []}
        )
        packages = common_packages.get("packages", [])
        record = {
            "remaining_count": 0,
            "total_count": 0,
            "used_count": 0,
            "packages": []
        }
        for pkg in packages:
            if pkg.get("remaining_count", 0) > 0:
                record["packages"].append(pkg)
                record["remaining_count"] += pkg.get("remaining_count", 0)
                record["total_count"] += pkg.get("total_count", 0)
                record["used_count"] += pkg.get("used_count", 0)

        common_packages.update(record)
        remaining = data.get("remaining", 0) + common_packages.get("remaining_count", 0)

        configs = {
            "daily_quota": {
                "used": data.get("used", 0) + common_packages.get("used_count", 0),
                "total": data.get("limit", 0) + common_packages.get("total_count", 0),
                "remaining": remaining,
                "common_packages": common_packages,
                "gift": data.get("gift", False),
                "gift_info": data.get("gift_info", {}),
                "service_gift": data.get("service_gift", False),
                "service_gift_info": data.get("service_gift_info", ""),
            },
            "config": {
                "models": sorted_models
            },
            "questions": questions,
        }
        return return_response(True, msg=configs)

    def get_models(self, get):
        """获取可用模型列表并同步模型配置
        """
        base_url = get.get('base_url', '')
        key = get.get('key', '')
        account_name = get.get('account_name', '')
        if not base_url or not key or not account_name:
            return return_response(False, lang('Missing parameters: url, key, or account_name'))

        accouts_keys = self.config.get('models', {}).keys()
        if not get.get("force", False) and account_name in accouts_keys:
            return return_response(False, lang('Account name already exists. Please choose a different name.'))

        if not base_url or not key or not base_url.startswith("http"):
            base_url = self.config.get('api_base_url', '')
            key = self.config.get('api_key', '')
            if OFFICIAL in base_url:
                base_url = self.DEFAULT_CONFIG['api_base_url']

        client = openai.OpenAI(
            api_key=key,
            base_url=base_url,
            default_headers=self.config['default_headers']
        )
        try:
            response = client.models.list()
            model_list = list(response.data)

            # 保留剔除逻辑
            if "aliyun" in public.get_oem_name() and OFFICIAL in base_url:
                for i in range(len(model_list) - 1, -1, -1):
                    model_id = model_list[i].id
                    if model_id.startswith("doubao") or model_id in [
                        "glm-4-7-251222", "deepseek-v3-2-251201", "deepseek-r1-250528", "kimi-k2-thinking-251104"
                    ]:
                        model_list.pop(i)

            models_data = [dict(model) for model in model_list]
            # 同步模型配置到config中
            self._sync_model_config_to_storage(models_data, account_name, base_url, key)
            return return_response(True, lang("Successfully"))
        except Exception as e:
            return return_response(False, str(e))

    def set_config(self, get):
        """配置设置"""
        config_str = get.get('config', '').strip()
        if not config_str:
            return return_response(False, lang('Missing config parameter'))

        try:
            user_config = json.loads(config_str)  # 校验整个config
            temp = user_config['models']  # 校验是否存在models
            for k, v in temp.items():
                if not isinstance(k, str):
                    return return_response(False, f"Config parameter 'models' format error, keys must be strings")
                if not isinstance(v, list):
                    return return_response(False, f"Config parameter 'models.{k}' format error, must be a list")
        except Exception:
            import traceback
            public.print_log(traceback.format_exc())
            return return_response(False, lang('Config parameter format error'))
        # models只允许修改白名单字段
        if 'models' in user_config:
            existing_models = self.config.get('models', {})
            user_config['models'] = self._filter_models_value_by_whitelist(
                user_config['models'], existing_models
            )
        # 过滤掉保护字段 (不允许用户设置动态字段)
        PROTECTED_FIELDS = ['has_quota', 'is_pro']
        for field in PROTECTED_FIELDS:
            if field in user_config:
                del user_config[field]

        # 以默认配置为基准
        new_config = json.loads(json.dumps(self.DEFAULT_CONFIG))
        # 合并配置
        self._merge_config_with_rules(new_config, user_config)
        # 保留保护字段的当前值 (动态字段不受用户设置影响)
        for field in PROTECTED_FIELDS:
            if field in self.config:
                new_config[field] = self.config[field]

        # 更新 self.config
        self.config = new_config
        status, msg = self._save_config()
        if status:
            return return_response(True, lang('Settings Successfully'))
        return return_response(False, msg)

    def get_tool_list(self, get):
        """
        获取所有工具列表、启用状态及显示配置（不包含 Skills internal_tools 工具）
        @param get: 前端请求参数字典
        @param type: 可选，'aics' 表示返回 AICS 专用工具列表
        @return: {
            "status": bool,
            "data": [
                {
                    "id": str,          # 工具ID
                    "name": str,        # 工具名称（中文或ID）
                    "name_cn": str,     # 工具中文名称
                    "category": str,    # 工具分类
                    "risk_level": str,  # 风险等级
                    "description": str, # 工具描述
                    "show": bool,       # 是否在前端菜单显示
                    "enabled": bool     # 是否启用
                }
            ]
        }
        """
        tools = registry.get_all_tools_info()
        internal_tools = [
            x.lower() for x in registry.get_internal_tools() if x and isinstance(x, str)
        ]
        # Skills等inernal隐身
        tools = [
            tool for tool in tools
            if str(tool.get("id", "")).lower() not in internal_tools
        ]
        return return_response(True, tools)

    def set_tool_show_status(self, get):
        """
        设置工具的前端显示状态（支持按 ID 或分类设置）
        @param get: {
            "tool_id": str,  # 选填：工具ID
            "category": str, # 选填：分类名称（如 'agent', 'file'），优先级高于 tool_id
            "show": str      # 选填：'True' 或 'False'，默认为 'True'
        }
        @return: { "status": bool, "msg": str }
        """
        tool_id = get.get('tool_id')
        category = get.get('category')
        show = str(get.get('show', 'True')).lower() == 'true'

        if not tool_id and not category:
            return return_response(False, lang('Parameter error: missing tool_id or category'))

        res = registry.set_tool_show_status(tool_id=tool_id, show=show, category=category)
        if res:
            return return_response(True, lang('Settings saved successfully'))
        return return_response(False, lang('Failed: no matching tool or category found'))

    def get_skill_list(self, get):  # noqa
        """
        获取所有 skills 列表及启用状态
        @return: {
            "status": True,
            "data": {
                "total": int,
                "enabled": int,
                "disabled": int,
                "skills": [
                    {
                        "name": str,
                        "description": str,
                        "enabled": bool,
                        "location": str,
                        "metadata": dict
                    }
                ]
            }
        }
        """
        all_skills = skill_manager.get_all_skills_info()

        # 只返回主技能（一级目录下的 SKILL.md）
        skills = []
        for skill in all_skills:
            location = skill.get("location", "")
            # 计算 SKILL.md 相对于 skills 目录的层级
            rel_path = location.replace(skill_manager.SKILLS_DIR, "").strip("/")
            path_parts = rel_path.split("/")
            # 只保留顶层技能（第一级目录）
            if len(path_parts) == 2 and path_parts[1] == "SKILL.md":
                skills.append(skill)

        enabled_count = len([skill for skill in skills if skill.get("enabled")])
        data = {
            "total": len(skills),
            "enabled": enabled_count,
            "disabled": len(skills) - enabled_count,
            "skills": skills
        }
        return return_response(True, data)

    def set_skill_status(self, get):
        """
        设置单个 skill 的启用状态
        @param get.skill_name: skill 名称
        @param get.enabled: 是否启用 (true/false/1/0)
        @return: return_response
        """
        skill_name = get.get('skill_name', '').strip()
        enabled_raw = str(get.get('enabled', '')).strip().lower()
        if not skill_name:
            return return_response(False, lang('Missing parameter: skill_name'))
        if enabled_raw not in ['true', 'false', '1', '0']:
            return return_response(False, lang('Parameter enabled format error, must be true/false'))
        enabled = enabled_raw in ['true', '1']
        result = skill_manager.set_skill_enabled(skill_name, enabled)
        if not result.get("status"):
            return return_response(False, result.get("msg", lang("Settings failed")))
        return return_response(True, result.get("msg", lang("Settings saved successfully")))

    def set_enabled_skills(self, get):
        """
        批量设置启用的 skills 列表
        @param get.enabled_skills: 启用的 skill 名称列表 (JSON string or list)
        @return: {
            "status": True,
            "data": {
                "enabled_skills": list[str],
                "disabled_skills": list[str],
                "invalid_skills": list[str]
            }
        }
        """
        enabled_skills = get.get('enabled_skills', '[]')
        if isinstance(enabled_skills, str):
            try:
                enabled_skills = ast.literal_eval(enabled_skills)
            except Exception:
                return return_response(False, lang('Parameter enabled_skills format error'))
        if not isinstance(enabled_skills, list):
            return return_response(False, lang('Parameter enabled_skills must be a list'))
        result = skill_manager.set_enabled_skills(enabled_skills)
        if not result.get("status"):
            return return_response(False, result.get("msg", lang("Settings failed")))
        return return_response(True, {
            "enabled_skills": result.get("enabled_skills", []),
            "disabled_skills": result.get("disabled_skills", []),
            "invalid_skills": result.get("invalid_skills", [])
        })

    def import_skills(self, get):
        """
        导入 skills 接口，支持 zip 和 tar.gz 格式
        @param get.file_path: 压缩文件路径
        @return: {
            "status": True,
            "msg": "导入成功"
        }
        """
        file_path = get.get('file_path', '').strip()
        if not file_path:
            return return_response(False, lang('Missing parameter: file_path'))

        if not os.path.exists(file_path):
            return return_response(False, lang('File does not exist'))

        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext == '.gz' and file_path.endswith('.tar.gz'):
            file_ext = '.tar.gz'

        if file_ext not in ['.zip', '.tar.gz']:
            return return_response(False, lang('Only zip and tar.gz formats are supported'))

        skills_dir = skill_manager.SKILLS_DIR
        if not os.path.exists(skills_dir):
            os.makedirs(skills_dir)

        try:
            # 获取压缩包文件名（不含扩展名）作为备用文件夹名
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            if base_name.endswith('.tar'):
                base_name = os.path.splitext(base_name)[0]

            # 检查压缩包内的文件结构
            has_top_level_dir = False
            if file_ext == '.zip':
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    names = zip_ref.namelist()
                    # 检查是否所有文件都在一个顶层文件夹内
                    top_dirs = set()
                    for name in names:
                        if '/' in name:
                            top_dir = name.split('/')[0]
                            if top_dir:
                                top_dirs.add(top_dir)
                        elif name:  # 根目录下的文件
                            top_dirs.add('')
                    has_top_level_dir = len(top_dirs) == 1 and '' not in top_dirs
            elif file_ext == '.tar.gz':
                with tarfile.open(file_path, 'r:gz') as tar_ref:
                    names = tar_ref.getnames()
                    # 检查是否所有文件都在一个顶层文件夹内
                    top_dirs = set()
                    for name in names:
                        if '/' in name:
                            top_dir = name.split('/')[0]
                            if top_dir:
                                top_dirs.add(top_dir)
                        elif name:  # 根目录下的文件
                            top_dirs.add('')
                    has_top_level_dir = len(top_dirs) == 1 and '' not in top_dirs

            # 如果没有顶层文件夹，创建一个以压缩包名命名的文件夹
            if not has_top_level_dir:
                extract_dir = os.path.join(skills_dir, base_name)
                if not os.path.exists(extract_dir):
                    os.makedirs(extract_dir)
            else:
                extract_dir = skills_dir

            # 解压文件
            if file_ext == '.zip':
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
            elif file_ext == '.tar.gz':
                with tarfile.open(file_path, 'r:gz') as tar_ref:
                    tar_ref.extractall(extract_dir)

            # 校验 SKILL.md 是否存在
            if has_top_level_dir:
                for entry in os.listdir(skills_dir):
                    entry_path = os.path.join(skills_dir, entry)
                    if os.path.isdir(entry_path) and entry in [n.split('/')[0] for n in names]:
                        if not os.path.exists(os.path.join(entry_path, 'SKILL.md')):
                            return return_response(False, lang(f'Invalid skill: Missing SKILL.md in {entry}'))
            else:
                skill_md = os.path.join(extract_dir, 'SKILL.md')
                if not os.path.exists(skill_md):
                    return return_response(False, lang('Invalid skill: Missing SKILL.md'))

            return return_response(True, lang('Import successful'))
        except zipfile.BadZipFile:
            return return_response(False, lang('Invalid zip file'))
        except tarfile.TarError:
            return return_response(False, lang('Invalid tar.gz file'))
        except Exception as e:
            return return_response(False, lang(f'Import failed: {str(e)}'))

    def delete_skill(self, get):
        """
        删除 skill 接口，通过 skill name 删除对应的文件夹
        @param get.skill_name: skill 名称（如 "weather"）
        @return: {
            "status": True,
            "msg": "删除成功"
        }
        """
        skill_name = get.get('skill_name', '').strip()
        if not skill_name:
            return return_response(False, lang('Missing parameter: skill_name'))

        # 通过 skill name 查找对应的 skill 对象
        skill = skill_manager.get(skill_name)
        if not skill:
            return return_response(False, lang(f'Skill does not exist: {skill_name}'))

        # 获取 skill 文件夹路径（location 是 SKILL.md 的路径，取其父目录）
        skill_dir = os.path.dirname(skill.location)

        try:
            if os.path.exists(skill_dir):
                shutil.rmtree(skill_dir)
                return return_response(True, lang('Delete successful'))
            else:
                return return_response(False, lang('Skill folder does not exist'))
        except Exception as e:
            return return_response(False, lang(f'Delete failed: {str(e)}'))

    def get_chat_historys(self, get):
        """获取聊天记录列表，支持传入多个 sessions_dir"""
        # 按会话分类过滤, 不传默认 'chat'
        filter_chat_type = get.get('chat_type', 'chat')
        sessions_dir_param = get.get('sessions_dir', '')
        sessions_dirs = self._resolve_history_sessions_dirs(sessions_dir_param)
        # 收集所有会话
        all_sessions = {}
        for dir_name in sessions_dirs:
            sessions_dir = os.path.join(self.data_path, dir_name)
            if not os.path.exists(sessions_dir):
                continue

            try:
                dirs = os.listdir(sessions_dir)
                for session_id in dirs:
                    session_path = os.path.join(sessions_dir, session_id)
                    if not os.path.isdir(session_path):
                        continue

                    session_file = os.path.join(session_path, 'sessions.json')
                    if not os.path.exists(session_file):
                        continue

                    try:
                        mtime = os.path.getmtime(session_file)
                        time_str = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')

                        title = session_id
                        ai_title = ''
                        with open(session_file, 'r', encoding='utf-8') as f:
                            history = json.load(f)
                            if history:
                                for msg in history:
                                    if msg.get('role') == 'user':
                                        content = msg.get('content', '')
                                        if isinstance(content, list):
                                            for item in content:
                                                if isinstance(item, dict) and item.get('type') == 'text':
                                                    content = item.get('text', '')
                                                    break
                                        title = content[:20] + '...' if len(content) > 20 else content
                                        break

                        session_chat_type = 'chat'
                        project = {}
                        meta_file = os.path.join(session_path, 'meta.json')
                        if os.path.exists(meta_file):
                            try:
                                with open(meta_file, 'r', encoding='utf-8') as f:
                                    meta = json.load(f)
                                    ai_title = meta.get('ai_title', '')
                                    session_chat_type = meta.get('chat_type', 'chat')
                                    project = meta.get('project') or {}
                                    ai_title = project.get('subject', ai_title)
                            except:
                                pass

                        # 按分类过滤, 不匹配则跳过
                        if session_chat_type != filter_chat_type:
                            continue

                        # 优先级: project的subject -> AI复写的标题 -> 使用第一句content
                        final_title = ai_title if ai_title else title

                        # 解析平台信息
                        platform = self._get_history_session_platform(session_id)

                        session_data = {
                            "session_id": session_id,
                            "title": final_title,
                            "timestamp": int(mtime),
                            "time_str": time_str,
                            "sessions_dir": dir_name,
                            "platform": platform,
                            "chat_type": session_chat_type,
                            "project": project
                        }

                        if session_id not in all_sessions or mtime > all_sessions[session_id]["timestamp"]:
                            all_sessions[session_id] = session_data
                    except:
                        continue
            except:
                continue

        sessions = sorted(all_sessions.values(), key=lambda x: x["timestamp"], reverse=True)
        return return_response(True, sessions)

    def get_chat(self, get):
        """获取聊天记录"""
        session_id = get.get('session_id')
        if not session_id:
            return return_response(False, lang("Missing parameter: session_id"))

        custom_sessions_dir = get.get('sessions_dir', '')
        sessions_dir = custom_sessions_dir if custom_sessions_dir else 'sessions'
        session_file = os.path.join(self.data_path, sessions_dir, session_id, 'sessions.json')
        # meta_file = os.path.join(self.data_path, sessions_dir, session_id, 'meta.json')

        if not os.path.exists(session_file):
            return return_response(True, [])

        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                history = json.load(f)

            # internal=True: 双侧剥离 assistant.tool_calls + role=tool 结果 (见 _filter_internal_tool_msgs)
            return return_response(True, self._filter_internal_tool_msgs(history))
        except Exception as e:
            return return_response(False, lang(f"Failed to read session records: {str(e)}"))

    def set_session_info(self, get):
        """
        修改会话 meta.json 信息, 仅允许白名单字段
        @param get.session_id: 会话 ID
        @param get.sessions_dir: 会话目录 (默认 sessions)
        @param get.info: JSON 字符串, 如 {"ai_title": "新标题"}
        @return: return_response
        """
        session_id = get.get('session_id', '')
        if not session_id:
            return return_response(False, lang('Missing parameter: session_id'))

        # 白名单
        allowed_fields = {'ai_title'}

        info_raw = get.get('info', '{}')
        if isinstance(info_raw, str):
            try:
                info = json.loads(info_raw)
            except Exception:
                return return_response(False, lang('Parameter info format error, must be JSON'))
        elif isinstance(info_raw, dict):
            info = info_raw
        else:
            return return_response(False, lang('Parameter info format error, must be JSON'))
        if not isinstance(info, dict):
            return return_response(False, lang('Parameter info format error, must be JSON'))

        # 仅应用白名单内字段, 其余静默丢弃
        updates = {k: info[k] for k in info if k in allowed_fields}
        if not updates:
            return return_response(False, lang('No fields to update'))

        sessions_dir = get.get('sessions_dir', 'aics_sessions')
        meta_file = os.path.join(self.data_path, sessions_dir, session_id, 'meta.json')
        if not os.path.exists(meta_file):
            return return_response(False, lang('Session does not exist'))

        from mod.project.agent.chat_client.tools.base import atomic_update_json
        try:
            ok, msg = atomic_update_json(meta_file, lambda d: {**d, **updates})
            if not ok:
                return return_response(False, lang(f'Failed to update session info: {msg}'))
        except Exception as e:
            return return_response(False, lang(f'Failed to update session info: {str(e)}'))

        return return_response(True, lang('Successfully'))

    def del_chat(self, get):
        """删除聊天记录, 连带清理绑定的 project 工作区(memories/projects/<id>/)"""
        session_id = get.get('session_id')
        if not session_id:
            return return_response(False, lang("Missing parameter: session_id"))

        custom_sessions_dir = get.get('sessions_dir', '')
        sessions_dir = custom_sessions_dir if custom_sessions_dir else 'sessions'
        session_dir = os.path.join(self.data_path, sessions_dir, session_id)
        if not os.path.exists(session_dir):
            return return_response(False, lang("Session does not exist"))

        try:
            # 连带清理 project 产物: 读 meta.project.id -> 删 memories/projects/<id>/
            # project 清理失败不阻断会话删除(主意图是删会话)
            from mod.project.agent.dynamic import MEMORIES_DIR
            from mod.project.agent.chat_client.tools.memory import _TOPIC_RE
            meta_file = os.path.join(session_dir, 'meta.json')
            if os.path.isfile(meta_file):
                try:
                    with open(meta_file, 'r', encoding='utf-8') as f:
                        proj_id = (json.load(f).get('project') or {}).get('id', '')
                    if proj_id and _TOPIC_RE.match(proj_id):
                        proj_dir = os.path.join(MEMORIES_DIR, 'projects', proj_id)
                        if os.path.isdir(proj_dir):
                            shutil.rmtree(proj_dir)
                except Exception:
                    pass

            shutil.rmtree(session_dir)
            return return_response(True, lang("Delete successful"))
        except Exception as e:
            return return_response(False, lang(f"Delete failed: {str(e)}"))

    def del_chat_msg(self, get):
        """删除聊天记录中的单个消息"""
        session_id = get.get('session_id')
        message_id = get.get('id')

        if not session_id:
            return return_response(False, lang("Missing parameter: session_id"))
        if not message_id:
            return return_response(False, lang("Missing parameter: id"))

        custom_sessions_dir = get.get('sessions_dir', '')
        sessions_dir = custom_sessions_dir if custom_sessions_dir else 'sessions'
        session_file = os.path.join(self.data_path, sessions_dir, session_id, 'sessions.json')
        if not os.path.exists(session_file):
            return return_response(False, lang("Session records do not exist"))

        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                history = json.load(f)

            original_len = len(history)
            # 过滤掉要删除的消息
            history = [msg for msg in history if msg.get('id') != message_id]

            if len(history) == original_len:
                return return_response(False, lang("Message not found"))

            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=4)

            return return_response(True, lang("Message deleted"))
        except Exception as e:
            return return_response(False, lang(f"Delete failed: {str(e)}"))

    def get_usage_records(self, get):
        """
        查询用户资源包使用记录（分页查询）
        @param get:
            p (int): 页码，默认 1
            limit (int): 每页条数，默认 20
            limit_key (str, optional): 资源包类型筛选，如 "openai_usage"
            start_date (str, optional): 开始日期，格式 "YYYY-MM-DD"
            end_date (str, optional): 结束日期，格式 "YYYY-MM-DD"
        """
        page = int(get.get('p', 1))
        page_size = int(get.get('limit', 20))
        # limit_key = get.get('limit_key', '').strip()
        start_date = get.get('start_date', '').strip()
        end_date = get.get('end_date', '').strip()
        search = get.get('search', '').strip()
        url = self.DEFAULT_CONFIG['api_usage_record_url']
        headers = self.config['default_headers'].copy()
        headers['Content-Type'] = 'application/json'

        payload = {
            "page": page,
            "page_size": page_size,
            "limit_key": "openai_usage"
        }

        # if limit_key:
        #     payload["limit_key"] = limit_key
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date
        if search:
            payload["search"] = search
        try:
            response = requests.post(url, json=payload, headers=headers)
            res = response.json()
            if 'status' not in res or not res['status']:
                return return_response(False, msg=res.get('message', lang("Failed to query usage records")))

            return return_response(True, res.get("data", {}))
        except Exception as e:
            return return_response(False, msg=lang(f"Exception querying usage records: {str(e)}"))

    def export_usage_records(self, get):
        """
        导出用户资源包使用记录
        @param get:
            start_date (str, optional): 开始日期，格式 "YYYY-MM-DD"
            end_date (str, optional): 结束日期，格式 "YYYY-MM-DD"
            limit_key (str, optional): 资源包类型筛选，如 "openai_usage"
        """
        start_date = get.get('start_date', '').strip()
        end_date = get.get('end_date', '').strip()
        # limit_key = get.get('limit_key', '').strip()

        url = self.DEFAULT_CONFIG['api_usage_record_url']
        headers = self.config['default_headers'].copy()
        headers['Content-Type'] = 'application/json'

        payload = {
            "export": True,
            "page": 1,
            "page_size": 999999,
            "limit_key": "openai_usage"
        }

        # if limit_key:
        #     payload["limit_key"] = limit_key
        if start_date:
            payload["start_date"] = start_date
        if end_date:
            payload["end_date"] = end_date

        try:
            response = requests.post(url, json=payload, headers=headers)
            res = response.json()

            if 'status' not in res or not res['status']:
                return return_response(False, msg=res.get('message', lang("Failed to export usage records")))

            data = res.get("data", {})
            items = data.get("items", [])

            if not items:
                return return_response(False, msg=lang("No usage records data available"))

            tmp_logs_path = "/tmp/export_usage_records"
            if not os.path.exists(tmp_logs_path):
                os.makedirs(tmp_logs_path, 0o600)
            tmp_logs_file = "{}/usage_records_{}.csv".format(tmp_logs_path, int(time.time()))

            with open(tmp_logs_file, mode="w+", encoding="utf-8") as fp:
                fp.write(lang("Record ID,Package ID,Deducted Count,"
                              "Remaining Count,Model Name,Usage Scenario,Deduction Time\n"))
                for item in items:
                    create_at = item.get("create_at", "")
                    if create_at and "T" in create_at:
                        create_at = create_at.replace("T", " ").split(".")[0]

                    row = (
                        str(item.get("id", "")),
                        str(item.get("common_limit_id", "")),
                        str(item.get("consumed_count", 0)),
                        str(item.get("remaining_count", 0)),
                        str(item.get("model", "")),
                        str(item.get("scenario", "other")),
                        str(item.get("total_tokens", 0)),
                        create_at,
                    )
                    fp.write(",".join(row) + "\n")

            return return_response(True, {"output_file": tmp_logs_file})
        except Exception as e:
            return return_response(False, msg=lang(f"Exception exporting usage records: {str(e)}"))

    def edit_account(self, get):
        """编辑现有账号配置，刷新模型配置"""
        account_name = get.get('account_name', '')
        base_url = get.get('base_url', '')
        key = get.get('key', '')
        if not account_name or not base_url or not key:
            return return_response(False, lang('Missing parameters: account_name, base_url, or key'))

        # 使用 force 调用 get_models 刷新账号配置
        res = self.get_models(public.to_dict_obj({
            "account_name": account_name,
            "base_url": base_url,
            "key": key,
            "force": True
        }))
        return res

    def del_account(self, get):
        """删除指定账号及其模型配置"""
        account_name = get.get('account_name', '')
        if not account_name:
            return return_response(False, lang('Missing parameter: account_name'))

        models = self.config.get('models', {})
        if account_name not in models:
            return return_response(False, lang(f'Account "{account_name}" not found'))

        del models[account_name]
        self.config['models'] = models
        self._save_config()
        return return_response(True, msg=lang(f'Account "{account_name}" deleted successfully'))

    def get_office_gift_info(self, get):  # noqa
        """限时活动信息"""
        try:
            jwt = public.get_user_info(jwt=True).get('jwt')
            headers = {'Authorization': f'bt {jwt}'}
            url = f"{public.OfficialApiBase()}/api/product/aiCreditActivity"
            gift_info = requests.get(url, headers=headers, timeout=10)
            gift_info = gift_info.json()
            if not gift_info.get("success"):
                return return_response(False, msg=lang(f"Failed to get office gift info"))
            return return_response(True, gift_info.get("res"))
        except Exception as e:
            return return_response(False, msg=lang(f"Failed to get office gift info: {str(e)}"))

    def ai_chat_widget(self, get):  # noqa
        """右下角ai浮窗组件开关"""
        AI_CHAT_WIDGET = os.path.join(APP_DATA_PATH, "ai_chat.pl")
        if not os.path.exists(AI_CHAT_WIDGET):
            public.writeFile(AI_CHAT_WIDGET, "1")
            return return_response(True, lang("Successfully"))
        public.ExecShell(f"rm -f '{AI_CHAT_WIDGET}'")
        return return_response(True, lang("Successfully"))

    def get_site_style(self, get):
        """
        获取 AI 建站设计风格列表 (5 个固定 + 3 个随机)
        @return: {
            "status": True,
            "data": ["风格文件名.md", ...]
        }
        """
        org_style = [
            "Minimal-Tech.md", "AI-Developer.md", "Enterprise.md", "E-Commerce.md", "Brand-Marketing.md"
        ]
        exclude = set(org_style)
        extra = random.sample([s for s in self.awesome_style if s not in exclude], 3)
        return return_response(True, extra + org_style)


class AutoDream:
    """睡梦机制
    整理 memories/(合并/淘汰/提炼).
    全链路 per-topic: 水位线过滤 → 逐 topic LLM → 并发隔离 → 按已知 topic 覆盖.
    """

    _STATE_RELPATH = "_sleep_state.json"  # 相对 MEMORIES_DIR, per-topic mtime 水位线
    # 合法条目(枚举 type + 日期 + 非空 content)
    _ENTRY_RE = re.compile(r"^-\s\[(fact|preference|decision|pitfall|milestone)\s\d{4}-\d{2}-\d{2}\]\s+\S")  # noqa

    def __init__(self):
        self.agent = main()

    def sleep_dream(self, dry_run: bool = False) -> dict:
        """
        睡梦机制
        编排预检查 → 水位线 → 逐 topic LLM → 并发隔离 → 写回 → 更新水位线 → 报告.
        Args:
            dry_run: True=只分析+报告.
        Returns:
            {dry_run, changed, merged, pruned, refined, aborted, skipped, churn_rate, docsave, report}
        """
        from mod.project.agent.dynamic import MEMORIES_DIR
        from mod.project.agent.chat_client.tools.memory import cleanup_pointers

        def dream_print(m):
            """dry_run 预览日志."""
            if dry_run:
                public.print_log("[SLEEP] %s" % m)

        rep = {"dry_run": dry_run, "changed": 0, "merged": 0, "pruned": 0,
               "refined": 0, "aborted": [], "skipped": [], "churn_rate": 0.0,
               "docsave": {}, "report": ""}

        # 节流 + 闲时 + 兜底(仅非 dry_run; dry_run 手动预览不受限)
        # 8h 定时器一天触发 3 次, 这里节流到 24h 实际执行一次, 并在 24-48h 窗口挑闲时(loadavg 低)
        # >48h 强制执行
        if not dry_run:
            age = time.time() - self._sleep_last_run()
            if age < 86400:
                rep["report"] = "[SLEEP] throttled (%.1fh < 24h)" % (age / 3600)
                dream_print("throttled: %.1fh since last run (<24h)" % (age / 3600))
                return rep
            if age < 172800 and not self._is_idle():
                rep["report"] = "[SLEEP] busy slot, skip (age %.1fh)" % (age / 3600)
                dream_print("busy slot, skip (age %.1fh, wait next 8h)" % (age / 3600))
                return rep

        # 0. 预检查(额度/目录)
        pre = self._sleep_preflight()
        if not pre["ok"]:
            rep["report"] = "[SLEEP] preflight fail: %s" % pre["reason"]
            dream_print("preflight fail: %s" % pre["reason"])
            return rep

        # 水位线过滤: 仅变化 topic
        prev = self._sleep_state_load()
        changed = self._read_changed_topics(MEMORIES_DIR, prev)
        rep["changed"] = len(changed)
        if not changed:
            rep["report"] = "[SLEEP] no change"
            dream_print("no change (zero cost)")
            return rep

        # 整理前: 账号 + 待整理 topic
        acct = "official" if OFFICIAL in self.agent.config.get("api_base_url", "") else "custom"
        dream_print("=== before === account=%s changed=%d %s" % (acct, len(changed), [t for t, _, _ in changed]))

        snap = {t: m for t, _, m in changed}  # 本轮快照(并发隔离基准)

        # 整理中: 逐 topic LLM → 并发隔离 → 格式校验 → 覆盖记忆
        dream_print("=== during ===")
        for topic, body, _mt in changed:
            new_body, stats = self._sleep_llm_topic(topic, body)
            if new_body is None:
                rep["skipped"].append(topic)
                dream_print("  %s: SKIP (llm/parse fail)" % topic)
                continue
            # 并发隔离: mtime 变动 = 在线 NoteSave 写入 → 放弃(保新数据)
            if self._current_mtime(MEMORIES_DIR, topic) != snap[topic]:
                rep["aborted"].append(topic)
                dream_print("  %s: ABORT (concurrent NoteSave)" % topic)
                continue
            if not self._validate_body(new_body):
                rep["skipped"].append(topic)
                dream_print("  %s: SKIP (invalid format)" % topic)
                continue
            new_body = self._sort_entries_by_date(new_body)
            dream_print("  %s: merged=%s pruned=%s refined=%s" % (
                topic, stats.get("merged", 0), stats.get("pruned", 0),
                "yes" if stats.get("refined") else "no"))
            dream_print("    --- before ---")
            for _ln in body.splitlines():
                dream_print("    " + _ln)
            dream_print("    --- after ---")
            for _ln in new_body.splitlines():
                dream_print("    " + _ln)
            if not dry_run:
                self._write_topic(MEMORIES_DIR, topic, new_body)
            rep["merged"] += int(stats.get("merged", 0) or 0)
            rep["pruned"] += int(stats.get("pruned", 0) or 0)
            rep["refined"] += 1 if stats.get("refined") else 0

        # 变动率观测(>50% 告警, 不强制整轮)
        rep["churn_rate"] = round(len(rep["aborted"]) / rep["changed"], 2)
        if rep["churn_rate"] > 0.5:
            public.print_log("[SLEEP] high churn %.0f%% — system busy" % (rep["churn_rate"] * 100))

        # DocSave 保守(仅悬空审计, 不 LLM 删); dry_run 跳过(cleanup_pointers 会写文件)
        if not dry_run:
            rep["docsave"] = cleanup_pointers(MEMORIES_DIR)
        else:
            rep["docsave"] = {"report": "[CLEANUP] skipped (dry-run)"}

        # 更新水位线: 重扫全部 mtime(含写回 + cleanup_pointers 改动), 完整保存作下轮基准
        # 只存 changed 会丢未变化 topic → 下轮误判重处理; dry_run 不更新 → 下轮重处理
        if not dry_run:
            self._sleep_state_save(self._scan_topic_mtimes(MEMORIES_DIR))

        # 整理后: 汇总
        dream_print("=== after === changed=%d merged=%d pruned=%d refined=%d aborted=%d skipped=%d churn=%.0f%%" % (
            rep["changed"], rep["merged"], rep["pruned"], rep["refined"],
            len(rep["aborted"]), len(rep["skipped"]), rep["churn_rate"] * 100))

        rep["report"] = "[SLEEP] %s changed=%d merged=%d pruned=%d refined=%d aborted=%d skipped=%d churn=%.0f%%" % (
            "DRY-RUN" if dry_run else "APPLIED", rep["changed"], rep["merged"],
            rep["pruned"], rep["refined"], len(rep["aborted"]), len(rep["skipped"]), rep["churn_rate"] * 100)
        return rep

    def _sleep_preflight(self) -> dict:
        """预检查: 官方→has_quota; 自定义→继续. + 目录非空."""
        from mod.project.agent.dynamic import MEMORIES_DIR
        base_url = self.agent.config.get("api_base_url", "")
        if OFFICIAL in base_url:
            try:
                self.agent.refresh_runtime_state(fetch_remote=True)
            except Exception:
                pass  # 刷新失败用缓存 has_quota
            if not self.agent.config.get("has_quota"):
                return {"ok": False, "reason": "official no quota"}
        if not os.path.isdir(MEMORIES_DIR):
            return {"ok": False, "reason": "memories dir missing"}
        if not any(f.endswith(".md") and os.path.isfile(os.path.join(MEMORIES_DIR, f))
                   for f in os.listdir(MEMORIES_DIR)):
            return {"ok": False, "reason": "no topics"}
        return {"ok": True}

    def _sleep_last_run(self) -> float:
        """读上次真整理(非 dry_run)的 epoch. 无记录返回 0(首次/强制)."""
        from mod.project.agent.dynamic import MEMORIES_DIR
        try:
            with open(os.path.join(MEMORIES_DIR, self._STATE_RELPATH), "r", encoding="utf-8") as f:
                return float(json.load(f).get("last_run", 0) or 0)
        except Exception:
            return 0.0

    @staticmethod
    def _is_idle() -> bool:
        """闲时判断: 1分钟 loadavg < 半核(系统闲 ≈ 用户闲). 读失败默认闲(不阻塞)."""
        try:
            return os.getloadavg()[0] < (os.cpu_count() or 1) * 0.5
        except Exception:
            return True

    def _sleep_llm_topic(self, topic: str, body: str) -> tuple:
        """单 topic 整理. 返回 (new_body, stats) 或 (None, {}). 失败/归错 topic → None."""
        from mod.project.agent.dynamic import _Dynamic_Pompts
        p = _Dynamic_Pompts().get_sleep_prompt()
        notes = "=== topic: %s ===\n%s" % (topic, body)
        raw = self._sleep_llm(p["system"], p["user"].replace("{notes}", notes))
        data = self._parse_json(raw)
        item = data[0] if isinstance(data, list) and data else data  # 数组取首, 对象直用
        if not isinstance(item, dict) or item.get("topic") != topic:  # 单 topic 保险: 归错 → 失败
            return None, {}
        return item.get("new_body", ""), item

    def _sleep_llm(self, system: str, user: str) -> str:
        """chat.completions.create. 失败返回 ''"""
        try:
            model, base_url = self._sleep_select_model()
            client = openai.OpenAI(
                api_key=self.agent.config.get("api_key", "--"),
                base_url=base_url,
                default_headers=self.agent.config.get("default_headers", {}))
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},  # noqa
                          {"role": "user", "content": user}],
                temperature=0.2)
            return resp.choices[0].message.content or ""
        except Exception as e:
            public.print_log("[SLEEP] llm fail: %s" % e)
            return ""

    def _sleep_select_model(self) -> tuple:
        """官方→qwen3.5-plus + api_sleep_model_url; 自定义→含 flash 的 active, 无则首个."""
        base_url = self.agent.config.get("api_base_url", "")
        if OFFICIAL in base_url:
            return "qwen3.5-plus", self.agent.config.get("api_sleep_model_url", base_url)
        active = [
            m for m in self.agent.config.get("models", {}).get("default", []) if m.get("active")
        ]
        flash = next((m["name"] for m in active if "flash" in m.get("name", "")), None)
        return (flash or (active[0]["name"] if active else "qwen3.5-flash")), base_url

    def _sleep_state_load(self) -> dict:
        """读 per-topic mtime 水位线 {topic: mtime}. 无文件返回 {}."""
        from mod.project.agent.dynamic import MEMORIES_DIR
        path = os.path.join(MEMORIES_DIR, self._STATE_RELPATH)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("topics", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _sleep_state_save(self, snap: dict) -> None:
        """原子写 per-topic mtime 水位线."""
        from mod.project.agent.dynamic import MEMORIES_DIR
        path = os.path.join(MEMORIES_DIR, self._STATE_RELPATH)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"topics": snap, "last_run": time.time()}, f)
            os.replace(tmp, path)
        except Exception as e:
            public.print_log("[SLEEP] state save fail: %s" % e)

    @staticmethod
    def _read_changed_topics(memories_dir: str, prev: dict) -> list:
        """扫顶层 *.md, mtime != prev[topic] → 变化. 返回 [(topic, body, mtime)]."""
        out = []
        for f in sorted(os.listdir(memories_dir)):
            if not f.endswith(".md"):
                continue
            p = os.path.join(memories_dir, f)
            if not os.path.isfile(p):
                continue
            topic, mt = f[:-3], os.path.getmtime(p)
            if topic in prev and prev[topic] == mt:
                continue  # 水位线: 自上轮未变 → 跳过(免LLM)
            try:
                body = open(p, "r", encoding="utf-8").read().strip()
            except Exception:
                continue
            if body:
                out.append((topic, body, mt))
        return out

    @staticmethod
    def _scan_topic_mtimes(memories_dir: str) -> dict:
        """扫顶层 *.md → {topic: mtime}(全部, 水位线完整保存用)."""
        out = {}
        for f in sorted(os.listdir(memories_dir)):
            p = os.path.join(memories_dir, f)
            if f.endswith(".md") and os.path.isfile(p):
                out[f[:-3]] = os.path.getmtime(p)
        return out

    @staticmethod
    def _current_mtime(memories_dir: str, topic: str) -> float:
        """当前 mtime, 不存在返回 -1."""
        try:
            return os.path.getmtime(os.path.join(memories_dir, "%s.md" % topic))
        except OSError:
            return -1.0

    @staticmethod
    def _parse_json(raw: str):
        """剥 ```fence, json.loads. 非数组/dict 返回 None."""
        if not raw:
            return None
        s = raw.strip()
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
            s = re.sub(r"\n?```$", "", s)
        try:
            data = json.loads(s)
            return data if isinstance(data, (list, dict)) else None
        except Exception:
            return None

    @classmethod
    def _validate_body(cls, body: str) -> bool:
        """安全格式校验: 首行 `# topic` + 其余非空行全为合法条目(枚举 type + 日期 + 非空 content).
        非法编造 type, 与 prompt INVARIANTS 双拦截.
        """
        if not body or not body.strip():
            return False
        lines = body.strip().splitlines()
        if len(lines) < 2 or not lines[0].startswith("# "):
            return False
        for ln in lines[1:]:
            s = ln.strip()
            if not s:
                continue  # 空行允许
            if not cls._ENTRY_RE.match(s):
                return False
        return True

    @staticmethod
    def _sort_entries_by_date(body: str) -> str:
        """条目按 date 升序(旧→新)兜底防 重排; 同 date 稳定(保持原序)"""
        try:
            lines = body.strip().splitlines()
            if not lines or not lines[0].lstrip().startswith("#"):
                return body
            # 行首 header(防 content 内日期误匹配)
            date_re = re.compile(r"^-\s\[[a-z]+\s(\d{4}-\d{2}-\d{2})\]")  # noqa
            entries = []
            for i, ln in enumerate(lines[1:]):
                m = date_re.match(ln)
                if m:
                    entries.append((m.group(1), i, ln))
            if len(entries) < 2:
                return body
            entries.sort(key=lambda x: (x[0], x[1]))
            return lines[0] + "\n\n" + "\n".join(e[2] for e in entries)
        except Exception:
            return body

    @staticmethod
    def _write_topic(memories_dir: str, topic: str, body: str) -> None:
        """覆盖 topic, 校验 _TOPIC_RE 防注入."""
        if not re.match(r'^[a-z0-9_]+(?:[_-][a-z0-9]+)*$', topic or ""):
            return
        try:
            with open(os.path.join(memories_dir, "%s.md" % topic), "w", encoding="utf-8") as f:
                f.write(body.strip() + "\n")
        except Exception as e:
            public.print_log("[SLEEP] write fail topic=%s: %s" % (topic, e))


if __name__ == '__main__':
    pass
