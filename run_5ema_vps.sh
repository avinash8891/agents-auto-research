#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

FAMILY="${AUTORESEARCH_FAMILY:-ema}"
REMOTE_HOST="${AUTORESEARCH_VPS_HOST:?AUTORESEARCH_VPS_HOST required}"
REMOTE_USER="${AUTORESEARCH_VPS_USER:?AUTORESEARCH_VPS_USER required}"
REMOTE_KEY="${AUTORESEARCH_VPS_KEY:?AUTORESEARCH_VPS_KEY required}"
REMOTE_DIR="${AUTORESEARCH_VPS_DIR:?AUTORESEARCH_VPS_DIR required}"

echo "=== Autoresearch VPS Launch ==="
echo "Family: $FAMILY"
echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

python3 <<'PYEOF'
import os

from vps_runner import (
    _ensure_remote_dir,
    config_from_env,
    connect_verified_ssh_client,
    sync_relative_paths,
)

family = os.environ.get("AUTORESEARCH_FAMILY", "ema")
vps_config = config_from_env()
remote_dir = vps_config.remote_dir

client = connect_verified_ssh_client(vps_config)
sftp = client.open_sftp()

for rel in sync_relative_paths():
    remote = f"{remote_dir}/{rel}"
    _ensure_remote_dir(sftp, remote)
    path = rel
    sftp.put(str(path), remote)

for dirname in [
    f"{family}-contracts",
    f"{family}-proposals",
    f"{family}-compilations",
    f"{family}-run-queue",
    f"{family}-research",
    f"{family}_autoresearch-runs",
    f"{family}-builder-requests",
    "logs",
]:
    try:
        sftp.mkdir(f"{remote_dir}/{dirname}")
    except IOError:
        pass

sftp.close()
client.close()
print("Sync done")
PYEOF

python3 <<'PYEOF'
import os

from vps_runner import config_from_env, connect_verified_ssh_client

family = os.environ.get("AUTORESEARCH_FAMILY", "ema")
vps_config = config_from_env()
remote_dir = vps_config.remote_dir

client = connect_verified_ssh_client(vps_config)
cmd = (
    f"cd {remote_dir} && "
    "export AUTORESEARCH_VPS=1 && "
    f"nohup ./venv/bin/python3 autoresearch_controller.py --family {family} "
    "> autoresearch_stdout.log 2>&1 & "
    "echo PID=$!"
)
_, stdout, stderr = client.exec_command(cmd, timeout=10)
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print(out)
if err:
    print(f"ERR: {err}")
client.close()
PYEOF

echo "Autoresearch running. Logs: $REMOTE_DIR/autoresearch_stdout.log"
