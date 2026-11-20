# 安装与启动

## 同学：离线阅读

下载 Release 中的 `starry-complex-analysis-20261120.zip`，完整解压，双击 `点这里开始阅读.html`。已有成品的阅读、图像交互、媒体播放和私人笔记不需要 Python、Node.js 或模型账号。不要在压缩包预览窗口中直接打开页面。

## 维护者：最小本地环境

需要 Python 3.12。整合阅读服务还需要 PATH 中能运行 Node.js；建议使用受支持的 Node.js LTS。制作新内容时按对应模块另配依赖。

从仓库根目录执行，macOS/Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-reader.txt
.venv/bin/python -m pip install .
.venv/bin/python -m aiforstudy --help
.venv/bin/python -m aiforstudy --workspace . ui --port 18917
```

Windows PowerShell 的对应命令：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-reader.txt
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe -m aiforstudy --help
.venv\Scripts\python.exe -m aiforstudy --workspace . ui --port 18917
```

打开终端给出的本机工作台地址。初始书目登记了一份已获授权分发的复变函数来源 PDF，解析与生成尚未运行。可以在工作台另行导入自己的教材。启动工作台不等于已经配置了教材解析、OCR 或模型生成能力。Windows 命令给出了路径写法；本轮没有在 Windows 上完成整套服务验收。

## 启动教材、练习与共享感悟

把阅读包解压到仓库内的 `downloads/`，使路径为：

```text
downloads/starry-complex-analysis-20261120/
  点这里开始阅读.html
  复变函数A/
    教材/
      index.html
      data/data.js
```

将 `config/integrated.example.json` 复制为 `config/integrated.local.json`。这个示例中的路径相对于配置文件，学习数据库会新建在 `work/community/`，制作工作台使用仓库本身的独立工作区。

macOS/Linux：

```bash
cp config/integrated.example.json config/integrated.local.json
.venv/bin/python -m aiforstudy integrated --config config/integrated.local.json --check
.venv/bin/python -m aiforstudy integrated --config config/integrated.local.json
```

Windows PowerShell：

```powershell
Copy-Item config/integrated.example.json config/integrated.local.json
.venv\Scripts\python.exe -m aiforstudy integrated --config config/integrated.local.json --check
.venv\Scripts\python.exe -m aiforstudy integrated --config config/integrated.local.json
```

检查通过后，学习入口是 `http://127.0.0.1:8792/start`，制作工作台是另一个本机地址。请先关闭前一节单独启动的工作台，避免占用 18917 端口。按 Ctrl+C 结束服务。

这些地址默认只在本机监听。本仓库没有把它们部署成全班可访问的网站；公开联网部署需要另外处理访问控制、备份和运维，不能通过简单暴露管理端口代替。

## 配置新的生成任务

公开的 `config/models.json` 是占位模板：无有效服务地址、无作者账户、无可用密钥。请按照 [模型配置](account-runtime.md) 设置自己的端点、模型、输出上限和凭据引用。配置未完成时任务应明确报错。

通过工作台导入教材会建立书籍登记。也可以参考 `config/books.example.json`，填写自己的来源路径；该文件是示例，不表示仓库附带可生成的教材解析缓存。默认登记可用 `aifs books` 查看；它是来源准备入口，现成 76 单元成品仍请从 Release 阅读包打开。

完整制作依赖的历史版本清单在 `requirements-python.txt`，Manim 依赖另见 `requirements-manim.lock`。`requirements.lock` 是 JSON 来源记录，不是可直接传给 `pip -r` 的 requirements 文件，也不代表全部依赖均可按哈希重建。

视频解析：配置本地音频识别、画面 OCR、模型服务以及 FFmpeg，见 [视频解析](video-analysis.md)。

配音与动画：配置本地配音模型、独立 Manim 解释器、字体、LaTeX 和隔离渲染能力，见 [配音说明](narration-providers.md)。

图片评论的 OCR：按 [图片批注](image-annotations.md) 配置。未配置时仍可按支持的方式发布图片，不能把未执行识别显示为已识别。

## 数据与版本

私人笔记在浏览器中；共享感悟在维护者指定的 SQLite 数据库中。不同浏览器、网址、端口和教材版本可能使用不同的存储范围。迁移前先导出备份，不要直接用新数据库覆盖旧数据库。

仓库忽略 `downloads/`、`work/`、`data/`、`.secrets/` 和本机配置。发布前仍应检查实际 Git 跟踪内容；忽略规则不会自动移除已经提交的敏感文件。
