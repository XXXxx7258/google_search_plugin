"""
知乎内容抓取器
支持处理知乎文章、问题、回答的抓取
"""
import os
import re
import json
import subprocess
import datetime
from typing import Tuple, Dict, Optional, Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

class ZhihuArticleFetcher:
    """通用知乎内容抓取器，支持文章、问题、回答"""
    
    cookie_string: str
    httpx_client: httpx.AsyncClient
    js_path: str
    headers: Dict[str, str]

    def __init__(self, cookie_string: str) -> None:
        self.cookie_string = cookie_string
        self.httpx_client = httpx.AsyncClient(timeout=20, http2=True)
        # 更新js_path以反映新的文件位置
        self.js_path = os.path.join(os.path.dirname(__file__), 'zhihu.js')
        self.headers: Dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "x-api-version": "3.0.91",
            "x-app-za": "OS=Web",
            "x-requested-with": "fetch",
            "x-zse-93": "101_3_3.0",
        }

    async def close(self) -> None:
        """关闭httpx客户端"""
        await self.httpx_client.aclose()

    def _get_sign_from_node(self, url: str) -> Dict[str, str]:
        """通过原生Node.js环境执行JS获取签名
        
        Args:
            url: 需要签名的URL路径
            
        Returns:
            包含签名信息的字典
        """
        command = ["node", self.js_path, url, self.cookie_string]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=True, encoding='utf-8'
            )
            return json.loads(result.stdout)
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                print("❌ 错误: 'node'命令未找到。请确保Node.js已安装并配置在系统的PATH中。")
            else:
                print(f"❌ 调用Node.js签名失败: {e}")
            raise

    async def _request_with_retry(self, url: str) -> httpx.Response:
        """为API请求封装Cookie挑战重试逻辑
        
        Args:
            url: 请求URL
            
        Returns:
            HTTP响应对象
        """
        for _ in range(3):
            path_for_sign = urlparse(url).path
            if urlparse(url).query:
                path_for_sign += "?" + urlparse(url).query
            
            sign_data = self._get_sign_from_node(path_for_sign)
            
            current_headers = self.headers.copy()
            current_headers['Cookie'] = self.cookie_string
            current_headers.update({
                'x-zst-81': sign_data['x-zst-81'],
                'x-zse-96': sign_data['x-zse-96'],
            })

            response = await self.httpx_client.get(url, headers=current_headers)

            if response.status_code != 403 or 'zh-zse-ck' not in response.text:
                return response

            soup = BeautifulSoup(response.text, 'lxml')
            new_ck_tag = soup.find('meta', id='zh-zse-ck')

            if isinstance(new_ck_tag, Tag) and new_ck_tag.has_attr('content'):
                new_ck_value = new_ck_tag['content']
                self.cookie_string += f"; zh-zse-ck={new_ck_value}"
            else:
                return response
        return response

    async def fetch_article(self, article_id: str) -> Tuple[bool, str]:
        """通过httpx+Node.js签名获取文章内容
        
        Args:
            article_id: 知乎文章ID
            
        Returns:
            (是否成功, 文章内容或错误信息) 的元组
        """
        api_url = f"https://www.zhihu.com/api/v4/articles/{article_id}?include=content"
        response = await self._request_with_retry(api_url)

        if response.status_code != 200:
            return False, f"API请求失败，最终状态码: {response.status_code}, 响应: {response.text[:200]}"

        data = response.json()
        title = data.get('title', '未知标题')
        content_html = data.get('content', '')

        soup = BeautifulSoup(content_html, 'lxml')
        content_text = soup.get_text('\n', strip=True)

        result = f"标题: {title}\n\n{content_text}"
        return True, result

    async def fetch_question(self, question_id: str) -> Tuple[bool, str]:
        """抓取知乎问题内容

        Args:
            question_id: 知乎问题ID

        Returns:
            (是否成功, 问题内容或错误信息) 的元组
        """
        api_url = f"https://www.zhihu.com/api/v4/questions/{question_id}?include=detail,excerpt,author,answer_count,follower_count,created"
        response = await self._request_with_retry(api_url)

        if response.status_code != 200:
            return False, f"API请求失败，状态码: {response.status_code}, 响应: {response.text[:200]}"

        try:
            data = response.json()
            title = data.get('title', '未知问题')
            detail = data.get('detail', data.get('excerpt', ''))
            author_info = data.get('author', {})
            author_name = author_info.get('name', '未知提问者')
            answer_count = data.get('answer_count', 0)
            follower_count = data.get('follower_count', 0)
            created = data.get('created', 0)

            # 解析HTML内容
            if detail:
                soup = BeautifulSoup(detail, 'lxml')
                detail_text = soup.get_text('\n', strip=True)
            else:
                detail_text = "无详细描述"

            created_time = datetime.datetime.fromtimestamp(created).strftime('%Y-%m-%d %H:%M:%S') if created else '未知'

            result = f"问题: {title}\n提问者: {author_name}\n提问时间: {created_time}\n回答数: {answer_count}\n关注数: {follower_count}\n\n问题描述:\n{detail_text}"
            return True, result
        except Exception as e:
            return False, f"解析问题数据失败: {e}"

    async def fetch_answer(self, answer_id: str) -> Tuple[bool, str]:
        """抓取知乎回答内容

        Args:
            answer_id: 知乎回答ID

        Returns:
            (是否成功, 回答内容或错误信息) 的元组
        """
        api_url = f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=content,excerpt,author,question,voteup_count,comment_count,created"
        response = await self._request_with_retry(api_url)

        if response.status_code != 200:
            return False, f"API请求失败，状态码: {response.status_code}, 响应: {response.text[:200]}"

        try:
            data = response.json()
            content = data.get('content', data.get('excerpt', ''))
            author_info = data.get('author', {})
            author_name = author_info.get('name', '未知回答者')
            author_headline = author_info.get('headline', '')
            question = data.get('question', {})
            question_title = question.get('title', '未知问题')
            voteup_count = data.get('voteup_count', 0)
            comment_count = data.get('comment_count', 0)
            created = data.get('created', 0)

            # 解析HTML内容
            soup = BeautifulSoup(content, 'lxml')
            content_text = soup.get_text('\n', strip=True)

            created_time = datetime.datetime.fromtimestamp(created).strftime('%Y-%m-%d %H:%M:%S') if created else '未知'

            result = f"问题: {question_title}\n回答者: {author_name}"
            if author_headline:
                result += f"\n回答者简介: {author_headline}"
            result += f"\n回答时间: {created_time}\n点赞数: {voteup_count}\n评论数: {comment_count}\n\n回答内容:\n{content_text}"
            return True, result
        except Exception as e:
            return False, f"解析回答数据失败: {e}"
