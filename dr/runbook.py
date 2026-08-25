"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

try:
    from dr.health_checker import probe as _probe  # noqa: E402
except Exception:  # noqa: BLE001
    _probe = None

LOG = pathlib.Path("reports/runbook-run.jsonl")
HEALTH_LOG = pathlib.Path("reports/health-events.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
EDGE = "http://127.0.0.1:8080"
CHAOS_LOG = pathlib.Path("chaos/chaos-events.jsonl")


def step(n, name, **kw):
    """TODO: ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time()}
    rec["iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(rec["ts"]))
    rec.update({"step": n, "name": name})
    rec.update(kw)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("RUNBOOK", json.dumps(rec, ensure_ascii=False), flush=True)
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """TODO: auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        return True
    ans = input(f"{msg} [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def probe(region: str, timeout: float = 2.0) -> tuple[bool, str]:
    if _probe is not None:
        return _probe(region, timeout)
    try:
        r = httpx.get(f"{URL[region]}/readyz", timeout=timeout)
        return r.status_code == 200, f"readyz_{r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def last_outage_ts():
    """Đọc t_outage_start gần nhất từ chaos-events.jsonl (nếu có)."""
    if not CHAOS_LOG.exists():
        return None
    for line in reversed(CHAOS_LOG.read_text().splitlines()):
        try:
            ev = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if ev.get("action") == "kill":
            return ev.get("ts")
    return None


def wait_for_alert(primary: str, after_ts, max_wait: float = 120.0):
    """Chờ health checker (3a) ghi state_change -> UNHEALTHY cho primary SAU t_outage.

    Đây là "alert" trong §4: operator chỉ hành động khi health check đã phát hiện,
    không phải khi chaos script vừa chạy — nếu không t_cutover < t_detect (đo tay).
    Trả về (ts_alert, line_no) hoặc (None, None) nếu hết max_wait.
    """
    t_end = time.time() + max_wait
    while time.time() < t_end:
        if HEALTH_LOG.exists():
            for i, line in enumerate(HEALTH_LOG.read_text().splitlines(), 1):
                try:
                    ev = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if (ev.get("event") == "state_change" and ev.get("region") == primary
                        and ev.get("to") == "UNHEALTHY"
                        and (after_ts is None or ev.get("ts", 0) >= after_ts)):
                    return ev.get("ts"), i
        time.sleep(0.5)
    return None, None


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """TODO: 7 bước ở trên."""
    t_start = time.time()
    out = {"ok": False, "primary": primary, "target": target, "backend": backend, "auto": auto}

    t_outage = last_outage_ts()
    t_alert, alert_line = wait_for_alert(primary, t_outage)
    probes = {r: [probe(r) for _ in range(3)] for r in (primary, target)}
    primary_down = all(not ok for ok, _ in probes[primary])
    step(1, "xac_nhan_outage", primary=primary, primary_down=primary_down,
         health_alert_ts=t_alert, health_alert_evidence=(
             f"reports/health-events.jsonl:{alert_line}" if alert_line else None),
         detect_delay_s=None if (t_alert is None or t_outage is None) else round(t_alert - t_outage, 2),
         primary_probes=[reason for _, reason in probes[primary]],
         target_probes=[reason for _, reason in probes[target]])
    if not primary_down:
        step(7, "post_incident", aborted=True, reason="primary van ready -> khong failover")
        out["error"] = "primary_not_down"
        return out

    t_notified = time.time()
    step(2, "thong_bao_incident", t_outage=t_outage, t_notified=t_notified,
         notify_delay_s=None if t_outage is None else round(t_notified - t_outage, 2),
         message=f"INCIDENT: region-{primary} DOWN, chuan bi failover sang region-{target}")

    if not confirm(auto, f"Failover {primary} -> {target} (backend={backend})?"):
        step(7, "post_incident", aborted=True, reason="operator tu choi confirm")
        out["error"] = "not_confirmed"
        return out

    res = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_ok=res.get("ok"), steps=res.get("steps"),
         wait_ready_s=res.get("wait_ready_s"), error=res.get("error"))
    out["failover"] = res

    rpo = res.get("rpo") or {}
    ready = res.get("ready_state") or {}
    step(4, "verify_state_replica", target=target,
         vectors=ready.get("vectors"), pool_state=ready.get("pool_state"),
         embed_model_version=res.get("embed_model_version"),
         rpo_seconds=rpo.get("rpo_seconds"), docs_lost=rpo.get("docs_lost"))

    cut = res.get("cutover") or {}
    step(5, "dns_cutover", ok=bool(cut.get("ok")), **{"from": cut.get("from"), "to": cut.get("to")})
    if not res.get("ok"):
        step(7, "post_incident", aborted=True, reason=res.get("error"),
             elapsed_s=round(time.time() - t_start, 2))
        out["error"] = res.get("error")
        return out

    lat, errs, regions = [], 0, {}
    for _ in range(10):
        t = time.time()
        try:
            r = httpx.get(f"{EDGE}/v1/infer", timeout=5.0)
            ok = r.status_code == 200
            try:
                reg = r.json().get("region")
                regions[reg] = regions.get(reg, 0) + 1
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            ok = False
        lat.append((time.time() - t) * 1000)
        if not ok:
            errs += 1
        time.sleep(0.3)
    lat_sorted = sorted(lat)
    p95 = lat_sorted[max(0, int(round(0.95 * len(lat_sorted))) - 1)]
    step(6, "verify_golden_signals", requests=10, errors=errs, error_rate=errs / 10,
         p95_latency_ms=round(p95, 1), served_by=regions)

    # 7 post_incident
    elapsed = round(time.time() - t_start, 2)
    step(7, "post_incident", elapsed_s=elapsed, failover_elapsed_s=res.get("elapsed_s"),
         rpo_seconds=rpo.get("rpo_seconds"), docs_lost=rpo.get("docs_lost"),
         measure_cmd="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    out["ok"] = True
    out["elapsed_s"] = elapsed
    out["golden_signals"] = {"errors": errs, "p95_latency_ms": round(p95, 1), "served_by": regions}
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))