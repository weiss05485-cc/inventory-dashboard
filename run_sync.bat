@echo off
cd /d "C:\Users\User\Desktop\תיקיית קלוד\inventory-dashboard"
python sync.py >> sync_log.txt 2>&1
git add docs/data.json >> sync_log.txt 2>&1
git commit -m "Auto sync %date% %time%" >> sync_log.txt 2>&1
git push >> sync_log.txt 2>&1
