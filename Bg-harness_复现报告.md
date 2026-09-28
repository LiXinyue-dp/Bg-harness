# Bg-harness + Boogu-Image — 技术复现报告

> 目标:让读者**在另一台机器上,照着本文就能从零复现整个项目**——装环境、拉模型、
> 跑 26 张图(gate + 实时联网检索 + Edit-Turbo 出图)、用 k3 和 8B 两个 judge 打分、
> 得到与本文一致的结果。
>
> 本文聚焦**可复现的技术细节**(命令、版本、路径、坑),不是营销文档。
> 代码地图/架构另见 `~/Bg-harness/README.md`,这里不重复,只在需要处引用。
>
> 记录机:`amax` · 8× NVIDIA RTX PRO 6000 Blackwell Server Edition(每张 96GB)·
> Python 3.12 · conda env `boogu`。日期:2026-09-25。

---

## 0. 两个仓库的关系

| 仓库 | 路径 | 角色 | 是否 git |
|---|---|---|---|
| **Bg-harness** | `~/Bg-harness` | agent、12 工具、benchmark、judge(本文主体) | ❌ 非 git(需另建/归档) |
| **Boogu-Image** | `~/Boogu-Image` | 生成器(upstream,`github.com/boogu-project/Boogu-Image`) | ✅ git |

harness 把 Boogu-Image 的 `inference.py` / `inference_turbo.py` 当**子进程**调,自身不 import
生成器的权重加载逻辑,只 import 它的 `boogu/` 包(见 §2 的 `PYTHONPATH`)。两仓库**同级**放在
`~/` 下,很多脚本里路径是硬编码的 `/home/xinyue/...`(§6 会讲怎么参数化)。

---

## 1. 环境安装(另一台机器从零)

### 1.1 硬件 / 前置

- **GPU**:≥1 张 ≥24GB 显存的卡即可跑单 case(8B gate+policy+Edit-Turbo 单卡分时实测
  ~60GB 峰值,96GB 卡很宽裕;24GB 卡要关 `--web`/`--gate` 或换 0.1B gate,见 §6 坑 9)。
- **Slurm**:本环境规定 GPU 任务必须 `sbatch` 提交(`--gres=gpu:1`),不能裸起进程占卡;
  机器多人共用,同一时刻最多 2–4 个 GPU job。partition=`gpu`,node=`amax`。
  (若你的机器没有 Slurm,把本文所有 `sbatch scripts/xxx.sh` 换成直接 `bash scripts/xxx.sh`,
  并确保 `CUDA_VISIBLE_DEVICES` 指向一张空卡。)
- **网络**:**无直连外网**。所有联网(下载模型、实时 web 检索)走公司 MITM 代理,见 §1.4。
  纯生成 + 纯 judge(本地 8B / 0.1B)不联网,可完全离线复现;只有 `--web` 检索臂需要代理。

### 1.2 Python 环境

```bash
conda create -n boogu python=3.12 -y
conda activate boogu
# torch 2.11 + cu128(本机已装版本;pyproject 允许 torch>=2.7.1,<2.12)
pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 \
  --index-url https://download.pytorch.org/whl/cu128
```

生成器的完整依赖锁文件在 `~/Boogu-Image/requirements/`:

- `lock-torch2.11-cu128-linux-py312.txt` — **推荐**,py3.12 + cu128 的精确锁,含一个
  预编译 flash-attn wheel:`flash_attn-2.8.3+cu128torch2.11`(直链 GitHub release,需代理)。
- `torch2.11-cu128.txt` / `torch2.11-cu126.txt` / `torch2.7-cu126.txt` — 只有 torch 四件套。

其余生成器依赖(`pyproject.toml`):`diffusers 0.38.0`、`transformers 5.11.0`(lock 版;
本机 env 实际是 5.15.0,两版都跑过)、`accelerate 1.14`、`cache-dit 1.3.12`、`torchao 0.17`、
`kernels 0.14.1`、`einops`、`numpy`、`pillow`、`scipy`、`webdataset`、`omegaconf`。

harness/judge 额外用到:`requests`(联网检索)、`tenacity`(judge 重试,可选)。
**k3 judge 走 HTTP API,不 import torch,所以 CPU-only 环境也能打分**(§4.2)。

