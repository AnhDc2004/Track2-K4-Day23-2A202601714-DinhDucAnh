# RTO/RPO Evidence — Lab 23 (TEMPLATE — sinh viên điền bằng SỐ CỦA MÌNH)

Quy tắc duy nhất: mỗi con số ở đây phải trỏ được về **một dòng log thật**
(`đường/dẫn.jsonl:số_dòng`). `pytest tests/test_rto_evidence.py` sẽ mở từng file ra kiểm tra.
Con số không có evidence = trượt, bất kể các phần khác.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | `<iso>` | chaos kill | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | `+__s` | dòng `ok:false` đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:__` |
| Request thành công sau đó | không có | không có dòng `ok:true` nào sau t_outage | `reports/measure-drill-1.json` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0 | `action:kill` | `chaos/chaos-events.jsonl:__` |
| User thấy lỗi đầu tiên | | dòng `ok:false` đầu | `reports/drill-2-withdr.jsonl:__` |
| Health check phát hiện | | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:__` |
| Snapshot restore xong | | `step:2_restore_snapshot` | `reports/failover-events.jsonl:__` |
| Region phụ ready | | `step:4_wait_ready` | `reports/failover-events.jsonl:__` |
| DNS cutover | | `step:5_dns_cutover` | `reports/failover-events.jsonl:__` |
| **RTO đo được** | | dòng `ok:true` đầu sau lỗi | `reports/drill-2-withdr.jsonl:__` |

| Chỉ số | Đo được | Mục tiêu (slide §1) | Verdict |
|---|---|---|---|
| RTO — Inference API | `__s` | 300s (5 phút) | |
| RPO — Vector DB | `__s` / `__` doc | 300s (5 phút) | |

## 3. RTO của tôi gồm những gì (bắt buộc — đây là phần chấm điểm hiểu bài)

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | | `interval_s × threshold` trong `reports/health-events.jsonl:__` | |
| Snapshot restore | | 2_restore → 3_scale | |
| GPU pool warm-up | | `waited_s` ở `4_wait_ready` | |
| DNS/LB TTL cache | | t_recovered − t_cutover | |
