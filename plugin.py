import os
import re
import asyncio
import random
from typing import List, Tuple, Type, Dict, Any, Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from src.common.logger import get_logger
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseTool,
    ComponentInfo,
    ConfigField,
    ToolParamType,
)

logger = get_logger("google_search")

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

SERP_HOSTS = {
    "www.baidu.com", "m.baidu.com", "baidu.com",
    "www.bing.com", "cn.bing.com", "m.bing.com",
    "www.google.com", "www.google.com.hk", "www.google.com.sg",
    "duckduckgo.com", "search.yahoo.com",
    "www.sogou.com", "so.com", "www.so.com",
}

def _norm_key(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.netloc.lower()}{p.path.rstrip('/')}"
    except Exception:
        return url
_ZH_PUNCT_RE = re.compile(r"[，。！？；、,.!?;]")
_WS_RE = re.compile(r"\s+")

def _link_density(node) -> float:
    try:
        text = node.get_text(" ", strip=True) or ""
        tlen = len(text)
        if tlen == 0:
            return 0.0
        alen = sum(len(a.get_text(" ", strip=True) or "") for a in node.find_all("a"))
        return alen / tlen
    except Exception:
        return 1.0

def _count_sentences(text: str) -> int:
    return len([s for s in re.split(r"[。！？!?；;]+", text) if s.strip()])

def _text_quality_score(text: str) -> float:
    text = _tidy(text)
    n = len(text)
    if n == 0:
        return 0.0
    punct = len(_ZH_PUNCT_RE.findall(text))
    sentences = _count_sentences(text)
    uniq_ratio = len(set(text)) / max(n, 1)
    punct_density = punct / n
    # 粗略打分：长度、句子数、标点密度与多样性
    score = (
        (n / 500.0) +                 # 长文加分
        (sentences / 5.0) +           # 句子多加分
        (0.5 if 0.005 <= punct_density <= 0.12 else 0.0) +  # 合理标点密度
        (0.5 if 0.1 <= uniq_ratio <= 0.9 else 0.0)          # 多样性
    )
    return score
def _extract_main_heuristic(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")

    # 1) 移除明显非正文/噪声结构（结构性，而非内容关键词）
    for tag in soup([
        "script","style","noscript","template","canvas","svg","iframe","form",
        "header","nav","footer","aside","ins"
    ]):
        tag.decompose()
    for tag in soup.select("[hidden], [aria-hidden=true], [role=navigation], [role=banner], [role=complementary], [role=contentinfo]"):
        tag.decompose()

    body = soup.body or soup
    candidates = []
    # 2) 候选：主语义块优先
    for node in body.find_all(["article","main","section","div"], limit=500):
        text = node.get_text(" ", strip=True) or ""
        if len(text) < 80:
            continue
        ld = _link_density(node)
        p_count = len(node.find_all("p"))
        punct = len(_ZH_PUNCT_RE.findall(text))
        tlen = len(text)

        # 标签偏置
        tag_bias = 0.0
        if node.name in {"article","main"}:
            tag_bias += 2.0
        elif node.name == "section":
            tag_bias += 0.5

        # 轻惩罚列表型
        li_count = len(node.find_all("li"))
        list_penalty = min(li_count / 20.0, 1.0)

        # 打分：文本长度、低链接密度、段落数、中文标点 -> 文章性
        score = (
            (tlen / 1000.0) * (1 - ld) ** 2 +
            (p_count * 0.6) +
            (punct * 0.03) +
            tag_bias -
            list_penalty
        )
        candidates.append((score, node))

    if not candidates:
        # 兜底：全局文本
        return _tidy(body.get_text(" ", strip=True))

    # 3) 选分数最高的块，二次清洗其子节点中的高链接密度块
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]

    for child in list(best.find_all(True, recursive=True)):
        try:
            if _link_density(child) > 0.5:
                child.decompose()
        except Exception:
            continue

    text = best.get_text("\n", strip=True)
    text = _dedupe_and_tidy_lines(text)
    return text
def _passes_quality_gate(text: str, min_chars=200, min_sentences=2, min_score=1.0) -> bool:
    text = _tidy(text)
    if len(text) < min_chars:
        return False
    if _count_sentences(text) < min_sentences:
        return False
    if _text_quality_score(text) < min_score:
        return False
    return True

def _dedupe_and_tidy_lines(text: str) -> str:
    # 行级去重，去掉超短/噪声行
    seen = set()
    out = []
    for raw in text.splitlines():
        line = _WS_RE.sub(" ", (raw or "").strip())
        if not line:
            continue
        if len(line) < 5:
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return "\n".join(out)
def _title_from_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.replace("www.", "")
        last = p.path.strip("/").split("/")[-1]
        hint = last.replace("-", " ").replace("_", " ")
        return f"{host}: {hint}" if hint else host or url
    except Exception:
        return url