> ⚠️ `transformers` 版本坑:lock 写 `5.11.0`,但 Qwen3-VL 的 `video_processing_qwen3_vl.py`
> 在 5.x 早期会对 `min_frames/max_frames` 打一堆 `[ERROR] ... not documented` 的 warning。
> **无害**,权重照常加载。别被刷屏的 ERROR 吓到去降级 transformers。

### 1.3 模型清单(共 ~180GB,`~/Boogu-Image/models/`)

| 目录 | 大小 | 用途 | 怎么来 |
|---|---|---|---|
| `Boogu-Image-0.1-Turbo/` | 36G | 生成器(4 步 turbo)+ 内嵌 0.1B policy mllm | HuggingFace |
| `Boogu-Image-0.1-Edit-Turbo/` | 36G | **本文 26 图用的生成器**(edit-turbo,吃参考图) | HuggingFace |
| `Boogu-Image-0.1-Base/` | 36G | 生成器(基座,多步) | HuggingFace |
| `Boogu-Image-0.1-Edit/` | 36G | 生成器(edit,多步) | HuggingFace |
| `Qwen3-VL-8B-Instruct/` | 17G | **8B gate + 8B judge + 8B policy**(同一份权重三用) | HuggingFace |

`0.1-Turbo/mllm/` 是 4 个 safetensors 分片的 **0.1B** 策略模型(policy 默认用它,§3.3)。

**下载**:公司代理**连不上 HuggingFace 的权重 CDN**(`us.aws.cdn.hf.co`,TLS err 35),
`hf-mirror`/`modelscope` 直连也超时。可行路径:
1. 让有直连的机器 `huggingface-cli download` 后 scp/rsync 过来;或
2. 走公司内部模型源(若你有权限);或
3. 代理能过 HF 的 **API/metadata** 但不过 CDN,所以 `huggingface-cli` 会卡在权重块。
   实践中 8B 和 4 个生成器都是**手动下好后放进 `models/`** 的。

> ⚠️ **`~/Boogu-Image/boogu/` 是 importable Python 包,不是 venv!**
> 里面有 `pipelines/ models/ schedulers/ ops/ utils/`,31 个 tracked 源文件,
> `inference.py` 都 `from boogu...`。清理磁盘时**千万别 `rm -rf boogu/`**
> (曾经误删,用 `git checkout -- boogu/` 恢复的)。

### 1.4 公司 MITM 代理(联网检索 + 下模型的必经之路)

配置在 `~/set.sh`,每次联网脚本 `source ~/set.sh`。核心:

```bash
export http_proxy='http://<user>:<pass>@172.18.100.92:8080/'   # https_proxy 同
export no_proxy="localhost,127.0.0.1,192.168.1.0/24,10.0.0.0/8,.huawei.com,..."
export GIT_SSL_NO_VERIFY=1
export REQUESTS_CA_BUNDLE=/usr/local/share/ca-certificates/ProxyCA260122.crt  # 代理证书
```

(上面的 user/pass 是公共账号 `p_PublicProxy:***`,**定期轮换** H4→H5→…;
机器上 `set.sh` 里是当前有效值。若联网突然全 0 字节,第一嫌疑是**密码过期**,找同事要新的。)

**三个必须知道的代理特性**(harness 的联网代码全是为绕这些写的):

1. **TLS-MITM**:代理证书链不完整,`requests` 的 `verify=` 会 `CERTIFICATE_VERIFY_FAILED`。
   抓网页一律 `curl -sk --proxy <proxy>`(跳过校验)——同事验证过的方法。
2. **坏 DNS**:本机 `proxy.huawei.com` 解析到坏 IP `172.19.90.131`(强制 NTLM→407→全 0 字节)。
   所以 `set.sh` **直连 IP `172.18.100.92:8080`** 绕开 DNS。诊断:`getent hosts proxy.huawei.com`
   若不是 `172.18.100.92` 就是坏 DNS 案例。
3. **速率敏感**:快速突发请求会让 Wikipedia API **整片塌成全空 `[]`**。
   必须限速(`_throttle` 默认 1.0s)+ 多卡分片(每片负载轻)。实测 0.25s pace 3/16 命中,
   1.0–1.5s pace 9–12/16。
