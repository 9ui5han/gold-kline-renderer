# Python + 302.AI K线视频渲染服务

本服务只负责一件事：下载 302.AI 已生成的旁白，按照输入的真实 OHLCV 和模型预测
精确画出 K 线、支撑压力位、买卖观察区，再用 FFmpeg 合成 1080×1920 MP4。

它不调用大模型，也不改写价格。测试版把成片放在服务自己的 `/media` 地址中；服务重启
后文件可能丢失，所以下载满意的成片即可。正式版再接对象存储。

## 本机验证（可选）

需要 Docker：

```bash
docker build -t gold-python-render .
docker run --rm -p 8000:8000 \
  -e RENDER_SERVICE_TOKEN=my-test-token \
  -e PUBLIC_BASE_URL=http://127.0.0.1:8000 \
  gold-python-render
```

浏览器打开：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

## Render云端部署

把本目录作为一个独立 Git 仓库上传，然后在任意支持 Docker 的云平台部署。设置：

| 环境变量 | 示例 | 是否保密 |
|---|---|---:|
| `RENDER_SERVICE_TOKEN` | 一段至少32位随机字符串 | 是 |
| `PUBLIC_BASE_URL` | 云平台给你的 `https://...` 域名 | 否 |
| `DATA_DIR` | 测试：`/tmp/gold-video`；正式：`/var/data/gold-video` | 否；正式环境必须挂载Render持久磁盘，保证TTS幂等记录跨重启保留 |
| `MAX_AUDIO_MB` | `30` | 否 |
| `MIN_RENDER_AUDIO_SECONDS` | `30` | 否 |
| `MAX_RENDER_AUDIO_SECONDS` | `900` | 否 |
| `MIN_TTS_AUDIO_SECONDS` | `1` | 否；分段TTS允许1秒起，最终视频仍按视频时长合同校验 |
| `MAX_TTS_AUDIO_SECONDS` | `900` | 否 |
| `MAX_TTS_TARGET_DRIFT_SECONDS` | `3` | 否；轻微自然变速后允许成片与计划时长双向相差的最大秒数；未设置时兼容读取旧变量`MAX_TTS_TARGET_OVERRUN_SECONDS` |
| `MAX_AUDIO_VIDEO_DRIFT_SECONDS` | `0.2` | 否 |
| `AI302_API_KEY` | 302.AI API Key | 是 |
| `ELEVENLABS_MODEL_ID` | `eleven_v3` | 否 |
| `TTS_PROFILE_CATALOG_JSON` | 自定义音色Profile的JSON数组；未填写时使用代码内文档候选 | 否；真实音色ID不是密钥 |
| `MACRO_USER_AGENT` | `GoldKlineRender/2.0 (+https://你的Render域名)` | 否；用于Fed/BLS/BEA及美国财政部识别请求来源 |
| `MACRO_CACHE_TTL_SEC` | `21600`（6小时） | 否 |
| `MACRO_CACHE_MAX_STALE_SEC` | `172800`（48小时） | 否 |
| `INDEXTTS2_SPEAKER_AUDIO_URL` | 已获授权的参考人声公网URL | 是 |
| `INDEXTTS2_MAX_POLLS` | `150` | 否 |
| `INDEXTTS2_POLL_INTERVAL_SEC` | `2` | 否 |
| `PORT` | 通常由平台自动设置 | 否 |

部署完成后先访问 `https://你的域名/health`。返回 `{"status":"ok"}` 才能继续 Dify。

浏览器图形化检查宏观事件服务：

```text
https://你的域名/macro-status/
```

页面不读取`RENDER_SERVICE_TOKEN`。它只调用公开的脱敏状态摘要
`/v1/macro-events/status-summary`；摘要不包含官方URL、响应正文或服务密钥，并在服务端
缓存至少60秒，避免页面刷新反复请求Fed、BLS、BEA和美国财政部。原始
`/v1/macro-events/source-health`接口继续要求Bearer Token，合同保持不变。

