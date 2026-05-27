@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo מרענן מכירות היום...
python sync_today.py
if %errorlevel% neq 0 (
    echo.
    echo *** שגיאה בהרצת הסקריפט ***
    pause
    exit /b 1
)
git add docs/today.json
git commit -m "Update today sales %date% %time%"
git push
echo.
echo הנתונים עודכנו באתר!
timeout /t 3
