@echo off
echo Setting up auto-sync tasks...

set SCRIPT="C:\Users\User\Desktop\תיקיית קלוד\inventory-dashboard\run_sync.bat"

schtasks /create /tn "HasidimSync_Midnight" /tr %SCRIPT% /sc DAILY /st 00:00 /f
schtasks /create /tn "HasidimSync_Afternoon" /tr %SCRIPT% /sc DAILY /st 15:00 /f
schtasks /create /tn "HasidimSync_Evening" /tr %SCRIPT% /sc DAILY /st 20:00 /f

echo Done! Sync scheduled for 00:00, 15:00, 20:00 daily.
pause