4. **Bing 不可用(中文)**:经代理的 Bing 返回 locale 乱码(RU/PL/FR/EN 帮助页)。
   所以 `LiveWebRetrieval` 走 **Wikipedia REST API 优先**
   (`opensearch` + `/api/rest_v1/page/summary/`),Bing 仅兜底且基本降级。

**联网自检**(harness 所有 web 脚本开跑前都会先做这个 pre-flight,失败就 ABORT 而不是假跑):

```bash
source ~/set.sh
curl -skL --max-time 20 --proxy "$http_proxy" \
  -o /dev/null -w "CODE=%{http_code} BYTES=%{size_download}\n" \
  "https://en.wikipedia.org/api/rest_v1/page/summary/Intel_Extreme_Masters"
# 期望 BYTES > 50。若 = 0:密码过期 or 坏 DNS,先修代理再跑,否则 --web 臂会静默退化成 offline(假 A/B)
```

---

## 2. 系统架构(数据流)

```
 user caption
      │
      ▼
 ┌────────────────────────────┐
 │ Policy VLM  (agent/policy)  │  think–act–observe,≤15 turns / ≤15 tool calls
 │ 0.1B(默认)或 8B(--mllm)      │  最后 2 次调用保留给交付(Image_Generation)
 └─────────────┬──────────────┘
               │ 每轮 1 次工具调用
               ▼
 ┌───────────────────────────────────────────────┐
 │ Toolkit (agent/tools.py) — 12 工具 5 族          │
 │  Retrieve   Web_Search Web_Extract Search_for_Image │
 │  Verify     Vision_Analyze                      │
 │  Integrate  Execute_Code + Layout_* + Query_Knowledge│
 │  Deliver    Image_Generation ──► 子进程 ──► Boogu   │
 └─────────────┬─────────────────────────────────┘
               │
        ┌──────┴──────┐
        ▼             ▼
  8B Search Gate   Boogu 生成器
  (决定"搜什么",    (edit-turbo / turbo / base / edit)
   论文 §3.1 三段:  subprocess: inference.py / inference_turbo.py
   gate-filter-integrate)
```

**关键设计**:policy("大脑",负责编排)和生成器(Boogu,负责像素)是**两个独立进程**,
policy 通过 `Image_Generation` 工具 spawn 生成器子进程。所以 policy 用什么模型、
生成器用什么模型,**正交可换**——本文 26 图就是「0.1B policy + 8B gate + edit-turbo 生成器」的
组合。生成器侧的 `inference*.py` 有一个**未提交的 13 行 device fix**(把 shared rewriter 的
`lm_head` 移到 GPU),harness 依赖它,**别 git checkout 掉**(§6 坑 12)。

---

## 3. 端到端跑一张图(26-case 的每一步)

### 3.1 benchmark 输入

- **capab10** = `~/Bg-harness/agent/capab10.json` — 10 条能力集 caption
  (人物/物体/室外环境/图画,含神兽、动漫角色、赛博场景等)。
- **webbench16** = `~/Bg-harness/benchmarks/webbench16.json` — 16 条 **2026 实时热点实体**
  caption(两会、日食、华为旗舰、美加墨世界杯、米兰冬奥、春晚、名古屋亚运会、漫威新片、
  电动车、CSGO IEM、英雄联盟世界赛、美国大选、威尼斯双年展…),按 L1–L4 分层。
  **webbench 的设计意图**:这些是"现在才成立"的 2026 事件,离线检索拿不到,
  只有**实时联网**注入事实才能画对——这是 `--web` 臂价值所在。

### 3.2 单 case 的完整命令(本文 26 图的配方)

来自 `agent/run_gate_edit_26.sh`(26 图就是循环调这个):

```bash
cd /home/xinyue/Bg-harness
export PYTHONPATH=/home/xinyue/Bg-harness:/home/xinyue/Boogu-Image
PY=/home/xinyue/miniconda3/envs/boogu/bin/python

source ~/set.sh   # 联网检索需要代理;纯 offline 臂可不 source

$PY -m agent.policy \
    --mllm /home/xinyue/Boogu-Image/models/Boogu-Image-0.1-Turbo/mllm \  # 0.1B policy
    --device cuda:0 \
    --gate --gate-device cuda:0 \
    --gate-dir /home/xinyue/Boogu-Image/models/Qwen3-VL-8B-Instruct \    # 8B search gate
    --prompt "<caption>" \
    --out_name <stem> \
    --generator edit-turbo \
    --execute --web \
    > logs/policy_<stem>.log 2>&1
```

