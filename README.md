# starry 星空

**把教材和课堂，整理成可以阅读、观看、练习和讨论的学习材料。**

starry 是一个面向课程学习的 AI 辅助项目。它把教材讲解、原文对照、交互图像、有声动画、习题和笔记放在同一个学习环境里，并提供教学视频解析与内容制作工作台。

目前可下载的成品是 **复变函数 A**：76 个教学单元、基础版 579 题／完整版 1240 题、16 段动画、2 个可操作三维图，以及覆盖卷、拟合卷、压力卷各 12 套和配套可打印材料。普通同学下载阅读包后，解压并双击 HTML 即可使用，无需配置模型账号。


**当前为公开预览版。源码可查看；未经作者事先书面许可，不得修改项目原创程序或发布的课程原件。** 允许下载、原样运行、正常配置及创建个人笔记。完整条款见 [LICENSE](LICENSE)，第三方组件沿用各自许可证。

## 直接开始学习

1. [下载复变函数完整阅读包](https://github.com/HaperN/ai-for-study-USTC-starry/releases/download/v0.2.0-preview.1/starry-complex-analysis-20261122.zip)。
2. 完整解压，保留文件夹内部结构。
3. 双击 **点这里开始阅读.html**，进入教材与练习。
4. 需要查功能和操作时，阅读包内 `先读我.txt` 或 [同学使用指南](docs/student-guide.md)。

[单独下载全部试卷与解析](https://github.com/HaperN/ai-for-study-USTC-starry/releases/download/v0.2.0-preview.1/starry-exam-papers-20261122.zip)。首页提供“教材解析”“日常巩固”“押题”三个入口；动画在 HTML 中播放，PDF 为静态打印版。

阅读包包含本地页面、公式字体、图片、动画、配音和 PDF。已有材料可离线阅读。私人笔记保存在当前浏览器中，换电脑前请先导出备份。**共享评论需要维护者启动服务，双击离线 HTML 不会连接公共讨论。**

![starry 复变函数学习入口](docs/assets/entry-20261122.png)

## 可以做什么

| 部分 | 实际用途 | 使用条件 |
| --- | --- | --- |
| 教材阅读 | 按单元学习，查看原文、讲解、证明和例题；较长推导可以展开阅读 | 离线包即可 |
| 交互图像 | 调整参数，观察复数、映射及其他数学对象的变化 | 离线包即可 |
| 动画与配音 | 查看分步演示、字幕和文字稿，控制播放、音量及进度 | 离线包即可 |
| 练习与试卷 | 在 579 题基础版和 1240 题完整版间选择，做题后回到对应教材位置；使用题册、解析、模拟卷和原卷 PDF | 离线包即可 |
| 私人笔记 | 保存自己的理解和批注，导入、导出备份；冲突导入保留本地编辑并另存副本 | 离线包即可 |
| 同学感悟 | 围绕具体原文发布文字或图片批注，作者可编辑、删除自己的内容 | 本地共享服务 |
| 图片识别 | 将手写推导或截图识别为可核对的文字，再由使用者决定是否发布 | 共享服务＋另配本地 OCR |
| 教学视频解析 | 转写完整音轨，结合定时及画面变化抽帧、OCR 和视觉分析，生成带时间引用的讲义并回看原片 | 工作台＋ASR/OCR/模型环境 |
| 内容制作 | 从教材来源准备、讲解生成、审核到媒体制作和页面发布；查看进度、取消或恢复任务 | 工作台＋相应生成环境 |
| 本机书架 | 导入已有交付、选择版本、保存个人阅读记录及离线缓存 | 本地书架服务 |

“视频解析”指从原片到转写、画面证据和讲义的处理流程。画面采用抽样，不能据此声称逐帧、逐字均已核实。共享评论通过进入小节或点击刷新读取更新，当前没有自动实时推送。

## 项目值得关注的地方

**讲解与来源能够对照。** 教材内容保留原文线索；视频讲义保留时间引用，遇到疑问可以回看相应原片。

**媒体参与讲解。** 图像可以交互，动画配有步骤、字幕或语音，习题还能回到教材对应位置。它们共同服务于同一段学习内容。

**笔记与讨论有具体落点。** 私人笔记和共享感悟围绕教材位置保存。正文版本变化时，旧讨论保留历史标记，避免把旧位置冒充当前原文。

**制作过程保留状态。** 来源、审核、媒体和版本分别记录；失败、取消、重试与恢复有明确状态，已有响应和缓存可在满足条件时复用。

## 在自己的电脑上启动服务

离线学习无需安装。需要共享评论或制作工作台时，按 [安装与启动](docs/quickstart.md) 配置 Python 3.12 与 Node.js。

```bash
git clone https://github.com/HaperN/ai-for-study-USTC-starry.git
cd ai-for-study-USTC-starry
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-reader.txt
.venv/bin/python -m pip install .
.venv/bin/python -m aiforstudy --workspace . ui --port 18917
```

以上启动本机制作工作台，不会自动生成课程或调用模型。Windows 的解释器路径与完整阅读服务配置见 [启动指南](docs/quickstart.md)。

公开配置使用占位模型地址和显式凭据引用，没有作者的登录信息；新生成任务需要维护者配置自己的服务。现成阅读包不需要这些服务。

内部 Python 包名 `aiforstudy` 和命令名 `aifs` 保持兼容，对外产品名使用 **starry**。

## 仓库结构

| 位置 | 内容 |
| --- | --- |
| `src/aiforstudy/` | 流程引擎、教材与视频处理、题库、共享服务及工作台 |
| `blocks/`、`lib/` | 仍被使用的解析、渲染、媒体与交互适配器 |
| `config/` | 无个人凭据的配置模板；默认登记一份复变函数来源 PDF |
| `examples/` | 小型交互教学与媒体制作示例 |
| `tests/` | 引擎、协议、阅读器和服务回归测试 |
| `tools/` | 检查、构包、媒体制作与维护工具 |
| `docs/` | 使用指南、模块说明、版本限制与验证范围 |
| `THIRD_PARTY_NOTICES/` | 第三方库及字体的许可声明 |

完整复变函数成品在 [Releases](https://github.com/HaperN/ai-for-study-USTC-starry/releases) 下载。开发期间的数据库、调用记录、私人批注和历史工作区不属于公开仓库。回归测试保留已匿名化的语义样例。

## 当前边界

- 本次公开课程只有复变函数 A。其他课程不是本次成品的一部分。
- “一门课程是一颗星”的知识星空是产品设计方向，星图式课程首页尚未实现。
- 历史教材留有 75 项待核对记录；本次题库专项审核和已完成订正不代表该历史清单全部关闭，工程验证也不等于全书数学内容已经认证。
- 没有随本仓库提供公网多人部署、真实学校统一认证或评论自动推送。
- 视频入口处理本地文件，尚不自动下载任意视频网站链接。
- 本地服务已在 macOS 环境验证；不宣称所有系统和浏览器均经过验收。
- 真实生成依赖用户自己的模型服务、识别模型、字体及渲染环境。测试中的模拟结果不代表真实生成质量。

详见 [已知限制](docs/known-limitations.md) 和 [验证范围](docs/verification.md)。

## 署名、引用与反馈

作者信息见 [AUTHORS.md](AUTHORS.md)，机器可读引用信息见 [CITATION.cff](CITATION.cff)。

发现内容或程序问题，可以提交 [Issue](https://github.com/HaperN/ai-for-study-USTC-starry/issues)，说明课程、小节、操作步骤与实际现象。当前未授予修改或发布衍生版本的许可；代码修改和合并需先获得作者书面授权，见 [反馈说明](CONTRIBUTING.md)。

## English overview

**starry** turns textbooks and lectures into source-linked learning materials with interactive figures, narrated animations, exercises and annotations. The current downloadable course is Complex Analysis A: 76 teaching units, 579-question basic and 1240-question full editions, 16 animations, two interactive 3D scenes, and 36 formal training papers.

Students can read the exported course offline. Shared annotations and content production require separately configured local services. Video analysis uses full-audio transcription and sampled visual evidence; shared comments currently update on navigation or manual refresh.

Equal authorship: **HaberN (50%) and 刘睿翔 (50%)**. This is a personal project. Source is visible under a custom no-modification licence, not an open-source licence. See [LICENSE](LICENSE) for permitted use and third-party exceptions.
