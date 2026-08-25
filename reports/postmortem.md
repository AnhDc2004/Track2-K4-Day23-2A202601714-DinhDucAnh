# Postmortem — DR Drill Lab 23

Blameless: câu hỏi là "hệ thống/process nào cho phép chuyện này", không phải "ai làm sai".
Giờ trong bảng là UTC+7 (giờ máy); `chaos-events.jsonl` ghi ISO theo UTC (09:49:42Z = 16:49:42).

## 1. Timeline (mọi dòng có evidence path:line)

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 16:49:42.36 (+0.0s) | outage bắt đầu — SIGSTOP region A (`netblock --mock`) | `chaos/chaos-events.jsonl:1` |
| 16:49:42.4 (+0.0s) | user đầu tiên bị ảnh hưởng: `ok:false`, ReadTimeout ~2s qua edge | `reports/drill-2-withdr.jsonl:25` |
| 16:49:57.33 (+15.0s) | health check alert: `region:a → UNHEALTHY` sau 3 probe timeout liên tiếp | `reports/health-events.jsonl:3` |
| 16:50:03.58 (+21.2s) | runbook xác nhận outage (alert + 3 probe timeout) | `reports/runbook-run.jsonl:1` |
| 16:50:03.58 (+21.2s) | operator biết tin / mở incident, `notify_delay_s: 21.22` | `reports/runbook-run.jsonl:2` |
| 16:50:03.58 (+21.2s) | operator confirm cutover (`--auto`) → `failover()` gọi 1 lần | `reports/runbook-run.jsonl:3` |
| 16:50:03.60 (+21.2s) | snapshot restore xong: `rpo_seconds 10.0, docs_lost 5`, model `vi-e5-base@v3` | `reports/failover-events.jsonl:2` |
| 16:50:03.61 (+21.2s) | pool B warm → full | `reports/failover-events.jsonl:3` |
| 16:50:09.83 (+27.5s) | region B ready (hết warm-up, `waited_s 6.22`, 215 vectors) | `reports/failover-events.jsonl:4` |
| 16:50:09.83 (+27.5s) | DNS cutover a → b | `reports/failover-events.jsonl:5` |
| 16:50:10.66 (+28.3s) | resolved — request đầu tiên `ok:true, served_by:b` | `reports/drill-2-withdr.jsonl:39` |
| 16:50:13.13 (+30.8s) | golden signals: 10/10 OK từ B, error rate 0, p95 44.6ms | `reports/runbook-run.jsonl:6` |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s · đo được: 28.3s · dư: 271.7s (`reports/measure-drill-2.json`)
- RPO mục tiêu: 300s · đo được: 10.0s (5 doc bị mất) · dư: 290s (`reports/failover-events.jsonl:2`)
- Bước tốn nhiều giây nhất: health-check detect floor — 15.0s ≈ 53% RTO. Vì health checker
  phải thấy đủ 3 lần fail cách nhau 5s mới được kết luận (chống flapping). Kế tiếp là hai
  khoảng 6.2s bằng nhau: runbook probe lại A 3 lần × timeout 2s để xác nhận, và GPU warm-up
  của B. Bản thân copy snapshot chỉ ~3ms; DNS TTL chỉ góp 0.8s vì cache vừa hết hạn.

Baseline (Drill 1, không DR): outage `10:02:00Z`, lỗi đầu tiên sau 0.1s, 16/32 request fail,
không có gì phát hiện, `rto_verdict: NO_RECOVERY` — region B lúc đó `count:0, weights:false,
pool_state:warm`, flip DNS cũng vô ích (`reports/measure-drill-1.json`).

Lần chạy drill 2 đầu tiên (đã huỷ, không dùng làm evidence) cho bài học thật: gọi runbook
ngay sau khi kill → cutover ở +12.5s trước khi health check báo (+19.3s) → `measure_rto`
cảnh báo "đo tay". Ngoài ra vòng poll của health checker ngủ trọn 5s sau khi probe A đã
treo 2s → chu kỳ 7s, detect trễ thành 19.3s. Đã sửa: runbook chờ alert từ
`health-events.jsonl`, health checker giữ cadence cố định.

## 3. Root cause (5 whys)

1. **Vì sao user thấy lỗi?** Region A bị network partition (mô phỏng SIGSTOP): TCP mở
   được nhưng không ai trả lời → client treo tới timeout 2s rồi 503.