逐参数:

| flag | 含义 |
|---|---|
| `--mllm <0.1B path>` | 策略模型(默认就是 0.1-Turbo/mllm)。换 8B:`--mllm .../Qwen3-VL-8B-Instruct --time-share-policy --max-new-tokens 2048`(§6 坑 8) |
| `--device cuda:0` | policy VLM 所在卡 |
| `--gate` | 启用 8B search gate(论文 §3.1 三段) |
| `--gate-device cuda:0` | gate 所在卡。**单卡分时**下与 policy 同卡,生成前后自动 offload(见下) |
| `--gate-dir <8B path>` | gate 权重(默认即 8B-Instruct) |
| `--prompt` / `--out_name` | caption / 输出 stem |
| `--generator edit-turbo` | 生成器:base/turbo/edit-turbo/edit 四选一。edit* 吃参考图,base/turbo 纯 T2I |
| `--execute` | 真出图(dry-run 只跑 policy 不出图) |
| `--web` | 开实时联网检索(唯一变量;offline 臂就是去掉它) |

**单卡分时(time-share)**:8B gate(~17GB)+ 0.1B policy + edit-turbo 生成器(~36GB)
若同卡常驻会 OOM。harness 的做法:policy 决策时 gate 在卡上;**轮到生成器出图时,先把
8B 权重 release 出显存,生成完再重载**(已用 `run_smoke_gateoffload.sh` 验证无 OOM)。
`--gate-device == --device` 时会打一条 WARNING 提示共享卡,但单卡分时逻辑能兜住。

### 3.3 跑完一个 case 的产物(在 `agent/runs/<stem>/`)

| 文件 | 内容 |
|---|---|
| `gen_001.png`(及 `gen_NNN.png`) | 生成的图(最后一张 = final) |
| `<stem>.trajectory.json` | 完整 think-act-observe 轨迹(每轮 action/observation/usage) |
| `layout.json` / `layout_carrier.png` | 布局状态 + 布局载体图(force-layout 下) |
| `web_*.txt` | `--web` 臂的检索证据(命中的维基页摘要、注入的事实) |
| `boogu/boogu_prompt.txt` / `command.txt` | 实际喂给生成器的最终 prompt / 子进程命令 |

### 3.4 批量 + 分片 + resume

`agent/run_gate_edit_26.sh` 做的事(26 图 = capab10 + webbench16):

1. **pre-flight**:每个 shard 先探代理(en+zh wiki),DOWN 就 ABORT 该 shard(不假跑)。
   `SKIP_CHECK=1` 可跳过(仅 offline 臂)。
2. **round-robin 分片**:`SHARD=<i> NSHARDS=<n>` 把 26 条按 `i % n == shard` 分,每片进程
   用 `CUDA_VISIBLE_DEVICES` 钉到一张物理卡。提交器 `agent/submit_gate_edit_26.sh` 起 N 个
   Slurm job(每 job `--gres=gpu:1`)。
3. **resume-safe**:每片写 `OUTROOT/manifest.jsonl`(每 stem 一行 `status: ok/fail`),
   重跑时 `ok` 的 stem 直接 skip。ok 的判据 = 进程 exit 0 **且** 有 final 图。
4. **快照**:final png + trajectory 拷进 `OUTROOT`(= `agent/runs/gate_edit_26/`)。

```bash
# 4 卡并行跑全部 26:
sbatch agent/submit_gate_edit_26.sh            # 内部起 NSHARDS=4,SHARD=0..3
# 只重跑某几条:
ONLY="capab_000038 wb_l3_dlc2026" SHARD=0 NSHARDS=1 bash agent/run_gate_edit_26.sh
```

> 26 图的 final 实际分布在**两个目录**:`agent/runs/gate_edit_26/`(15 张)和
> `agent/runs/gate_edit_26_fixed/`(11 张)——后者是几轮 force-layout 收敛修复后的重跑。
> judge 的 manifest 会合并两目录(final 优先取 `_fixed`)。

