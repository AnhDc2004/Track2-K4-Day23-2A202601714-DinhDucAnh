"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl

CÂU HỎI PHẢI TRẢ LỜI TRƯỚC KHI VIẾT (ghi câu trả lời vào reports/postmortem.md):
  interval=5s, threshold=3 -> sớm nhất bạn có thể phát hiện outage là bao nhiêu giây?
  Con số đó nằm TRONG RTO của bạn. Muốn RTO 5 phút thì được phép chọn interval bao nhiêu?
"""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """TODO: trả về (ready, reason). Timeout PHẢI có — netblock làm request treo mãi."""
    try:
        r = httpx.get(f"{URL[region]}/readyz", timeout=timeout)
        if r.status_code == 200:
            return True, "readyz_200"
        try:
            reasons = r.json().get("reasons") or r.json().get("reason") or r.text
        except Exception:
            reasons = r.text
        return False, f"readyz_{r.status_code}:{reasons}"
    except httpx.TimeoutException:
        return False, f"timeout_after_{timeout}s"
    except httpx.HTTPError as e:
        return False, f"conn_error:{type(e).__name__}"


def _emit(out: pathlib.Path, **kw):
    out.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time()}
    rec["iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(rec["ts"]))
    rec.update(kw)
    with out.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("HEALTH", json.dumps(rec, ensure_ascii=False), flush=True)


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """TODO: vòng lặp poll + phát hiện transition + ghi JSONL."""
    state = {r: "HEALTHY" for r in URL}
    fails = {r: 0 for r in URL}
    _emit(out, event="start", interval_s=interval, threshold=threshold,
          timeout_s=timeout, detect_floor_s=interval * threshold)

    t_end = time.time() + duration
    while time.time() < t_end:
        t_cycle = time.time()
        for region in URL:
            ready, reason = probe(region, timeout)
            if ready:
                if state[region] == "UNHEALTHY":
                    state[region] = "HEALTHY"
                    _emit(out, event="state_change", region=region, to="HEALTHY",
                          reason=reason, consecutive_fails=fails[region],
                          interval_s=interval, threshold=threshold)
                fails[region] = 0
            else:
                fails[region] += 1
                if fails[region] >= threshold and state[region] == "HEALTHY":
                    state[region] = "UNHEALTHY"
                    _emit(out, event="state_change", region=region, to="UNHEALTHY",
                          reason=reason, consecutive_fails=fails[region],
                          interval_s=interval, threshold=threshold)
        time.sleep(max(0.0, interval - (time.time() - t_cycle)))

    _emit(out, event="stop", final_state=state)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))