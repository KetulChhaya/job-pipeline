# Scheduler: Windows Task Scheduler

1. Open Task Scheduler > Create Task (not Basic Task).
2. General: name it `job-pipeline-claude`, run whether user is logged on or not.
3. Triggers: New > Daily > Repeat task every 6 hours, indefinitely.
4. Actions: Start a program >
   - Program: `C:\Windows\System32\bash.exe` (or `wsl.exe` if using WSL)
   - Arguments: `/path/to/job-pipeline/run_claude.sh`
5. Repeat steps 2-4 for `job-pipeline-gemini` with `run_gemini.sh`,
   offset by 30 minutes (set the trigger start time 30 min later).

WSL is the smooth path on Windows: keep the folder inside WSL, install the
`claude`/`gemini` CLIs and python deps there, and point the tasks at
`wsl.exe /home/<you>/job-pipeline/run_claude.sh`.