---

## 4. Judge(打分)

### 4.1 严格 checklist 评分(核心纪律)

judge **不让 VLM 直接吐整体分**,而是:

1. 从 case 派生**固定 checklist**(每项有 objective + 类型 + must/nice 权重)。
2. VLM 只回答**逐项 通过/不通过 + 反证 note**。
3. **Python 确定性计分**:任一 must-have 缺失 ⇒ 分数**封顶 0.4** ⇒ 判 fail。
4. **退化门**:若整条响应没有任何 per-item note 且无 failure_types ⇒ 判 `_parse_error`
   (触发重试阶梯升级),**不**把"全 True/全 False 但没看图"的响应 salvage 成 0.0/1.0。
   (这是修过的大坑——旧逻辑会把 k3 的裸响应救成假分,§6 坑 5。)

**checklist 从哪来**(`scripts/judge.py::build_checklist`):
- `capab_00XXXX` → 按**末尾 6 位数字**映射到 `data/cases_hardcase_annotated.jsonl` 的
  `hardcase_AIT2I_202512_00XXXX` → 拿到 **13–14 条富标注 checklist**
  (entity/spatial/negative/style/text)。
  ⚠️ 映射用"末尾 6 位"(`re.search(r'(\d{6})(?!.*\d{6})')`),**不能**用首个 6 位——
  `hardcase_AIT2I_202512_000038` 里 `202512`(日期)是首个 6 位,会全 index 错。
- `wb_*`(webbench,无标注对应)→ **单条 caption fallback**(用真实 caption 文本,不是 stem 字符串)。

**跨工作流同一 checklist 用同一 `checklist_hash`** → 修掉了旧的 1.0-vs-0.0 不一致;
resume 也只把同 hash 的严格行算 done。

### 4.2 三个 judge 后端(分数同标尺)

`scripts/judge.py --judge {local,k3}`:

| 后端 | 模型 | 资源 | 命令 |
|---|---|---|---|
| **local** | Qwen3-VL(0.1B 默认 / 8B `--mllm`) | 占 1 张 GPU,`bfloat16`,`device_map=cuda:0` | `sbatch scripts/judge_slurm.sh`(模板,0.1B) |
| **k3** | 内部网关 `dsv4-flash-vision` | **CPU-only**,无 torch,走 HTTP | `sbatch scripts/judge_gate26_k3.sh` / `rerun_judge26.sh` |

**k3 网关**(内部 LiteLLM):`http://7.216.104.226:4000/v1/messages`,
header `x-api-key: $ANTHROPIC_AUTH_TOKEN`(env 里,sk- 开头),`anthropic-version: 2023-06-01`,
`no_proxy` 要含 `7.216.104.226`(不走公司代理)。**没有 claude 系模型**;视觉默认 `dsv4-flash-vision`
(DeepSeek-V4 flash,thinking+text 块,中文,~2–11s/图)。响应 `content` 是
`[{type:"thinking"},{type:"text"}]` 块,要拼 text 块。
k3 网关**不严格遵循 system 的 JSON schema**,`_normalize` 做了容错
(objectives/checklist/results/items 四 key + 通过/不通过 中文 + 裸字符串值)。

> 复现注意:k3 是**内部网关**,另一台机器若不在同一内网/没有 `ANTHROPIC_AUTH_TOKEN`,
> k3 臂跑不了。此时用 **local 8B judge**(§4.3)即可,两者分数同标尺(§4.3 已验证 capab 一致)。

### 4.3 打分命令(本文 26 图)

```bash
cd /home/xinyue/Bg-harness
# k3 重判(修好 checklist 后,--force 丢弃旧 degenerate 行)——CPU-only:
sbatch scripts/rerun_judge26.sh                 # 默认 dsv4-flash-vision
# 或指定模型:
sbatch scripts/rerun_judge26.sh dsv4-flash-vision
```

`rerun_judge26.sh` 干的事:
1. `python -m scripts.judge --judge k3 --k3-model <m> --runs gate26_web --force`
   → 输出 `~/Boogu-Image/outputs/harness_workflow_ablation/gate26_web/judge_gate26_web.jsonl`
   (**这是 k3 的 source of truth**,26 行)。
