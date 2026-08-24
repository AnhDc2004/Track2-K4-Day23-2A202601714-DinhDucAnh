# Postmortem — DR Drill Lab 23 (TEMPLATE)

Theo đúng template §4 "Sau Failover: Blameless Postmortem". Blameless: câu hỏi là
"hệ thống/process nào cho phép chuyện này", không phải "ai làm sai".

## 1. Timeline (mọi dòng phải có evidence path:line)

| ISO time | Sự kiện | Evidence |
|---|---|---|
| | outage bắt đầu | |
| | user đầu tiên bị ảnh hưởng | |
| | health check alert | |
| | operator confirm cutover | |
| | resolved (request đầu tiên OK từ region phụ) | |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s · đo được: `__s` · gap: `__s`
- RPO mục tiêu: 300s · đo được: `__s` (`__` doc bị mất) · gap: `__s`
- **Bước tốn nhiều giây nhất:** `____` — vì sao?

## 3. Root cause (5 whys)

Không phải "vì tôi chạy chaos script". Câu hỏi: *nếu đây là outage thật, bước nào
trong runbook của tôi sẽ thất bại?*

## 4. Action items (có owner + deadline)

| # | Action | Owner | Deadline | Giảm RTO/RPO bao nhiêu giây |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |

## 5. Ba câu hỏi bắt buộc trả lời

1. `interval × threshold` của bạn là bao nhiêu giây? Nó chiếm bao nhiêu % RTO?
2. Nếu hạ interval xuống 1s, RTO giảm mấy giây — và bạn trả giá gì (§4 flapping)?
3. Nếu outage kéo dài 6 giờ và region chính mất dữ liệu vĩnh viễn, `docs_lost` của
   bạn có nghĩa gì với khách hàng?
