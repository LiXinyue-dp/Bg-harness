# Agentic Image Generation 与 Boogu/Bg-harness 汇报笔记

> 版本：2026-09-07  
> 面向对象：刚加入 Boogu-Image / Bg-harness 方向的新同学  
> 目标：把 PPT 中的文章和领导补充文章梳理成可汇报、可落地的研究路线。

## 0. 先说明：到底是九篇还是十篇？

按你给的 PPT，主线文章包括：

1. GenRouter
2. GenClaw
3. Generation Navigator
4. SearchGen
5. TRACE-Bench
6. MultiBanana
7. MICON-Bench

领导后来补充：

8. VisionDirector
9. WeAgent-MMGenEdit
10. SpatialGuard

所以严格数是 10 个条目。如果汇报标题必须写“九篇文章”，可以把 MultiBanana 和 MICON-Bench 合并为“多参考 / 多图上下文 benchmark”一节；但从理解工作脉络看，建议保留 10 篇分别讲，因为它们给 Boogu/Bg-harness 的启发不同。

## 1. 入门版总览：为什么这些工作都叫 agentic？

传统文生图或图像编辑可以理解为：

```text
用户 prompt / reference image -> 图像模型 -> 输出图
```

这种方式像一个“一次性出图函数”。它很强，但遇到下面几类问题会不稳：

- prompt 很长，里面有很多目标：布局、文字、logo、局部对象、风格、材质。
- prompt 需要真实世界知识：真实地点、真实产品、近期事件、冷门 IP、文化符号。
- prompt 需要精确空间关系：A 在 B 左边、C 在两者后面、遮挡、视角、数量。
- prompt 需要多张参考图：保留 A 的身份，抽取 B 的发型，应用到 C，最后组合场景。
- 生成完之后需要判断哪里错了，并决定是小修、重画，还是停止。

Agentic image generation 的核心不是“多一个 LLM”，而是把一次性出图变成任务执行流程：

```text
理解目标 -> 判断缺什么 -> 选择动作 -> 调用工具/生成中间状态
-> 生成图像 -> 检查结果 -> 根据结果继续修或停止
```

更直白地说，agentic 就是让系统像一个设计助理，而不是像一个黑箱生成器。

## 2. 这批文章的主线地图

| 类别 | 文章 | 主要回答的问题 | 对 Bg-harness 的启发 |
|---|---|---|---|
| Workflow routing | GenRouter | 什么时候用 Direct / Rewrite / Search / Sketch / Verify？ | 做 workflow ablation 和 routing policy |
| 可执行中间状态 | GenClaw | 为什么不能只 rewrite prompt？ | 加 layout/sketch/code canvas |
| 多轮决策 | Generation Navigator | 失败后是停、修、还是重画？ | 加 STOP/REFINE/REGENERATE trace |
| 外部知识 | SearchGen | 什么该搜，什么不该搜，怎么防噪声？ | 真 web/image search，不是普通 rewrite |
| 多参考诊断 | TRACE-Bench | 多参考失败到底坏在哪个能力？ | 用 Anchor/Disentangle/Apply/Compose 标注 case |
| 多参考难度因子 | MultiBanana | 参考图数量、跨域、尺度、稀有概念怎么影响性能？ | 为 hardcase 加 hard_factors |
| 多图上下文评测 | MICON-Bench | 多图组合、属性迁移、故事推理怎么自动评？ | 做 checkpoint-level judge |
| 长目标闭环设计 | VisionDirector | 专业设计 prompt 很长，如何分阶段修？ | 对海报/PPT/文字类 case 做 goal decomposition |
| Full-stack harness | WeAgent-MMGenEdit | 搜索、验证、集成、数据、训练如何打通？ | Bg-harness 本身可以成为核心贡献 |
| 空间可验证生成 | SpatialGuard | 空间关系如何可规划、可验证、可修复？ | 加 layout harness 和空间约束检查 |

## 3. 对 Boogu 的总体判断

Boogu-Image-0.1 已经不是纯粹的：

```text
prompt -> image
```

它已经有轻量 agentic 能力：

```text
prompt -> instruction reasoner / rewriter -> generator
```

对应到文章里的术语，它已经覆盖了部分 RewriteGen / Reason-before-generation。仓库中可以看到 `--use_rewrite_text_instruction`、`--rewriter_system_prompt_type default/ppt/custom`、local rewriter、remote DashScope rewriting、保存 rewritten instruction 等入口。

因此 Bg-harness 下一步不应该重复做一个普通 VLM rewrite。真正有价值的问题是：

```text
Boogu 0.1 的 rewriter 在哪些 hardcase 上已经够用？
哪些 case 必须引入 SearchGen？
哪些 case 必须引入 SketchGen / SpatialGuard?
哪些 case 必须引入 VerifyGen / Navigator?
```

这就是 harness 的研究价值。

---

# 4. 文章 1：GenRouter

## 4.1 基本信息

