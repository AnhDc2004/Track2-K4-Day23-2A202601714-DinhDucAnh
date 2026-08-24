# Hướng dẫn Lab 23 — Region Failover

## Mục tiêu

Bạn sẽ làm hỏng region chính trước, quan sát request của mình thất bại, rồi mới xây đường phục hồi và chạy lại cùng cuộc tấn công. Toàn bộ hệ thống chạy local/offline sau bước cài dependency: hai FastAPI process thay cho hai region, SQLite thay cho vector DB, file thay cho DNS và object storage. Không cần AWS hay budget cloud; điều cần giữ đúng là hành vi health-check polling, replication lag, GPU warm-up và DNS TTL cache.

Kết thúc lab, bạn phải chứng minh được:

- baseline không có DR không tự phục hồi;
- failover chỉ cutover sau khi region phụ thật sự ready;
- RTO không vượt 300 giây và được tính từ log request;
- RPO có cả số giây lẫn số document bị mất;
- mọi số trong báo cáo trỏ về `đường/dẫn:số_dòng` thật.

## Timeline 2 giờ

| Bước | Thời lượng | Việc chính | Ngữ cảnh |
|---|---:|---|---|
| 0. Setup | 10 phút | Cài dependency, seed state, bật stack | Chuẩn bị đồng hồ và hệ thống thử nghiệm |
| 1. Baseline | 15 phút | Đọc đường đi request, kiểm tra region B | §1 RTO/RPO fundamentals, case study outage |
| 2. Red team | 25 phút | Giết region A khi đang có traffic | §1 Case Study, §4 Anti-Patterns |
| 3. Contain | 50 phút | Viết ba file DR và runbook vận hành | §2 Multi-Region Patterns, §3 State Recovery, §4 Failover Automation |
| 4. Prove | 20 phút | Tấn công lại, đo RTO/RPO, lập evidence | §4 Runbook/Postmortem, §6 Game Day |

## Bước 0 — Setup (10 phút)

Sau khi clone, đi vào thư mục repo:

```bash
cd lab23-region-failover
```

Sau đó chạy:

```bash
pip install -r requirements.txt
make seed
bash scripts/up_bare.sh
curl localhost:8080/v1/infer
```

`make seed` tạo region A với 200 document và model weights; region B khởi đầu rỗng. Request cuối phải trả JSON có `"edge_region":"a"` và `"answer":"[a] ..."`.

Checkpoint: cả ba endpoint phải phản hồi:

```bash
curl localhost:8001/healthz
curl localhost:8002/healthz
curl localhost:8080/edge/state
```

Nếu một service không lên, đọc `run/region-a.log`, `run/region-b.log` hoặc `run/edge.log`. Đừng cài Docker giữa giờ để chữa lỗi bare mode; kiểm tra dependency và xung đột cổng 8001, 8002, 8080 trước.

## Bước 1 — Baseline (15 phút)

Đọc `serving/app.py` và `edge/proxy.py`, rồi vẽ đường đi của một request qua ba lớp:

1. `edge/proxy.py`: DNS/LB giả lập, chọn upstream từ `edge/active_region` nhưng có TTL cache.
2. `serving/app.py`: compute/inference; `/readyz` kiểm tra pool, weights và vector count.
3. `state/`: vector DB SQLite cùng model weights trên đĩa.

Quan sát region phụ:

```bash
curl localhost:8002/v1/state
```

Trả lời trước khi viết code:

1. Nếu region A chết ngay bây giờ, thành phần nào phát hiện?
2. Region B hiện có dữ liệu và model weights hay không?
3. Nếu đổi `edge/active_region` thành `b` ngay lúc này, người dùng nhận gì?

Kết quả mong đợi: chưa có thành phần nào phát hiện outage; B có `count:0`, `weights:false`; chuyển traffic sớm sẽ trả `region_not_ready`. Với AI infrastructure, một process sống chưa đồng nghĩa region có thể phục vụ inference.

## Bước 2 — Red team (25 phút)

Mở traffic 2 request/giây trong 40 giây, chờ 8 giây rồi làm region A treo:

