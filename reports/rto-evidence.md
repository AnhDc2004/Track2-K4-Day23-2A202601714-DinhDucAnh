# RTO/RPO Evidence — Lab 23

Quy tắc duy nhất: mỗi con số ở đây phải trỏ được về một dòng log thật
(`đường/dẫn.jsonl:số_dòng`). `pytest tests/test_rto_evidence.py` sẽ mở từng file ra kiểm tra.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | `2026-08-25T10:02:00` | chaos kill | `chaos/chaos-events.jsonl:3` |
| Request fail đầu tiên | `+0.1s` | dòng `ok:false` đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:17` |
| Request thành công sau đó | không có | không có dòng `ok:true` nào sau t_outage (16/32 request fail) | `reports/measure-drill-1.json` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

Health checker: `--interval 5 --threshold 3 --timeout 2` → detect floor = 5 × 3 = **15s**
(`reports/health-events.jsonl:1`). t_outage = `1787651382.36` (`2026-08-25T09:49:42Z` / 16:49:42 UTC+7).

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0 | `action:kill` | `chaos/chaos-events.jsonl:1` |
| User thấy lỗi đầu tiên | 0.0 | dòng `ok:false` đầu (ReadTimeout qua edge) | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện | 15.0 | `to:UNHEALTHY, region:a` (ts 1787651397.33) | `reports/health-events.jsonl:3` |
| Operator biết tin / mở incident | 21.2 | `step:2 thong_bao_incident` (ts 1787651403.58) | `reports/runbook-run.jsonl:2` |
| Snapshot restore xong | 21.2 | `step:2_restore_snapshot` (ts 1787651403.60) | `reports/failover-events.jsonl:2` |
| Region phụ ready | 27.5 | `step:4_wait_ready`, `waited_s:6.22` (ts 1787651409.83) | `reports/failover-events.jsonl:4` |
| DNS cutover | 27.5 | `step:5_dns_cutover` (ts 1787651409.83) | `reports/failover-events.jsonl:5` |
| **RTO đo được** | **28.3** | dòng `ok:true, served_by:b` đầu sau lỗi | `reports/drill-2-withdr.jsonl:39` |

| Chỉ số | Đo được | Mục tiêu (slide §1) | Verdict |
|---|---|---|---|
| RTO — Inference API | `28.3s` | 300s (5 phút) | **PASS** (`reports/measure-drill-2.json`: `rto_measured_s: 28.3`) |
| RPO — Vector DB | `10.0s` / `5` doc | 300s (5 phút) | **PASS** (`reports/failover-events.jsonl:2`: `rpo_seconds:10.0, docs_lost:5`) |

RPO chi tiết: snapshot cuối `put` lúc `1787651395.28` với `latest_doc_ts 1787651393.28`; primary lúc restore có
`latest_doc_ts 1787651403.28` → mất 10.0s dữ liệu = 5 doc (ingest 0.5 doc/s). Cùng `embed_model_version`
`embed-model=vi-e5-base@v3` ở cả snapshot và bản restore → index tương thích.

## 3. RTO của tôi gồm những gì

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | **15.0** | `interval_s × threshold` = 5 × 3 trong `reports/health-events.jsonl:1`; đo thực tế t_detect − t_outage = 1787651397.33 − 1787651382.36 = 14.97 (`reports/health-events.jsonl:3`) | Giảm `interval` 5 → 3s, giữ `threshold=3` (floor 9s). Không hạ threshold xuống 1–2: 1 nhịp mạng lỗi sẽ kích failover → flapping. |
| Snapshot restore (kể cả xác nhận của runbook) | **6.3** | t(`2_restore_snapshot`) − t_detect = 1787651403.60 − 1787651397.33 (`reports/failover-events.jsonl:2`). Trong đó copy snapshot thật chỉ ~3ms (`reports/failover-events.jsonl:1` → `reports/failover-events.jsonl:2`); 6.2s còn lại là runbook bước 1 probe lại A 3 lần × timeout 2s để xác nhận trước khi hành động (`reports/runbook-run.jsonl:1`) | Runbook probe song song hoặc tin alert của health checker (đã 3 fail liên tiếp) thay vì probe lại tuần tự; snapshot đã rất nhanh vì `fs` backend. |
| GPU pool warm-up | **6.2** | `waited_s: 6.22` ở `4_wait_ready`, `reports/failover-events.jsonl:4` (`WARMUP_SECONDS` sau khi pool warm→full ở `:3`) | Giữ B ở `pool_state=full` thường trực (hot standby) — đổi bằng chi phí GPU idle; hoặc active-active. |
| DNS/LB TTL cache | **0.8** | t_recovered − t_cutover = (t_outage + 28.3) − 1787651409.83 (`reports/drill-2-withdr.jsonl:39` − `reports/failover-events.jsonl:5`); `ttl_seconds: 5.0` nhưng cache vừa hết hạn nên chỉ chờ 0.8s | Hạ `EDGE_TTL_SECONDS` 5 → 1; hoặc edge tự health-check upstream thay vì cache pointer. |
| **Tổng** | **28.3** | = `rto_measured_s` 28.3 trong `reports/measure-drill-2.json` (sai số 0.0s) | |