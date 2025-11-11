# Search 插件(qq:3103908461)

这是一个搜索插件，还有缩写翻译，还有图片搜索

（**使用此插件需要在bot_config中开启工具调用**）

（**搜图功能无法直连**）

（tavily搜索引擎可以前往https://app.tavily.com 注册后获得密钥）

<img width="735" height="308" alt="image" src="https://github.com/user-attachments/assets/9bc86124-b3a8-43e0-addb-1884133658c2" />

## 📦 依赖安装

为了确保插件正常工作，您需要安装Python依赖。**在你的麦麦的运行环境**中于**本插件**的根目录下执行以下命令即可：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

```
如果是uv安装，在pip前面加上uv即可，如uv pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

注意：**一键包**用户在“点我启动！！！.bat”后选择"11. 交互式安装pip模块",在其中输入requirements.txt的路径即可！（如："E:\Downloads\MaiBotOneKey\modules\MaiBot\plugins\google_search_plugin\requirements.txt"）

## 工作流程

1.  **接收问题**: 插件接收到用户的原始问题。
2.  **查询重写**: 插件内部的LLM结合聊天上下文，将原始问题重写为一个或多个精确的搜索关键词。
3.  **后端搜索**: 使用重写后的关键词，调用Google、Bing等搜索引擎执行搜索。
4.  **内容抓取**: (可选) 抓取搜索结果网页的主要内容。
5.  **阅读总结**: 内部LLM阅读所有搜索到的材料。
6.  **生成答案**: LLM根据阅读的材料，生成最终的总结性答案并返回。

## 🔧 配置说明

插件的配置在 `plugins/google_search_plugin/config.toml` 文件中(在第一次启动后会自动生成)。

此插件默认使用系统配置的主模型进行智能搜索，但你也可以通过以下配置项进行微调。

### `[model_config]`
- `model_name` (str): 指定一个在系统配置中存在的模型名称，用于本次搜索。默认为 "replyer"，即系统主回复模型。如果指定的模型不存在，会自动回退到主回复模型。
- `temperature` (float): 单独设置本次搜索时模型的温度。默认为 0.7。
- `context_time_gap` (int): 获取最近多少秒的**全局**聊天记录作为上下文。默认 300。
- `context_max_limit` (int): 最多获取多少条**全局**聊天记录作为上下文。默认 15。

### `[search_backend]`
这里配置供模型调用的“后端”搜索引擎的行为。

- `default_engine` (str): 默认使用的搜索引擎 (`google`, `bing`, `sogou`, `duckduckgo`, `tavily`)。
- `max_results` (int): 每次搜索返回给模型阅读的结果数量。
- `timeout` (int): 后端搜索引擎的超时时间。
- `proxy` (str): 用于后端搜索的HTTP/HTTPS代理地址，例如 'http://127.0.0.1:7890'。默认为空字符串，表示不使用代理。
- `fetch_content` (bool): 是否抓取网页正文供模型阅读。
- `content_timeout` (int): 网页抓取的超时时间。
- `max_content_length` (int): 抓取的单个网页最大内容长度。

### `[engines]`
对每个具体搜索引擎的可选配置项：

- `google.enabled` (bool): 是否启用 Google。
- `bing.enabled` (bool): 是否启用 Bing。
- `sogou.enabled` (bool): 是否启用搜狗。
- `duckduckgo.enabled` (bool): 是否启用 DuckDuckGo。
- `tavily.enabled` (bool): 是否启用 Tavily（需要提供 API key）。
- `tavily.api_keys` (list[str]): Tavily API key 列表，填写多个时会随机选取一个使用。
- `tavily.api_key` (str): 单个 Tavily API key，可作为后备值；也可以通过环境变量 `TAVILY_API_KEY` 读取。
- `tavily.include_answer` (bool): 是否直接使用 Tavily 返回的汇总答案（默认开启，不再额外抓取网页）。
- `tavily.include_raw_content` (bool): 是否让 Tavily 返回网页正文片段供总结使用。
- `tavily.search_depth` (str): 搜索深度，可选 `basic` 或 `advanced`。
- `tavily.topic` (str): Tavily 的主题参数，例如 `general` 或 `news`。
- `tavily.turbo` (bool): 是否开启 Tavily Turbo 模式。
- ... 其他特定引擎的参数。

## 使用说明

当你向麦麦提出需要外部知识或最新信息的问题时，它会自动被触发。

### 场景

你可以像和朋友聊天一样，直接提出你的问题。

**例如：**
> "能搜一下最近很火的《Ave Mujica》吗？"
> > "我是爱厨，找一张千早爱音图片给我~"
<img src="0d116086-0df6-4694-97d3-28d521184223.png" alt="千早爱音示例" width="400">


麦麦会自动调用本插件，搜索相关信息，并给你一个总结好的答案。

### 总结
你只需要自然地与麦麦对话，当她认为需要“上网查一下”的时候，这个插件就会被激活


---



