2. **Vì sao không tự hồi phục?** Không có health checker; edge chỉ đọc pointer
   `edge/active_region` và cache 5s, không biết upstream chết.
3. **Vì sao không thể flip sang B ngay?** B "sống" (`/healthz` ok) nhưng `/readyz` 503:
   không có weights, vector DB rỗng, pool `warm`. Alive ≠ ready.
4. **Vì sao B không có state?** Replication chỉ chạy khi có người bật `state/replicate.py`;
   thiết kế ban đầu là active-passive lạnh, không có snapshot định kỳ.
5. **Vì sao process này được phép tồn tại?** Chưa từng có game day; RTO/RPO "5 phút" chỉ
   nằm trên slide, chưa được đo. Root cause thật: không có drill thì không có số.

Nếu là outage thật, bước dễ hỏng nhất trong runbook: bước 3 (restore) nếu snapshot chưa
từng `put` hoặc `embed_model_version` lệch giữa A và B; bước 4 nếu warm-up dài hơn `wait=60s`
(runbook sẽ abort đúng, nhưng không có kế hoạch B).

## 4. Action items (có owner + deadline)

| # | Action | Owner | Deadline | Giảm RTO/RPO bao nhiêu giây |
|---|---|---|---|---|
| 1 | Chạy `state/replicate.py --every 30` như service thường trực (systemd), alert nếu >2 chu kỳ không `put` | Platform | +1 tuần | RPO trần cố định ≤30s, không phụ thuộc người nhớ bật |
| 2 | Giảm `--interval 5` → `3`, giữ `threshold 3`; probe 2 region song song (thread) | SRE | +1 tuần | Detect floor 15s → 9s (−6s RTO) |
| 3 | Runbook bước 1 tin alert của health checker, bỏ 3 probe tuần tự có timeout | SRE | +1 tuần | −6s RTO (khoảng 21.2 − 15.0) |
| 4 | Giữ region B `pool_state=full` thường trực (hot standby) | Platform | +1 tháng | Bỏ warm-up 6.2s |
| 5 | Hạ `EDGE_TTL_SECONDS` 5 → 1 | SRE | +1 tuần | Trường hợp xấu nhất ở lớp DNS 5s → 1s |
| 6 | Game day hàng tháng, lưu output `measure_rto.py` làm evidence | Incident Commander | định kỳ | Giữ RTO/RPO luôn là số đo, không phải ước lượng |

Nếu làm đủ 2–5: RTO ước tính ≈ 9 + 0 + 0 + ≤1 ≈ 10s.

## 5. Ba câu hỏi bắt buộc trả lời

1. `interval × threshold` = 5 × 3 = 15s; đo thực tế 14.97s, chiếm 53% RTO 28.3s.
   Đây là sớm nhất có thể phát hiện. Với RTO 300s và phần còn lại ~13.3s, floor được phép
   ≈ 286s → interval tối đa ≈ 95s với threshold 3; thực tế nên giữ ≤ 10s để còn dư cho
   restore chậm hoặc warm-up dài hơn.
2. Hạ interval xuống 1s → floor 3s, RTO ≈ 16s (−12s). Trả giá: một nhịp mạng chập chờn 3s
   là đủ kích failover, và khi A hồi lại cũng chỉ cần 1 probe 200 → 2 region flap qua lại
   (§4 Anti-Patterns) — mỗi lần flap là thêm warm-up + mất data chiều ngược. Thêm nữa
   timeout 2s > interval 1s nên probe chồng nhau; phải hạ timeout hoặc tăng threshold lên
   5–10, và giữ rollback là hành động có người bấm.
3. Với outage 6 giờ và A mất dữ liệu vĩnh viễn, `docs_lost = 5` nghĩa là 5 ticket khách
   hàng ingest trong 10s cuối trước snapshot biến mất hoàn toàn: khách hỏi lại sẽ "không
   tìm thấy", phải nhập lại thủ công và doanh nghiệp phải xin lỗi. RPO không phải "tuổi
   snapshot" mà là số bản ghi khách hàng bị mất. Ở prod với hàng trăm doc/s, cùng chu kỳ
   30s sẽ mất hàng nghìn bản ghi — đó là lý do action item 1 quan trọng hơn mọi tối ưu RTO.