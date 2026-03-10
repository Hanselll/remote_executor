# Remote Executor Upload API

一个仅依赖 Python 3.6 标准库的常驻 API Server，用于接收 `/tool/upload_file` 请求，并把 **curl 客户端上传的文件内容** 通过 SSH/SFTP 上传到远端服务器。

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

## 上传接口（推荐：multipart/form-data）

```bash
curl -s http://127.0.0.1:58080/tool/upload_file \
  -F 'file=@/mnt/hgfs/ScriptTransportation/cases/modular_partition_ddb_with_upc_upu_upclb_kill.yaml' \
  -F 'file_name=modular_partition_ddb_with_upc_upu_upclb_kill.yaml' \
  -F 'server_ip=10.230.246.195' \
  -F 'username=gsta' \
  -F 'password=gsta123' \
  -F 'ssh_port=50163' \
  -F 'remote_path=/home/gsta/chaosmesh_workflow_runner_v16/chaos_runner/cases/'
```

说明：

- `file` 必填，表示 curl 客户端本地文件，服务端会接收二进制内容并写入临时文件再上传。
- `file_name` 可选；不传则默认使用 `file=@...` 的原始文件名。
- `remote_path` 视为目标目录，服务端会自动拼接文件名作为远端文件名。
- 上传前会自动执行 `mkdir -p remote_path`。
- 当前版本不再支持 JSON 里仅传 `file_name` 让 API 服务器查本地路径；如果用该模式会返回 `local_path_not_supported`。
- 该实现调用系统 `ssh`/`sftp` 命令，代码本身不依赖第三方 Python 库。
