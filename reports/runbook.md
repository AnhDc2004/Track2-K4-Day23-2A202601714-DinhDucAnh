# Runbook 1 trang — Region chính down

Runbook phải chạy được lúc 3h sáng bởi người KHÔNG viết nó. Mỗi bước: lệnh copy-paste
được + cách biết bước đó xong. Toàn bộ chạy từ thư mục gốc repo, bare mode, backend `fs`.

**Tự động hoá toàn bộ 7 bước (bán tự động, có hỏi Y/N):**
`python3 dr/runbook.py --primary a --target b --backend fs`
Các dòng dưới là lệnh thủ công tương đương, dùng khi cần chạy từng bước hoặc script tự động hỏng.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `for i in 1 2 3; do curl -s -m 2 -o /dev/null -w "%{http_code}\n" localhost:8001/readyz; sleep 5; done` | 3 lần liên tiếp KHÔNG trả `200` (timeout `000` hoặc `503`) và `tail -1 reports/health-events.jsonl` có `to:UNHEALTHY, region:a`. Đồng thời `curl -s localhost:8002/healthz` phải `alive:true` (region phụ còn sống — nếu không, STOP, không failover). | on-call SRE |
| 2 | Mở incident + bấm giờ RTO | `python3 - <<'X'`<br>`import json,time;print(json.dumps({"ts":time.time(),"step":2,"name":"thong_bao_incident","msg":"region-a DOWN"}))`<br>`X`<br>`>> reports/runbook-run.jsonl` (hoặc để `dr/runbook.py` ghi) | Có dòng `step:2` trong `reports/runbook-run.jsonl`; `ts` của nó > `ts` dòng `action:kill` mới nhất trong `chaos/chaos-events.jsonl`. Thông báo kênh incident. | on-call SRE (Incident Commander được thông báo) |
| 3 | Restore state ở region phụ | `python3 state/snapshot.py get --region b --backend fs` | Lệnh in JSON có `embed_model_version` và `restored_at`; `curl -s localhost:8002/v1/state` cho `weights:true`, `count>0`. Ghi lại `rpo_seconds`/`docs_lost` (`python3 state/snapshot.py lag --backend fs`). Nếu lỗi "khong tim thay MANIFEST" → chưa từng có snapshot, escalate cho Data/Platform. | on-call SRE |
| 4 | Scale pool warm→full | `echo full > state/region-b/pool_state` rồi `until curl -s -m 2 -o /dev/null -w "%{http_code}" localhost:8002/readyz \| grep -q 200; do sleep 1; done; echo READY` | `/readyz` của b trả `200` (hết `WARMUP_SECONDS`). Chưa 200 thì TUYỆT ĐỐI KHÔNG sang bước 5. Quá 60s không ready → abort, giữ nguyên `edge/active_region=a`, escalate. | on-call SRE |
| 5 | DNS/LB cutover | `echo b > edge/active_region` | `curl -s localhost:8080/edge/state` cho `active_region:"b"` (tối đa 5s do `ttl_seconds`); `curl -s localhost:8080/v1/infer` trả answer bắt đầu `[b]`. | on-call SRE, chỉ sau khi Incident Commander confirm (Y/N) |
| 6 | Verify golden signals | `for i in $(seq 10); do curl -s -m 5 -o /dev/null -w "%{http_code} %{time_total}\n" localhost:8080/v1/infer; sleep 0.3; done` | ≥ 9/10 trả `200` (error rate < 10%), p95 latency < 500ms, `region` trong response là `b`. Không đạt → xem bước Rollback. | on-call SRE |
| 7 | Đo RTO + postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | Output có `valid:true`, `rto_verdict:"PASS"`, `warnings:[]`, `rpo_at_restore_s`/`docs_lost` khác null. Điền `reports/rto-evidence.md` và `reports/postmortem.md` trong 24h. | on-call SRE viết, Incident Commander review |

**Rollback (failover ngược về A):**

- Điều kiện: (1) region A trả `/readyz` 200 liên tiếp ≥ 3 lần cách nhau 5s (`for i in 1 2 3; do curl -s -m 2 -o /dev/null -w "%{http_code}\n" localhost:8001/readyz; sleep 5; done`) và đã ổn định tối thiểu 10 phút — cùng ngưỡng anti-flap với health checker, không rollback ngay lần 200 đầu tiên; hoặc (2) region B không đạt golden signals ở bước 6 (error rate ≥ 10% / p95 ≥ 500ms) trong khi A đã 200 liên tiếp 3 lần.
- *Trước khi rollback:* ingest lại vào A những doc đã ghi vào B sau cutover (`state/snapshot.py put --region b` → `get --region a`), nếu không sẽ mất dữ liệu chiều ngược.
- Lệnh: `python3 chaos/kill_region.py restore --region a --backend bare` (nếu process bị SIGSTOP) → xác nhận 3×200 → `echo a > edge/active_region` → chạy lại bước 6 với `[a]`.
- Ai quyết định: chỉ Incident Commander ra lệnh rollback (không phải on-call, không tự động). Lý do: §4 Anti-Patterns — failover 2 chiều full-auto không có circuit breaker sẽ khiến 2 region flap qua lại khi A chập chờn. Rollback là hành động có người bấm, tối đa 1 lần/30 phút.