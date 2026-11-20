# 复变函数 A 习题与试卷制作工具

本轮制作的是有来源、经独立数学核验的基础版与完整版初版。教材正文使用现有读本，练习按具体正文段落折叠插入，独立习题集与试卷使用同一题目数据。基础版和完整版不是难度标签；必会、可能考、罕见是题目优先级，每级内按难度排列。

## 当前可以重跑的部分

1. 已审题库的选择、教师优先级覆盖、纸卷题位配置、HTML交付与PDF输出。
2. 新的文本题：在来源题面和元数据核对之后，通过现有配置模型执行作者、隐藏作者答案的独立求解、最终对照，保存真实响应、尝试、缓存和审核收据。
3. 教师新增题可以超出通用题库，但必须有独立核验与最终内容摘要，不能自行把一个布尔值改成通过。新题还要对应存在的教学单元和精确正文锚点。

本轮没有完成任意新PDF一键提取、任意带图题全自动求解或真实教学增益/未来押题率验证。文字题生产工具发现必须提供的题图时会阻断，避免把图丢掉继续求解。实际作业题号未提供，教材练习只标“作业候选”。

## 环境

Python 3.12；主要应用依赖见 requirements-python.txt。requirements.lock 是历史来源JSON，不是pip文本依赖清单。浏览器检查/打印用项目 package-lock.json 安装的 Playwright，以及实际可运行的浏览器。可通过 AIFORSTUDY_BROWSER_EXECUTABLE 指定浏览器可执行文件；默认尝试 Edge。

读取与重建已交付题库不需要模型调用。新题生成使用 config/models.json 中已有的角色：lesson负责作者，text_judge负责独立求解与对照。默认账户路由依赖本机已授权的Harness应用；其他电脑应配置自己的受支持API或本地路由。源码不包含个人账号或密钥。相关接入说明见 account-runtime.md。

## 从现有交付复现

在源码根目录，执行以下命令，将路径换成实际位置：

```sh
python tools/rebuild_practice_delivery.py --delivery /path/to/交付目录 --out /path/to/新的输出目录
node tools/check_practice_delivery.cjs /path/to/新的输出目录 /path/to/检查报告
node tools/print_practice_pdf.cjs /path/to/新的输出目录
```

输出目录必须是新的空目录，并且不能与输入教材存在包含关系。历史源路径作为来源记录保留；重新构建根据当前交付中的文件、哈希和审核证据寻找输入，不要求另一台电脑存在原作者的用户目录。

## 教师定制

复制 examples/practice/teacher-profile.example.json，填写自己的实际依据。include_ids表示额外纳入，exclude_ids表示排除，topics表示选取指定专题；priority_overrides可改优先级但必须写原因。该操作不改数学内容，也不改公共原版。

```sh
python tools/rebuild_practice_delivery.py --delivery /path/to/交付目录 --out /path/to/定制输出 --profile /path/to/teacher.json
```

若增加公共题库之外的题，增加 --additions 指向已审核题目JSON。新增题的evidence_files相对于新增题JSON所在文件夹；发布时会读取实际证据、校验题目ID、最终内容摘要及独立解答，而不仅查看status字段。试卷配置在papers.json，按question_id引用已审题；指定edition的卷不能使用该版未收录的题。

若教师要求新的知识章节，先在已有教学系统中补齐有来源的正文单元，再配置练习挂载；不存在的unit_id会被阻断。当前题目层定制不冒充已自动扩写任意课程正文。

## 制作新文本题

输入为JSON数组，每题至少包含id、title、stem、topic、unit_id、priority、priority_reason、difficulty（1–5）、source。source包含课程、真实来源类型，以及原题的物理PDF页、题号、原文。保留题面和完整小问，勿把参考答案混入stem或common_header。methods可记录训练方法。

```sh
python tools/produce_practice_batch.py --workspace . --questions /path/to/已核对题面.json --out /path/to/新的生产记录目录
```

工具会运行三个独立阶段并落盘author、blind、comparison及evidence。只有独立求解完成、最终对照明确通过且内容摘要一致的题目才进入catalog.json；其他题进入blocked.json。发生分歧或容量截断时保留原始记录，不改变题意或清空尝试记录来绕过失败。复杂大题宜分为小批次，但不得丢小问。

准备完整作者稿和独立解答后，也可以分别使用 practice_model.py 的author、blind、compare模式。最终审核必须针对实际发布的题面、答案、解析、来源与方法字段。对照工具保存host-owned bound_comparisons，不能让模型自己编造内容哈希。

## 将新练习挂进教材

每道题的unit_id或mount_unit_id必须是现有读本中的真实单元。使用下面的工具，从实际渲染正文中选取唯一锚点，随后查看放置是否符合教学顺序：

```sh
node tools/compile_practice_placements.cjs /path/to/catalog.json /path/to/教材目录 /path/to/placements.json
```

该工具不改题目、答案或数学审核摘要；只生成显示位置。真正发布前还需check_practice_delivery检查实际挂载与公式。定位失败不静默退到章节末尾。

## 试卷与验证口径

原卷和生成卷分别保存，最终放进同一试卷包。正式训练卷与历史结构参照试制卷明确分开。仅匹配题数与分值的卷不能称为完全拟合；任务链、方法、关键条件均需与母卷逐位核对。训练限时是编辑估计，不是官方时长或学生实测。

源头模型输出、符号/数值核验、独立数学对照、实际页面和PDF检查是不同证据。新功能测试通过不代表旧仓库所有回归通过，也不代表学生一定提分。

## 动画与静态 SVG

新增习题媒体独立于题目的数学正文：通过题号和 content_digest 绑定已审题目。动画目录汇集原正文动画和新增习题动画；题目页面在展开答案后才装载播放器，收起外层习题、答案或筛选移除题目时会暂停并卸载。纯题卷不加载动画数据；PDF只保留静态图。

交付中的 `媒体源/manifest.json` 保存真实视频、SVG、源程序、渲染收据、实际画面审查和发布配置摘要。重建工具自动携带这份媒体清单。修改公式、字幕、步骤、播放音轨或文件字节后都必须重审，不能沿用旧审核状态。渲染器返回 `needs_review` 只代表技术检查通过。

三段 Manim 源码及经审分镜在 `examples/practice_media/`。重渲染使用 `tools/render_practice_media.py --specs examples/practice_media/programs.json --out /path/to/new-renders --profile draft --manim-python /path/to/manim/python`。最终有声版选择 `--profile final --speech-config /path/to/local-speech.json`；配置使用现有 local-qwen 后端，提供本机模型目录、校验清单、Python 与缓存目录，不发布模型权重或账号资料。全审查模式和 OS 隔离保持启用。

实际视频画面、静态图和数学需要独立检查后，才可用 `tools/package_practice_media.py` 生成发布清单。默认新成品保持原有正文媒体字节不变。当前配音通过声稿审核、解码及非静音检查，未作人工听感评估。
