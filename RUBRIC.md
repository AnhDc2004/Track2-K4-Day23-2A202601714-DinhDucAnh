# Rubric Lab 23 — 100 điểm

Điểm được chấm từ code DR, log sinh ra trong chính lần drill của sinh viên và ba báo cáo trong `reports/`. Một con số không truy ngược được về timestamp thật không được tính là evidence.

## Bảng điểm

| Tiêu chí | Điểm | Cách đo chính xác |
|---|---:|---|
| Bước 2: attack thành công, region không tự phục hồi | 15 | `pytest tests/test_rto_evidence.py::test_drill1_ton_tai_va_khong_phuc_hoi`; đồng thời `python3 tools/measure_rto.py --loadgen reports/drill-1-nodr.jsonl --target-rto 300` phải có request fail và trả `"rto_verdict":"NO_RECOVERY"`. |
| `dr/health_checker.py`: chống flapping đúng threshold | 15 | `pytest tests/test_failover.py::test_health_checker_can_threshold_lien_tiep`; một lần fail không được đổi trạng thái, transition đầu sang `UNHEALTHY` phải có ít nhất ba fail liên tiếp. |
| `dr/failover.py`: không cutover trước khi target ready | 20 | `pytest tests/test_failover.py::test_failover_khong_cutover_khi_target_chua_ready`; `reports/failover-events.jsonl` phải có `1_verify_target → 2_restore_snapshot → 3_scale_pool → 4_wait_ready → 5_dns_cutover` theo đúng thứ tự. |
| RTO đo được ≤ 300 giây, log hợp lệ, phục hồi bằng region B | 20 | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` phải trả `"valid":true`, `"rto_verdict":"PASS"`; chạy thêm `pytest tests/test_rto_evidence.py::test_drill2_hop_le` và `pytest tests/test_rto_evidence.py::test_rto_do_duoc_bang_timestamp`. |
| RPO có cả giây và số document bị mất | 10 | `pytest tests/test_rto_evidence.py::test_rpo_duoc_do_chu_khong_uoc_luong`; output phải có `rpo_at_restore_s` và `docs_lost` khác `null`. Hàm tính RPO nền được kiểm tra bởi `pytest tests/test_failover.py::test_rpo_dem_dung_so_doc_bi_mat`. |
| `reports/rto-evidence.md`: evidence thật, điền đủ, số khớp log | 10 | `pytest tests/test_rto_evidence.py::test_evidence_table_tro_vao_file_that`, `pytest tests/test_rto_evidence.py::test_evidence_table_da_dien_that`, `pytest tests/test_rto_evidence.py::test_so_trong_bang_khop_voi_so_trong_log`; kiểm tra thêm cấu hình health check bằng `pytest tests/test_rto_evidence.py::test_health_check_interval_duoc_ghi_lai`. |
| `reports/runbook.md` và `reports/postmortem.md` đầy đủ | 10 | `pytest tests/test_rto_evidence.py::test_postmortem_co_gap_analysis`; `reports/runbook.md` được đối chiếu thủ công: đủ bảy bước, lệnh copy-paste, điều kiện hoàn tất, owner và rollback. Repo hiện không có test tự động riêng cho `reports/runbook.md`. |
| **Tổng** | **100** | Chạy toàn bộ bằng `python3 -m pytest tests/ -v`. |

## Điều kiện trượt cứng

Trượt bất kể tổng điểm nếu gặp một trong hai trường hợp:

1. `tools/measure_rto.py` trả `"valid":false` cho drill Bước 4. Các nguyên nhân gồm double outage do `--i-really-want-both`, kill khi region còn lại không alive, không có request fail sau kill, loadgen và chaos không cùng cửa sổ thời gian, hoặc request “phục hồi” vẫn do region đã bị giết phục vụ. Gate liên quan: `pytest tests/test_rto_evidence.py::test_drill2_hop_le`, `pytest tests/test_rto_evidence.py::test_rto_do_duoc_bang_timestamp`, `pytest tests/test_rto_evidence.py::test_chaos_khong_giet_ca_hai_region`.
2. RTO ghi trong `reports/rto-evidence.md` lệch quá 1 giây so với `rto_measured_s` do `tools/measure_rto.py` tính, hoặc RPO trong bảng không khớp log. Gate: `pytest tests/test_rto_evidence.py::test_so_trong_bang_khop_voi_so_trong_log`.

`test_drill2_hop_le` còn yêu cầu danh sách `warnings` rỗng. Đặc biệt, cutover trước lúc health checker phát hiện (`t_cutover < t_detect`) phải được sửa bằng cách chạy lại đúng automation, không dùng để nộp như một drill sạch.
