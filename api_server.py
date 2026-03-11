#!/usr/bin/env python3
"""Simple upload API server using only Python standard library (Python 3.6 compatible)."""

import cgi
import io
import json
import os
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer


CASE_WORKDIR = os.environ.get("CASE_WORKDIR", "/home/gsta/chaosmesh_workflow_runner_v16")
CASE_RELATIVE_DIR = os.environ.get("CASE_RELATIVE_DIR", "chaos_runner/cases")
CASE_RUN_TIMEOUT_SECONDS = int(os.environ.get("CASE_RUN_TIMEOUT_SECONDS", "3600"))


def _run_case_command(file_name):
    safe_name = os.path.basename(file_name or "")
    if not safe_name:
        return 400, {"error": "missing_fields", "fields": ["file_name"]}

    case_file = os.path.join(CASE_WORKDIR, CASE_RELATIVE_DIR, safe_name)
    if not os.path.isfile(case_file):
        return 400, {"error": "case_file_not_found", "file_name": safe_name, "case_file": case_file}

    command = [
        "python3",
        "-m",
        "chaos_runner.runner",
        "--case",
        os.path.join(CASE_RELATIVE_DIR, safe_name),
    ]

    try:
        process = subprocess.Popen(
            command,
            cwd=CASE_WORKDIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        stdout, stderr = process.communicate(timeout=CASE_RUN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return 500, {
            "error": "case_run_timeout",
            "timeout_seconds": CASE_RUN_TIMEOUT_SECONDS,
            "stdout": stdout,
            "stderr": stderr,
        }

    payload = {
        "status": "success" if process.returncode == 0 else "failed",
        "return_code": process.returncode,
        "file_name": safe_name,
        "case_file": case_file,
        "command": " ".join(command),
        "stdout": stdout,
        "stderr": stderr,
    }
    if process.returncode != 0:
        return 500, payload
    return 200, payload


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




def _build_ssh_auth_options(ssh_port):
    return [
        "-F",
        "/dev/null",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "PreferredAuthentications=password",
        "-o",
        "PubkeyAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=yes",
        "-p",
        str(ssh_port),
    ]


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
    command = ["ssh"] + _build_ssh_auth_options(ssh_port) + [
        target,
        "mkdir -p '%s'" % remote_path.replace("'", "'\\''"),
    ]
    return _run_with_password(command, password)


def _sftp_put(server_ip, username, password, ssh_port, local_file, remote_path):
    target = "%s@%s:%s" % (username, server_ip, remote_path)
    base = _build_ssh_auth_options(ssh_port)
    scp_base = list(base)
    if "-p" in scp_base:
        port_index = scp_base.index("-p")
        scp_base[port_index] = "-P"
    command = ["scp"] + scp_base + [local_file, target]
    return _run_with_password(command, password)


def _parse_json_payload(body):
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _parse_multipart_payload(handler, body):
    form = cgi.FieldStorage(
        fp=io.BytesIO(body),
        headers=handler.headers,
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": handler.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
        },
        keep_blank_values=True,
    )

    upload_item = form["file"] if "file" in form else None
    if upload_item is None:
        return None, {"error": "missing_fields", "fields": ["file"]}
    if isinstance(upload_item, list):
        upload_item = upload_item[0]
    if not getattr(upload_item, "filename", None):
        return None, {"error": "invalid_file", "detail": "file part has no filename"}

    payload = {
        "server_ip": form.getfirst("server_ip"),
        "username": form.getfirst("username"),
        "password": form.getfirst("password"),
        "ssh_port": form.getfirst("ssh_port"),
        "remote_path": form.getfirst("remote_path"),
        "file_name": form.getfirst("file_name") or os.path.basename(upload_item.filename),
        "uploaded_bytes": upload_item.file.read(),
    }
    return payload, None


class UploadHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            _json_response(self, 200, {"status": "ok"})
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self):
        if self.path == "/tool/upload_file":
            self._handle_upload_file()
            return
        if self.path == "/tool/run_case":
            self._handle_run_case()
            return
        _json_response(self, 404, {"error": "not_found"})

    def _handle_upload_file(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(self, 400, {"error": "invalid_content_length"})
            return

        body = self.rfile.read(length)
        content_type = (self.headers.get("Content-Type") or "").lower()

        if content_type.startswith("multipart/form-data"):
            payload, parse_err = _parse_multipart_payload(self, body)
            if parse_err:
                _json_response(self, 400, parse_err)
                return
        else:
            payload = _parse_json_payload(body)
            if not payload:
                _json_response(self, 400, {"error": "invalid_json"})
                return
            _json_response(
                self,
                400,
                {
                    "error": "local_path_not_supported",
                    "detail": "Use multipart/form-data and send the file content with -F file=@/path/to/file",
                },
            )
            return

        required = ["file_name", "server_ip", "username", "password", "ssh_port", "remote_path"]
        missing = [field for field in required if not payload.get(field)]
        if missing:
            _json_response(self, 400, {"error": "missing_fields", "fields": missing})
            return

        try:
            ssh_port = int(payload["ssh_port"])
        except (TypeError, ValueError):
            _json_response(self, 400, {"error": "invalid_ssh_port"})
            return

        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix="upload_api_", suffix="_" + os.path.basename(payload["file_name"]))
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload["uploaded_bytes"])

            remote_dir = payload["remote_path"].rstrip("/")
            if not remote_dir:
                remote_dir = "/"
            remote_file = remote_dir.rstrip("/") + "/" + os.path.basename(payload["file_name"])

            code, _out, err = _ssh_mkdir(payload["server_ip"], payload["username"], payload["password"], ssh_port, remote_dir)
            if code != 0:
                _json_response(self, 500, {"error": "remote_mkdir_failed", "detail": err.strip()})
                return

            code, out, err = _sftp_put(payload["server_ip"], payload["username"], payload["password"], ssh_port, temp_path, remote_file)
            if code != 0:
                _json_response(self, 500, {"error": "upload_failed", "detail": (err or out).strip()})
                return

            _json_response(
                self,
                200,
                {
                    "status": "success",
                    "file_name": payload["file_name"],
                    "remote_file": remote_file,
                    "server_ip": payload["server_ip"],
                    "ssh_port": ssh_port,
                },
            )
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _handle_run_case(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(self, 400, {"error": "invalid_content_length"})
            return

        body = self.rfile.read(length)
        payload = _parse_json_payload(body)
        if not payload:
            _json_response(self, 400, {"error": "invalid_json"})
            return

        file_name = payload.get("file_name")
        status_code, response = _run_case_command(file_name)
        _json_response(self, status_code, response)

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
