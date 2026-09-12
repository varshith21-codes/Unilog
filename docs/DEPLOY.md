# Deploying the prototype

How [axiom.vectoredu.online](https://axiom.vectoredu.online) gets updated.

Written after a deploy where none of this was written down: the procedure lived in one shell
history, and the SSH key it used had been deleted by its own cleanup step. Recovering it took
longer than the deploy did. Hence this file.

> **There is no CD.** `.github/workflows/` contains `ci.yml` and `regression.yml`, and both are
> test gates. Pushing to `master` runs CI and touches nothing else. The live site changes only
> when somebody runs the steps below. A green CI run is not a deploy, and a red one does not
> block one.

---

## What the runtime actually is

A single EC2 instance running three containers under Docker Compose, reached through a
**Cloudflare named tunnel** rather than through a public web port.

```
browser
  → Cloudflare edge (TLS terminates here; DNS is proxied, origin IP hidden)
  → cloudflared on the host   systemd unit: axiom-named.service
                              cloudflared tunnel --url http://localhost:80 run axiom
  → :80 docker-proxy
  → axiom-proxy-1   Caddy 2, mounts deploy/Caddyfile.tunnel
      ├── /api/artifact/*  → axiom-api        FastAPI :8000
      └── everything else  → axiom-console-1  Next.js :3000
```

Two consequences that explain most of the surprises:

- **The security group opens only port 22.** No 80, no 443. Inbound web traffic arrives through
  the tunnel's outbound connection, so there is nothing to point a browser at directly.
- **The console is compiled into its image.** `next build` runs at image build time, so
  `docker compose restart` will never pick up a source change. The image has to be rebuilt.

## Prerequisites

- AWS CLI with the `axiom` profile configured (`aws sts get-caller-identity --profile axiom`)
- An SSH keypair to push — `~/.ssh/id_ed25519` is fine
- `tar` (bundled with Windows 10+ as `tar.exe`) and `scp`

You do **not** need the `axiom-demo` EC2 keypair. Its private key is gone, and the access path
below does not use it.

## Getting a shell

Use **EC2 Instance Connect**, which pushes a public key valid for ~60 seconds using your IAM
identity. Nothing persists in `authorized_keys`, and no long-lived key has to exist.

The public IP is **not** an Elastic IP, so it changes whenever the instance stops and starts.
Always resolve it, never paste it from memory:

```powershell
$env:AWS_PROFILE = "axiom"; $env:AWS_PAGER = ""

$ID = aws ec2 describe-instances `
  --region us-east-2 `
  --filters "Name=tag:Name,Values=axiom-demo-2" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].InstanceId" --output text

$IP = aws ec2 describe-instances `
  --region us-east-2 --instance-ids $ID `
  --query "Reservations[].Instances[].PublicIpAddress" --output text

"instance=$ID  ip=$IP"
```

Then, immediately before each connection:

```powershell
aws ec2-instance-connect send-ssh-public-key `
  --region us-east-2 --instance-id $ID --instance-os-user ubuntu `
  --ssh-public-key "file://$env:USERPROFILE/.ssh/id_ed25519.pub"

ssh -i "$env:USERPROFILE\.ssh\id_ed25519" ubuntu@$IP
```

The key expires in about a minute, but an established session survives. For scripted multi-step
work, re-push before every new connection rather than trying to keep one alive.

The login user is `ubuntu` (Ubuntu 24.04). `ec2-user` does not exist on this AMI.

## Host layout

The live Compose project is `axiom`, rooted at `/opt/axiom`:

```
/opt/axiom/
  compose.yaml               from the repo
  compose.tunnel.yaml        HOST ONLY — not in the repo
  .env.deploy                HOST ONLY — mode 600, root — credentials and the Caddy auth hash
  deploy/Caddyfile.tunnel    HOST ONLY — not in the repo
  deploy/Caddyfile           from the repo, and NOT the one in use
  apps/console/              the Next.js source that gets rebuilt
/srv/axiom/data              bind-mounted data volume
```

**Three files must never be clobbered.** `compose.tunnel.yaml`, `.env.deploy`, and
`deploy/Caddyfile.tunnel` exist only on the host. A `git checkout` or a full-repo extract will
not delete them, but anything that wipes the directory first will, and the tunnel stops working
without them.

`compose.tunnel.yaml` supplies the Bedrock credentials to `api`, sets the console's
`NEXT_PUBLIC_AXIOM_API_URL` build arg to empty, and swaps Caddy onto `Caddyfile.tunnel`. Every
Compose command therefore needs **both** files and the env file:

```bash
cd /opt/axiom
sudo docker compose -f compose.yaml -f compose.tunnel.yaml --env-file .env.deploy <command>
```

`sudo` is required because `.env.deploy` is mode 600 owned by root. That is correct — it holds
secrets — so the deploy adapts rather than loosening it.

## Deploying a console-only change

The common case: anything under `apps/console/**`. Rebuilds one service and leaves `api` and
`proxy` running.

**1. Package the source.** From the repo root, locally:

```powershell
tar.exe -czf "$env:TEMP\axiom_console.tgz" `
  --exclude=node_modules --exclude=.next --exclude=tsconfig.tsbuildinfo `
  --exclude=src/data/fixture.json --exclude=*.tgz `
  -C apps/console .
```

`src/data/fixture.json` is excluded deliberately. It is generated, gitignored, and the host's
copy determines which `/review/[sku]` and `/certificates/[sku]` routes get statically
prerendered. Shipping a locally generated one silently changes which pages exist. Leave the
host's in place unless changing it is the point — and note the console image **cannot build
without it**, so never delete it from the host.

**2. Upload.**

```powershell
scp -i "$env:USERPROFILE\.ssh\id_ed25519" "$env:TEMP\axiom_console.tgz" ubuntu@${IP}:/tmp/
```

**3. Back up, then extract.** On the host:

```bash
cd /opt/axiom/apps
sudo rm -rf console.bak && sudo cp -a console console.bak
sudo tar -xzf /tmp/axiom_console.tgz -C /opt/axiom/apps/console
```

`sudo` on the extract matters. `/opt/axiom` is root-owned, so an unprivileged `tar` writes the
file contents and then fails setting directory timestamps and modes — exiting non-zero and
looking like a failed deploy after it has already half-applied.

**4. Confirm the payload landed** before spending time on a build:

```bash
cd /opt/axiom/apps/console
grep -c 'prove where they came from' src/app/page.tsx   # expect 1, or your own marker
test -f src/data/fixture.json && echo 'fixture present'
```

**5. Build, then swap.**

```bash
cd /opt/axiom
sudo docker compose -f compose.yaml -f compose.tunnel.yaml --env-file .env.deploy build console
sudo docker compose -f compose.yaml -f compose.tunnel.yaml --env-file .env.deploy up -d --no-deps console
```

`--no-deps` is what keeps `api` and `proxy` from being recreated. The build finishes before the
container is replaced, so a build failure leaves the current site serving — which makes this
step safe to retry.

Roughly two minutes on the current instance type. `npm ci` installs devDependencies, which
`next build` needs because it type-checks `**/*.ts`, test files included.

## Deploying a change outside the console

If `packages/axiom/**`, `schema/**`, `Dockerfile.api`, or `compose.yaml` changed, the `api`
service needs rebuilding too. Its build context is the repo root, so the whole tree has to be
shipped:

```powershell
tar.exe -czf "$env:TEMP\axiom_deploy.tgz" `
  --exclude=.git --exclude=node_modules --exclude=.next --exclude=./data/cache `
  --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache `
  --exclude=*.log --exclude=*.tgz .
```

Extract it the same way, then rebuild the services that changed:

```bash
sudo docker compose -f compose.yaml -f compose.tunnel.yaml --env-file .env.deploy build api console
sudo docker compose -f compose.yaml -f compose.tunnel.yaml --env-file .env.deploy up -d
```

Verify `compose.tunnel.yaml`, `.env.deploy`, and `deploy/Caddyfile.tunnel` all still exist after
extracting. A root-level extract is where they are most likely to be lost.

Only the console-only path above has actually been exercised. This variant is derived from the
same Compose setup and is untested — treat the first run as one.

## Verifying

Check the host before trusting the public URL, so a failure is attributable to the app rather
than to the tunnel or the CDN:

```bash
docker ps --format '{{.Names}}  {{.Status}}'
docker inspect axiom-console-1 --format '{{.State.Health.Status}}'   # want: healthy
curl -s -o /tmp/live.html -w '%{http_code}\n' http://localhost:80/
grep -c 'prove where they came from' /tmp/live.html
```

Proving the image actually changed is worth doing explicitly — a no-op build that silently
reuses a cached image looks identical to a successful deploy:

```bash
docker inspect axiom-console-1 --format '{{.Image}}'   # compare before and after
```

Then, from anywhere:

```powershell
curl.exe -s https://axiom.vectoredu.online | Select-String "prove where they came from"
foreach ($p in @("/","/operations","/review","/certificates","/delivery","/quality","/pipeline","/enrich")) {
  curl.exe -s -o NUL -w "$p -> %{http_code}`n" "https://axiom.vectoredu.online$p"
}
```

HTML currently returns `cf-cache-status: DYNAMIC`, so Cloudflare is not caching pages and no
purge is needed. If that ever changes, a stale page will look exactly like a failed deploy —
check `cf-cache-status` and `age` before re-running anything.

Finally, tidy up:

```bash
rm -f /tmp/axiom_console.tgz /tmp/live.html
sudo rm -rf /opt/axiom/apps/console.bak    # only once the deploy is confirmed good
```

## Rolling back

The source backup from step 3 is the fast path:

```bash
cd /opt/axiom/apps
sudo rm -rf console && sudo mv console.bak console
cd /opt/axiom
sudo docker compose -f compose.yaml -f compose.tunnel.yaml --env-file .env.deploy build console
sudo docker compose -f compose.yaml -f compose.tunnel.yaml --env-file .env.deploy up -d --no-deps console
```

To skip the rebuild, the previous image may still be on the host — `docker images axiom-console`
and retag the older ID to `axiom-console:latest` before running `up -d --no-deps console`.

## Known issues

Not blockers, but each one will cost somebody time:

- **No Elastic IP.** Stopping and starting the instance changes its public address. Anything
  that pins the old one breaks. The tunnel is unaffected — it dials out.
- **Port 22 is open to `0.0.0.0/0`** on the instance's security group. Worth narrowing, now that
  Instance Connect removes any need for broad SSH access.
- **The live site has no authentication.** The repo's `deploy/Caddyfile` wraps everything in
  `basic_auth` — "keep the entire prototype, including immutable supplier artifacts, behind one
  credential" — but the `Caddyfile.tunnel` actually in use omits that block, so every route
  including `/api/artifact/*` is publicly readable. Intentional for a public demo, but it is not
  what the repo's own config implies.
- **`/opt/axiom` is mode 777.** Broader than it needs to be for a directory holding the
  deployment.
- **No CD.** Every deploy is manual. If this repeats often, the console-only path is small
  enough to script against a `workflow_dispatch` trigger and an OIDC role.
