# Copyright 2025 ZTE Corporation.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
import json
import time
from json import JSONDecodeError

from typing import List, Dict, Any

from openai import OpenAI
from openai.types.chat import ChatCompletion

from app.agent_dispatcher.infrastructure.entity.exception.ZaeFrameworkException import ZaeFrameworkException
from app.cosight.task.time_record_util import time_record
from app.common.logger_util import logger


class ChatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, client: OpenAI, max_tokens: int = 4096,
                 temperature: float = 0.0, stream: bool = False, tools: List[Any] = None):
        self.tools = tools or []
        self.client = client
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.stream = stream
        self.temperature = temperature
        self.max_tokens = max_tokens

    @staticmethod
    def clean_none_values(data):
        """
        递归遍历数据结构，将所有 None 替换为 ""
        静态方法，无需实例化类即可调用
        """
        if isinstance(data, dict):
            return {k: ChatLLM.clean_none_values(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [ChatLLM.clean_none_values(item) for item in data]
        elif data is None:
            return ""
        else:
            return data

    @time_record
    def create_with_tools(self, messages: List[Dict[str, Any]], tools: List[Dict]):
        """
        Create a chat completion with support for function/tool calls
        """
        # 清洗提示词，去除None
        messages = ChatLLM.clean_none_values(messages)
        max_retries = 5
        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=self.temperature
                )
                logger.info(f"LLM with tools chat completions response is {response}")
                if hasattr(response, 'choices') and response.choices and len(response.choices) > 0:
                    self.check_and_fix_tool_call_params(response)
                elif hasattr(response, 'message') and response.message:
                    raise Exception(response.message)
                else:
                    raise Exception(response)
                break
            except json.JSONDecodeError as json_error:
                logger.error(f"JSON decode error on attempt {attempt + 1}: {json_error}")
                
                # 更详细的错误信息记录
                response_info = "No response object"
                if response is not None:
                    if hasattr(response, 'content'):
                        response_info = f"Response content: {response.content}"
                    elif hasattr(response, 'text'):
                        response_info = f"Response text: {response.text}"
                    elif hasattr(response, 'choices') and response.choices:
                        try:
                            content = response.choices[0].message.content if response.choices[0].message else "No message content"
                            response_info = f"Response message content: {content[:500]}..." if len(str(content)) > 500 else f"Response message content: {content}"
                        except Exception:
                            response_info = f"Response object: {type(response)} - {str(response)[:500]}..."
                    else:
                        response_info = f"Response object: {type(response)} - {str(response)[:500]}..."
                
                logger.error(f"Response details: {response_info}")
                
                if attempt == max_retries - 1:
                    raise ZaeFrameworkException(400, f"JSON decode error after {max_retries} attempts: {json_error}")
                time.sleep(5)  # 增加等待时间
            except Exception as e:
                logger.warning(f"chat with LLM error: {e} on attempt {attempt + 1}, retrying...", exc_info=True)
                if "TPM limit reached" in str(e):
                    time.sleep(60)
                elif "rate limit" in str(e).lower():
                    time.sleep(30)
                elif "timeout" in str(e).lower():
                    time.sleep(10)
                if attempt == max_retries-1:
                    logger.error(f"Failed to create after {max_retries} attempts.")
                    raise ZaeFrameworkException(400, f"chat with LLM failed, please check LLM config. reason：{e}")
                time.sleep(3)  # 增加等待时间，避免频繁重试

        if response and isinstance(response, ChatCompletion):
            # 去除think标签
            content = response.choices[0].message.content
            if content is not None and '</think>' in content:
                response.choices[0].message.content = content.split('</think>')[-1].strip('\n')
            return response.choices[0].message
        else:
            raise ZaeFrameworkException(400, f"chat with LLM failed, LLM response：{response}")

    def check_and_fix_tool_call_params(self, response):
        if response.choices[0].message.tool_calls:
            for attempt in range(3):
                try:
                    tool_call = response.choices[0].message.tool_calls[0].function
                    json.loads(tool_call.arguments)
                    break
                except JSONDecodeError as jsone:
                    logger.warning(f"Tool call arguments JSON decode error on attempt {attempt + 1}: {jsone}")
                    logger.warning(f"Invalid arguments: {tool_call.arguments}")
                    
                    try:
                        # 尝试修复JSON格式
                        fixed_arguments = self.chat_to_llm([{"role": "user",
                                                           "content": f"下面的json字符串格式有错误，请帮忙修正。重要：仅输出修正的字符串。\n{tool_call.arguments}"}])
                        # 验证修复后的JSON是否有效
                        json.loads(fixed_arguments)
                        tool_call.arguments = fixed_arguments
                        logger.info(f"Successfully fixed tool call arguments on attempt {attempt + 1}")
                        break
                    except Exception as fix_error:
                        logger.error(f"Failed to fix tool call arguments on attempt {attempt + 1}: {fix_error}")
                        if attempt == 2:  # 最后一次尝试
                            # 如果修复失败，使用默认的空JSON对象
                            tool_call.arguments = "{}"
                            logger.warning("Using empty JSON object as fallback for tool call arguments")
                            break

    @time_record
    def chat_to_llm(self, messages: List[Dict[str, Any]]):
        # 清洗提示词，去除None
        messages = ChatLLM.clean_none_values(messages)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature
        )
        logger.info(f"LLM chat completions response is {response}")
        # 去除think标签
        content = response.choices[0].message.content
        if content is not None and '</think>' in content:
            response.choices[0].message.content = content.split('</think>')[-1].strip('\n')

        return response.choices[0].message.content
