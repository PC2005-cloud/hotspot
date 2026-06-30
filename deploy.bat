@echo off
cd /d "%~dp0"

rmdir /s /q results >nul 2>&1

set PLAYWRIGHT_BROWSERS_PATH=./browsers
uv run python -m hotspot
if %errorlevel% neq 0 goto end

python -c "import json,glob; files=sorted(glob.glob('results/**/report.json', recursive=True)); exit(1) if not files else exit(0 if json.load(open(files[-1],'r')).get('total_hotspots',0) > 0 else 1)"
if %errorlevel% neq 0 goto end

git fetch origin gh-pages
git worktree add gh-pages-copy origin/gh-pages 2>nul || git worktree prune && git worktree add gh-pages-copy origin/gh-pages
xcopy /e /i /y results\* gh-pages-copy\ >nul 2>&1
echo hotspot.lxpavilion.top > gh-pages-copy\CNAME
echo. > gh-pages-copy\.nojekyll
cd gh-pages-copy
git add -A
git commit -m "update report"
git push origin gh-pages
cd ..
rmdir /s /q gh-pages-copy

:end
pause
