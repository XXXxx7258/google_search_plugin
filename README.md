# Google Search Plugin

一个支持多个搜索引擎的网络搜索插件，具有自动降级功能和丰富的配置选项。

## 功能特点

- 支持多个搜索引擎：Google、Bing、Sogou
- 自动降级机制：当某个搜索引擎不可用时自动切换
- 简单易用的接口
- 丰富的配置选项
- 支持重试机制
- 可自定义 User-Agent

## 使用方法

### 基本搜索

```python
# 执行搜索
results = await web_search.execute({
    "query": "搜索关键词",
    "with_content": True,    # 是否抓取内容（可选）
    "max_results": 5         # 返回结果数量（可选）
})
```

### 搜索引擎降级策略

插件会根据配置的默认搜索引擎顺序尝试，如果某个搜索引擎失败或被禁用，会自动尝试下一个：

1. **默认引擎**（可配置：google/bing/sogou）
2. **备用引擎1**
3. **备用引擎2**

## 配置说明

插件配置文件位于 `plugins/google_search_plugin/config.toml`，包含以下配置项：

### 基础配置

```toml
[search]
# 默认搜索引擎 (google/bing/sogou)
default_engine = "google"
# 默认返回结果数量
max_results = 5
# 搜索超时时间（秒）
timeout = 30
# 失败重试次数
retry_count = 2
# 重试延迟（秒）
retry_delay = 1.0
```

### 搜索引擎配置

```toml
[engines.google]
# 是否启用Google搜索
enabled = true
# Google搜索间隔（秒），避免429错误
pause_time = 5.0
# 搜索语言
language = "zh-cn"
# 搜索国家/地区
country = "cn"

[engines.bing]
# 是否启用Bing搜索
enabled = true
# Bing市场区域
market = "zh-CN"
# 搜索语言
language = "zh-CN"

[engines.sogou]
# 是否启用搜狗搜索
enabled = true
# 搜索类型 (web/news)
type = "web"
```

### 高级配置

```toml
[advanced]
# User-Agent列表
user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
]
# 是否抓取网页内容
fetch_content = true
# 内容抓取超时（秒）
content_timeout = 10
# 最大内容长度
max_content_length = 5000
```

## 依赖安装

```bash
pip install googlesearch-python aiohttp beautifulsoup4 lxml readability-lxml trafilatura charset-normalizer aiohttp-socks
```

或者使用 requirements.txt 安装所有依赖：

```bash
pip install -r requirements.txt
```

## 注意事项

- Google 搜索有频率限制，建议将 `pause_time` 设置为 5 秒或更长
- 可以通过禁用某些搜索引擎来加快搜索速度
- 如果某个搜索引擎经常失败，可以通过配置文件禁用它
- 搜索结果会自动格式化，包含标题、链接和摘要
- 插件会自动处理搜索失败的情况，并尝试备用搜索引擎
