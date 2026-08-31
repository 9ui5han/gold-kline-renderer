# 独立教学图表接口（2026-08-31）

## 本次范围

新增 POST /v1/carousel/charts/render，保留 /v1/photo/* 的现有代码和行为，不改视频、宏观或旧图文业务。只在本地修改，未提交、推送、部署、购买或调用外部 API。

新增接口复用已有教学 K线渲染器，不增加依赖。不声称示例是真实历史行情。请求格式与当前 TOOL-04 的 chart_req_json 一致，只需在部署后切换 HTTP URL。

## 完整文件

- app/carousel/__init__.py：新模块。
- app/carousel/routes.py：独立请求类型及路由。
- app/main.py：导入并注册新路由，依赖原 require_token。
- tests/test_carousel_routes.py：新增接口行为测试。
- docs/carousel-chart-endpoint.md：本文。

app/photo/routes.py、app/photo/models.py、app/photo/market_chart_renderer.py 本次未修改。

## 接口合同

- 方法：POST
- 部署后地址：https://gold-kline-renderer.onrender.com/v1/carousel/charts/render
- Header：Authorization: Bearer 后端的 RENDER_SERVICE_TOKEN
- Header：Content-Type: application/json
- Body：完整 chart_req_json，不是包裹它的外层对象，不要二次 JSON 编码。
- schema_version：photo-chart-request-v1（保持现有合同）
- content_type：仅 educational_reconstruction
- language：en 或 zh-CN
- pages：原 PhotoChartPage 数组，最多10项；与 route_payload.analysis_pages 页码一一对应。
- route_payload：carousel-route-v2，analysis_mode 必须 educational_reconstruction；包含 market、timeframe、input_meta、analysis_pages。
- analysis_pages：沿用 pb-edu-v1 教学结构、visible_kline、zones、markers 等；K线保留 t/o/h/c/l/v。
- 同步接口：同一请求完成本批渲染后返回，不返回异步任务 ID。
- 成功：HTTP 200，schema_version=photo-chart-v1，assets 为现有图表素材数组；保留 asset_path、data_fingerprint、coordinate_map、source_type 等。
- 无/错误密钥：401。未配置默认密钥：503。
- 请求类型、版本、页码、K线或标注不合法：422。不能进入付费生图主线。
- 文件写入失败等服务器异常不伪装为成功；沿用 FastAPI 服务器异常处理。
- 输出位置：DATA_DIR/carousel-work/carousel-随机ID/chart_页码.png。
- 隔离：先在独立临时目录渲染完整批次，成功后发布目录；某页失败不发布半批图表。
- 不新增静态下载接口。asset_path 是后端本地路径，不是用户浏览器下载 URL，保持现有组装合同；最终完整页面生产仍由后续流程承担。

## Dify 节点配置核对表

本次没有修改任何 Dify DSL。

|节点/类型|输入与处理|输出/下一步|
|---|---|---|
|构建图表和GPT-image2请求 / 代码|用户输入.trusted_page_plan_json，String；已有代码不变|chart_req_json String；build_valid Boolean；build_error String|
|生产请求门禁 / 条件|build_valid=true 且 build_error为空|只有通过才到后端图表 HTTP|
|后端确定性图表渲染 / HTTP|部署后使用上面新 URL；raw BODY 选择 构建图表和GPT-image2请求.chart_req_json String，非空；其他请求字段不变|status_code Number、body String；成功响应必须通过现有组装检查|
|组装七页与最终质检 / 代码|chart_code 来源后端图表.status_code；chart_body 来源后端图表.body；页面计划和封面推广结果保持原有绑定|输出字段不变；tool4_valid=true 且 tool4_error为空才通过 G04|
|TOOL-04 输出 / End|carousel_delivery_json、carousel_zh_text_json String；tool4_valid Boolean；tool4_error String|四个名字唯一且均不超过30字符，跨工作流合同不变|

现有 HTTP 超时配置本次不修改。Render 冷启动或整批绘制的云端耗时尚未实测，不能用本地测试时长承诺云端延迟。

## 关联影响清单

|关联项|结论与依据|
|---|---|
|TOOL-02 教学结构|无需修改：仍接受相同 carousel-route-v2/pb-edu-v1 数据|
|TOOL-03 页面计划|无需修改：carousel-page-plan-v2 仍由原构建代码消费|
|TOOL-04 请求构建|无需修改：新接口接受当前 chart_req_json 格式|
|TOOL-04 后端 HTTP URL|需要用户部署后修改到新路径；本次未修改云端或 DSL|
|后端 app/main.py|需要修改：注册新路由并绑定原 require_token，避免无鉴权入口|
|后端 app/carousel/routes.py|需要新增：只处理 educational_reconstruction，独立存储路径|
|旧 photo 路由及模型|无需修改：旧行为和原文件保持不变，回归验证通过|
|共用教学渲染器|无需修改：复用已有数据和坐标验证，PNG及指纹合同不变|
|TOOL-04 组装及 G04|无需修改：真实图表响应成功通过现有组装；错误图表响应被拒绝|
|总控与 End|无需修改：外层输入输出名、类型和唯一性不变|
|部署依赖|无需修改：沿用 requirements.txt 和 Dockerfile，保持单服务单端口|

注意：公开图片的完整合成、封面和推广 Prompt 完善并未在本次实现。不要因此运行付费生图。

## 本地验证记录

- 添加测试后，在无新路由时5项测试按预期404失败，旧接口检查通过。
- 实现后新接口6项测试通过：真实 PNG、独立目录、正确坐标与返回合同、401/503、错误类型/版本/坐标422、旧接口行为。
- 旧 photo 相关48项和 propulsion9项测试通过；共63项。
- 使用用户真实 TOOL-03 输出经过原04_build、新接口和原04_assemble：HTTP200，5张图，页码2～6，K线数23/23/23/27/23，组装tool4_valid=true。
- 上述组装中的封面/推广响应为本地固定假数据，没有实际访问生图接口；不是全七页生产验收。
- 故意破坏最后一页标注后得到422，没有新增半批目录，组装拒绝错误响应。
- 抽查真实产出PNG，1080×720，可见教学说明、K线和订单块/推进块区域。
- 未运行云端部署；无法确认当前 Render 服务是否关联此仓库和分支。

## 重跑测试

在本项目目录执行；以下是本次已准备的临时测试环境，不是生产部署命令：

```bash
DATA_DIR=/private/tmp/carousel-route-validation DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib /private/tmp/carousel-route-test-venv/bin/python -B -m unittest discover -s tests -p test_carousel_routes.py
```

部署仍使用原 Dockerfile 的 app.main:app，不需要额外监听端口。

## 用户下一步

先由用户在 GitHub Desktop 检查这个仓库的新文件和 app/main.py 修改，然后主动提交并 Push。确认 Render 部署了该提交后，才在 Dify 将后端 HTTP URL 改为新路径并单独测试。若返回404，优先检查部署版本和服务对应仓库，而不是改请求类型。

回退时使用旧应用与旧 /v1/photo 路径；不需要覆盖或删除旧接口。暴露过的密钥应更换，不要写入仓库。
