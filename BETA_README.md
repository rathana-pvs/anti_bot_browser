# Automat FB Beta v0.1

This is a beta build intended for supervised testing. Begin with one profile and
one post. Keep the Facebook account available for manual review during the first
run on a new machine.

## Supported beta environment

- Ubuntu Linux, or Windows with WSL2
- Docker Engine
- Node.js 18 or newer and npm
- Python 3.10 or newer
- `jq`, `zip`, and standard Linux desktop utilities

## Smart installation

Linux or WSL2:

```bash
bash install.sh --check
bash install.sh --install
```

If installation was interrupted or a dependency changed:

```bash
bash install.sh --repair
```

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-windows.ps1 -Mode check
.\install-windows.ps1 -Mode install
```

The Windows bootstrap detects WSL2, Ubuntu, and Docker Desktop. It can install
WSL/Ubuntu and can offer Docker Desktop through `winget`; Windows may require a
restart, Docker license acceptance, and enabling Docker Desktop's WSL
integration. Everything inside Ubuntu is then installed automatically.

## Manual installation

From the extracted release directory:

```bash
bash scripts/build_container.sh

cd automation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..

cd manager-app
npm install
npm run build
npm start
```

Open the dashboard URL printed by Vite. Create a new profile in the dashboard,
start it, open its browser, and log in to Facebook manually. The release contains
no developer accounts, cookies, media, proxies, or previous queue history.

## Safe beta workflow

1. Leave Resource Mode on **Auto** for the first run.
2. Test one profile before selecting multiple profiles.
3. Start with one photo or Reel that is safe to publish.
4. If an execution says **Needs Review**, inspect Facebook before retrying it.
5. Do not copy browser profile folders between different users.
6. Back up the `profiles/` directory before applying an update.

## Reporting a problem

In **Posting Timeline**, click the bug icon beside the affected execution. Add a
short description and download the support ZIP. Screenshots and caption/comment
content are excluded unless you explicitly select them. Review the ZIP before
sending it to support.

Use **Export diagnostics** when the problem is not associated with a particular
execution. Never send passwords, cookies, the entire `chrome_data` directory, or
the active proxy pool.

## Beta limitations

- Facebook UI changes can require a Brain or application update.
- OCR-heavy stages may be slow on CPU-only computers.
- Ambiguous publication outcomes intentionally stop for manual review rather
  than risking a duplicate post.
- Keep a copy of the previous working release so an update can be rolled back.
