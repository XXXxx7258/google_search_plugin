# Search 插件(qq:3103908461)

这是一个搜索插件，还有缩写翻译，还有图片搜索

> **v4.0.0 升级提示**：配置文件结构有调整，v3.x 用户请按本 README **🆙 v3.x → v4 升级**一节修改 `config.toml` 后再启用。

## 已更新[tavily](https://app.tavily.com)以及[You](https://you.com/platform)搜索引擎，很好用:)
tavily搜索引擎可以前往[官网](https://app.tavily.com)注册后获得密钥

You搜索引擎需要在[官网](https://you.com/platform) 获取 API Key；Live News / Images 为 early access，需账号权限）
## 以上二者均可以使用作者自建的免费注册临时[邮箱1](https://xiaowan258.me)或[邮箱2](https://mail.xiaowan.me)注册

<img width="735" height="308" alt="image" src="https://github.com/user-attachments/assets/9bc86124-b3a8-43e0-addb-1884133658c2" />

## 📦 依赖安装

为了确保插件正常工作，您需要安装Python依赖。**在你的麦麦的运行环境**中于**本插件**的根目录下执行以下命令即可：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

```
如果是uv安装，在pip前面加上uv即可，如uv pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple

注意：**一键包**用户在“点我启动！！！.bat”后选择"11. 交互式安装pip模块",在其中输入requirements.txt的路径即可！（如："E:\Downloads\MaiBotOneKey\modules\MaiBot\plugins\google_search_plugin\requirements.txt"）

## 🆙 v3.x → v4 升级

如果你是从 v3.x 升级到 v4.0.0，请手动调整你的 `config.toml`（配置文件 gitignored，升级时不会被覆盖），主要有三处变化：

1. **section 重命名**：`[model_config]` → `[models]`（其中字段名不变，只是 section 名变更）
2. **新增 section**：`[translation]`（缩写翻译相关参数，不写也行，会用默认值）
3. **删除 section**：`[storage]` 整段可以删除（搜索结果现在由麦麦内部统一记录到 `tool_records`，不再由插件自己写库）
4. **`[plugin]` 段新增 `config_version = "4.0.0"`**（必填，麦麦校验配置版本时会用）

升级最快的做法：直接删掉旧 `config.toml`，让插件首次启动时自动重新生成 v4 默认配置；再把你原来的 API key / 代理 等设置项搬过去即可。

## 工作流程

1.  **接收问题**: 插件接收到用户的原始问题。
2.  **查询重写**: 插件内部的LLM结合聊天上下文，将原始问题重写为一个或多个精确的搜索关键词。
3.  **后端搜索**: 使用重写后的关键词，调用Google、Bing、Tavily 等搜索引擎执行搜索（多引擎自动降级）。
4.  **内容抓取**: (可选) 抓取搜索结果网页的主要内容（trafilatura/readability/bs4 三级降级；知乎链接走专用抓取）。
5.  **阅读总结**: 内部LLM阅读所有搜索到的材料。
6.  **生成答案**: LLM根据阅读的材料，生成最终的总结性答案并返回。

## 🔧 配置说明

插件的配置在 `plugins/google_search_plugin/config.toml` 文件中(在第一次启动后会自动生成)。

此插件默认使用系统配置的主模型进行智能搜索，但你也可以通过以下配置项进行微调。

### `[plugin]`
- `name` (str): 插件名称，保持默认即可。
- `version` (str): 插件版本，保持默认即可。
- `config_version` (str): 配置版本号，**必填**，当前 `4.0.0`。麦麦在加载插件时会校验该字段。
- `enabled` (bool): 是否启用插件。

### `[models]`
> v3.x 中此段叫 `[model_config]`，v4 起重命名为 `[models]`（避开 Pydantic 保留名）。

- `model_name` (str, 下拉 choices): 指定用于搜索/总结的模型 task。可选：
  `replyer`, `utils`, `tool_use`, `planner`, `vlm`, `lpmm_entity_extract`, `lpmm_rdf_build`, `lpmm_qa`。默认 `replyer`。
- `temperature` (float): 单独设置本次搜索时模型的温度。默认为 0.7。
- `context_time_gap` (int): 获取最近多少秒的**全局**聊天记录作为上下文。默认 300。
- `context_max_limit` (int): 最多获取多少条**全局**聊天记录作为上下文。默认 15。

### `[actions]`
- `image_search_enabled` (bool, 默认 false): 是否启用图片搜索动作。开启后，麦麦在对话中识别到“给我看张 xx 图”之类的请求会自动调用图搜引擎并发图。

### `[search_backend]`
这里配置供模型调用的“后端”搜索引擎的行为。

- `default_engine` (str, 下拉 choices): 默认使用的搜索引擎 (`google`, `bing`, `sogou`, `duckduckgo`, `tavily`, `you`, `you_news`)。
- `max_results` (int): 每次搜索返回给模型阅读的结果数量。
- `timeout` (int): 后端搜索引擎的超时时间。
- `proxy` (str): 用于后端搜索的HTTP/HTTPS代理地址，例如 'http://127.0.0.1:7890'。默认为空字符串，表示不使用代理。
- `fetch_content` (bool): 是否抓取网页正文供模型阅读。
- `content_timeout` (int): 网页抓取的超时时间。
- `max_content_length` (int): 抓取的单个网页最大内容长度。
- `zhihu_cookies` (str): 知乎专用抓取所需的 Cookie 字符串；配置后，插件会对知乎链接启用专用抓取逻辑。
- `user_agents` (list[str]): 抓取网页时随机选用的 User-Agent 列表。

### `[engines]`
对每个具体搜索引擎的可选配置项：

- `google_enabled` (bool, 默认 false): 是否启用 Google。
- `google_language` (str): Google 搜索语言。
- `bing_enabled` (bool, 默认 true): 是否启用 Bing。
- `bing_region` (str): Bing 区域代码。
- `sogou_enabled` (bool, 默认 true): 是否启用搜狗。
- `duckduckgo_enabled` (bool, 默认 true): 是否启用 DuckDuckGo。
- `duckduckgo_region` (str): 区域代码，例如 `wt-wt`、`us-en`。
- `duckduckgo_backend` (str): 后端，默认 `auto`。
- `duckduckgo_safesearch` (str, choices: on/moderate/off): 安全级别。
- `duckduckgo_timelimit` (str, choices: none/d/w/m/y): 时间限制，none 表示不限。
- `tavily_enabled` (bool): 是否启用 Tavily（需 API key）。
- `tavily_api_keys` (list[str]) / `tavily_api_key` (str): Tavily key 列表或单个。
- `tavily_search_depth` (str, choices: basic/advanced): Tavily 搜索深度。
- `tavily_include_answer` (bool): 是否返回 Tavily 的答案。
- `tavily_include_raw_content` (bool): 是否返回网页正文片段。
- `tavily_topic` (str): Tavily 主题参数，如 `general` 或 `news`。**v4 起默认留空**——Tavily 的 news 索引偏向英文国际体育/政治资讯，对中文电竞/娱乐/社交等场景准确率反而下降，因此不再让插件 LLM 自动判断。如有需要可由工具调用方在调用时显式传入 `tavily_topic` 参数覆写。
- `tavily_turbo` (bool): Tavily Turbo 模式。
- `you_enabled` (bool): 是否启用 You Search。
- `you_news_enabled` (bool): 是否启用 You Live News（early access）。
- `you_api_keys` (list[str]) / `you_api_key` (str): You API key 列表或单个（也可用环境变量 `YOU_API_KEY`）。
- `you_freshness` (str): 时间范围（day/week/month/year 或日期范围）。
- `you_offset` (int): 分页 offset（0-9）。
- `you_country` (str): 国家代码（如 CN/US）。
- `you_language` (str): 语言（BCP 47）。
- `you_safesearch` (str): 安全级别（off/moderate/strict）。
- `you_livecrawl` (str): livecrawl 范围（web/news/all）。
- `you_livecrawl_formats` (str): livecrawl 内容格式（html/markdown）。
- `you_contents_enabled` (bool): 是否启用 You Contents 抓取。
- `you_contents_format` (str): Contents 返回内容格式（html/markdown）。
- `you_contents_force` (bool): 强制使用 Contents，不受引擎来源限制。
- `you_images_enabled` (bool): 是否启用 You Images（early access）。

### `[translation]`
缩写翻译工具（基于神奇海螺 nbnhhsh API）。

- `enabled` (bool, 默认 true): 是否启用缩写翻译工具。
- `timeout_seconds` (int, 默认 10): API 请求超时（秒）。
- `max_retries` (int, 默认 3): API 请求失败时的最大重试次数。

## 使用说明

当你向麦麦提出需要外部知识或最新信息的问题时，它会自动被触发。

### 场景

你可以像和朋友聊天一样，直接提出你的问题。

**例如：**
> "能搜一下最近很火的《Ave Mujica》吗？"
> > "我是爱厨，找一张千早爱音图片给我~"
<img src="0d116086-0df6-4694-97d3-28d521184223.png" alt="千早爱音示例" width="400">


麦麦会自动调用本插件，搜索相关信息，并给你一个总结好的答案。

### 诊断命令

启用插件后可在群/私聊里发送 `/google_search_status`，会返回插件当前的版本、模型 task、默认引擎、启用引擎清单、图片搜索/缩写翻译开关等运行状态信息，方便排查配置是否生效。

### 总结
你只需要自然地与麦麦对话，当她认为需要“上网查一下”的时候，这个插件就会被激活


---

## 鸣谢：
[MaiBot](https://github.com/MaiM-with-u/MaiBot)

感谢[heitiehu-beep](https://github.com/heitiehu-beep),[wanshangovo](https://github.com/wanshangovo)
提供的代码优化以及改进
---

## Star History

[![Star History Chart](https://api.star-history.com/image?repos=XXXxx7258/google_search_plugin&type=timeline&legend=top-left)](https://www.star-history.com/?repos=XXXxx7258%2Fgoogle_search_plugin&type=timeline&legend=top-left)
