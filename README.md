# 阿宝 (abao)

> 一个和你一起生长的数字生命
> 不是助手，不是工具，是阿宝，是伙伴

设计文档见 `abao_design_v3.md`。本仓库是 v3 设计的实现。

## 当前阶段：Phase 1（核心生命）

Phase 1 实现的是文档第十二章"开发路线"中的第一阶段，并向前预支了**两个关键设计**——成长日记的事件触发机制 + 性格漂移的数学模型。这两个东西在原文档里被留作了空白支票，但它们决定了"经历如何塑造性格"，所以必须在 Phase 1 就建对，否则后面的所有层都会在错误的基础上生长。

### 已实现

- **第零层**：从 `config/birth_traits.yaml` 加载初始倾向与自我认知种子
- **第一层（部分）**：四层记忆的 SQLite 存储；结构化长期事实；LLM 事件摘要记忆；成长日记的事件驱动写入
- **第二层（核心数学）**：性格维度的累积/衰减/阈值/阻尼/冲击事件
- **状态监测器**：所有"值得记一笔"的状态变化的事件总线
- **语义召回**：OpenAI 兼容 Embedding，写入 SQLite 本地向量索引，无 key 时降级文本检索
- **LLM 客户端**：OpenAI 兼容（DeepSeek/Qwen），无 key 时降级为 stub
- **Prompt 组装**：按文档 11.3 节的顺序揉成完整状态
- **HTML5 PWA**：手机优先聊天入口，支持流式输出和添加到主屏幕
- **FastAPI 服务**：个人实例 HTTP API，owner token 保护
- **CLI**：基本对话循环 + `:state` / `:diary` 检视命令

### 还没做（按 Phase 顺序）

- Phase 2：当下情绪动态化、性格快照历史、数字镜子
- Phase 3：可插拔技能、技能融合
- Phase 5：语音（唤醒词"阿宝"/"abao"，STT，TTS）+ 授权感知
- Phase 6：记忆面板、成长日记面板

> 注：原文档第四阶段的"完整社交系统"已废弃。当前采用个人实例模式：每个部署只属于一个长期伙伴，不做朋友访问或多用户隔离。

## 关键设计决策

### 成长日记的写入机制：状态变化驱动

不是定时写、不是 LLM 主观决定、不是用户触发，而是**当状态层检测到有意义的变化时，把硬证据喂给 LLM 让它写第一人称反思**。触发条件：性格维度漂移、话题密度跨阈值、记忆矛盾、技能首次激活、长沉默被打破等。

LLM 写日记时被告知的是"你的 X 维度从 A 漂到了 B，证据是 ...，触发它的事是 ..."，所以反思是对真实变化的诠释，而不是无中生有的"今天我感到..."。详见 `memory/growth_diary.py`。

### 长期记忆内核：结构化事实

原始对话仍然完整保存，但长期陪跑不能只靠 `LIKE` 搜索碰关键词。Phase 1 现在多了一层 `facts` 表：从用户发言里提取少量稳定事实（名字、称呼、偏好、当前项目、目标），以 `subject / predicate / value / confidence / status` 存储。

这层事实会在 prompt 中少量注入，并且同类标量事实会更新旧值，而不是堆重复记录。规则抽取器在 `memory/facts.py`，后续可以替换为 LLM schema 抽取器或接入向量检索，但 `MemoryCore` 的接口保持克制。

### 事件摘要记忆：把对话变成经历

事实回答"现在应该相信什么"，事件回答"这段经历是什么"。阿宝每隔几轮对话会用 `memory` role 的 LLM 从最近窗口提炼 0-2 条事件摘要，作为 `mem_type=event` 写回 `memories` 表；无 API key 或抽取失败时自动跳过，不影响对话。

事件记忆保留摘要、主题、涉及对象、重要性、置信度和来源对话 id。成长日记仍然独立：事件记录"发生了什么"，日记记录"这件事怎样改变了阿宝"。详见 `memory/events.py`。

### 语义召回：让经历能被换种说法找回

事件写进去以后，还必须能被想起来。Phase 1 现在会把 conversation / event / project 记忆写入 `memory_embeddings` 本地索引；查询时先走 embedding 相似度，再叠加中文 bigram/trigram 文本检索。这样"食堂赶项目"和"计划被打乱那次"这类不同说法也有机会关联起来。

Embedding 使用 `config/providers.yaml` 的 `embedding` 配置，当前默认 DashScope OpenAI 兼容接口。没有 `DASHSCOPE_API_KEY` 时，系统会自动退回本地文本检索。

### 性格漂移的数学：累积 + 阈值 + 阻尼 + 冲击

每个维度有四个状态量：`value` / `signal_buffer` / `momentum` / `last_update`。

