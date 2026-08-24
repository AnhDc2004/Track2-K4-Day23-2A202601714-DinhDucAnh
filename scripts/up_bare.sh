#!/usr/bin/env bash
# make up-bare: 2 "region" + edge chạy trực tiếp bằng uvicorn, KHÔNG cần docker daemon.
# Dùng khi Docker Desktop chưa có/chưa chạy, và là đường chấm điểm của --mock.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p run reports
: > run/region-a.pid; : > run/region-b.pid; : > run/edge.pid

start_region () {  # $1=region $2=port
  REGION=$1 STATE_DIR=state/region-$1 WARMUP_SECONDS=${WARMUP_SECONDS:-6} \
  python3 -m uvicorn serving.app:app --host 127.0.0.1 --port $2 --log-level warning \
    > run/region-$1.log 2>&1 &
  echo $! > run/region-$1.pid
  echo "region-$1 pid=$(cat run/region-$1.pid) port=$2"
}

start_region a 8001
start_region b 8002
EDGE_TTL_SECONDS=${EDGE_TTL_SECONDS:-5} python3 -m uvicorn edge.proxy:app \
  --host 127.0.0.1 --port 8080 --log-level warning > run/edge.log 2>&1 &
echo $! > run/edge.pid
echo "edge pid=$(cat run/edge.pid) port=8080"

# PID sống KHÔNG có nghĩa là uvicorn đã bind xong cổng (port bị chiếm, import lỗi,
# v.v. vẫn cho pid hợp lệ trong vài trăm ms đầu). Verify bằng /healthz thật, không
# tin theo tên biến port đã echo ở trên.
echo "cho service len (toi da 10s)..."
ok=1
for name_port in "region-a:8001" "region-b:8002" "edge:8080"; do
  name=${name_port%%:*}; port=${name_port##*:}
  up=0
  for _ in $(seq 1 10); do
    if curl -sf -o /dev/null "http://127.0.0.1:${port}/healthz" 2>/dev/null \
       || curl -sf -o /dev/null "http://127.0.0.1:${port}/edge/state" 2>/dev/null; then
      up=1; break
    fi
    sleep 1
  done
  if [ "$up" = "1" ]; then
    echo "  $name (port $port): UP"
  else
    echo "  $name (port $port): KHONG PHAN HOI -- xem run/$name.log (co the cong da bi chiem)"
    ok=0
  fi
done
[ "$ok" = "1" ] || { echo "MOT SO SERVICE CHUA LEN -- doc log truoc khi chay drill"; exit 1; }
curl -s localhost:8080/edge/state; echo
