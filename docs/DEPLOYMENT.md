# Deployment Guide

[中文](DEPLOYMENT.zh-CN.md) | [English](DEPLOYMENT.md)

This guide is written for first-time users.  
The goal is simple: get the stack running on a Windows PC and make it reachable from your phone.

## Expected result

After deployment, you should be able to:

- start the local Codex control services on your PC
- open the web panel from your phone through a private address
- approve a new phone from the desktop tool on first login
- continue viewing and sending Codex messages from the phone

## Recommended environment

### OS

- Windows 10 / 11

### Required software

- Python 3.11+
- Node.js 22 LTS
- Git
- nginx for Windows
- Tailscale (recommended)

### Why Tailscale is recommended

Because it matches this project well:

- private access for your own devices
- no need to expose the app directly to the public internet
- easy to connect both phone and PC to the same private network

## Directory layout

Recommended structure:

```text
mobileCodexHelper/
├─ deploy/
├─ docs/
├─ scripts/
├─ upstream-overrides/
├─ vendor/
│  └─ claudecodeui-1.25.2/
├─ mobile_codex_control.py
└─ requirements.txt
```

Where:

- `vendor/claudecodeui-1.25.2/` is your downloaded upstream source
- `upstream-overrides/claudecodeui-1.25.2/` contains this project's patch layer

## Step 1: Prepare upstream source

Download upstream:

- `siteboon/claudecodeui`
- version: `v1.25.2`

Place it here:

```text
vendor/claudecodeui-1.25.2
```

## Step 2: Apply the override layer

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/apply-upstream-overrides.ps1
```

This copies this project's modified files into the upstream checkout.

If you want to validate the published override set before doing a real install, you can also run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke-test-override-flow.ps1 -UpstreamZip <path-to-upstream-zip>
```

This extracts the upstream zip into a temporary folder, applies the overrides, and verifies that every override file lands where expected.

## Step 3: Install Node dependencies

```powershell
cd vendor/claudecodeui-1.25.2
npm install
```

If this fails, first check:

- whether you are using Node 22 LTS
- whether npm can access the network

## Step 4: Install Python dependency

If you only want to run the desktop tool directly:

- you usually do not need extra Python packages

If you want to package the desktop tool as an `.exe`:

```powershell
pip install -r requirements.txt
```

## Step 5: Check the local environment

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check-mobile-codex-runtime.ps1
```

Important things to confirm:

- `UpstreamExists = True`
- `Node` is present
- `Nginx` is present
- if you want remote phone access, `Tailscale` should also be present

## Step 6: Start the stack

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-mobile-codex-stack.ps1
```

By default this starts:

- app service on `127.0.0.1:3001`
- nginx proxy on `127.0.0.1:8080`

## Step 7: Launch the desktop control tool

```powershell
python mobile_codex_control.py
```

or:

```powershell
scripts\launch-mobile-codex-control.cmd
```

You should see:

- PC app service state
- nginx state
- Tailscale state
- phone connection state
- pending device approvals
- trusted device whitelist

## Step 8: First account registration

Open this in a desktop browser:

```text
http://127.0.0.1:3001
```

Complete the first registration.

Notes:

- this is a single-user setup
- the first account becomes the main account for the system

## Step 9: Configure phone access

### Option A: local testing only

First verify from the desktop browser:

- login works
- project list loads correctly
- sending a message works

### Option B: private remote access through Tailscale

Make sure:

- the PC is logged into Tailscale
- the phone is logged into the same tailnet

Then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/enable-mobile-codex-remote.ps1
```

After that:

- confirm remote publish state in the desktop control tool
- open the private HTTPS address shown by Tailscale on the phone

## Step 10: First-time device approval

When a new device logs in for the first time:

1. the phone page shows that approval is required
2. the desktop control tool shows a pending device request
3. you verify the device details
4. you approve it on the PC
5. the phone automatically continues the login flow

This is an important security feature. Do not skip it.

## Optional environment variables

Defaults are usually enough. Only set these if your environment is unusual.

- `MOBILE_CODEX_UPSTREAM_DIR`
  - custom upstream `claudecodeui` directory
- `MOBILE_CODEX_NODE`
  - custom Node executable path
- `MOBILE_CODEX_NGINX`
  - custom nginx executable path
- `MOBILE_CODEX_TAILSCALE`
  - custom Tailscale executable path
- `MOBILE_CODEX_ASCII_ALIAS`
  - custom ASCII alias path for Windows path compatibility

## Fast troubleshooting order

If you are not sure where to start, this order saves the most time:

1. run `scripts/check-mobile-codex-runtime.ps1`
2. confirm `http://127.0.0.1:3001` opens in the desktop browser
3. confirm the desktop tool shows both the PC app and nginx as healthy
4. test the phone browser
5. test the wrapper app / WebView last

## Troubleshooting

### Scripts run, but the page does not open

Check:

- whether `127.0.0.1:3001` responds
- whether `127.0.0.1:8080` responds
- whether both services show healthy in the desktop tool

### Mobile browser works, but a wrapped app does not

Check whether the wrapper supports:

- `localStorage`
- `Authorization` headers
- WebSocket
- cookies

If the browser works but the wrapper does not, it is often a wrapper compatibility issue.

### You get 502 errors

Check:

- `tmp/logs/mobile-codex-app.stdout.log`
- `tmp/logs/mobile-codex-app.stderr.log`
- nginx logs

## Post-deployment self-check

If this is for your own long-term use, verify at least these points:

- the first phone login really creates a pending approval on the PC
- a different phone does not inherit trust automatically
- if the PC-side service stops, the phone no longer gets a working session
- no real `.env`, database, or log files were left inside the repository

## Suggested next steps

Once the deployment works, consider:

- packaging the desktop tool as an `.exe`
- configuring your own Tailscale access flow
- adjusting nginx or Caddy templates for your environment
