# Remote Executor Upload API

一个仅依赖 Python 3.6 标准库的常驻 API Server，用于接收 `/tool/upload_file` 请求，并把本地文件通过 SSH/SFTP 上传到远端服务器。

## 启动

```bash
python3 api_server.py
```

可选环境变量：

- `UPLOAD_API_HOST`（默认 `0.0.0.0`）
- `UPLOAD_API_PORT`（默认 `58080`）
- `UPLOAD_API_PATH_PREFIX_MAP`（默认空），用于路径映射，格式为 JSON 字典。
  - 例如：`{"/mnt/hgfs/":"/data/shared/"}`
  - 当请求中的 `file_name` 在服务端不可见时，会尝试按前缀替换后再查找。

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
- `file_name` 必须是 **API 服务进程所在机器** 可以访问的路径。
- 若返回 `file_not_found`，响应中的 `tried_paths` 会列出服务端实际尝试过的本地路径，便于排查。
- 该实现调用系统 `ssh`/`sftp` 命令，代码本身不依赖第三方 Python 库。