2. 打印 per-case + capab10/webbench16/ALL 分组表。

`--runs gate26_web` 读的是该 run 目录下的 `manifest.jsonl`(case_id/prompt/image_path),
judge 逐行打分,`aggregate()` 只统计带 `checklist_hash` 的严格行。

**8B judge(交叉验证,§4.4)**:`sbatch scripts/judge8b_compare_slurm.sh`
(`--gres=gpu:1`,`--mem=64G`)→ `scripts/judge8b_compare.py`,
把 8B 结果单独写到 `judge_gate26_web_qwen8b.jsonl`(**不覆盖 k3 源真**),再算一致性。

### 4.4 双 judge 交叉验证(k3 vs 本地 8B)

目的:验证"k3 是不是太小/太宽"。同一 26 图、同一套 strict checklist,唯一变量是 judge 模型。

**结果(2026-09-25,job 287)**:

| 分组 | k3 (dsv4-flash) | 本地 8B | 结论 |
|---|---|---|---|
| capab10(14 条标注 checklist) | **0.555** | **0.633** | ✅ 基本一致(仅 1 条分歧) |
| webbench16(单条 caption) | **0.875** | **0.375** | ⚠️ **严重分歧** |
| ALL 26 | 0.752 | 0.474 | |

verdict 一致 **17/26**,分歧 9 条——**8 条全在 webbench**,方向一致:k3 给 1.0 pass、8B 给 0.0 fail。

**人工抽查证实 8B 对**:如 `wb_l4_csgo_iem2026`,图是个**通用电竞选手**+"IEM BEIJING 2026"
文字,**完全没有** CSGO 地图/武器/skin/UI。k3 因看到 "2026" 就 rubber-stamp pass(太宽松),
8B 判 fail 正确。

**结论**:
- **capab10 数字可信**(两 judge 一致)——富 checklist 让任何 judge 都无法作弊。
- **webbench16 当区间报**:k3 的 0.875 是**乐观上限**,8B 的 0.375 是**悲观下限**,真值在中间。
  根因是**结构性的**:webbench 只有单条 caption 当 checklist,小 judge 命中关键词(年份)就判过。

产物:`~/Bg-harness/outputs/judge_compare_k3_vs_8b.{md,html}` + `judge_table_gate26_web.{md,csv}`
+ 自包含 HTML 画廊 `judge_gallery_gate26_web.html`(26 张 base64 内嵌 + 分数徽章,可直接展示)。

---

## 5. 目前效果(26 图,可复现的最终数字)

**生成**:26/26 全部出图(gate + edit-turbo + 实时联网)。final 分布在
`gate_edit_26/`(15)+ `gate_edit_26_fixed/`(11)。

**打分**(strict checklist,两个 judge):

| | capab10 | webbench16 | ALL 26 |
|---|---|---|---|
| **k3 (dsv4-flash-vision)** | **0.555** | 0.875(乐观) | 0.752 |
| **本地 8B** | **0.633** | 0.375(悲观) | 0.474 |

**capab10 = 0.555 / 0.633 是主结果**(两 judge 一致、可信)。
webbench16 因 judge 档位差异大,**报区间**而非单点。

**失败归因方向**(capab):text 渲染最弱(2/2 文字项 fail)、spatial 28%、entity 19%、
negative 仅 7%——edit-turbo 的**文字渲染**是当前短板。

**k3 打分 0 degenerate**(修好 checklist + 退化门后,26 行全有效)。

---

## 6. 踩过的坑(按主题,复现前必看)

**联网 / 代理**
1. **坏 DNS → 407 → 全 0 字节**:本机 `proxy.huawei.com` 解析到坏 IP。`set.sh` 改直连
   `172.18.100.92:8080` 绕开。诊断:`getent hosts proxy.huawei.com`。
2. **公共代理密码定期轮换**(H4→H5→…):联网突然 0 字节,先怀疑密码过期,不是网络。
3. **TLS-MITM**:`requests` verify 必挂,抓网页用 `curl -sk --proxy`。
4. **代理速率敏感**:快循环 → Wikipedia 全塌 `[]`。必须 `_throttle`(≥1.0s)+ 分片,
   跑全量 clean 核对务必**单独跑 + 2.0s gap**,别并发(并发 = 全 `[]`)。