- 标题：GenRouter: Unified Workflow Routing for Agentic Image Generation
- 链接：[arXiv:2608.16721](https://arxiv.org/abs/2608.16721)
- 核心关键词：workflow routing、GenCanvas、demand profiling、memory-guided matching、Pareto filtering

## 4.2 它解决什么问题？

这篇文章的出发点非常现实：agentic image generation 确实能增强图像生成，但如果每个 prompt 都走最重的 agent pipeline，就会很浪费。

例如：

```text
画一只猫
```

其实 DirectGen 或简单 RewriteGen 就够了。但如果系统仍然执行：

```text
Search -> Reason -> Verify -> Refine -> Generate
```

就会带来高成本、高延迟，而且不一定更好。

反过来，如果 prompt 是：

```text
生成一个香港摩天轮在日落时的真实照片，旁边有维港天际线
```

它可能需要外部知识；如果只是 DirectGen，模型可能会画成 generic Ferris wheel。

所以 GenRouter 关心的问题是：

```text
不同难度、不同能力需求的 prompt，应该分配给不同 workflow。
```

## 4.3 方法核心

GenRouter 先定义一个统一的 workflow 空间，称为 GenCanvas。它把各种 agentic image generation 方法归纳成一些基础能力和可执行模板。

PPT 中列出的 9 个 workflow templates 是：

1. DirectGen：直接生成。
2. RewriteGen：先重写 prompt，再生成。
3. SearchGen：搜索外部知识，再生成。
4. RefGen：找视觉参考，再生成。
5. ReasonGen：先推理，再重写生成。
6. SkillGen：调用特定技能或工具。
7. SketchGen：先生成布局/草图，再生成。
8. VerifyGen：生成后检查，不行再 refine。
9. HybridGen：组合搜索、草图、验证等重流程。

然后它做三步 routing：

### Step 1：Demand Profiling

先分析 prompt 到底需要什么能力。论文中用轻量 LLM 抽取 7 维 task signature：

```text
semantic
factual
reference
logical
composition
critique
layout
```

可以简单理解为：这条 prompt 是不是需要事实知识？是不是需要参考图？是不是需要逻辑推理？是不是需要布局？

### Step 2：Memory-Guided Matching

系统会保存过去的执行经验：

```text
prompt
workflow
generator
score
cost
latency
```

当新 prompt 来了，它查找相似任务之前哪个 workflow 效果好、成本多少、延迟多少。

### Step 3：Pareto Filtering

不是只选最高分，而是比较质量、成本、延迟。  
如果一个方案质量更低、成本更高、速度更慢，那它就被淘汰。

论文摘要中报告，相比固定重 pipeline，GenRouter 在保持/提升 alignment 的同时，大幅降低执行成本和延迟；原文摘要提到成本降低超过 95%、延迟降低 65%。

## 4.4 创新点

1. 把离散的 agentic workflows 统一到同一个模板空间。
2. 不再“一刀切”用重 agent，而是按 prompt 需求选择 workflow。
3. 引入历史经验 memory，让 routing 随执行记录自我改进。
4. 把质量、成本、延迟放在同一个决策框架里。

## 4.5 为什么能发文章？

因为它回答了 agentic image generation 发展到一定阶段后的关键问题：

```text
不是能不能做 agent，而是什么时候值得做 agent。
```

这是一个系统级问题。它不是又提出一个工具，而是提出一个调度框架。

## 4.6 可改进点

1. 需求打分依赖 LLM profiler，可能存在误判。
2. 早期 cold-start memory 不足，routing 会不稳。
3. workflow templates 是否覆盖所有真实业务场景，还有扩展空间。
4. 质量评估依赖 judge，judge 如果偏，router 也会学偏。

## 4.7 对 Boogu/Bg-harness 的启发

Bg-harness 应该把 GenRouter 作为长期目标。  
第一步不是训练 router，而是收集 routing 所需的数据：

```json
{
  "case_id": "...",
  "prompt": "...",
  "capability_tags": ["spatial", "dense_text"],
  "workflow": "rewrite_ppt",
  "generator": "boogu_turbo",
  "score": 0.72,
  "latency_sec": 12.4,
  "cost_proxy": 1.0,
  "failure_type": "text_rendering"
}
```

这样未来才能回答：

- 哪些 hardcase Direct 就够？
- 哪些需要 Boogu 现有 rewriter？
- 哪些需要 search？
- 哪些需要 sketch？
- 哪些值得多轮 verify/refine？

---

# 5. 文章 2：GenClaw

## 5.1 基本信息

- 标题：GenClaw: Code-Driven Agentic Image Generation
- 链接：[arXiv:2605.30248](https://arxiv.org/abs/2605.30248)
- 核心关键词：code-driven generation、Think-Sketch-Color、executable canvas、SVG/HTML/Three.js

## 5.2 它解决什么问题？

很多 image agent 看似 agentic，但本质仍是：

```text
prompt -> rewrite prompt -> generator
```

也就是说，agent 只能在自然语言里“下命令”，最终画面结构仍然由黑箱图像模型自己猜。

这会导致：

- 物体数量不稳定。
- 左右/前后/遮挡关系不稳定。
- 海报/PPT/信息图布局不稳定。
- 文本位置、字号、层级难控制。
- 失败后不知道是理解错、布局错，还是 generator 没跟上。

GenClaw 认为：只在 prompt 里描述视觉结构是不够的。  
LLM 真正擅长的是写代码、做结构化推理，所以应该让它用代码构造一个可执行的视觉中间状态。

## 5.3 方法核心

GenClaw 把图像生成拆成三层：

```text
Think -> Sketch -> Color
```

### Think：Cognitive Structuring Layer

这一层做用户意图理解、搜索、知识补全、逻辑推理，把自然语言变成结构化记录。

例如：

```json
{
  "objects": ["elderly man", "trash can", "traffic light"],
  "relations": [
    "elderly man in center",
    "trash can in background",
    "traffic light visible behind"
  ],
  "style": "street photography, Leica texture"
}
```

### Sketch：Executable Canvas Layer

把上面的结构化记录转成可执行视觉代码：

- SVG
- HTML/CSS
- Python plotting
- Three.js
- 分层 JSON

这个 sketch 不追求最终真实感，而是固定：

- 物体数量
- 位置
- 大小
- 层级
- z-order
- 文本区域
- 版式结构

### Color：Visual Generation and Review Layer

最后把 sketch 交给图像模型，由图像模型负责纹理、光照、材质、真实感。  
生成后还可以用 VLM 或用户反馈 review。

## 5.4 创新点

1. 把 code 作为图像生成的中间画布，而不是只作为后处理工具。
2. 把 LLM 的优势从“写长 prompt”转成“写可执行结构”。
3. 明确分工：agent 负责结构，generator 负责像素真实感。
4. 让失败可追踪：搜索错、代码草图错、渲染错可以分开定位。

## 5.5 为什么能发文章？

因为它指出了现有 agentic image generation 的一个根本问题：

```text
如果 agent 只能写自然语言，它对视觉空间没有真正控制权。
```

GenClaw 提出一个新范式：code-as-brush。  
这不是小修小补，而是把生成过程从黑箱变成阶段式、可解释、可检查。

## 5.6 可改进点

1. 代码草图不适合表达复杂自然纹理。
2. LLM 生成的 SVG/HTML/Three.js 可能有 bug。
3. 从 sketch 到真实图像时，generator 仍可能不遵守结构。
4. 复杂 3D 或物理场景的 code sketch 成本较高。
5. 对一般摄影类 prompt，SketchGen 可能没有必要。

## 5.7 对 Boogu/Bg-harness 的启发

Bg-harness 可以先做一个轻量 SketchGen：

```text
prompt -> layout.json -> sketch.svg/sketch.png -> Boogu Edit -> final image
```

最适合优先覆盖：

- 空间关系
- 精确数量
- PPT/海报/信息图
- 多对象组合
- 文字区域明确的设计图

不要一开始追求完整 GenClaw。第一版只要能保存：

```text
layout.json
sketch.svg
final image
judge.json
```

就已经有研究价值。

---

# 6. 文章 3：Generation Navigator

## 6.1 基本信息

- 标题：Generation Navigator: A State-Aware Agentic Framework for Image Generation
- 链接：[arXiv:2605.17969](https://arxiv.org/abs/2605.17969)
- 核心关键词：state-aware generation、STOP/REFINE/REGENERATE、trajectory、PRE-GRPO

## 6.2 它解决什么问题？

实际生成图像时，经常不是一次成功。人会反复试：

- 这张已经不错了，别再改。
- 这张主体对，但文字错了，小修。
- 这张结构完全错了，重画。

很多系统的问题是：固定流程不懂当前图像状态。

比如：

```text
固定 refine：可能在结构已经错的图上越修越差。
固定 regenerate：可能丢掉已经正确的好图。
固定多轮：可能浪费成本，还破坏已有结果。
```

Generation Navigator 认为，多轮生成应该是一个状态决策问题。

## 6.3 方法核心

每一轮系统都有一个 state：

```text
原始 prompt
历史动作
历史图像
reviewer feedback
score
```

Navigator 根据 state 输出 action：

```text
STOP
REFINE
REGENERATE
```

其中：

- STOP：当前结果够好了。
- REFINE：整体结构对，只需要局部修。
- REGENERATE：全局结构错，应该重新生成。

论文中特别强调：最终交付不一定是最后一轮图，而是整个 trajectory 中分数最高的图。这个设计很重要，因为多轮编辑可能会把好图改坏。

## 6.4 训练方法

它不是只训练 prompt rewrite，而是训练 action policy。

### Trajectory SFT

先收集多轮轨迹，让模型学会输出结构化动作。

论文中提到通过 prompt pool、复杂度打分、hard/expert prompt 采样、branch-and-select 等方法构造轨迹。

### PRE-GRPO

PRE 表示：

- Peak：轨迹里是否找到过高质量图。
- Retention：后续是否保住高质量，不把好图毁掉。
- Efficiency：是否少走无效轮数。

也就是说，它不是只看 final score，而是评价整条生成过程。

## 6.5 创新点

1. 把多轮图像生成建模为 state-conditioned action-making。
2. 明确动作空间：STOP / REFINE / REGENERATE。
3. 提出 trajectory-level reward，而不是只奖励最终图。
4. 强调 retention，避免多轮操作毁掉已有好结果。

## 6.6 为什么能发文章？

因为它解决了多轮生成最核心的问题：

```text
怎么知道下一步该做什么？
```

这比“失败了再跑一次”更系统。它把图像生成从一次性任务变成有状态的决策过程。

## 6.7 可改进点

1. 需要可靠 judge，否则 state 信号不准。
2. 多轮生成成本高。
3. REFINE 依赖底层 I2I 模型是否支持局部稳定编辑。
4. 复杂任务里动作空间可能不止三类，还可能需要 rollback、search、sketch、ask user。

## 6.8 对 Boogu/Bg-harness 的启发

Bg-harness 不要一开始就做 Navigator。正确顺序是：

```text
先有 judge -> 再有 VerifyGen -> 最后做 Navigator
```

第一版 Navigator 可以很简单：

```json
{
  "round": 1,
  "score": 0.42,
  "failed_checks": ["text unreadable", "missing object"],
  "action": "REGENERATE",
  "reason": "global structure failed",
  "next_prompt": "..."
}
```

重点不是多跑几轮，而是保存 action trace。

---

# 7. 文章 4：SearchGen

## 7.1 基本信息

- 标题：Search Beyond What Can Be Taught: Evolving the Knowledge Boundary in Agentic Visual Generation
- 链接：[arXiv:2607.05382](https://arxiv.org/abs/2607.05382)
- 核心关键词：world knowledge、knowledge boundary、Gate-Search-Filter-Integrate、SearchGen-20K、SearchGen-Corpus-1M

## 7.2 它解决什么问题？

图像模型不是不会画，而是不知道。  
特别是：

- 近期事件
- 真实地点
- 冷门 IP
- 小众文化符号
- 真实产品
- UI/榜单/数据图
- 长尾实体

这类任务不是靠 rewrite 就能解决，因为 rewrite 仍然只用模型参数里的知识。

SearchGen 的核心观点是：

```text
世界知识是开放的，而模型训练数据是固定的。
```

所以外部搜索是必要的。

## 7.3 但为什么不能无脑搜索？

论文强调：Naive Search 也会失败。

因为搜索结果可能带来：

- 错误 reference
- 过时信息
- 不相关背景
- 复制姿势/风格/构图
- 把模型本来会的东西污染掉

例如模型本来知道一个常见角色，但搜索结果里混入同人图、低质量截图、错误版本，最后生成反而变差。

## 7.4 方法核心

SearchGen 的流程可以概括为：

```text
Gate -> Search -> Filter -> Integrate -> Generate
```

### Gate

判断是否需要搜索。

不是所有 prompt 都搜。简单猫、普通产品、抽象风格通常不需要。

### Search

生成查询，搜索网页或图片。

### Filter

筛掉无关、错误、噪声 reference。

### Integrate

只把必要视觉事实整合到 prompt 中，同时说明不要复制什么。

例如：

```text
Use the reference only for the wheel structure and waterfront skyline.
Do not copy the exact camera angle or unrelated crowd/background.
```

## 7.5 数据和 benchmark

论文构造了 SearchGen-20K / SearchGen-Bench / SearchGen-Corpus-1M。

公开摘要和正文中提到：

- 20,839 个 world-knowledge-grounded prompts。
- 覆盖 12 类 failure categories 和 22 个 domains。
- 提供预执行的 multimodal search corpus，支持离线可复现研究。
- SearchGen-Bench 上 frontier open generators 只有 21-28 / 100，说明现有 benchmark 看不到这种知识塌陷。

PPT 中也强调了 answer-first 数据构造：

```text
先确定正确知识和 reference
再围绕 knowledge gaps 合成 prompt
最后用 checklist/rubric 评估
```

## 7.6 创新点

1. 把 world-knowledge failure 从普通图像质量 failure 中分离出来。
2. 证明 blind search 会伤害生成。
3. 提出 generator-specific knowledge boundary。
4. 数据构造不是简单让 LLM 写 prompt，而是 answer-first。
5. 提供可 replay 的 search corpus，使搜索增强生成可复现。

## 7.7 为什么能发文章？

因为它证明了一个结构性问题：

```text
模型参数永远覆盖不了真实用户的长尾知识需求。
```

并且它不是只说“加搜索”，而是证明“加搜索也要学习边界”。

## 7.8 可改进点

1. 真实搜索 API 会变，结果不稳定。
2. 搜索和版权/来源可信度有关，需要治理。
3. 自动 judge 对知识细节可能误判。
4. 搜索增强可能增加延迟。
5. 对 Boogu 这类开放模型，要先测清楚它已经知道什么。

## 7.9 对 Boogu/Bg-harness 的启发

这篇对你的工作非常关键。

Boogu 已经有 rewriter，所以 Bg-harness 的 SearchGen 不能只是：

```text
prompt -> VLM rewrite -> Boogu
```

而应该是真的：

```text
prompt -> need_search -> web/image search -> evidence filter
-> enriched prompt/reference -> Boogu -> knowledge judge
```

建议在 manifest 里保存：

```json
{
  "need_search": true,
  "queries": ["..."],
  "raw_search_results_path": "...",
  "selected_evidence_path": "...",
  "generation_prompt": "...",
  "search_noise_notes": "..."
}
```

---

# 8. 文章 5：TRACE-Bench

## 8.1 基本信息

- 标题：TRACE-Bench: Decomposing and Diagnosing Multi-Reference Image Generation
- 链接：[arXiv:2608.16765](https://arxiv.org/abs/2608.16765)
- 核心关键词：multi-reference、operator、Anchor、Disentangle、Apply、Compose、diagnostic tree

## 8.2 它解决什么问题？

多参考图生成不是简单“给多张图”。真正的问题是：

```text
从哪张图保留身份？
从哪张图抽属性？
把属性绑定到哪个对象？
多个对象如何组合？
```

传统 benchmark 常按任务名组织：

- virtual try-on
- style transfer
- subject composition
- group photo

但这种任务名很难诊断失败。  
同样叫 style transfer，可能有的 case 只需要迁移颜色，有的需要迁移复杂材质，有的还要保持人物身份。

## 8.3 方法核心

TRACE-Bench 提出四个原子 operator：

### Anchor

锁定一个参考实体，保持身份。

例子：

```text
输出人物必须是参考图 A 里的同一个人。
```

### Disentangle

从某张图里干净抽取属性，不把无关东西带出来。

例子：

```text
只抽取参考图 B 的发型，不要抽取脸、背景、姿势。
```

### Apply

把抽取的属性绑定到目标对象。

例子：

```text
把 B 的发型应用到 A 的人物上。
```

### Compose

把多个实体或属性组合成合理场景。

例子：

```text
人物 A、宠物 B、背景 C 组合成一张自然照片。
```

## 8.4 Formula-first 思想

TRACE-Bench 不先定义任务名，而是先定义公式。

例如：

```text
C(f1, T_e ⊕ g1)
```

可以理解成：

```text
保留一个参考实体 + 抽取另一个参考的属性并应用 + 组合成场景
```

这让任务复杂度可以被结构化描述。

## 8.5 Evaluation

论文强调 operator-aligned evaluation。  
也就是说，不只给整体分数，还分别评：

- Anchor 是否成功？
- Disentangle 是否成功？
- Apply 是否成功？
- Compose 是否成功？

论文还用 diagnostic tree：把复杂公式逐步简化，观察某个错误是在完整组合时才出现，还是单独 operator 本身就做不好。

## 8.6 创新点

1. 从 task-oriented benchmark 转向 capability-oriented benchmark。
2. 用四个 operator 统一描述多参考任务。
3. 用 formula 控制结构复杂度。
4. 用 diagnostic tree 做失败归因。

## 8.7 为什么能发文章？

因为多参考图生成是新能力，但评测体系不够。  
TRACE 的贡献是让 multi-reference evaluation 变得可解释、可诊断。

## 8.8 可改进点

1. 四个 operator 是否足够覆盖所有真实任务，可以继续扩展。
2. operator 打分依赖 checkpoint 设计。
3. 公式复杂度不完全等于真实视觉难度。
4. 对模型训练的直接指导还需要进一步实验。

## 8.9 对 Boogu/Bg-harness 的启发

如果 Boogu 未来要做多参考编辑，Bg-harness 应该直接采用 TRACE 术语。

case schema 里不要只写：

```json
{"task_type": "multi_ref"}
```

而应该写：

```json
{
  "operators": ["anchor", "disentangle", "apply"],
  "checklist": [
    "identity comes from reference A",
    "hairstyle comes from reference B",
    "face from reference B is not copied"
  ]
}
```

---

# 9. 文章 6：MultiBanana

## 9.1 基本信息

- 标题：MultiBanana: A Challenging Benchmark for Multi-Reference Text-to-Image Generation
- 链接：[arXiv:2511.22989](https://arxiv.org/abs/2511.22989)
- 核心关键词：multi-reference、1-8 references、domain mismatch、scale/view mismatch、rare concept、multilingual

## 9.2 它解决什么问题？

已有 benchmark 通常只测单图或少量 reference。  
但真实多参考生成里，难点不只是 reference 数量，还包括：

- 多张图风格不同：照片、动漫、CG、手绘混合。
- 参考图尺度不同：近景、远景、局部细节。
- 视角不同：正面、侧面、俯视。
- 稀有概念：模型没见过的颜色、物种、物体组合。
- 多语言文字 reference：中英日混合。

## 9.3 方法核心

MultiBanana 系统覆盖 1-8 张 reference。

两张 reference 时，包含 11 类任务：

- subject addition
- subject replacement
- background change
- color modification
- material modification
- pose modification
- hairstyle modification
- makeup modification
- tone transformation
- style transfer
- text correction

3-8 张 reference 时，用四类结构：

- X Objects：组合 X 个参考对象。
- X-1 Objects + Background：对象组合加背景。
- X-1 Objects + Local：对象组合加局部属性迁移。
- X-1 Objects + Global：对象组合加全局风格/色调。

## 9.4 数据构造

论文使用真实图和合成图混合：

- 从 LAION-5B 选高质量图片。
- 用 Nano Banana / ChatGPT-Image 补齐人物、动物、物体等不足类别。
- 用 YOLO、SAM2、CLIP、Gemini、人审做过滤和分类。
- 构造任务时用参考图类别约束，生成 instruction，再做难度筛选。

## 9.5 创新点

1. 系统测 reference 数量从 1 到 8 的变化。
2. 把 reference difficulty 显式建模。
3. 把跨域、尺度/视角、稀有概念、多语言作为 hard factors。
4. 发现一些 agentic inference 会提升 GPT 类模型，但也可能让 Nano Banana 退化，因为多轮 prompt refinement 会丢失原始指令信息。

## 9.6 为什么能发文章？

因为多参考生成正在变成主流能力，但缺少足够难、足够系统的 benchmark。  
MultiBanana 的价值在于让模型能力边界暴露出来。

## 9.7 可改进点

1. 任务仍有一定 template 化。
2. 自动评估和人类偏好可能不完全一致。
3. benchmark 无唯一 ground-truth output，评价更依赖 judge。
4. 多参考图版权和来源治理需要注意。

## 9.8 对 Boogu/Bg-harness 的启发

即使 Boogu 当前公开说明中 Edit 主要稳定支持单参考，Bg-harness 也可以先为未来多参考准备字段：

```json
{
  "reference_count": 4,
  "hard_factors": ["cross_domain", "scale_view_mismatch", "multilingual"],
  "capability_tags": ["multi_reference", "attribute_binding"]
}
```

它还提醒我们：agentic 不一定总提升，多轮 refinement 可能丢信息。  
所以 harness 必须保留原始 prompt、每轮 prompt、每轮图，不能只看最终图。

---

# 10. 文章 7：MICON-Bench

## 10.1 基本信息

- 标题：MICON-Bench: Benchmarking and Enhancing Multi-Image Context Image Generation in Unified Multimodal Models
- 链接：[arXiv:2602.19497](https://arxiv.org/abs/2602.19497)
- 核心关键词：multi-image context、Evaluation-by-Checkpoint、Dynamic Attention Rebalancing

## 10.2 它解决什么问题？

统一多模态模型开始支持多图输入和图像生成，但现有评测多是：

- 文生图
- 单图编辑
- 简单参考图组合

它们不够测试 multi-image context。

Multi-image context 指的是：模型需要理解多张图之间的关系，然后生成结果。  
这不只是“参考图更多”，而是要跨图推理。

## 10.3 任务设置

MICON-Bench 包含六类任务：

1. Object Composition：主体 + 场景。
2. Spatial Composition：多对象 + 空间关系。
3. Attribute Disentanglement：主体、风格、背景来自不同图。
4. Component Transfer：把局部组件从一张图迁移到另一张图。
5. FG/BG Composition：前景和背景组合。
6. Story Generation / Story Inference：根据前序图推断后续事件。

论文中 benchmark 有 1,043 cases 和 2,518 images。

## 10.4 Evaluation-by-Checkpoint

这是对 Bg-harness 最重要的部分。

它不是只问：

```text
这张图好不好？
```

而是为每个 case 生成多个 checkpoint：

```text
是否包含所有指定对象？
相对位置是否正确？
身份是否保留？
属性是否来自正确参考图？
故事后续是否符合因果？
文字是否正确？
```

然后用 MLLM verifier 自动判断每个 checkpoint。

论文还做了人类一致性和稳定性分析，说明 checkpoint evaluation 相比纯整体评分更可解释。

## 10.5 Dynamic Attention Rebalancing

MICON 还提出一个 training-free 方法 DAR。  
它观察到多参考生成时模型注意力容易平均分散到无关区域，导致身份混淆或 hallucination。DAR 通过调整注意力响应，让模型更关注相关参考区域，抑制干扰。

## 10.6 创新点

1. 系统定义 multi-image context generation benchmark。
2. 用 checkpoint 自动评估，让分数可解释。
3. 指出多图输入中注意力分散是重要失败原因。
4. 提出 DAR 作为低开销 inference-time 改进。

## 10.7 为什么能发文章？

因为它不仅有 benchmark，还有评价方法和改进方法。  
它回答了：

```text
多图生成到底怎么测？
为什么模型会混淆？
能不能不用重训就改善？
```

## 10.8 可改进点

1. MLLM judge 仍可能有偏差。
2. checkpoint 质量决定评价上限。
3. DAR 是否适配 Boogu 架构，需要看模型内部 attention 是否可改。
4. story inference 评估较复杂，容易出现多解。

## 10.9 对 Boogu/Bg-harness 的启发

短期最该学的是 Evaluation-by-Checkpoint。

Bg-harness 的 judge 输出应类似：

```json
{
  "overall_score": 0.67,
  "checks": [
    {
      "dimension": "spatial_relation",
      "criterion": "red cube is left of blue sphere",
      "passed": false,
      "severity": "major"
    }
  ]
}
```

这比“好/不好”更能指导 Boogu 改进。

---

# 11. 文章 8：VisionDirector

## 11.1 基本信息

- 标题：VisionDirector: Vision-Language Guided Closed-Loop Refinement for Generative Image Synthesis
- 链接：[arXiv:2512.19243](https://arxiv.org/abs/2512.19243)
- 核心关键词：long-goal prompt、closed-loop refinement、staged edits、semantic verification、rollback、LGBench

## 11.2 它解决什么问题？

真实设计任务往往不是一句简单 prompt，而是长目标 prompt。  
例如一个商业海报可能要求：

- 产品在中央。
- Logo 在左上角。
- 中文标题可读。
- 五个卖点图标。
- 背景是厨房早晨光。
- 颜色要清新。
- 不要遮挡产品。
- 文案位置要有层级。

现在模型很会画漂亮图，但经常漏目标。VisionDirector 论文摘要中提到，Long Goal Bench 的平均 instruction 含 18-22 个紧耦合目标，SOTA 模型满足目标比例仍低于 72%。

## 11.3 方法核心

VisionDirector 是一个 vision-language supervisor。  
它做四件事：

### Goal extraction

把长 prompt 拆成结构化 goals。

例如：

```json
[
  {"goal": "title text is readable", "region": "top"},
  {"goal": "product centered", "region": "center"},
  {"goal": "four selling point icons", "region": "middle"}
]
```

### One-shot vs staged edits

判断是一次生成，还是分阶段编辑。

### Micro-grid sampling

对局部区域做候选采样，而不是整张盲目重画。

### Semantic verification and rollback

每一步都检查目标是否满足，如果破坏了已有目标，就回滚。

## 11.4 Benchmark：Long Goal Bench

LGBench 包含 2,000 个任务，T2I 和 I2I 各 1,000。  
它关注真实工作流中的复杂设计目标，包括 layout、object placement、typography、logo fidelity。

## 11.5 创新点

1. 把专业设计 prompt 看成多目标任务。
2. 用 vision-language supervisor 做闭环控制。
3. 支持 staged edits，而不是一次性生成。
4. 引入 rollback，防止修 A 时毁掉 B。
5. 记录 goal-level rewards，可用于 planner 训练。

## 11.6 为什么能发文章？

因为它瞄准了真实商业设计场景。  
传统 benchmark 很多是短 prompt，而真实用户尤其设计师会给长目标 prompt。VisionDirector 把这个 gap 变成 benchmark 和方法。

## 11.7 可改进点

1. 需要强 VLM 检查每个 goal，成本较高。
2. 目标拆解不准会影响后续流程。
3. 局部编辑依赖底层编辑模型稳定性。
4. 对抽象美学目标，semantic verification 不一定可靠。

## 11.8 对 Boogu/Bg-harness 的启发

Boogu README 强调海报、PPT、文字渲染、产品图。  
这些正是 VisionDirector 的场景。

Bg-harness 应该对设计类 hardcase 加：

```json
{
  "goal_list": [
    "Chinese title is readable",
    "product is centered",
    "three-column layout is preserved"
  ],
  "region_checks": [
    {"region": "top", "criterion": "title text"}
  ]
}
```

对复杂海报/PPT，不要只跑 Direct/Rewrite，而要考虑 staged verify/refine。

---

# 12. 文章 9：WeAgent-MMGenEdit

## 12.1 基本信息

- 标题：WeAgent-MMGenEdit: A Full-Stack Recipe for Multimodal Agentic Image Generation and Editing
- 链接：[arXiv:2609.05171](https://arxiv.org/abs/2609.05171)
- 作者信息：Hui Zhang 等，Weixin AI, Tencent
- 核心关键词：WeAgent-Harness、persistent evidence、retrieve-verify-integrate-deliver、dense carrier、three-layer checklist、agent/image two-sided post-training

## 12.2 它解决什么问题？

这篇和你的 Bg-harness 关系最直接。

它认为图像生成/编辑遇到 external world knowledge 时会不可靠。  
已有 agentic 方法虽然会检索，但仍有三个问题：

1. 视觉验证不足：搜到图片后没有真正检查像素内容。
2. policy model 负担过重：一个模型既要规划、检索、筛选，又要记住大量证据。
3. 文本证据和视觉证据整合弱：实体、属性、布局绑定不明确。

简单说：

```text
搜到了资料，不代表用对了资料。
用对资料，也不代表生成器能正确绑定到画面。
```

## 12.3 方法核心：WeAgent-Harness

论文提出 WeAgent-Harness，围绕四个阶段：

```text
Retrieve -> Verify -> Integrate -> Deliver
```

### Retrieve

工具包括：

- Web_Search
- Web_Extract
- Search_for_Image
- Search_by_Image

也就是既搜网页，也搜图片，还支持反向图片搜索。

### Verify

使用 Vision_Analyze 对候选图片做明确检查。  
关键是 retrieved image 不能只靠标题、caption、URL 判断，而要真正看图。

### Integrate

使用 Execute_Code 把验证后的文本和图片证据组织成 dense carrier。  
可以理解为：不是把一堆证据松散塞给模型，而是把它们编排成一个结构化视觉载体，明确实体、属性、位置之间的绑定关系。

### Deliver

调用 Image_Generation 生成最终图。

## 12.4 Persistent Multimodal Evidence Store

这是对 harness 很关键的点。  
所有证据都要有稳定 ID 和路径：

- 用户上传图
- 搜索到的图片
- 网页证据
- 代码渲染 carrier
- 最终输出图

不要把所有图片像素都塞进上下文，而是保存到 workspace，用 ID 引用。这样可以减少上下文压力，也方便复现和训练。

## 12.5 数据与评测

论文摘要和正文中提到：

- 构建 WeDataset-MMGenEdit。
- 收集 23K supervised trajectories。
- 构建 14.7K RL tasks。
- 使用三层 verifiable checklists：
  - agentic chain 是否正确；
  - generation input 是否充分；
  - final image 是否正确。
- 构建 WeBench-MMGenEdit，300 个 bilingual benchmark cases，generation/editing 和中英文平衡。
- 编辑 case 中相当大比例涉及多张用户提供图片。

## 12.6 Post-training

它不是只训练 agent policy，也训练 image backend。

### Agent side

SFT + checklist-grounded RL，让 policy 更会获取、验证、整合证据。

### Image side

多参考编辑 SFT + 多目标 RL，让 image backend 更会使用异质视觉参考和 dense carrier。

这就是 full-stack recipe 的含义。

## 12.7 创新点

1. 把 harness、数据构造、benchmark、agent 训练、image backend 训练打通。
2. 明确 retrieve-verify-integrate-deliver 四阶段。
3. 证据持久化，不让 multimodal evidence 在长轨迹中丢失。
4. 引入 dense carrier 解决跨模态绑定弱的问题。
5. 用三层 checklist 分离 agent 错、输入错、生成器错。

## 12.8 为什么能发文章？

因为它不是单点方法，而是 full-stack 系统。  
它把 agentic image generation 从“会搜索的 demo”推进到：

```text
harness + data + benchmark + policy training + backend training
```

这对工业团队尤其有价值。

## 12.9 可改进点

1. 系统复杂，工程成本高。
2. 搜索和证据验证依赖外部工具。
3. dense carrier 如何设计会强烈影响生成效果。
4. 三层 judge 需要隔离视角，否则容易混淆错误来源。
5. 对开源小团队，完整复现成本较高。

## 12.10 对 Boogu/Bg-harness 的启发

这是最应该直接指导 Bg-harness 的文章。

Bg-harness 下一步要学的不是“一步到位训练 agent”，而是先做 runtime 与日志：

```text
case
-> search evidence
-> visual verification
-> integrated prompt/carrier
-> Boogu output
-> process/input/final three-level judge
```

对应到文件结构：

```text
outputs/<run_id>/<case_id>/
  input.json
  search_plan.json
  raw_search_results.json
  verified_evidence.json
  carrier.html
  carrier.png
  boogu_prompt.txt
  output.png
  judge_agent_chain.json
  judge_generation_input.json
  judge_final_image.json
  trace.jsonl
```

这会让 Bg-harness 本身从实验脚本变成真正的 agentic harness。

---

# 13. 文章 10：SpatialGuard

## 13.1 基本信息

- 标题：SpatialGuard: Harness-Guided Verifiable Spatial Reasoning for Text-to-Image Generation
- 链接：[arXiv:2609.01582](https://arxiv.org/abs/2609.01582)
- 作者：Ziyun Qian 等
- 核心关键词：3D spatial layout、Layout Harness、Visual Realizer、Visual Alignment Critic、verifiable spatial reasoning

## 13.2 它解决什么问题？

空间关系一直是 T2I 的弱点。  
模型可能能画出所有物体，但位置错：

```text
red cube left of blue sphere
green cone behind both
```

可能变成：

- 物体都有，但左右反了。
- behind 变成 beside。
- 物体被遮挡不合理。
- 视角和深度关系不一致。

SpatialGuard 认为，复杂空间生成不是普通语义问题，而是几何问题。  
需要中间 layout state，而不是只靠 prompt。

## 13.3 方法核心

SpatialGuard 包括：

### Spatial Layout Architect

把 prompt 解析成空间 layout：

- objects
- spatial relations
- depth order
- occlusion
- scale
- visibility
- camera parameters

### Visual Realizer

把 layout 转成 visual conditions 和 candidate image。

### Visual Alignment Critic

检查 prompt、layout、image 是否一致。

### Layout Harness

维护可编辑 layout state。论文中强调 Layout Harness 组织：

- rule constraints
- tool invocation
- shared knowledge
- feedback loops

也就是说，空间关系不是一次性解析完就扔掉，而是在多轮中持续保存和修复。

## 13.4 创新点

1. 把空间生成从 implicit prompt following 变成显式 layout execution。
2. 让空间关系可验证、可修复。
3. 用 harness state 保存约束、工具、历史修复记录。
4. 把 planning、realization、validation、repair 打通。

## 13.5 为什么能发文章？

因为空间关系是生成模型长期短板，而且普通 prompt/rewrite 很难解决。  
SpatialGuard 的贡献是给空间任务建立一个稳定的中间状态和闭环。

## 13.6 可改进点

1. 完整 3D layout 构建成本高。
2. 自然语言到空间结构的解析可能出错。
3. Visual Alignment Critic 的可靠性决定闭环效果。
4. 对非几何类艺术 prompt，空间 harness 可能过重。

## 13.7 对 Boogu/Bg-harness 的启发

第一版不需要完整 3D。可以先做 2D layout harness：

```json
{
  "objects": [
    {"id": "red_cube", "bbox": [160, 420, 220, 220]},
    {"id": "blue_sphere", "bbox": [620, 420, 220, 220]},
    {"id": "green_cone", "bbox": [390, 300, 180, 180], "depth": "behind"}
  ],
  "relations": [
    ["red_cube", "left_of", "blue_sphere"],
    ["green_cone", "behind", "red_cube"],
    ["green_cone", "behind", "blue_sphere"]
  ]
}
```

然后输出：

```text
layout.json -> sketch.png -> Boogu output -> spatial judge
```

这对 Boogu hardcase 中的空间、计数、布局类 prompt 很有价值。

---

# 14. 综合比较：这些文章到底各自创新在哪里？

| 文章 | 最核心创新 | 不是在做什么 | 对 Boogu 的最直接落点 |
|---|---|---|---|
| GenRouter | 按 prompt 需求动态选择 workflow | 不是固定重 agent | workflow ablation + routing |
| GenClaw | 用代码/草图做可执行中间画布 | 不是普通 prompt engineering | sketch_layout |
| Generation Navigator | 根据当前图像状态选择 STOP/REFINE/REGENERATE | 不是盲目 retry | navigator trace |
| SearchGen | 学 generator 的 knowledge boundary | 不是无脑搜索 | search_auto + evidence filtering |
| TRACE-Bench | 用 operator 诊断多参考能力 | 不是按任务名打总分 | operators/checklist schema |
| MultiBanana | 系统构造多参考 hard factors | 不是只数 reference 数量 | hard_factors tagging |
| MICON-Bench | checkpoint-level 多图上下文评测 | 不是单一 CLIP/美学分 | judge.py |
| VisionDirector | 长目标设计任务的分阶段验证与回滚 | 不是一次性画海报 | goal decomposition |
| WeAgent-MMGenEdit | full-stack harness + data + benchmark + post-training | 不是单个工具 demo | Bg-harness 目标形态 |
| SpatialGuard | 空间 layout harness | 不是只让 prompt 更详细 | spatial layout verifier |

## 15. 我们有没有必要用这些技术？

答案：有必要，但不要全部默认用。

更合理的策略是：

```text
简单 prompt -> Direct / Turbo
普通描述但不够具体 -> Boogu existing rewrite
真实世界知识 -> SearchGen
空间/计数/版式 -> SketchGen / SpatialGuard
多约束复杂任务 -> VerifyGen
设计长目标 -> VisionDirector-style staged edits
多参考任务 -> TRACE/MultiBanana/MICON schema
```

换句话说，agentic 不是默认加重流程，而是：

```text
对 hardcase 按失败类型分配额外工具和计算。
```

这和 GenRouter 的思想一致。

## 16. Bg-harness 的建议路线

### Step 1：先接入 hardcase

把：

```text
Boogu-Image/outputs/hardcase/selected.jsonl
```

标准化成：

```text
data/cases_hardcase_selected.jsonl
```

每条至少包含：

```json
{
  "case_id": "...",
  "task_type": "...",
  "prompt": "...",
  "capability_tags": [],
  "operators": [],
  "hard_factors": [],
  "checklist": [],
  "raw": {}
}
```

### Step 2：测 Boogu 已有 rewriter 的边界

先跑：

```text
direct
rewrite_default
rewrite_ppt
```

问题是：

```text
Boogu 0.1 现有 agentic rewriter 到底在哪些 hardcase 上够用？
```

### Step 3：给 hardcase 打标签

参考这些论文，打：

```text
world_knowledge
spatial
counting
dense_text
poster_or_ppt
multi_reference
attribute_binding
identity
layout
```

### Step 4：真正做 SearchGen

SearchGen 要接真实 web/image search，并保存 evidence。

不要把 SearchGen 做成普通 rewrite。

### Step 5：做 SketchGen / SpatialGuard-lite

先做 2D layout JSON 和 SVG sketch。

### Step 6：做 checkpoint judge

参考 MICON 和 WeAgent 的三层评价：

- agent chain 是否正确？
- generation input 是否充分？
- final image 是否达标？

### Step 7：再做 Navigator

有了 judge 后，再做：

```text
STOP / REFINE / REGENERATE
```

## 17. 汇报时可以用的一句话总结

> 这批文章共同说明：图像生成的瓶颈正在从“像素质量”转向“复杂目标完成”。Boogu-Image-0.1 已经具备轻量 agentic rewriter，但 Bg-harness 的价值在于测清它的边界，并为不同 hardcase 引入外部搜索、可执行布局、checkpoint 验证和多轮决策。最终目标不是让所有 prompt 都走重 agent，而是建立 GenRouter-style 的证据：什么任务用什么 workflow 最划算、最稳定。

## 18. Sources

- GenRouter: [https://arxiv.org/abs/2608.16721](https://arxiv.org/abs/2608.16721)
- GenClaw: [https://arxiv.org/abs/2605.30248](https://arxiv.org/abs/2605.30248)
- Generation Navigator: [https://arxiv.org/abs/2605.17969](https://arxiv.org/abs/2605.17969)
- SearchGen: [https://arxiv.org/abs/2607.05382](https://arxiv.org/abs/2607.05382)
- TRACE-Bench: [https://arxiv.org/abs/2608.16765](https://arxiv.org/abs/2608.16765)
- MultiBanana: [https://arxiv.org/abs/2511.22989](https://arxiv.org/abs/2511.22989)
- MICON-Bench: [https://arxiv.org/abs/2602.19497](https://arxiv.org/abs/2602.19497)
- VisionDirector: [https://arxiv.org/abs/2512.19243](https://arxiv.org/abs/2512.19243)
- WeAgent-MMGenEdit: [https://arxiv.org/abs/2609.05171](https://arxiv.org/abs/2609.05171)
- SpatialGuard: [https://arxiv.org/abs/2609.01582](https://arxiv.org/abs/2609.01582)
- Boogu-Image: [https://github.com/boogu-project/Boogu-Image](https://github.com/boogu-project/Boogu-Image)