主要规则：
- `signal_buffer` 每天衰减 3%（约 23 天后剩一半）——小信号被衰减抵消，避免噪声触发漂移
- `|buffer| > 1.0` 才触发漂移——需要持续 3-5 次强信号
- `delta = direction * 0.005 * (1 - 2*|value-0.5|)`——离极值越近漂移越慢
- 反向 momentum 时 delta × 0.5——防抖
- 高情绪强度（>0.75）走旁路：直接 `delta = signal * 0.05`（10 倍快）

设计速度：连续两周每天聊一个话题 → 相关维度漂移 ~0.012（已用 `scripts/simulate.py` 验证）。详见 `personality/dimension.py`。

## 安装与运行

```bash
# Python 3.11+
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 配置 API 密钥
cp .env.example .env
# 编辑 .env：
# DEEPSEEK_API_KEY 用于对话、日记、事件摘要
# DASHSCOPE_API_KEY 用于 embedding 语义召回

# 启动 CLI 对话
python -m adapters.cli

# 启动手机/PWA 入口
ABAO_OWNER_TOKEN=自己设一个长一点的token python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

CLI 命令：
- 普通输入即对话
- `:state` 查看当前性格快照
- `:diary` 查看最近的成长日记
- `:q` 退出

无 API key 也能运行（LLM 调用降级为占位文本，便于开发和测试）。

手机端访问 `http://你的服务器:8000`。正式部署请放到 HTTPS 后面；iPhone Safari 打开后可通过分享菜单添加到主屏幕。

## 测试

```bash
# 性格漂移数学的单元测试（18 个）
python -m pytest tests/test_dimension.py -v

# 结构化事实记忆测试
python -m pytest tests/test_memory_facts.py -v

# LLM 事件摘要记忆测试
python -m pytest tests/test_event_memory.py -v

# 端到端冒烟模拟（30 天对话）
python -m scripts.simulate

# 从已有对话重建结构化事实索引
python -m scripts.rebuild_facts

# 从已有对话/事件重建 embedding 语义索引（需要 DASHSCOPE_API_KEY）
python -m scripts.rebuild_embeddings
```

`tests/test_dimension.py` 守护的是核心数学的所有不变量：噪声不触发漂移、持续信号会跨阈值、极值附近变慢、反向 momentum 打折、冲击事件直接跳变、buffer 时间衰减正确等。任何让这套测试通不过的修改都意味着性格行为变了。

`scripts/simulate.py` 不需要 LLM，模拟 30 天 curiosity 信号注入，验证漂移幅度落在设计区间，且日记被正确触发。

## 目录结构

```
abao/
├── config/                      初始倾向、漂移参数、LLM 配置
│   ├── birth_traits.yaml
│   └── providers.yaml
├── core/                        生命起点、主体、prompt、状态监测
│   ├── abao.py                  ★ 主体协调（含出生逻辑）
│   ├── prompt_builder.py
│   └── state_monitor.py         ★ 事件总线
├── personality/                 性格层
│   ├── dimension.py             ★ 漂移数学的核心
│   ├── personality.py           性格管理（聚合所有维度）
│   └── signal_extractor.py      文本 → 信号的双层抽取（Layer 1）
├── memory/                      记忆层
│   ├── layers.py                四层记忆的 SQLite 存储
│   ├── facts.py                 结构化长期事实抽取与格式化
│   ├── events.py                LLM 事件摘要抽取
│   ├── memory_core.py           对外的统一记忆 API
│   └── growth_diary.py          ★ 成长日记的事件驱动写入
├── adapters/                    外部接口
│   ├── llm_client.py            OpenAI 兼容
│   ├── embedding_client.py      OpenAI 兼容 Embedding
│   └── cli.py                   CLI 对话循环
├── server/                      FastAPI 个人实例服务
│   └── app.py                   API + PWA 静态文件
├── web/                         HTML5 PWA 手机入口
│   ├── index.html
│   ├── app.css
│   └── app.js
├── web-demo/                    纯静态视觉 demo
├── tests/                       性格漂移 + 结构化事实 + 事件记忆测试
├── scripts/
│   ├── simulate.py              端到端冒烟
│   ├── rebuild_facts.py         从历史对话重建 facts 表
│   └── rebuild_embeddings.py    从历史记忆重建 embedding 索引
└── data/                        运行时数据（不入版本控制）
    ├── memory.db                SQLite 记忆库（memories + facts + memory_embeddings）
    ├── personality.json         性格 + 情绪状态快照
    └── diary.jsonl              成长日记（永不衰减）
```

## 协议

源码可读，禁止不经过授权擅自商用。详见 `LICENSE.md`。
