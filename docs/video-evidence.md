# 本地视频证据

视频证据必须由使用者明确指定课程和单元；不同 `course_id` 不能关联到目标书。依赖 `ffmpeg`、`ffprobe` 在 PATH 中，不需要模型、网络或下载。命令从仓库根目录执行（替换 `python` 为项目环境中的解释器）：

```sh
PYTHONPATH=src python tools/video_evidence.py validate /path/to/manifest.json
PYTHONPATH=src python tools/video_evidence.py import /path/to/manifest.json /path/to/bundles
PYTHONPATH=src python tools/video_evidence.py attach /path/to/manifest.json /path/to/parse --course-id BOOK_ID --unit-id UNIT_ID
PYTHONPATH=src python tools/video_evidence.py project /path/to/bundles/FINGERPRINT /path/to/empty-view --course-id BOOK_ID
PYTHONPATH=src python tools/video_evidence.py extract /path/to/video.mp4 /path/to/empty-output --start 710 --end 730
```

`attach` 会复制并校验资产，保存只读的内容寻址包，并更新对应 parse 的显式单元索引。生成正文时，已定位观察通过 `video_observation` 来源引用绑定清单路径、清单 SHA-256 和 JSON pointer；完整包的哈希进入来源上下文，包内文件的哈希进入运行输入。重建 Reader 时会复制包及独立回看页并列出入口，已有快照的来源改变会导致校验失败。只读权限防止普通误改；内容哈希负责检测后续更改。不要把无时间定位的记录当作可定位来源。

清单 `schema_version` 为 `1.0`，包含 `course_id`、`media_id`、`duration_seconds`、`width`、`height`、视频流的 `time_base`、`files`（唯一 `id`、相对 `path`、`sha256`）及 `observations`（唯一 `id`、`source_id`、源秒 `start`/`end`、可选 `[x0,y0,x1,y1]` 像素 `bbox`、`evidence_ids`）。未定位记录的时间两端均为 null；候选、原始文件和核查材料保留各自类别与链接。可填写 `streams`，其 index、codec_type、codec_name、time_base 必须和实际 ffprobe 输出一致。所有相对路径必须留在清单目录内；读取每个文件并核验哈希和实际视频流，未通过则不发布。`host_verified: true` 被拒绝；`model_status` 只标为模型自报，不等于宿主核验。

提取命令在空目录保存窗口、首帧、存在时的单声道 16 kHz 音频和 `extraction.json`，包含源哈希、请求范围、每条命令、退出状态、错误和结果哈希。输出时间轴重置；所请求的时间不是精确帧 PTS。失败记录可用于定位原因，换空目录重试。原始识别和模型输出必须单独保留，禁止把短窗或全时长的选择当作完整语义理解。

本次已有试验可复现清单：`PYTHONPATH=src python tools/prepare_video_trial.py /path/to/video-BV1ki4y1A7Mh`。它仅读取已有试验产物并验证已有的原片、帧和模型输出哈希，不下载、不重跑模型。该样本的课程 ID 为 `electromagnetism-experiment`，不可关联到复变课程。所保留的识别输出、模型观点及人工局部帧检查各自带来源，不能据此声称全片语义已核实。
