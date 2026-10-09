"""The WSL payload refresh must preserve user state during upgrades."""
from pathlib import Path
import subprocess


def test_payload_refresh_keeps_user_state_and_updates_bundled_code(tmp_path):
    source=tmp_path/'payload with spaces'
    destination=tmp_path/'existing runtime'
    files={
        source/'automation/brains/catalog.json':'bundled activation',
        source/'automation/brains/facebook_live/bundled_default/manifest.json':'new live',
        source/'backend/main.py':'new backend',
        destination/'automation/brains/catalog.json':'user activation',
        destination/'automation/brains/facebook_reel/1.2.3/manifest.json':'custom brain',
        destination/'profiles/p/chrome_data/session':'existing session',
    }
    for path,value in files.items():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(value)
    script=Path(__file__).resolve().parents[2]/'scripts/copy_runtime_payload.sh'
    subprocess.run(['bash',str(script),str(source),str(destination)],check=True)
    assert (destination/'automation/brains/catalog.json').read_text()=='user activation'
    assert (destination/'automation/brains/facebook_live/bundled_default/manifest.json').read_text()=='new live'
    assert (destination/'backend/main.py').read_text()=='new backend'
    assert (destination/'profiles/p/chrome_data/session').read_text()=='existing session'
    assert (destination/'automation/brains/facebook_reel/1.2.3/manifest.json').read_text()=='custom brain'
    fresh=tmp_path/'fresh install'
    subprocess.run(['bash',str(script),str(source),str(fresh)],check=True)
    assert (fresh/'automation/brains/catalog.json').read_text()=='bundled activation'