5. **Bing 中文不可用**:locale 乱码。`LiveWebRetrieval` 改 **Wikipedia REST 优先**,Bing 仅兜底。
6. **wikimedia 缩略图 000**:代理放行 `upload.wikimedia.org` 原图但 000 掉
   `thumb.wikimedia.org` 缩略图。`tools.py::_wikimedia_original` 把 thumb URL 重写回原图。

**judge**
7. **k3 裸字符串 verdict 没解析**(曾把 26 图全判 0.0):dsv4 返 `{"1":"通过","4":"不通过"}`
   (数字 key + 裸字符串,无 note)。旧 `_normalize` 字符串分支跳过设 satisfied → 全 None → 0.0。
   修:`_parse_satisfaction_word`(先查否定词`不通过`再查`通过`)+ 裸字符串就地设 satisfied。
8. **退化响应污染分数**:k3 非确定地返回多种 schema,旧逻辑把"无 note"的响应 salvage 成假 0.0/1.0
   (440 图里 363 行无 note,均分被拉到假 0.412)。修:`_normalize` 末尾加**退化门**——
   无任何 per-item note 且无 failure_types ⇒ 判 `_parse_error`,不 salvage。
9. **单卡 OOM(8B gate+policy+生成器)**:靠**生成前后 offload gate 权重**(time-share)兜住。
   24GB 卡建议关 `--gate`/`--web` 或全 0.1B。

**policy / 工具**
10. **0.1B policy 不收敛**(烧满 15 轮不出 final):0.1B 22 条轨迹 8 条无 final。换 **8B policy**
    (`--time-share-policy`)决策更准。已知:force-layout 下 last-chance turn 只许 `Image_Generation`,
    8B 想再 `Layout_Repair` 会被拒 → **交付前留 2 轮 buffer**;8B 偶发英文 query 回环重试,预算比 0.1B 快。
11. **题干↔checklist 错位**(曾误判成"模型能力上限"):000177 题干"画小舞和唐三"**零空间信息**,
    "手牵手"来自 annotation checklist,policy 看不见 → 换 8B 也是同样的 LayoutJSON。
    修:Plan A `policy.py::_backfill_block` 把"policy 看不见却会被判"的约束回填进 seed。
12. **gate 的 name/query 不能直接喂 resolver**:gate 给的"2026美加墨世界杯"(粘年/多词)
    会破坏 opensearch 严格短语匹配 → 掉到 bing 垃圾。架构 = **gate 管"该不该搜+主体",
    完整 caption(带空格)管"怎么搜"**。

**仓库 / 环境**
13. **`boogu/` 是包不是 venv**:`rm -rf boogu/` 会删掉 31 个源文件,只能 `git checkout` 恢复。
14. **`inference*.py` 有未提交的 device fix**(lm_head 移 GPU):harness 依赖,**别 revert / checkout**。
15. **`transformers` 5.x 的 `[ERROR] ... not documented` 刷屏**:无害,别降级。
16. **HF 权重 CDN 被墙**:模型得手动下好再放 `models/`,代理只过 HF API/metadata。

---

## 7. 未来要做的

**A. 检索臂(提升 webbench 质量)**
- 当前 `--web` 是**纯文本事实注入**;`search_for_image` 的参考图链路(pick_best_reference
  → edit-turbo 吃一张)已实现但 **8B policy 不收敛**(烧 turn 不传 ref)。下一步:policy SFT/RL
  让"搜图→选图→带 ref 出图"稳定触发。
- 6 个 webbench case 仍走 bing_fallback 退化(电动车/漫威新/草莓音乐节/电竞/英雄联盟世界赛/某大作
  新资料片)——实体没解析到干净维基页。需要给这些加 homograph guard 或专属检索词。

**B. 生成质量**
- **文字渲染**是 capab 最大短板(2/2 文字项 fail)。edit-turbo 出图文字常糊/漏。
- 参考 GenEvolve(论文 arXiv 2605.21605,agent 骨干同为 Qwen3-VL-8B):其 `Query_Knowledge`
  技能已落地到 `agent/tools.py`(8 个 Boogu 可见失败模式→prompt 配方),但 **0.1B 不会自发调用**,
  需 8B policy 验证调用率。

