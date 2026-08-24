# Runbook 1 trang — Region chính down (TEMPLATE, sinh viên điền)

Runbook phải chạy được lúc 3h sáng bởi người KHÔNG viết nó. Mỗi bước: lệnh copy-paste
được + cách biết bước đó xong.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python chaos/kill_region.py status` | `a.alive=false` 3 lần liên tiếp | on-call |
| 2 | Mở incident + bấm giờ RTO | | ts ghi vào `reports/runbook-run.jsonl` | on-call |
| 3 | Restore state ở region phụ | `python state/snapshot.py get --region b --backend fs` | | |
| 4 | Scale pool warm→full | | `/readyz` của b trả 200 | |
| 5 | DNS/LB cutover | | `curl localhost:8080/edge/state` cho `active_region=b` | |
| 6 | Verify golden signals | | p95 < ___ms, error rate < ___ | |
| 7 | Đo RTO + postmortem | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl` | `rto_verdict` != null | |

**Rollback (failover ngược):** điều kiện nào thì trả traffic về region A? Ai quyết định?
(§4 Anti-Patterns: full-auto không có circuit breaker → 2 region flap qua lại.)
