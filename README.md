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

## 云端部署

把本目录作为一个独立 Git 仓库上传，然后在任意支持 Docker 的云平台部署。设置：

| 环境变量 | 示例 | 是否保密 |
|---|---|---:|
| `RENDER_SERVICE_TOKEN` | 一段至少32位随机字符串 | 是 |
| `PUBLIC_BASE_URL` | 云平台给你的 `https://...` 域名 | 否 |
| `DATA_DIR` | `/tmp/gold-video` | 否 |
| `MAX_AUDIO_MB` | `30` | 否 |
| `MIN_RENDER_AUDIO_SECONDS` | `30` | 否 |
| `MAX_RENDER_AUDIO_SECONDS` | `900` | 否 |
| `MIN_TTS_AUDIO_SECONDS` | `30` | 否 |
| `MAX_TTS_AUDIO_SECONDS` | `900` | 否 |
| `MAX_TTS_TARGET_DRIFT_SECONDS` | `3` | 否；轻微自然变速后允许成片与计划时长双向相差的最大秒数；未设置时兼容读取旧变量`MAX_TTS_TARGET_OVERRUN_SECONDS` |
| `MAX_AUDIO_VIDEO_DRIFT_SECONDS` | `0.2` | 否 |
| `AI302_API_KEY` | 302.AI API Key | 是 |
| `ELEVENLABS_MODEL_ID` | `eleven_v3` | 否 |
| `MACRO_CACHE_TTL_SEC` | `21600`（6小时） | 否 |
| `MACRO_CACHE_MAX_STALE_SEC` | `172800`（48小时） | 否 |
| `INDEXTTS2_SPEAKER_AUDIO_URL` | 已获授权的参考人声公网URL | 是 |
| `INDEXTTS2_MAX_POLLS` | `150` | 否 |
| `INDEXTTS2_POLL_INTERVAL_SEC` | `2` | 否 |
| `PORT` | 通常由平台自动设置 | 否 |

部署完成后先访问 `https://你的域名/health`。返回 `{"status":"ok"}` 才能继续 Dify。

## API

验证免费官方宏观日历来源（只检查可访问性和最小响应结构，不判断涨跌）：

```http
GET /v1/macro-events/source-health
Authorization: Bearer <RENDER_SERVICE_TOKEN>
```

返回 `fed`、`bls`、`bea` 三个来源的HTTP状态、响应类型和结构校验结果。

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

响应统一返回Fed、BLS、BEA来源状态及预测窗口前后24小时内的白名单事件。
`data_status`为`complete`、`partial`或`unavailable`；任何情况下
`directional_bias`都固定为`not_calculated`。正常缓存6小时，来源刷新失败时最多
回退到48小时内最后一次成功缓存并明确标记`stale=true`。缓存文件保存在
`DATA_DIR`；Railway配置Volume时会随`RAILWAY_VOLUME_MOUNT_PATH`持久保存。

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
如果在 Railway 中设置了 `ELEVENLABS_MODEL_ID`，只能填写 302.AI 官方模型列表中明确支持
文字转语音的模型。切换模型前先用一小段文本试听，再决定是否用于完整视频。
`AI302_API_KEY` 可以填写纯密钥，也可以误带一次 `Bearer ` 前缀，服务启动时会自动去除首尾空格
并避免重复拼接鉴权前缀。修改 Railway 环境变量后必须重新部署，服务进程才会读取新值。

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
