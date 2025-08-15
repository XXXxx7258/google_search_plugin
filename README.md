\# 麦麦Google 搜索插件



这是一款集成了 Google 搜索和网页正文提取功能的插件。它允许 AI 代理在互联网上查找信息，并直接阅读网页内容以获得更深入的回答。



\## 1. 安装



首先，将插件文件放入您的项目插件目录中，然后安装所需的依赖库：



```bash

pip install googlesearch-python aiohttp beautifulsoup4 readability-lxml trafilatura

```



\## 2. 配置 (可选)



插件开箱即用。如果你需要通过代理访问 Google 或想更改搜索区域，可以创建一个 `config.toml` 文件进行配置。



\*\*示例 `config.toml`:\*\*



```toml

\[search]

\# 如果你所在地区无法直接访问 Google，请设置代理

\# 支持 http 和 socks5, 例如: "http://127.0.0.1:7890" 或 "socks5://127.0.0.1:1080"

proxy = "http://127.0.0.1:7890"



\# 可以修改搜索地区和语言

tld = "com.hk"  # 使用香港地区搜索

lang = "zh-tw"  # 使用繁体中文

```



\## 3. 使用方法



插件提供了两个工具供 AI 调用：



\### 工具一: `web\_search` (网络搜索)



这是最常用的工具。它执行一次网络搜索，并抓取网页内容进行总结。



\*\*参数:\*\*



\*   `query` (字符串): \*\*必需\*\*，你想搜索的关键词或问题。

\*   `with\_content` (布尔值, 可选):

&nbsp;   \*   `true` (默认): 返回搜索结果的同时，抓取每个网页的正文内容。

&nbsp;   \*   `false`: 只返回搜索结果的标题、链接和摘要，不抓取正文。

\*   `max\_results` (整数, 可选): 返回结果的数量，默认为 5。



\*\*使用示例:\*\*



调用 `web\_search` 工具搜索 "Python 的主要特点"。



```json

{

&nbsp; "tool\_name": "web\_search",

&nbsp; "parameters": {

&nbsp;   "query": "Python 的主要特点"

&nbsp; }

}

```



\*\*返回内容:\*\*



```text

1\. Python - 维基百科，自由的百科全书 https://zh.wikipedia.org/wiki/Python

Python是一种广泛使用的高级编程语言...设计哲学强调代码的可读性和简洁的语法...

Python的设计哲学是“优雅”、“明确”、“简单”。它的语法简洁，支持多种编程范式，包括面向对象、命令式、函数式和过程式编程。Python拥有一个庞大而活跃的社区...



2\. Welcome to Python.org https://www.python.org

The official home of the Python Programming Language...

Python is powerful... and fast; plays well with others; runs everywhere; is friendly \& easy to learn; is Open. Whether you're new to programming or an experienced developer, it's easy to learn and use Python...

```



\### 工具二: `fetch\_page` (抓取网页)



当你已经有一个明确的网址，并想读取它的正文内容时，使用此工具。



\*\*参数:\*\*



\*   `url` (字符串): \*\*必需\*\*，要抓取内容的网页地址。



\*\*使用示例:\*\*



```json

{

&nbsp; "tool\_name": "fetch\_page",

&nbsp; "parameters": {

&nbsp;   "url": "https://www.python.org/"

&nbsp; }

}

```



\*\*返回内容:\*\*



```text

Python is powerful... and fast; plays well with others; runs everywhere; is friendly \& easy to learn; is Open. Whether you're new to programming or an experienced developer, it's easy to learn and use Python. Start with our Beginner's Guide...

```

