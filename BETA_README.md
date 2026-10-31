# Automat FB Beta v0.1

This is a beta build intended for supervised testing. Begin with one profile and
one post. Keep the Facebook account available for manual review during the first
run on a new machine.

## Supported beta environment

- Linux only: Ubuntu, Debian, or a system based on them (Windows is not supported)
- Docker Engine
- Node.js 18 or newer and npm
- Python 3.10 or newer
- `jq`, `zip`, and standard Linux desktop utilities

### What a new Linux PC needs

`bash install.sh --check` reports each of these; the installer stops in the
first minute when a blocker is missing.

| Need | Required | Notes |
|---|---|---|
| System | Ubuntu, Debian, or a system based on them (Linux Mint, Pop!_OS, Zorin) | |
| Processor | Intel or AMD (x86_64) | Chrome for Linux and the proxy tunnel exist only for x86_64 |
| Free disk | 25 GiB for a first install | Then about 0.3-1.5 GiB per account; housekeeping keeps it from growing |
| Memory and CPU | 8 GB and 4 threads (16 GB or more for several accounts) | Below this only a warning: fewer accounts run at once |
| Internet | Reachable `pypi.org` and `dl.google.com` | A first install downloads several GB |
| Proxy tunnel | `/dev/net/tun` | Loaded by setup if missing; without it proxy accounts cannot start |
| Administrator rights | One password prompt | Installs packages and Docker; afterwards **sign out and back in** once for Docker access |
| NVIDIA GPU (optional) | NVIDIA driver installed first | Setup then installs GPU OCR and verifies it; with no driver, an old driver, or no NVIDIA card it uses CPU OCR. The end of setup prints which one is used |

Desktop package: prefer the `.deb`. The AppImage needs FUSE 2, which Ubuntu
22.04 and later do not install by default (`sudo apt install libfuse2t64`, or
`libfuse2` on 22.04).

## Smart installation

```bash
bash install.sh --check
bash install.sh --install
```

If installation was interrupted or a dependency changed:

```bash
bash install.sh --repair
```


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
