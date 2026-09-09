# GitHub 发布操作步骤

## 第 1 步：确认项目和远程地址

```powershell
Set-Location E:\robot_system
git status --short
git remote -v
```

确认当前目录正确，并确认远程地址是你自己的 GitHub 仓库。

## 第 2 步：检查敏感信息

```powershell
git status --ignored --short | Select-String "\.env|users|knowledge|models|venv|\.jwt_secret|logs"
rg -n --hidden -g '!venv/**' -g '!models/**' -g '!.git/**' "sk-[A-Za-z0-9]|TTS_ACCESS_TOKEN|password=|PG_DSN=.*@" .
```

不能发布：`.env`、真实 API Key、数据库密码、TTS 凭据、用户历史、私人知识库、模型权重、虚拟环境、日志、数据库文件和 Cloudflare 临时地址。

如果密钥曾经进入 Git 历史，先撤销并更换密钥。删除本地文件不能清除历史提交中的密钥。

## 第 3 步：移除开发备份

以下内容留在本机，不发布：

```text
data/backups/
data/stage5_backup/
data/stage6_backup/
data/stage7_backup/
data/requirements_before_sync.txt
data/stage6_deep_research_implementation_guide.updated.md
data/stage7_conversation_plan_worker_implementation_guide.md
data/evals/
data/research/
```

先检查是否被跟踪：

```powershell
git ls-files data/backups data/stage5_backup data/stage6_backup data/stage7_backup data/evals data/research data/requirements_before_sync.txt
```

确认不需要公开后，从 Git 索引移除但保留本地文件：

```powershell
git rm -r --cached --ignore-unmatch data/backups data/stage5_backup data/stage6_backup data/stage7_backup data/evals data/research
git rm --cached --ignore-unmatch data/requirements_before_sync.txt
git rm --cached --ignore-unmatch data/stage6_deep_research_implementation_guide.updated.md
git rm --cached --ignore-unmatch data/stage7_conversation_plan_worker_implementation_guide.md
```

## 第 4 步：检查忽略规则

```powershell
git check-ignore -v .env venv models logs data/users/test.json data/knowledge/test.txt
```

每个路径都应显示来自 `.gitignore` 的匹配规则。

## 第 5 步：运行测试

```powershell
E:\robot_system\venv\Scripts\python.exe -m unittest discover -s tests -v
E:\robot_system\venv\Scripts\python.exe -m compileall app domain infrastructure interfaces scripts run_web.py run_research_worker.py
```

## 第 6 步：暂存并检查

```powershell
git add README.md LICENSE .gitignore requirements.txt .env.example docker-compose.yml docs examples .github app domain infrastructure interfaces migrations scripts tests evals run_web.py run_research_worker.py start_robot_system.bat start_robot_system.ps1 start_web_service.ps1 start_research_worker_service.ps1 start_cloudflare_tunnel.ps1
git diff --cached --name-only
git diff --cached --check
```

暂存区中不应出现 `.env`、`data/users`、`data/knowledge`、`models`、`venv`、`logs` 或数据库 dump。

## 第 7 步：提交

```powershell
git commit -m "Prepare DeepResearch for public release"
```

## 第 8 步：在 GitHub 创建仓库

1. 打开 GitHub，点击 `New repository`。
2. 仓库名填写 `DeepResearch`。
3. 选择 `Public` 或 `Private`。
4. 不要勾选初始化 README、`.gitignore` 或 License。
5. 创建后复制远程仓库地址。

## 第 9 步：绑定并推送

新建远程仓库时：

```powershell
git remote add origin https://github.com/<你的用户名>/DeepResearch.git
git branch -M main
git push -u origin main
```

已经存在 `origin` 时，先确认地址，再执行：

```powershell
git remote -v
git push -u origin main
```

## 第 10 步：发布后验收

在 GitHub 页面确认：

- README 首页能说明项目和启动方式；
- 源码、测试、迁移和部署文件齐全；
- 没有密钥、账号数据、知识库原文和数据库备份；
- GitHub Actions 测试通过；
- 截图不包含 Cookie、Token、密码或个人路径。