```bash
python3 loadgen/traffic.py --duration 40 --rps 2 --out reports/drill-1-nodr.jsonl &
sleep 8
python3 chaos/kill_region.py --region a --mode netblock --mock
```

Trong bare mode, `netblock --mock` gửi `SIGSTOP`: kết nối có thể mở nhưng ứng dụng không trả lời, tương tự packet bị DROP, nên client chờ đến timeout. `--mode stop` dùng `SIGKILL`, thường tạo lỗi kết nối ngay.

Đợi load generator kết thúc, sau đó đo baseline:

```bash
python3 tools/measure_rto.py --loadgen reports/drill-1-nodr.jsonl --target-rto 300
```

Một lần chạy tham chiếu tạo 32 request: request lỗi đầu tiên xuất hiện khoảng `+0.2s`, có latency `2017.7ms` tại `reports/drill-1-nodr.jsonl:17`; 16/32 request thất bại và kết quả là `"rto_verdict":"NO_RECOVERY"`. Đây chỉ là ví dụ; evidence của bạn phải lấy từ log của chính bạn.

Khôi phục region A trước khi sang bước tiếp theo. Ở bare mode, cờ `--backend bare` là bắt buộc:

```bash
python3 chaos/kill_region.py restore --region a --backend bare
```

`restore` không có `--mock`; nếu bỏ `--backend bare`, script mặc định chọn Docker. Nếu trước đó dùng `--mode stop`, process đã bị `SIGKILL`; sự kiện restore sẽ báo `need_manual_start`, khi đó dừng và bật lại stack bare mode.

## Bước 3 — Contain (50 phút)

Ba file trong `dr/` là skeleton có `NotImplementedError`. Không sửa công cụ có sẵn để làm test dễ hơn. Mỗi file đã có docstring mô tả field log, thứ tự bước và CLI; đọc toàn bộ docstring trước khi triển khai.

### 3a. `dr/health_checker.py` — 12 phút

Poll `/readyz` của cả hai region mỗi `interval` giây. Chỉ chuyển sang `UNHEALTHY` sau `threshold` lần lỗi liên tiếp và chỉ ghi JSONL khi trạng thái thay đổi. Dòng `state_change` phải có ít nhất `ts`, `region`, `to`, `reason`, `interval_s`, `threshold`; test còn kiểm tra `consecutive_fails`.

`interval × threshold` là detect floor nằm trong RTO. Với `interval=5s`, `threshold=3`, sàn phát hiện là 15 giây. Trong lần chạy tham chiếu, 15 giây này chiếm khoảng 53% RTO 28.5 giây.

Chạy unit test đúng phần này:

```bash
pytest tests/test_failover.py::test_health_checker_can_threshold_lien_tiep
```

### 3b. `dr/failover.py` — 15 phút

Giữ đúng năm bước và đúng thứ tự:

1. `1_verify_target`
2. `2_restore_snapshot`
3. `3_scale_pool`
4. `4_wait_ready`
5. `5_dns_cutover`

Restore bằng các hàm trong `state/snapshot.py`; RPO phải có `rpo_seconds`, `docs_lost`, `embed_model_version`. Chỉ ghi `b` vào `edge/active_region` sau khi `/readyz` của B trả 200. Nếu hết `--wait`, abort và giữ nguyên active region.

Chạy unit test chống cutover sớm:

```bash
pytest tests/test_failover.py::test_failover_khong_cutover_khi_target_chua_ready
```

### 3c. `dr/runbook.py` — 13 phút

Tự động hoá bảy bước của §4 “Runbook: Region Chính Down”: xác nhận outage, thông báo incident và bấm giờ, gọi `failover.failover(...)` đúng một lần, verify state replica, đọc kết quả DNS cutover, đo golden signals bằng 10 request thật, rồi ghi post-incident. Mặc định phải hỏi `y/N`; `--auto` chỉ dành cho drill chấm điểm/CI.

Log riêng của runbook là `reports/runbook-run.jsonl`. Log năm bước con của failover là `reports/failover-events.jsonl`.

### 3d. `reports/runbook.md` — 10 phút

Điền template một trang để người không viết code vẫn vận hành được lúc 3 giờ sáng. Mỗi bước cần:

