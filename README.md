# Remote Executor Upload API

一个仅依赖 Python 3.6 标准库的常驻 API Server，用于接收 `/tool/upload_file` 请求，并把本地文件通过 SSH/SFTP 上传到远端服务器。

## 启动

```bash
python3 api_server.py
```

可选环境变量：

- `UPLOAD_API_HOST`（默认 `0.0.0.0`）
- `UPLOAD_API_PORT`（默认 `58080`）

## 健康检查

```bash
curl -s http://127.0.0.1:58080/health
```

## 上传接口

```bash
curl -s http://127.0.0.1:58080/tool/upload_file \
  -H 'Content-Type: application/json' \
  -d '{
    "file_name": "/path/to/local/file.yaml",
    "server_ip": "10.0.0.1",
    "username": "user",
    "password": "password",
    "ssh_port": 22,
    "remote_path": "/home/user/target_dir/"
  }'
```

说明：

- `remote_path` 视为目标目录，服务端会自动拼接本地文件名作为远端文件名。
- 上传前会自动执行 `mkdir -p remote_path`。
- 该实现调用系统 `ssh`/`sftp` 命令，代码本身不依赖第三方 Python 库。