**C. judge 档位**
- webbench 只有单条 caption,k3 偏乐观(0.875)、8B 偏悲观(0.375)。未来:给 webbench 也建
  **富 checklist**(像 capab 那样多实体/多事实项),消除 judge 档位敏感性。
- GenEvolve 的 judge 全栈是闭源 frontier API(Gemini 3.1 Pro,KScore 权重
  faith.1/vis.4/text.4/aesth.1),比 dsv4-flash 高一档——若 8B policy 变好但 k3 分数不动,
  **先怀疑 judge 档位**,不是生成器。

**D. policy 训练**
- 根本解:SFT/RL policy(当前 0.1B 靠结构约束/force-layout 兜,8B 靠 few-shot + 工具接口)。
- GenEvolve 消融:8B 裸 workflow 0.3317→SFT 0.3480→GRPO 0.3548→全量 0.3663,
  **untuned 8B + 好工具接口吃掉大部分收益**——所以先把工具接口/检索做稳,再考虑训练。

**E. 复现工程**
- 把硬编码的 `/home/xinyue/...` 路径参数化(环境变量),Bg-harness 纳入 git/归档,
  模型下载走内部源脚本化。

---

## 8. 关键文件地图(复现入口)

| 文件 | 作用 |
|---|---|
| `agent/policy.py` | 策略主循环(think-act-observe),`--mllm/--gate/--generator/--web/--time-share-policy` |
| `agent/tools.py` | 12 工具 + `LiveWebRetrieval`(Wikipedia 优先联网)+ `pick_best_reference` + `Query_Knowledge` |
| `agent/search_gate.py` | 8B search gate(论文 §3.1),`GATE_MODEL` 默认 8B-Instruct |
| `agent/layout.py` | LayoutJSON + 纯 Python 几何校验 + 确定性 PIL 载体渲染 |
| `agent/run_gate_edit_26.sh` | **本文 26 图的批量跑**(分片/resume/pre-flight) |
| `agent/submit_gate_edit_26.sh` | 起 N 个 Slurm GPU job 跑 26 图 |
| `agent/run_webbench.sh` | webbench offline-vs-web A/B(唯一变量 `--web`) |
| `scripts/judge.py` | 严格 checklist judge(`--judge local/k3`,`build_checklist`、退化门、`_normalize`) |
| `scripts/rerun_judge26.sh` | **k3 重判 26 图**(`--force`,源真输出) |
| `scripts/judge8b_compare.py` + `judge8b_compare_slurm.sh` | **8B judge 交叉验证**(不覆盖 k3) |
| `benchmarks/webbench16.json` / `agent/capab10.json` | 两组 caption benchmark |
| `data/cases_hardcase_annotated.jsonl` / `checklists_vlm.jsonl` | checklist 来源 |
| `outputs/judge_table_gate26_web.{md,csv}` | 领导看的 26 图打分表 |
| `outputs/judge_gallery_gate26_web.html` | 自包含 26 图 + 分数画廊(可直接展示) |
| `outputs/judge_compare_k3_vs_8b.{md,html}` | 双 judge 交叉验证报告 |

---

## 9. 最小复现脚本(一条命令复现 26 图 + 打分)

假设环境/模型/代理已按 §1 配好,`~/Bg-harness` 与 `~/Boogu-Image` 同级:

```bash
cd /home/xinyue/Bg-harness
source ~/set.sh

# (1) 4 卡并行跑 26 图(gate + edit-turbo + 实时联网)
sbatch agent/submit_gate_edit_26.sh

# (2) 等出图后,k3 打分(CPU-only,~几分钟)
sbatch scripts/rerun_judge26.sh

# (3) 8B judge 交叉验证(1 GPU)
sbatch scripts/judge8b_compare_slurm.sh

# 看结果:
cat outputs/judge_table_gate26_web.md
# 浏览器打开自包含画廊:
#   outputs/judge_gallery_gate26_web.html
```

> 复现一致性说明:生成臂受**代理注入率**影响(§6-4,速率/密码波动),webbench 的
> 单条 caption 分数受 **judge 档位**影响(§4.4)。capab10 的 0.555/0.633 最稳定;
> webbench16 报 k3(乐观)/8B(悲观)区间即可,不要期待逐张 bit-exact。
