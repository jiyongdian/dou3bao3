from __future__ import annotations

import os
import posixpath
import stat
import sys
import time
from pathlib import Path

import paramiko


HOST = os.environ.get("DEPLOY_HOST", "")
USER = os.environ.get("DEPLOY_USER", "root")
PASSWORD = os.environ.get("DEPLOY_PASSWORD", "")
LOCAL_ROOT = Path(__file__).resolve().parent.parent
REMOTE_ROOT = "/opt/dola-fetch-service"


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30, banner_timeout=30, auth_timeout=30)
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int = 600) -> str:
    print(f"\n$ {command}", flush=True)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out_chunks: list[str] = []
    err_chunks: list[str] = []
    start = time.time()
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            text = stdout.channel.recv(65535).decode("utf-8", errors="replace")
            out_chunks.append(text)
            print(text, end="", flush=True)
        if stdout.channel.recv_stderr_ready():
            text = stdout.channel.recv_stderr(65535).decode("utf-8", errors="replace")
            err_chunks.append(text)
            print(text, end="", flush=True)
        if time.time() - start > timeout:
            raise TimeoutError(command)
        time.sleep(0.2)
    while stdout.channel.recv_ready():
        text = stdout.channel.recv(65535).decode("utf-8", errors="replace")
        out_chunks.append(text)
        print(text, end="", flush=True)
    while stdout.channel.recv_stderr_ready():
        text = stdout.channel.recv_stderr(65535).decode("utf-8", errors="replace")
        err_chunks.append(text)
        print(text, end="", flush=True)
    code = stdout.channel.recv_exit_status()
    output = "".join(out_chunks + err_chunks)
    if code != 0:
        raise RuntimeError(f"command failed ({code}): {command}\n{output}")
    return output


def mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    current = ""
    for part in parts:
        current = "/" + part if not current else current + "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def upload_dir(client: paramiko.SSHClient) -> None:
    sftp = client.open_sftp()
    try:
        mkdir_p(sftp, REMOTE_ROOT)
        skip_dirs = {"__pycache__", ".git", ".venv", "data", "bin", "obj", "dist", "ffmpeg"}
        skip_suffixes = {".pyc", ".pyo", ".pdb", ".exe", ".dll", ".zip", ".log"}
        for local in LOCAL_ROOT.rglob("*"):
            rel = local.relative_to(LOCAL_ROOT)
            if any(part in skip_dirs for part in rel.parts):
                continue
            if local.is_file() and local.suffix.lower() in skip_suffixes:
                continue
            remote = posixpath.join(REMOTE_ROOT, *rel.parts)
            if local.is_dir():
                mkdir_p(sftp, remote)
                continue
            mkdir_p(sftp, posixpath.dirname(remote))
            sftp.put(str(local), remote)
            if local.suffix == ".sh":
                sftp.chmod(remote, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    finally:
        sftp.close()


def main() -> None:
    if not HOST:
        print("DEPLOY_HOST is required", file=sys.stderr)
        raise SystemExit(2)
    if not PASSWORD:
        print("DEPLOY_PASSWORD is required", file=sys.stderr)
        raise SystemExit(2)
    client = connect()
    try:
        run(client, "mkdir -p /opt/dola-fetch-service /var/lib/dola-fetch-service")
        upload_dir(client)
        run(client, "chmod +x /opt/dola-fetch-service/scripts/*.sh")
        run(client, "apt-get update", timeout=900)
        run(client, "DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl ca-certificates", timeout=900)
        run(client, "cd /opt/dola-fetch-service && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt", timeout=900)
        run(client, "cd /opt/dola-fetch-service && .venv/bin/python -m playwright install --with-deps chromium", timeout=1200)
        run(client, "cp /opt/dola-fetch-service/systemd/dola-fetch-service.service /etc/systemd/system/dola-fetch-service.service && systemctl daemon-reload && systemctl enable dola-fetch-service && systemctl restart dola-fetch-service", timeout=300)
        run(client, "sleep 3 && systemctl --no-pager --full status dola-fetch-service | sed -n '1,18p'", timeout=120)
        token = run(client, "/opt/dola-fetch-service/scripts/show-token.sh", timeout=60).strip().splitlines()[-1]
        print(f"\nAPI_TOKEN={token}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