页面还会显示已配置的CPI、PPI、非农/就业、PCE和FOMC事件类型，以及解析缓存中的
事件数量、最近一次和下一次时间。官方来源可用但尚未生成缓存时，卡片显示“等待缓存”。

Render免费实例可能休眠，且 `/tmp` 文件会在实例重建后丢失。测试阶段可以继续使用
`DATA_DIR=/tmp/gold-video`；正式保存成片和宏观缓存时应改用Render持久磁盘或对象存储。
正式TTS还会在 `DATA_DIR/tts-idempotency.json` 保存 `request_id`、请求指纹和任务状态；
因此未挂载持久磁盘时只能用于无付费调用的测试，不能承诺重启后仍防止重复扣费。

## API

查看后端候选音色Profile（免费，不生成音频）：

```http
GET /v1/tts-profiles
Authorization: Bearer <RENDER_SERVICE_TOKEN>
```

检查302.AI免费音色列表状态：

```http
GET /v1/tts-profiles/source-health
Authorization: Bearer <RENDER_SERVICE_TOKEN>
```

该检查按302官方文档调用 `GET /elevenlabs/voices` 和
`POST /dubbingx/v1/getTTSTimbreList`。MiniMax没有在本项目采用免费的302音色状态接口，
因此只返回 `documentation_only`，必须通过用户确认后的极短付费试听才能标记为可用。

正式Dify请求只需要提交统一的 `narrator_profile_id`。文档候选默认不能直接进入付费TTS；
试听阶段可以显式提交 `allow_unverified_profile=true`。验证完成后，把对应Profile通过
`TTS_PROFILE_CATALOG_JSON`覆盖为 `status=verified`。旧的 `tts_provider` 和平台专属音色ID
继续保留为兼容回退。

DubbingX会读取`narration_json.segments`中的逐段`speed`和`pause_after_ms`，验证分段文字
拼接后与完整`text`一致，再调用302并由Render真实插入段后停顿。任何文字漂移、速度越界
或停顿越界都会在付费请求前失败。

验证免费官方宏观日历来源（只检查可访问性和最小响应结构，不判断涨跌）：

```http
GET /v1/macro-events/source-health
Authorization: Bearer <RENDER_SERVICE_TOKEN>
```

返回 `fed`、`bls`、`bea`、`fed_speeches`、`nyfed_williams_speeches`、`whitehouse_remarks`、`state_diplomacy`、`treasury_auctions`、`treasury_buybacks`、`treasury_press` 十个来源的 HTTP 状态、响应类型和结构校验结果。

讲话类事件只使用官方链接和官方发布时间/日期：美联储来源覆盖 Kevin Warsh、Philip Jefferson、Michelle Bowman、Christopher Waller 与 Jerome Powell；纽约联储 John Williams 页面只有发布日期，因此该类事件标为 `date_only`，不会被用于关联某一根精确时间的 K 线。白宫总统讲话和国务院外交官员讲话/声明需要同时满足“官方来源、明确发言人或职务、宏观或地缘关键词”三层筛选；它们的时间是官方发布时间，不等于已证明的市场因果。

按预测范围查询正式宏观事件上下文：

```http
POST /v1/macro-events/context
Authorization: Bearer <RENDER_SERVICE_TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "request_id": "macro-20260803-001",
  "symbol": "XAUUSD",
  "data_as_of": "2026-08-03T09:45:00Z",
  "forecast_horizon": {
    "schema_version": "forecast-horizon-v1",
    "timeframe": "15m",
    "start_time": "2026-08-03T10:00:00Z",
    "end_time": "2026-08-03T12:00:00Z",
    "duration_minutes": 120
  }
}
```

