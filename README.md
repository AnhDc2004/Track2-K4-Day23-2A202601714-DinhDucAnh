# Lab 23 — Kill region của chính mình trước, rồi mới học cách sống sót

Đây là lab thực hành 2 giờ về Disaster Recovery và High Availability cho hạ tầng AI. Bạn sẽ dựng hai FastAPI process đóng vai hai region, tạo traffic liên tục, làm hỏng region chính rồi đo RTO/RPO từ timestamp trong log. Con số trên slide chỉ là mục tiêu; con số được chấp nhận trong lab phải truy ngược được về request và sự kiện thật.

## Chạy nhanh: bare mode

Bare mode là đường mặc định của lớp, không cần Docker hay tài khoản cloud.

```bash
pip install -r requirements.txt
make seed
bash scripts/up_bare.sh
curl localhost:8080/v1/infer
```

Kết quả ban đầu phải có `"edge_region":"a"` và câu trả lời bắt đầu bằng `"[a]"`. Dừng stack bằng:

```bash
bash scripts/down_bare.sh
```

Nếu máy đã có Docker Desktop đang chạy, có thể khởi động các service bằng `docker compose up -d`. Tuy nhiên, toàn bộ luồng drill và chấm điểm trong [GUIDE.md](GUIDE.md) dùng bare mode với `--mock` để kết quả không phụ thuộc tốc độ Docker hoặc mạng máy cá nhân.

## Cấu trúc repo

- `serving/`: inference API cho từng region; readiness phụ thuộc pool, model weights và vector DB.
- `edge/`: proxy đóng vai DNS/LB; `edge/active_region` là record đang được cache theo TTL.
- `state/`: seed, ingest, snapshot/restore và replication cho SQLite cùng model weights giả lập.
- `chaos/kill_region.py`: tạo lỗi `stop` hoặc `netblock`, ghi sự kiện chaos.
- `loadgen/traffic.py`: phát traffic và ghi một dòng JSONL cho mỗi request; đây là đồng hồ RTO.
- `dr/`: ba skeleton `health_checker.py`, `failover.py`, `runbook.py` mà sinh viên phải hoàn thiện.
- `tools/measure_rto.py`: tính RTO/RPO từ log, đồng thời kiểm tra drill có hợp lệ hay không.
- `tests/`: unit test cho phần DR và gate kiểm chứng evidence.
- `reports/`: template runbook, evidence, postmortem và các log sinh ra khi chạy drill.
- `scripts/`: bật/tắt stack bare mode; `docker-compose.yml` và hai Dockerfile phục vụ Docker mode.

Làm lab theo [GUIDE.md](GUIDE.md). Cách chấm và điều kiện trượt nằm trong [RUBRIC.md](RUBRIC.md).

## Nội quy an toàn

Chỉ tấn công stack local của chính bạn và chỉ dùng các endpoint `127.0.0.1`/`localhost` đã có trong repo; không đổi `URL`, `UPSTREAM` hoặc load-generator để trỏ sang máy khác. `--mock` chỉ dùng process signal và file local, backend `fs` chỉ dùng đĩa local; lab này không gọi hay phá bất kỳ cloud region thật nào.
