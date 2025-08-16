# Google Search 插件

一个网络搜索和内容提取插件，支持 Google 搜索和网页内容抓取。

## 安装

### 1. 安装依赖

```bash
pip install googlesearch-python aiohttp beautifulsoup4 lxml readability-lxml trafilatura charset-normalizer
```

如需 SOCKS 代理支持（可选）：
```bash
pip install aiohttp-socks
```

### 2. 启用插件

确保插件已在系统中注册并启用。

## 功能说明

### 1. 网络搜索 (web_search)

搜索网络并返回结果，支持自动抓取网页正文内容。

**参数：**
- `query`: 搜索关键词（必填）
- `with_content`: 是否抓取正文内容（默认：true）
- `max_results`: 返回结果数量（默认：8）
- `show_links`: 是否显示链接（默认：true）

**使用示例：**
```python
# 基础搜索
result = await web_search(query="Python教程")

# 只要标题和链接，不抓取内容
result = await web_search(query="天气预报", with_content=False)

# 获取更多结果
result = await web_search(query="机器学习", max_results=10)
```

### 2. 网页抓取 (fetch_page)

抓取指定网页的正文内容。

**参数：**
- `url`: 网页地址（必填）

**使用示例：**
```python
content = await fetch_page(url="https://example.com/article")
```

## 配置文件

在 `config.toml` 中配置：

```toml
[search]
# Google 搜索设置
tld = "com"              # 顶级域名 (com/co.jp/com.hk等)
lang = "zh-cn"           # 语言
num_results = 8          # 默认结果数
timeout = 10             # 搜索超时(秒)
sleep_interval = 0.7     # 请求间隔(秒)
proxy = ""               # 代理设置，如 "socks5://127.0.0.1:7890"
user_agent = ""          # 自定义UA，留空使用默认

[output]
# 内容抓取设置
content_max_chars = 700  # 正文最大长度
fetch_timeout = 6        # 抓取超时(秒)
fetch_concurrency = 3    # 并发数
```

## 代理设置

支持多种代理配置方式：

1. **配置文件设置**
```toml
[search]
proxy = "http://127.0.0.1:7890"
# 或 SOCKS 代理
proxy = "socks5://127.0.0.1:7890"
```

2. **环境变量设置**
```bash
export HTTPS_PROXY=http://127.0.0.1:7890
# 或
export ALL_PROXY=socks5://127.0.0.1:7890
```

## 常见问题

### 1. 搜索无结果
- 检查网络连接
- 尝试设置代理
- 适当增加 `sleep_interval` 避免触发反爬

### 2. 内容抓取失败
- 某些网站可能有反爬保护
- 可以尝试调整 `fetch_timeout`
- 检查目标网站是否需要登录

### 3. 中文乱码
- 插件会自动检测编码
- 如仍有问题，确保安装了 `charset-normalizer`

## 注意事项

- 请遵守网站的 robots.txt 和使用条款
- 避免过于频繁的请求，建议保持默认的 `sleep_interval`
- 部分地区可能需要代理才能访问 Google

## 示例

```python
# 搜索最新新闻
news = await web_search(query="今日新闻", max_results=5)

# 搜索技术文档（不需要内容）
docs = await web_search(query="pandas DataFrame教程", with_content=False)

# 抓取特定文章
article = await fetch_page(url="https://example.com/blog/post")
```