- lệnh copy-paste được;
- dấu hiệu xác nhận bước đã xong;
- vai trò chịu trách nhiệm;
- điều kiện rollback về region A và người có quyền quyết định.

Runbook phải tránh failover hai chiều liên tục: full-auto không có circuit breaker là anti-pattern của §4.

## Bước 4 — Prove và thu evidence (20 phút)

`dr/failover.py` sẽ restore snapshot vào region B. Repo mới chưa có snapshot, vì vậy phải chạy ingest và replication trước loadgen/chaos/runbook. `state/replicate.py` gọi `put` ngay đầu chu kỳ; `sleep 5` cho lần snapshot đầu hoàn tất.

Chạy nguyên khối sau bằng bare mode:

```bash
python3 state/ingest.py --region a --rate 0.5 --duration 150 &
python3 state/replicate.py --every 30 --duration 150 --backend fs &
sleep 5   # cho chu ky replicate dau tien chay xong, khong thi snapshot rong

python3 loadgen/traffic.py --duration 100 --rps 2 --out reports/drill-2-withdr.jsonl &
python3 dr/health_checker.py --interval 5 --threshold 3 --duration 100 \
    --out reports/health-events.jsonl &
sleep 12
python3 chaos/kill_region.py --region a --mode netblock --mock
python3 dr/runbook.py --primary a --target b --backend fs --auto
python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300
```

### Đọc kết quả

Một lần chạy tham chiếu cho các mốc sau. Đây là ví dụ minh hoạ, không phải đáp án để chép:

| Mốc từ `t_outage` | +giây | Evidence của lần chạy tham chiếu |
|---|---:|---|
| User thấy lỗi đầu tiên | +0.2 | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện A `UNHEALTHY` | +14.9 | `reports/health-events.jsonl:3` |
| Snapshot restore xong | +17.2 | `reports/failover-events.jsonl:2` |
| Region B ready | +23.3 | `reports/failover-events.jsonl:4` |
| DNS cutover | +23.4 | `reports/failover-events.jsonl:5` |
| Request đầu tiên thành công từ B | +28.5 | `reports/drill-2-withdr.jsonl:39` |

Trong ví dụ đó, RTO là 28.5 giây so với mục tiêu 300 giây: PASS. RPO của một lần chạy là 14.04 giây/7 document; lần khác là 4.01 giây/2 document. RPO dao động tự nhiên theo vị trí của lần snapshot gần nhất trong chu kỳ 30 giây, còn RTO tham chiếu ổn định khoảng 28.3–28.6 giây. Không chép bất kỳ số RPO mẫu nào vào bài của bạn.

### Hoàn thiện evidence và postmortem

Điền `reports/rto-evidence.md` và `reports/postmortem.md` bằng số của lần chạy hiện tại. Mọi ô Evidence dùng `đường/dẫn:số_dòng` thật. Bảng phân rã RTO phải có bốn phần và tổng phải khớp RTO đo được:

1. health-check detect floor;
2. snapshot restore;
3. GPU pool warm-up;
4. DNS/LB TTL cache.

Chạy gate tài liệu:

```bash
pytest tests/test_rto_evidence.py::test_evidence_table_tro_vao_file_that
pytest tests/test_rto_evidence.py::test_evidence_table_da_dien_that
pytest tests/test_rto_evidence.py::test_so_trong_bang_khop_voi_so_trong_log
pytest tests/test_rto_evidence.py::test_postmortem_co_gap_analysis
```

Cuối cùng chạy toàn bộ test:

```bash
python3 -m pytest tests/ -v
```

## Bẫy đã lường trước

