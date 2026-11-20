# 课程资料导入与阅读

课程资料通过共享阅读器的“课程资料”按钮打开。数据来自用户显式导入的本地清单，按课程、学年/网页年份、资料类型和目录文字筛选；教材可按章或顶层题号跳到本地 PDF 的物理页。读取过程不请求研究服务，不依赖原始导入目录。

导入目录应包含 `ustc-exams/manifest.json`、`textbook/homework-index.json` 及两份清单引用的 PDF。使用项目运行环境（需要 PyMuPDF）执行：

```sh
python tools/import_course_materials.py \
  --materials /path/to/materials \
  --index /path/to/materials/course-material-index.json \
  --output data/parse/complex-analysis-v2/course-library
```

`--index` 可省略；指定时必须与两份原始清单的规范化结果一致。导入逐个检查 PDF 哈希和真实页数、133 个现有页范围及课程/角色来源。将验证后的原始清单与 PDF 写入独立快照后，原子更新 `current.json`。验证失败不改变原有有效资料；历史快照仍保留。

普通生产流程从对应教材的 `<parse-root>/course-library` 读取。准备阶段的目录复制与全目录指纹涵盖资料快照；读取器投影将 PDF 作为已有 `source_assets` 的内容寻址资源复制到 `book/assets/source`，并把全部来源加入快照指纹。旧教材没有此可选资料时按钮保持隐藏。新增资料不改变原书目录资料的内容或审核结果。

全书专用重建默认使用 `data/parse/complex-analysis-v2/course-library`，也支持指定已导入的可搬移目录：

```sh
python tools/build_wholebook_delivery.py \
  --source /path/to/source-reader \
  --previous-delivery /path/to/previous-delivery \
  --output /path/to/new-delivery \
  --course-library /path/to/imported/course-library
```

源码打包脚本已有 `data/parse` 收集规则，因此默认目录中的所有原始清单、PDF 与指针均进入源码包。显式使用项目之外的 `--course-library` 时，应同时保存该导入目录，并在移机重建时传入其新位置。阅读包中的 PDF 已独立复制，不需要它。

目前数据是 39 份考试 PDF / 51 场（通修复变函数 A 7 场、B 6 场、数学专业复分析 38 场），以及教材 9 章 / 129 个顶层题号。50 场有题卷、4 场有成套解答，其中 1 场仅有解答。教材参考结果涉及部分题号，不保证子题或过程完整。实际布置作业、精确考试日期、历史可得时间保持未知；保留已标错题、网页教师列与具体场次教师的区别。目录不执行 OCR、题目理解、题型分析、解答正确性验证或成绩预测。

PDF 页跳转使用 `#page=N`，多数浏览器内置查看器支持；若外部查看器忽略该参数，可按链接显示页码手动输入。所有资源在本地，来源网址仅在用户主动点击时访问。

验证：

```sh
python -m pytest tests/test_course_library.py tests/test_source_material_reader.py \
  tests/test_source_materials.py tests/contracts/test_source_material_snapshot.py \
  tests/integration/test_source_materials_preparation_v7.py -q
node --test tests/ui/course_library.test.cjs tests/ui/source_material_reader.test.cjs
```
