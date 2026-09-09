# 本地部署操作步骤

## 1. 进入项目并安装依赖

```powershell
Set-Location E:\robot_system
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 2. 创建配置

```powershell
Copy-Item .env.example .env
notepad .env
```

至少填写 `DEEPSEEK_API_KEY`、`PG_DSN` 和 `POSTGRES_PASSWORD`，其中 `PG_DSN` 中的密码要与 `POSTGRES_PASSWORD` 一致。本地 ASR 使用 `ASR_ALLOW_DOWNLOAD=0`。PDF 导出需要检查 `MICROSOFT_WORD_BINARY` 是否指向本机的 `WINWORD.EXE`。

## 3. 启动 PostgreSQL

```powershell
docker compose up -d postgres
docker exec robot-pg pg_isready -U postgres -d robot
```

## 4. 初始化数据库

只对新数据库执行一次。按文件名顺序执行：

```powershell
Get-ChildItem migrations\*.sql | Sort-Object Name | ForEach-Object {
    Write-Host "Applying $($_.Name)"
    Get-Content -Raw -LiteralPath $_.FullName |
        docker exec -i robot-pg psql -v ON_ERROR_STOP=1 -U postgres -d robot
    if ($LASTEXITCODE -ne 0) { throw "Migration failed: $($_.Name)" }
}
```

如果数据库已经初始化过，不要重复执行；正式环境执行迁移前应先备份数据库。

## 5. 启动 Web

终端一：

```powershell
venv\Scripts\python.exe run_web.py
```

看到 `Uvicorn running on http://0.0.0.0:18923` 后，打开：

```text
http://127.0.0.1:18923/login
```

## 6. 启动 Research Worker

终端二：

```powershell
venv\Scripts\python.exe run_research_worker.py
```

看到 `Research worker started` 表示 Worker 已启动。

## 7. 一键启动

关闭手动启动的 Web 和 Worker 后，双击：

```text
start_robot_system.bat
```

正常情况下会打开 `DeepResearch Web`、`DeepResearch Research Worker`，选择公网演示时还会打开 `Cloudflare Tunnel`。

## 8. 健康检查

```powershell
curl.exe -s http://127.0.0.1:18923/health
```

重点确认数据库和 `research_worker` 的状态为 `ok`。

## 9. 停止服务

分别在 Web、Research Worker 和 Cloudflare 窗口按 `Ctrl+C`，然后执行：

```powershell
docker compose down
```