响应统一返回Fed、BLS、BEA、美联储讲话及美国财政部来源状态，并返回预测窗口前后24小时内的白名单事件。讲话与临时公告使用官方发布时间，不把它们误写成预定讲话时间。
`data_status`为`complete`、`partial`或`unavailable`；任何情况下
`directional_bias`都固定为`not_calculated`。正常缓存6小时，来源刷新失败时最多
回退到48小时内最后一次成功缓存并明确标记`stale=true`。缓存文件保存在
`DATA_DIR`。Render免费实例使用`/tmp`时缓存可能在重启后丢失；配置持久磁盘后应把
`DATA_DIR`指向挂载目录。旧`RAILWAY_VOLUME_MOUNT_PATH`只保留兼容，不是当前部署方式。

当前最小可行性测试使用同步接口：

```http
POST /v1/test-video
```

它直接生成约 10 秒的一条 MP4 并返回 `video_url`，不创建异步任务、不轮询，也不拼接
多个视频片段。

创建任务：

```http
POST /v1/render-jobs
Authorization: Bearer <RENDER_SERVICE_TOKEN>
Content-Type: application/json
```

立即返回 HTTP 202、`job_id` 和 `status_url`。查询：

```http
GET /v1/render-jobs/{job_id}
Authorization: Bearer <RENDER_SERVICE_TOKEN>
```

状态只有 `queued`、`rendering`、`completed`、`failed`。完成后返回 `video_url` 和
`thumbnail_url`。

## Dify 结构路径请求契约

趋势预测视频使用 `style.forecast_mode = "structure_paths"`。此模式不会预测未来精确
OHLC；渲染器只使用 Dify 已校验的结构路径坐标绘制方向箭头。

请求必须同时包含：

- `forecast_paths.schema_version = "structure-path-v1"`；
- 三个情景及不同的 `primary_scenario`、`alternate_scenario`；
- 每个情景三至四个带 `resolved_value` 的 `path_points`。

缺少这些字段时，接口会在请求校验阶段返回明确错误，不会把空对象静默当成无预测继续渲染。

ElevenLabs 分段 TTS 同样要求 `narration_json.segments` 为非空数组，且分段文字拼接后必须
与完整 `text` 一致；这能把 Dify 的空结果问题挡在付费语音请求之前。

302.AI 的 ElevenLabs 请求默认使用官方文档列出的 `eleven_v3` 模型。
如果在 Render 中设置了 `ELEVENLABS_MODEL_ID`，只能填写 302.AI 官方模型列表中明确支持
文字转语音的模型。切换模型前先用一小段文本试听，再决定是否用于完整视频。
`AI302_API_KEY` 可以填写纯密钥，也可以误带一次 `Bearer ` 前缀，服务启动时会自动去除首尾空格
并避免重复拼接鉴权前缀。修改 Render 环境变量后必须重新部署，服务进程才会读取新值。

## MiniMax Speech 2.8 Turbo 逐句节奏

当 `/v1/tts-jobs` 请求使用 `tts_provider=minimax` 时，服务读取
`narration_json.segments` 中已经校验的英文、`effective_speed`（或 `speed`）和
`pause_after_ms`。每个segment会在句末标点后的空白处继续拆成完整句子；价格小数（例如
`4,273.04`）不会被拆开，也不会改写任何英文或数字。

每句话调用一次302.AI的 `speech-2.8-turbo`，固定使用请求中的
`minimax_voice_id`、`language_boost=English`、`emotion=calm`。句速只在segment基础值
附近按 `-0.01 / 0 / +0.01` 确定性变化，并限制在 `0.90` 至 `1.01`；段内停顿为
`200` 或 `220` 毫秒，段末保留Dify传入的 `pause_after_ms`。任意一句失败会让整个TTS任务
失败，不会返回缺句音频。

音频拼接和停顿插入后，字幕直接使用每句话的真实音频边界；成功结果的
`alignment_method` 为 `minimax_sentence_boundary_contract`。自动重试由Dify保持关闭，
避免付费请求重复执行。
