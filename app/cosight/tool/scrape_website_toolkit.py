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

import asyncio
import os
import re
from typing import Any, Optional, Type
import requests
from bs4 import BeautifulSoup

from app.common.logger_util import logger


class ScrapeWebsiteTool:
    name: str = "Read website content"
    description: str = "A tool that can be used to read a website content."
    website_url: Optional[str] = None
    cookies: Optional[dict] = None

    def __init__(
            self,
            website_url: str,
            cookies: Optional[dict] = None
    ):
        proxy = os.environ.get("PROXY")
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

        if website_url is not None:
            self.website_url = website_url
            self.description = (
                f"A tool that can be used to read {website_url}'s content."
            )
            if cookies is not None:
                self.cookies = {cookies["name"]: os.getenv(cookies["value"])}
        else:
            raise RuntimeError("website_url can not be null")
        self.headers: Optional[dict] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    async def _run(
            self,
            website_url: str,
    ) -> Any:
        page = requests.get(
            website_url,
            timeout=15,
            verify=False,
            headers=self.headers,
            cookies=self.cookies if self.cookies else {},
            proxies=self.proxies
        )

        page.encoding = page.apparent_encoding
        parsed = BeautifulSoup(page.text, "html.parser")

        text = parsed.get_text(" ")
        text = re.sub("[ \t]+", " ", text)
        text = re.sub("\\s+\n\\s+", "\n", text)
        return text


def fetch_website_content(website_url):
    try:
        if not is_valid_url(website_url):
            return f'current url is valid {website_url}'
        scrapeWebsiteTool = ScrapeWebsiteTool(website_url)
        logger.info(f'starting fetch {website_url} Content')
        # 检查是否在事件循环中
        try:
            loop = asyncio.get_running_loop()
            # 如果已经在事件循环中，创建新任务
            task = loop.create_task(scrapeWebsiteTool._run(website_url))
            return loop.run_until_complete(task)
        except RuntimeError:
            # 如果没有事件循环，创建新的
            loop = asyncio.new_event_loop()
            return loop.run_until_complete(scrapeWebsiteTool._run(website_url))
    except Exception as e:
        logger.error(f"fetch_website_content error {str(e)}", exc_info=True)
        # 确保返回的是字符串而不是协程
        return f"fetch_website_content error: {str(e)}"


from urllib.parse import urlparse, urljoin
import requests
import json


def is_valid_url(url: str) -> bool:
    return is_valid_pattern_url(url)


def is_valid_pattern_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def fetch_website_content_with_images(website_url):
    """
    获取网页内容并提取图片信息
    
    Args:
        website_url (str): 要抓取的网站URL
        
    Returns:
        dict: 包含文本内容和图片信息的字典
        {
            "text_content": "网页文本内容",
            "images": [
                {
                    "src": "图片URL",
                    "alt": "图片描述",
                    "title": "图片标题",
                    "width": "宽度",
                    "height": "高度"
                }
            ],
            "background_images": ["背景图片URL列表"],
            "total_images": "图片总数"
        }
    """
    try:
        if not is_valid_url(website_url):
            return {
                "error": f"Invalid URL: {website_url}",
                "text_content": "",
                "images": [],
                "background_images": [],
                "total_images": 0
            }
        
        scrapeWebsiteTool = ScrapeWebsiteTool(website_url)
        logger.info(f'Starting fetch {website_url} content with images')
        
        # 获取网页HTML内容
        page = requests.get(
            website_url,
            timeout=15,
            verify=False,
            headers=scrapeWebsiteTool.headers,
            cookies=scrapeWebsiteTool.cookies if scrapeWebsiteTool.cookies else {},
            proxies=scrapeWebsiteTool.proxies
        )
        
        page.encoding = page.apparent_encoding
        parsed = BeautifulSoup(page.text, "html.parser")
        
        # 获取文本内容（保持原有功能）
        text = parsed.get_text(" ")
        text = re.sub("[ \t]+", " ", text)
        text = re.sub("\\s+\n\\s+", "\n", text)
        
        # 提取图片信息
        images = []
        img_tags = parsed.find_all('img')
        
        for img in img_tags:
            img_info = {
                "src": img.get('src', ''),
                "alt": img.get('alt', ''),
                "title": img.get('title', ''),
                "width": img.get('width', ''),
                "height": img.get('height', ''),
                "class": img.get('class', []),
                "id": img.get('id', '')
            }
            
            # 处理相对URL
            if img_info["src"] and not img_info["src"].startswith(('http://', 'https://', 'data:')):
                img_info["src"] = urljoin(website_url, img_info["src"])
            
            images.append(img_info)
        
        # 提取CSS背景图片
        background_images = []
        style_tags = parsed.find_all('style')
        
        for style in style_tags:
            if style.string:
                # 查找background-image属性
                bg_matches = re.findall(r'background-image:\s*url\(["\']?([^"\']+)["\']?\)', style.string)
                for bg_url in bg_matches:
                    if not bg_url.startswith(('http://', 'https://', 'data:')):
                        bg_url = urljoin(website_url, bg_url)
                    background_images.append(bg_url)
        
        # 查找内联样式的背景图片
        elements_with_bg = parsed.find_all(attrs={"style": re.compile(r"background-image")})
        for element in elements_with_bg:
            style_attr = element.get('style', '')
            bg_matches = re.findall(r'background-image:\s*url\(["\']?([^"\']+)["\']?\)', style_attr)
            for bg_url in bg_matches:
                if not bg_url.startswith(('http://', 'https://', 'data:')):
                    bg_url = urljoin(website_url, bg_url)
                background_images.append(bg_url)
        
        result = {
            "text_content": text,
            "images": images,
            "background_images": list(set(background_images)),  # 去重
            "total_images": len(images) + len(set(background_images)),
            "url": website_url,
            "status": "success"
        }
        
        logger.info(f'Successfully fetched {website_url} with {len(images)} img tags and {len(set(background_images))} background images')
        return result
        
    except Exception as e:
        logger.error(f"fetch_website_content_with_images error {str(e)}", exc_info=True)
        return {
            "error": f"fetch_website_content_with_images error: {str(e)}",
            "text_content": "",
            "images": [],
            "background_images": [],
            "total_images": 0,
            "url": website_url,
            "status": "error"
        }


def fetch_website_images_only(website_url):
    """
    仅获取网页中的图片信息，不返回文本内容
    
    Args:
        website_url (str): 要抓取的网站URL
        
    Returns:
        dict: 仅包含图片信息的字典
    """
    try:
        result = fetch_website_content_with_images(website_url)
        
        # 只返回图片相关信息
        return {
            "images": result.get("images", []),
            "background_images": result.get("background_images", []),
            "total_images": result.get("total_images", 0),
            "url": result.get("url", website_url),
            "status": result.get("status", "error")
        }
        
    except Exception as e:
        logger.error(f"fetch_website_images_only error {str(e)}", exc_info=True)
        return {
            "error": f"fetch_website_images_only error: {str(e)}",
            "images": [],
            "background_images": [],
            "total_images": 0,
            "url": website_url,
            "status": "error"
        }
