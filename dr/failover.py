"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")
ACTIVE = pathlib.Path("edge/active_region")


def emit(**kw):
    """TODO: append 1 dòng JSONL có ts + iso vào LOG, và print ra stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time()}
    rec["iso"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(rec["ts"]))
    rec.update(kw)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("FAILOVER", json.dumps(rec, ensure_ascii=False), flush=True)
    return rec


def state_of(region: str) -> dict:
    """Đọc /v1/state; không bao giờ raise (region có thể đang chết)."""
    try:
        return httpx.get(f"{URL[region]}/v1/state", timeout=3.0).json()
    except Exception as e:  # noqa: BLE001
        return {"region": region, "error": f"{type(e).__name__}: {e}"}


def failover(target: str, backend: str, wait: float) -> dict:
    """TODO: 5 bước ở trên, đúng thứ tự."""
    primary = "a" if target == "b" else "b"
    t0 = time.time()
    result = {"ok": False, "target": target, "primary": primary, "backend": backend,
              "t_start": t0, "steps": []}

    st = state_of(target)
    emit(step="1_verify_target", region=target, pool_state=st.get("pool_state"),
         weights=st.get("weights"), count=st.get("count"), error=st.get("error"))
    result["steps"].append("1_verify_target")
    result["target_state_before"] = st

    try:
        meta = snapshot.get(target, backend)
    except SystemExit as e:
        emit(step="2_restore_snapshot", ok=False, error=str(e))
        result["error"] = f"restore_failed: {e}"
        return result
    rpo = snapshot.rpo(pathlib.Path(f"state/region-{primary}/vectors.sqlite"),
                       pathlib.Path(f"state/region-{target}/vectors.sqlite"))
    emit(step="2_restore_snapshot", ok=True, region=target,
         rpo_seconds=rpo.get("rpo_seconds"), docs_lost=rpo.get("docs_lost"),
         embed_model_version=meta.get("embed_model_version"),
         snapshot_at=meta.get("snapshot_at"), snapshot_latest_doc_ts=meta.get("latest_doc_ts"),
         primary_latest_doc_ts=rpo.get("primary_latest_doc_ts"),
         restored_latest_doc_ts=rpo.get("restored_latest_doc_ts"))
    result["steps"].append("2_restore_snapshot")
    result["rpo"] = rpo
    result["embed_model_version"] = meta.get("embed_model_version")

    # 3_scale_pool
    pool = pathlib.Path(f"state/region-{target}/pool_state")
    prev = pool.read_text().strip() if pool.exists() else None
    pool.parent.mkdir(parents=True, exist_ok=True)
    pool.write_text("full\n")
    emit(step="3_scale_pool", region=target, **{"from": prev, "to": "full"})
    result["steps"].append("3_scale_pool")

    # 4_wait_ready
    t_wait = time.time()
    ready = False
    last_reason = None
    while time.time() - t_wait < wait:
        try:
            r = httpx.get(f"{URL[target]}/readyz", timeout=2.0)
            if r.status_code == 200:
                ready = True
                try:
                    result["ready_state"] = r.json()
                except Exception:  # noqa: BLE001
                    result["ready_state"] = {}
                break
            try:
                last_reason = r.json().get("reasons", r.text)
            except Exception:  # noqa: BLE001
                last_reason = r.text
        except Exception as e:  # noqa: BLE001
            last_reason = f"{type(e).__name__}"
        time.sleep(0.5)
    waited = round(time.time() - t_wait, 2)
    emit(step="4_wait_ready", region=target, ready=ready, waited_s=waited,
         last_reason=last_reason, **{k: result.get("ready_state", {}).get(k)
                                     for k in ("pool_state", "vectors")})
    result["steps"].append("4_wait_ready")
    result["wait_ready_s"] = waited
    if not ready:
        emit(step="abort", reason=f"target {target} not ready after {wait}s -> KHONG cutover",
             active_region_unchanged=True)
        result["error"] = "target_not_ready"
        return result

    before = ACTIVE.read_text().strip() if ACTIVE.exists() else None
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE.write_text(target)
    emit(step="5_dns_cutover", **{"from": before, "to": target})
    result["steps"].append("5_dns_cutover")
    result["cutover"] = {"from": before, "to": target, "ok": True}
    result["ok"] = True
    result["elapsed_s"] = round(time.time() - t0, 2)
    return result

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))