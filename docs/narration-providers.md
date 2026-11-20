# 配音供应商与真实时间记录

公开配置默认关闭新配音生成。已有阅读包的声音可以直接播放；要生成新配音，请先配置依赖并显式启用 `config/narration.json`。

默认 `config/narration.json` 为自动本地 Qwen；Edge 仅为显式可选后端。新后端从现有 `render_generated_code` → `configured_backend` → `prepare_step_audio` 入口使用；旧的 `narrate_animation(..., backend=...)` 也支持。配置通过工作区 `config/narration.json` 读取，`enabled: false` 可关闭新制作配音。已有静态书、MP4、字幕不需要重新生成。

两家付费接口只做过离线传输夹具测试，没有联网合成或听审。当前缺少服务凭据和选定音色的实测，不宣称音质提升、中文术语正确或句内动画已完成。

## 可用配置

选择 MiniMax 时，把下面内容用于 `config/narration.json`；`voice` 必须换成账户可用的音色 ID：

```json
{
  "enabled": true,
  "backend": "minimax",
  "model": "speech-2.8-hd",
  "voice": "YOUR_MINIMAX_VOICE_ID",
  "token_env": "MINIMAX_API_KEY",
  "speed": 1.0,
  "volume": 1.0,
  "pitch": 0,
  "subtitle_type": "word",
  "dictionary_revision": "complex-analysis-1",
  "pronunciation_dict": {"tone": []}
}
```

