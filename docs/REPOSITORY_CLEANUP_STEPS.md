# 仓库清理操作步骤

## 建议保留

```text
app/ domain/ infrastructure/ interfaces/
migrations/ scripts/ tests/ evals/
requirements.txt .env.example .gitignore
README.md docs/ examples/
run_web.py run_research_worker.py
start_*.bat start_*.ps1
```

## 不要发布

```text
.env
venv/
models/
logs/
data/users/
data/knowledge/
data/*.db
data/backups/
data/stage*_backup/
.workbuddy/
.docx_assets/
__pycache__/
*.pyc
```

## 查看大文件

```powershell
Get-ChildItem -Force -Recurse -File |
    Where-Object { $_.FullName -notmatch '\\(\.git|venv|models)\\' } |
    Sort-Object Length -Descending |
    Select-Object -First 30 FullName,@{n='MB';e={[math]::Round($_.Length / 1MB, 2)}}
```
