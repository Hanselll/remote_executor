#!/usr/bin/env python3
"""Simple upload API server using only Python standard library (Python 3.6 compatible)."""

import json
import os
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer


def _json_response(handler, status_code, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_askpass_script(password):
    fd, script_path = tempfile.mkstemp(prefix="askpass_", suffix=".sh")
    os.close(fd)

    escaped = password.replace("'", "'\"'\"'")
    script_content = "#!/bin/sh\necho '%s'\n" % escaped
    with open(script_path, "w") as handle:
        handle.write(script_content)
    os.chmod(script_path, 0o700)
    return script_path


def _run_with_password(command, password):
    askpass_path = _build_askpass_script(password)
    env = os.environ.copy()
    env["SSH_ASKPASS"] = askpass_path
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env["DISPLAY"] = ":0"

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            universal_newlines=True,
            start_new_session=True,
        )
        stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr
    finally:
        try:
            os.remove(askpass_path)
        except OSError:
            pass


def _ssh_mkdir(server_ip, username, password, ssh_port, remote_path):
    target = "%s@%s" % (username, server_ip)
    command = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-p",
        str(ssh_port),
        target,
        "mkdir -p '%s'" % remote_path.replace("'", "'\\''"),
    ]
    return _run_with_password(command, password)


def _sftp_put(server_ip, username, password, ssh_port, local_file, remote_path):
    target = "%s@%s" % (username, server_ip)

    fd, batch_path = tempfile.mkstemp(prefix="sftp_batch_", suffix=".txt")
    os.close(fd)
    try:
        with open(batch_path, "w") as batch:
            batch.write("put \"%s\" \"%s\"\n" % (local_file, remote_path))

        command = [
            "sftp",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-P",
            str(ssh_port),
            "-b",
            batch_path,
            target,
        ]
        return _run_with_password(command, password)
    finally:
        try:
            os.remove(batch_path)
        except OSError:
            pass


class UploadHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            _json_response(self, 200, {"status": "ok"})
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/tool/upload_file":
            _json_response(self, 404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(self, 400, {"error": "invalid_content_length"})
            return

        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            _json_response(self, 400, {"error": "invalid_json"})
            return

        required = ["file_name", "server_ip", "username", "password", "ssh_port", "remote_path"]
        missing = [field for field in required if not payload.get(field)]
        if missing:
            _json_response(self, 400, {"error": "missing_fields", "fields": missing})
            return

        file_name = payload["file_name"]
        server_ip = payload["server_ip"]
        username = payload["username"]
        password = payload["password"]
        ssh_port = payload["ssh_port"]
        remote_path = payload["remote_path"]

        try:
            ssh_port = int(ssh_port)
        except (TypeError, ValueError):
            _json_response(self, 400, {"error": "invalid_ssh_port"})
            return

        if not os.path.isfile(file_name):
            _json_response(self, 400, {"error": "file_not_found", "file_name": file_name})
            return

        remote_dir = remote_path.rstrip("/")
        if not remote_dir:
            remote_dir = "/"
        remote_file = remote_dir.rstrip("/") + "/" + os.path.basename(file_name)

        code, _out, err = _ssh_mkdir(server_ip, username, password, ssh_port, remote_dir)
        if code != 0:
            _json_response(self, 500, {"error": "remote_mkdir_failed", "detail": err.strip()})
            return

        code, out, err = _sftp_put(server_ip, username, password, ssh_port, file_name, remote_file)
        if code != 0:
            _json_response(self, 500, {"error": "upload_failed", "detail": (err or out).strip()})
            return

        _json_response(
            self,
            200,
            {
                "status": "success",
                "file_name": file_name,
                "remote_file": remote_file,
                "server_ip": server_ip,
                "ssh_port": ssh_port,
            },
        )

    def log_message(self, format_str, *args):
        return


def main():
    host = os.environ.get("UPLOAD_API_HOST", "0.0.0.0")
    port = int(os.environ.get("UPLOAD_API_PORT", "58080"))
    server = HTTPServer((host, port), UploadHandler)
    print("Upload API server listening on %s:%s" % (host, port))
    server.serve_forever()


if __name__ == "__main__":
    main()
