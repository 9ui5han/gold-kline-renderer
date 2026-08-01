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
| `INDEXTTS2_SPEAKER_AUDIO_URL` | 已获授权的参考人声公网URL | 是 |
| `INDEXTTS2_MAX_POLLS` | `150` | 否 |
| `INDEXTTS2_POLL_INTERVAL_SEC` | `2` | 否 |
| `PORT` | 通常由平台自动设置 | 否 |

部署完成后先访问 `https://你的域名/health`。返回 `{"status":"ok"}` 才能继续 Dify。

## API

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
