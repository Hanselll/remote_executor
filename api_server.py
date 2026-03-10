#!/usr/bin/env python3
"""Simple upload API server using only Python standard library (Python 3.6 compatible)."""

import cgi
import io
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
    target = "%s@%s" % (username, server_ip)
    escaped_remote = remote_path.replace("'", "'\\''")
    command = ["ssh"] + _build_ssh_auth_options(ssh_port) + [
        target,
        "cat > '%s'" % escaped_remote,
    ]

    with open(local_file, "rb") as handle:
        payload = handle.read()

    askpass_path = _build_askpass_script(password)
    env = os.environ.copy()
    env["SSH_ASKPASS"] = askpass_path
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env["DISPLAY"] = ":0"

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        stdout, stderr = process.communicate(input=payload)
        return process.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    finally:
        try:
            os.remove(askpass_path)
        except OSError:
            pass


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
        if self.path != "/tool/upload_file":
            _json_response(self, 404, {"error": "not_found"})
            return

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