def _trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rstrip() + "..."

def _tidy(text: str) -> str:
    return " ".join((text or "").replace("\r", " ").replace("\n", " ").split())

def _as_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"1","true","yes","y","on"}: return True
        if s in {"0","false","no","n","off"}: return False
    return default

def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default

def _is_serp_url(url: str) -> bool:
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        path = p.path.lower()
        if host in SERP_HOSTS:
            return True
        # 典型 SERP 路径
        if any(seg in path for seg in ("/search", "/s", "/results")):
            return True
    except Exception:
        pass
    return False

def _extract_serp_top_links(html: str, host: str) -> List[str]:
    """从常见搜索引擎结果页解析前几个自然结果链接"""
    links: List[str] = []
    soup = BeautifulSoup(html or "", "html.parser")
    host = host.lower()

    def add(a):
        if not a: return
        href = a.get("href") or ""
        if not href or href.startswith("javascript:") or href == "#":
            return
        if href.startswith("/"):  # 相对链接转绝对前缀
            href = f"https://{host}{href}"
        links.append(href)

    try:
        if "baidu.com" in host:
            for a in soup.select("#content_left h3 a, h3.t a, div.result h3 a")[:5]:
                add(a)
        elif "bing.com" in host:
            for a in soup.select("ol#b_results li.b_algo h2 a")[:5]:
                add(a)
        elif "google.com" in host or "google.com.hk" in host or "google.com.sg" in host:
            # 有时不可直接抓，但尽量取 h3 的父链接
            for h3 in soup.select("div#search h3")[:5]:
                a = h3.find_parent("a")
                add(a)
        elif "sogou.com" in host:
            for a in soup.select("div.results h3 a, h3.vr-title a")[:5]:
                add(a)
        elif "so.com" in host:
            for a in soup.select("ul#m-result li h3 a, div.result h3 a")[:5]:
                add(a)
    except Exception:
        pass

    # 去重保持顺序
    seen = set(); uniq = []
    for u in links:
        k = _norm_key(u)
        if k in seen: continue
        seen.add(k); uniq.append(u)
    return uniq