MiniMax 请求 `POST https://api.minimax.io/v1/t2a_v2`，固定非流式、hex 编码 WAV、24 kHz 单声道，读取 `base_resp.status_code` 并要求完成状态。词典 `tone` 是 `original/replacement` 字符串数组，保留 MiniMax 原生规则；词典内容与版本都参与缓存身份。`volume` 范围为大于 0 且不超过 10，`pitch` 必须为 -12 至 12 的整数。返回的 `subtitle_file` 另行下载，不转发认证头；毫秒明确除以 1000。句级文件若没有字符偏移，就保存未知偏移，不按字数分配词级时间。详见 [MiniMax HTTP 协议](https://platform.minimax.io/docs/api-reference/speech-t2a-http.md) 和 [官方 CLI 字幕消费方式](https://github.com/MiniMax-AI/cli/blob/main/src/commands/speech/synthesize.ts)。

选择 Eleven 时：

```json
{
  "enabled": true,
  "backend": "elevenlabs",
  "model": "eleven_v4",
  "voice": "YOUR_ELEVEN_VOICE_ID",
  "token_env": "ELEVENLABS_API_KEY",
  "pronunciation_dictionary_locators": []
}
```

已有 Eleven 词典可将空数组替换为：

```json
[{"pronunciation_dictionary_id": "YOUR_DICTIONARY_ID", "version_id": "PINNED_VERSION_ID"}]
```

Eleven 请求 `POST /v1/text-to-speech/{voice}/with-timestamps?output_format=wav_24000`，用 `xi-api-key` 认证，明确发送 `model_id=eleven_v4`、`language_code=zh`。响应的 `audio_base64` 解码为 WAV；原始与归一化字符对齐分别记录，时间字段已是秒。要求词典固定版本，最多三项；不接受未实现的速度/风格控制。详见 [Eleven 单人 TTS 时间戳接口](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps.md)。

`token_env` 只保存外部环境变量名称。使用者应通过自己的安全配置在启动进程时提供其值；不要把密钥写入 JSON、教材或命令示例。缺少值时抛出明确配置错误，运行时报告 blocked，不自动换供应商。不读取任意密钥文件，也不自动创建词典或音色。可选 `endpoint` 仅支持 HTTPS、无认证信息/查询参数的完整 MiniMax 请求 URL，或 Eleven 的 `/v1/text-to-speech` 基址；默认无需填写。

当前明确拒绝 `stream: true` / `word_streaming`。本实现不接收累计字幕流，因此不会把累计快照错误追加成重复词；以后新增流式路径必须独立实现覆盖语义。

## 文字层与审核边界

旧步骤的 `caption`、`observation`、`narration_text` 继续有效。可选 `display_markdown` 用作实际显示字幕，`director_notes` 独立保留并标记 `direction_status: not_applied`，不会朗读备注或声称已执行强调/停顿。

明确审定的口语可由编辑调用方提供：

```json
{
  "step_id": "residue",
  "caption": "留数",
  "display_markdown": "$a_{-1}$",
  "canonical_spoken_text": "a下标负一，也就是负一次方项的系数。",
  "spoken_review": {"status": "approved", "reviewer": "实际审稿人"},
  "director_notes": "强调负号；本轮适配器不自动执行此备注。"
}
```

该口语原样送出，不再经旧 LaTeX 启发式改写。`spoken_review` 是调用方的编辑声明，不是系统证明；生成与技术修复模型的输出 schema 不提供自行填写这两项审定字段的入口，实际响应接收时也在 full、balanced、json_only 三种模式下检查。新生成拒绝模型添加审定字段；技术修复仅按唯一 step_id 从宿主保存的原稿恢复审定信息，并要求原稿关联的口语、标题、观察文字及显示文字等保持不变。没有此层的旧稿标记 `legacy_unreviewed`，不因成功合成而获得审核。

## 时间数据的生产消费点

`prepare_step_audio` 从实际 PCM 帧数产生 `sample_count`、`sample_rate`、时长、音频 SHA256，并保存供应商原始/归一化对齐的独立轨道。损坏 WAV、截断 PCM、非有限/越界时间和不等长字符数组会失败，不发布假音频。

实际制作的 `_animation_resources` 把 worker 报告、宿主核验过的步骤起点写入每条音频记录的 `timeline`，再发布并计算 `source/narration.json` 资源哈希。旧片再配音也将重排后的 MP4 起点写入回执。`alignment` 内时间始终属于分段音频，`timeline` 属于成片视频，两个时钟明确区分。

可以直接查询这些生产回执，范围是 Python Unicode 码点的含头不含尾偏移：

```python
import json
from aiforstudy.blocks.media.narration import narration_anchor
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
cue = narration_anchor(receipt, step_id="residue", text_start=0, text_end=5,
                       expected_text="a下标负一")
```

只在明确文字范围与供应商真实边界一致时返回 `status=aligned` 及视频秒数；不匹配、重复身份、缺少偏移、没有全局时钟等返回 `unresolved`，不含推测时间。不会用归一化文本的下标硬套原稿；不会在句级字幕中插值出某一个字的时间。

这是可查询的真实时间接口和回执接入，尚未把词句范围绑定到数学对象、生成镜头动作或实现逐词高亮。现有播放器仍按整步字幕/MP4 时钟播放，完整的句内语义动作同步与听审仍待后续验证。

## 缓存与离线验证

配音源身份包含 provider/model/voice、协议版本、端点哈希、选项及词典身份；脚本身份包含口语、审定记录、显示文本、导演备注。动画资源的上层生成缓存纳入 `config/narration.json` 及适配器源码；修改供应商或词典不再命中旧配音资源。密钥值不进入身份。固定渲染环境自检显式关闭旁白，不因工作区启用配音而联网；真实制作仍照常读取配音配置。

从源码目录运行针对性测试（合成音调仅为测试数据）：

```sh
../.venv-linux/bin/python -m pytest tests/test_narration_providers.py tests/test_narration.py tests/test_natural_narration.py tests/test_media_code_dependencies.py tests/test_media_code_contracts.py -q
```

### Offline Qwen3 CustomVoice (pinned native worker)

`tools/local_speech_worker.py` runs separately in an environment pinned to
`mlx-audio==0.6.5` on Apple Silicon. Pass `--model-dir`, `--model-manifest`,
`--corpus`, `--cache-dir`, and `--output-config` explicitly. The model manifest
must contain a repository, immutable revision, `verified_complete` status, and
SHA-256 for every file. The worker rehashes every file before loading, disables
Hub network access, and uses `trust_remote_code=False`. Model weights stay outside
source and delivery packages. Missing dependencies, mismatched files, capped
output, or invalid PCM fail explicitly; there is no Edge/system fallback.

The generated configuration selects `local-cache` through the existing narration
factory. Cache keys include exact spoken text, all model/tokenizer file hashes,
revision, Serena voice, seed, instruction, effective token budget, streaming mode,
and runtime version. Each cache entry retains original float WAV, PCM16 WAV,
text, identity, sample count and hashes. The packaging adapter rechecks the entry
identity/hash and bounded PCM before returning it. Audio alignment remains
unavailable; no timestamps or human review are fabricated.

`tools/revoice_wholebook.py` accepts `--source` (delivery with `neural-media.json`
and `book/`), `--output`, `--config`, and the frozen `--corpus`. It uses each
current MP4's `step.start/end`, preserving older `source_start/end` under
`prior_clock_lineage`. The established narration compositor retains original
motion and adds end-frame holds if required by measured PCM. It writes a separate
stage suitable for the whole-book builder's `--previous-delivery` input. The
original delivery is never changed. `tools/verify_revoiced_delivery.py` accepts
the same arguments and checks all 57 PCM slices, resource hashes, scripts, LaTeX,
VTT timings and decoded seek frames in the 12-animation corpus.

The worker's fixed voice is a model-supplied voice, not a clone. A technical pass
or an ASR match does not establish human-perceived naturalness or mathematical
pronunciation correctness. Keep the same-script audition evidence alongside the
selection decision.

For previously narrated clips, optionally pass `--receipt-source` to the prior
receipt-bearing delivery. Hold removal requires its MP4 hash to match the current
input, its step clocks to match exactly, and a recognized compositor contract
explicitly recording original motion followed by final-frame holds. Only the
original motion **length** is reused, added to the current step start; old raw
start times are never treated as positions in the current MP4. Missing or
mismatched evidence retains the entire current interval with an explicit reason.
Receipts record both current input/motion windows and prior clock lineage.

### Default automatic local synthesis

The default factory and `config/narration.json` now select `local-qwen`. Set
`AIFORSTUDY_TTS_MODEL_DIR`, `AIFORSTUDY_TTS_MODEL_MANIFEST`,
`AIFORSTUDY_TTS_PYTHON` (the MLX virtual environment's interpreter), and
`AIFORSTUDY_TTS_CACHE_DIR`, or pass the corresponding explicit `model_dir`,
`model_manifest`, `python`, `cache_dir` configuration. The interpreter's virtual
environment symlink must be preserved. The package-local worker is launched with
`-m aiforstudy.blocks.media.local_speech_worker`; no unbundled tools path is needed.
A cache miss automatically generates and verifies audio. Valid cache hits do not
wait for the GPU; corrupt completed entries fail explicitly. Across processes,
misses share `~/.cache/aiforstudy/tts-gpu.lock` (override with
`AIFORSTUDY_TTS_LOCK_FILE`), recheck the cache after locking, and release the lock
on success, exception or worker timeout. Do not configure different lock files
for processes sharing the same GPU. The default wait/worker timeout is 900 seconds.
No model dependency is installed automatically and no online/system fallback is
attempted. Edge remains an explicit `backend: edge-tts` option only.

`--overrides` on the revoice and verification tools accepts a JSON `changes` list
with explicit unit/request/step IDs and complete `before`/`after` spoken strings.
The before text must match the original input exactly; display prose is changed
only at that exact string, preserving LaTeX. Use a separately versioned corpus
containing the new expected strings, and retain the original corpus and media.

Animation resource and compiler identities also bind the resolved verified model
manifest, native runtime versions and local backend/worker source files. Changes
to those inputs invalidate animation reuse without invalidating unrelated drawing
or review identities. Pipeline cancellation propagates through each audio step,
GPU-lock waits and active model communication. The model process group is stopped
and the worker reaped before releasing its GPU lock; later steps do not start.