| Bẫy | Cách xử lý |
|---|---|
| Không có Docker | Dùng `bash scripts/up_bare.sh`; đây là đường chính, không phải phương án hạng hai. |
| MinIO làm mất thời gian setup | Dùng `--backend fs`, snapshot nằm dưới `state/_replica/`. MinIO là stretch goal. |
| Giết cả hai region | Script từ chối nếu region còn lại không alive. `--i-really-want-both` ghi `forced_both:true` và làm drill `INVALID`. |
| Đo RTO bằng cảm giác | Chỉ chấp nhận timestamp trong log của `loadgen/traffic.py`. Kill phải nằm trong đúng cửa sổ loadgen. |
| Quên detect floor | Ghi `interval_s` và `threshold` trong health event; `interval × threshold` phải xuất hiện riêng trong phân rã RTO. |
| Đổi `edge/active_region` bằng tay | `measure_rto.py` cảnh báo nếu `t_cutover < t_detect`; gate `test_drill2_hop_le` không chấp nhận warning. Chạy lại drill bằng automation. |
| Tính warm-up ngay lúc boot | `serving/app.py` chỉ bắt đầu đếm warm-up khi `pool_state` đổi trong lúc process đang chạy; đừng sửa serving để né thời gian này. |
| Hiểu `netblock` là process chết hẳn | Bare mode dùng `SIGSTOP`; khôi phục bằng `restore --region a --backend bare`. `stop` mới dùng `SIGKILL`. |
| Chưa có snapshot | Bật `state/replicate.py` và chờ lần `put` đầu trước failover; nếu không `state/snapshot.py get` dừng ở `2_restore_snapshot`. |
| Dùng Docker mode cho drill chấm điểm | `--backend docker` dùng chung một `docker-compose.yml`, đủ chạy nhưng KHÔNG reproducible bằng bare `--mock` (phụ thuộc tốc độ Docker/mạng máy). Drill bắt buộc ở Bước 2 và Bước 4 luôn dùng bare `--mock`. |

## Debug nhanh

- Cổng 8001/8002/8080 bị chiếm: chạy `bash scripts/down_bare.sh`, xác định process đang giữ cổng, rồi bật lại; xem `run/*.log`.
- Region A không ready ngay từ đầu: chạy lại `make seed`; kiểm tra `state/region-a/pool_state`, weights và SQLite đã được tạo.
- Restore báo chưa từng có `put`: kiểm tra `reports/replication.jsonl` và `state/_replica/dr-artifacts/MANIFEST.json`.
- Baseline không có request fail: xác nhận loadgen đi qua cổng 8080 và sự kiện kill nằm giữa timestamp đầu/cuối của file JSONL.
- Drill cũ làm log khó đọc: `make clean` xoá state/log sinh ra; sau đó chạy lại `make seed` và `bash scripts/up_bare.sh`.

## Stretch goals

1. **MinIO thật:** chạy `docker compose up minio`, lặp lại snapshot/failover với `--backend minio`, so sánh thời gian `put/get` với backend `fs`.
2. **Postgres PITR:** thêm metadata store Postgres, dùng `pg_basebackup` và WAL archiving, restore về một thời điểm cụ thể rồi đo RTO riêng cho lớp metadata (§3 PITR).
3. **Active-active:** giữ hai region ở `pool_state=full`, route 50/50 qua edge và thiết kế conflict resolution khi cả hai phía ingest (§2 Active-Passive vs Active-Active).
4. **Terraform:** viết, không chạy, một `aws_s3_bucket_replication_configuration`; map `put/get` trong `state/snapshot.py` với cross-region replication và `MANIFEST.json` với versioning.
5. **Chaos ngẫu nhiên:** random thời điểm kill cùng mode `stop`/`netblock`, chạy năm lần, tính RTO trung bình và độ lệch (§6 Chaos Engineering Nhẹ).
6. **DR maturity:** tự chấm hệ thống từ Level 0–4 và nêu thay đổi cụ thể để đạt level kế tiếp.

## Ba câu hỏi chốt buổi

1. RTO của bạn gồm detect floor, snapshot restore, GPU warm-up và DNS TTL cache. Có thể giảm phần nào mà không làm tăng nguy cơ flapping, và phải trả giá gì?
2. Nếu `dr/health_checker.py` chạy cùng process với serving API mà nó theo dõi, khi process đó chết thì ai phát cảnh báo? Kiểm tra health checker của bạn có phụ thuộc/import từ `serving/` hay không.
3. Khi được hỏi “RTO 5 phút có đúng không?”, bạn mở file log/evidence nào để trả lời bằng số thật thay vì ước lượng?