async def _fetch_readable(
    session: aiohttp.ClientSession,
    url: str,
    timeout_sec: int,
    max_chars: int,
    proxy: Optional[str],
    follow_serp: bool = True,
) -> str:
    headers = {
        "User-Agent": random.choice(_UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    try:
        async with session.get(
            url,
            headers=headers,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
            allow_redirects=True,
            max_redirects=5,
        ) as resp:
            html = await resp.text(errors="ignore")
            final_url = str(resp.url)
    except Exception as e:
        logger.debug(f"fetch failed: {url} -> {e}")
        return ""

    # SERP：尝试前 3 条候选，如果都抽不出合格正文则判空
    try:
        if follow_serp and _is_serp_url(final_url):
            host = urlparse(final_url).netloc
            candidates = _extract_serp_top_links(html, host)
            for target in candidates[:3]:
                text = await _fetch_readable(
                    session, target, timeout_sec, max_chars, proxy, follow_serp=False
                )
                if _passes_quality_gate(text, min_chars=180, min_sentences=2, min_score=0.9):
                    return _trim(text, max_chars)
            return ""
    except Exception:
        pass

    extracted = ""
    # 层 1：trafilatura
    try:
        import trafilatura
        t = trafilatura.extract(
            html,
            favor_recall=True,
            include_comments=False,
            include_tables=False,
            include_images=False
        ) or ""
        t = _tidy(t)
        if _passes_quality_gate(t, min_chars=180, min_sentences=2, min_score=0.9):
            extracted = t
    except Exception:
        pass

    # 层 2：readability
    if not extracted:
        try:
            from readability import Document
            doc = Document(html)
            summary = doc.summary(html_partial=True)
            soup = BeautifulSoup(summary, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            t = soup.get_text("\n", strip=True)
            t = _dedupe_and_tidy_lines(t)
            if _passes_quality_gate(t, min_chars=180, min_sentences=2, min_score=0.9):
                extracted = t
        except Exception:
            pass

    # 层 3：结构化启发式
    if not extracted:
        try:
            t = _extract_main_heuristic(html)
            if _passes_quality_gate(t, min_chars=160, min_sentences=2, min_score=0.8):
                extracted = t
        except Exception:
            pass

    extracted = _tidy(extracted)
    if not extracted:
        return ""

    # 最终清洗与截断
    extracted = _dedupe_and_tidy_lines(extracted)
    return _trim(extracted, max_chars)

class WebSearchTool(BaseTool):
    """Web 搜索"""

    name = "web_search"
    description = "进行网络搜索并聚合结果"
    parameters = [
        ("query", ToolParamType.STRING, "搜索关键词或问题", True, None),
        ("with_content", ToolParamType.BOOLEAN, "是否抓取正文内容", False, None),  # 注意：枚举为 None
        ("max_results", ToolParamType.INTEGER, "返回的结果数量", False, None),
        ("show_links", ToolParamType.BOOLEAN, "结果中是否展示链接", False, None),
    ]
    available_for_llm = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # 搜索配置
        self.tld = (self.get_config("search.tld") or "com").strip()
        self.lang = (self.get_config("search.lang") or "zh-cn").strip()
        self.timeout = int(self.get_config("search.timeout") or 10)
        self.default_num_results = int(self.get_config("search.num_results") or 8)
        self.sleep_interval = float(self.get_config("search.sleep_interval") or 0.7)
        self.user_agent = self.get_config("search.user_agent") or _UA_POOL[0]
        self.proxy = (
            self.get_config("search.proxy")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or ""
        )

        # 抓取与输出
        self.content_max_chars = int(self.get_config("output.content_max_chars") or 700)
        self.fetch_timeout = int(self.get_config("output.fetch_timeout") or 6)
        self.fetch_concurrency = int(self.get_config("output.fetch_concurrency") or 3)

        # 轻量重试
        self._max_attempts = 2
        self._retry_delay = 0.7

    async def execute(self, function_args) -> dict[str, str]:
        try:
            query = (function_args.get("query") or "").strip()
            if not query:
                return {"name": self.name, "content": "查询关键词为空"}

            with_content = _as_bool(function_args.get("with_content"), True)
            max_results = _as_int(function_args.get("max_results"), 5)
            show_links = _as_bool(function_args.get("show_links"), True)

            results = await self._google_search_with_retry(query, max_results=max_results)
            if not results:
                return {"name": self.name, "content": f"未找到关于「{query}」的相关信息。"}

            content_map: Dict[str, str] = {}
            if with_content:
                urls = [r["url"] for r in results[:max_results] if r.get("url")]
                proxy = self.proxy or None
                sem = asyncio.Semaphore(self.fetch_concurrency)
                async with aiohttp.ClientSession(trust_env=True) as session:
                    async def task(u: str):
                        async with sem:
                            return u, await _fetch_readable(
                                session, u, self.fetch_timeout, self.content_max_chars, proxy
                            )
                    pairs = await asyncio.gather(*(task(u) for u in urls), return_exceptions=True)
                for p in pairs:
                    if isinstance(p, Exception):
                        continue
                    url, text = p
                    if text:  # <- 只保留通过门控返回的非空正文
                        content_map[url] = text

            content = self._format_llm_output(
                results[:max_results],
                content_map,
                show_links=show_links,
            )
            return {"name": self.name, "content": content}

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Web 搜索执行异常: {e}", exc_info=True)
            return {"name": self.name, "content": f"Web 搜索失败: {str(e)}"}

    async def direct_execute(self, **function_args) -> str:
        result = await self.execute(function_args)
        return result.get("content", "")

    async def _google_search_with_retry(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        last_exc = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._google_search(query, max_results=max_results)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_exc = e
                logger.warning(f"搜索失败({attempt}/{self._max_attempts}): {e}")
                if attempt < self._max_attempts:
                    await asyncio.sleep(self._retry_delay * attempt)
        if last_exc:
            raise last_exc
        return []

    async def _google_search(self, query: str, max_results: int) -> List[Dict[str, Any]]:
        try:
            from googlesearch import search as google_search
        except Exception as e:
            logger.error("请先安装 googlesearch-python：pip install googlesearch-python")
            raise e

        def _blocking_call():
            n = max_results
            base = dict(lang=self.lang, timeout=self.timeout, proxy=self.proxy or None, user_agent=self.user_agent)
            call_variants = [
                dict(advanced=True, num_results=n, sleep_interval=self.sleep_interval, **base),
                dict(advanced=True, num=n, stop=n, pause=self.sleep_interval, **base),
                dict(num_results=n, sleep_interval=self.sleep_interval, **base),
                dict(num=n, stop=n, pause=self.sleep_interval, **base),
                dict(num_results=n),
                dict(num=n, stop=n),
                dict(),
            ]
            enriched = []
            for i, kw in enumerate(call_variants):
                if i < 4 and self.tld:
                    tkw = dict(kw); tkw["tld"] = self.tld
                    enriched.append(tkw)
                enriched.append(kw)
            last_err = None
            for kw in enriched:
                try:
                    return list(google_search(query, **kw))
                except TypeError as te:
                    last_err = te
                    continue
            if last_err:
                raise last_err
            return []

        items = await asyncio.to_thread(_blocking_call)

        results: List[Dict[str, Any]] = []
        seen = set()
        for idx, i in enumerate(items, start=1):
            if isinstance(i, str):
                url = i
                title = _title_from_url(i)
                abstract = ""
            else:
                title = getattr(i, "title", "") or getattr(i, "name", "")
                url = getattr(i, "url", getattr(i, "link", "")) or ""
                abstract = getattr(i, "description", getattr(i, "snippet", "")) or ""
            if not url:
                continue
            key = _norm_key(url)
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "title": title or url,
                "abstract": _trim(abstract, 200),
                "url": url,
                "rank": idx,
            })
            if len(results) >= max_results:
                break
        return results

    def _format_llm_output(
        self,
        results: List[Dict[str, Any]],
        content_map: Dict[str, str],
        show_links: bool = True,
    ) -> str:
        lines: List[str] = []
        for idx, r in enumerate(results, start=1):
            title = r.get("title") or "(无标题)"
            url = r.get("url") or ""
            snippet = r.get("abstract") or ""
            content = content_map.get(url, "")

            header = f"{idx}. {title}"
            if show_links and url:
                header += f" {url}"
            lines.append(header)

            if snippet:
                lines.append(snippet)
            if content:
                lines.append(content)
            lines.append("")  # 空行分隔

        return "\n".join(lines).strip()


class FetchPageTool(BaseTool):
    """抓取网页正文并返回清洗后的纯文本"""

    name = "fetch_page"
    description = "抓取网页正文并返回纯文本"
    parameters = [
        ("url", ToolParamType.STRING, "要抓取的网页 URL", True, None),
    ]
    available_for_llm = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fetch_timeout = int(self.get_config("output.fetch_timeout") or 6)
        self.content_max_chars = int(self.get_config("output.content_max_chars") or 700)
        self.proxy = (
            self.get_config("search.proxy")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or ""
        )

    async def execute(self, function_args) -> dict[str, str]:
        url = (function_args.get("url") or "").strip()
        if not url:
            return {"name": self.name, "content": "URL 为空"}
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                text = await _fetch_readable(
                    session, url, self.fetch_timeout, self.content_max_chars, self.proxy or None
                )
            return {"name": self.name, "content": text or ""}
        except Exception as e:
            logger.warning(f"fetch_page 失败: {e}")
            return {"name": self.name, "content": ""}


@register_plugin
class google_search(BasePlugin):
    """google_search 插件：搜索 + 正文提取 + LLM 聚合输出"""

    plugin_name: str = "google_search"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = [
        "googlesearch-python",
        "aiohttp",
        "beautifulsoup4",
        "readability-lxml", 
        "trafilatura",
    ]
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本信息",
        "search": "搜索设置",
        "output": "正文提取与展示设置",
    }

    config_schema: dict = {
        "plugin": {
            "name": ConfigField(type=str, default="google_search", description="插件名称"),
            "version": ConfigField(type=str, default="1.0.1", description="插件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "search": {
            "tld": ConfigField(type=str, default="com", description="顶级域名，如 com、co.jp、com.hk"),
            "lang": ConfigField(type=str, default="zh-cn", description="语言代码"),
            "timeout": ConfigField(type=int, default=10, description="单次搜索超时（秒）"),
            "num_results": ConfigField(type=int, default=8, description="默认返回结果数量"),
            "sleep_interval": ConfigField(type=float, default=0.7, description="请求间隔，适当增大可降低风控"),
            "proxy": ConfigField(type=str, default="", description="可选代理，如 socks5://127.0.0.1:7890 或 http://127.0.0.1:7890"),
            "user_agent": ConfigField(type=str, default="", description="自定义 User-Agent，留空使用默认"),
        },
        "output": {
            "content_max_chars": ConfigField(type=int, default=700, description="每条正文摘要最大长度"),
            "fetch_timeout": ConfigField(type=int, default=6, description="单页抓取超时（秒）"),
            "fetch_concurrency": ConfigField(type=int, default=3, description="并发抓取上限"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (WebSearchTool.get_tool_info(), WebSearchTool),
            (FetchPageTool.get_tool_info(), FetchPageTool),
        ]