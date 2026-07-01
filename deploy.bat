@echo off
cd /d "%~dp0"

set PLAYWRIGHT_BROWSERS_PATH=./browsers

:: 检查已有合法结果（上次推送失败留下的），有则跳过爬虫
python -c "import json,glob; files=sorted(glob.glob('results/**/report.json', recursive=True)); exit(1) if not files else exit(0 if json.load(open(files[-1],'r',encoding='utf-8')).get('total_hotspots',0) > 0 else 1)" 2>nul
if %errorlevel% equ 0 goto deploy

:: ==================== 爬虫阶段 ====================
uv run python -m hotspot
if %errorlevel% neq 0 goto end

python -c "import json,glob; files=sorted(glob.glob('results/**/report.json', recursive=True)); exit(1) if not files else exit(0 if json.load(open(files[-1],'r',encoding='utf-8')).get('total_hotspots',0) > 0 else 1)"
if %errorlevel% neq 0 goto end

:: ==================== 部署阶段 ====================
:deploy
rmdir /s /q gh-pages-copy >nul 2>&1
git worktree prune >nul 2>&1

git fetch origin gh-pages
git worktree add gh-pages-copy origin/gh-pages 2>nul
if %errorlevel% neq 0 (
    git worktree prune >nul 2>&1
    git worktree add gh-pages-copy origin/gh-pages 2>nul
)
if %errorlevel% neq 0 (
    echo [错误] 创建 gh-pages worktree 失败，终止部署
    goto end
)
xcopy /e /i /y results\* gh-pages-copy\ >nul 2>&1
echo hotspot.lxpavilion.top > gh-pages-copy\CNAME
echo. > gh-pages-copy\.nojekyll
cd gh-pages-copy
git add -A
git commit -m "update report"
git push origin HEAD:gh-pages
set PUSH_RESULT=%errorlevel%
cd ..
rmdir /s /q gh-pages-copy >nul 2>&1
git worktree prune >nul 2>&1

:: 推送成功才清理结果，失败则保留供下次重试
if %PUSH_RESULT% equ 0 rmdir /s /q results >nul 2>&1

:end
pause
