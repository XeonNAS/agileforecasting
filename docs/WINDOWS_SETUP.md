# AgileForecasting — Windows Setup Guide

This guide walks a Windows user through installing, configuring, and running
AgileForecasting from scratch. No prior experience with Python or Streamlit is
assumed.

**Supported:** Windows 10 (21H2 or later) and Windows 11.

---

## What is AgileForecasting?

AgileForecasting is a web app that runs locally in your browser. It connects to
**Azure DevOps**, pulls your team's throughput and sprint calendar data, and runs
**Monte Carlo simulations** to answer two questions:

- **How Many** — how many items are we likely to finish by a target date?
- **When** — when are we likely to finish a given number of items?

It is a Streamlit app: you run it on your machine with one command, then open it
at `http://localhost:8501` in any browser.

---

## Step 1 — Install Git

If you do not already have Git, download and install it from:

> https://git-scm.com/download/win

Accept the defaults. After installation, open a new **PowerShell** window and
confirm it works:

```powershell
git --version
```

Expected output: `git version 2.x.x.windows.x`

---

## Step 2 — Install Python 3.12

Check whether Python is already installed:

```powershell
python --version
```

or (if `python` is not recognised):

```powershell
py --version
```

If the version shown is **3.12 or later**, skip to Step 3. If Python is missing
or older than 3.12, download the latest 3.12 installer from:

> https://www.python.org/downloads/windows/

During installation:

- **Check "Add python.exe to PATH"** on the first screen.
- Leave all other defaults.

After installation, close any open PowerShell windows and open a new one, then
verify:

```powershell
python --version
```

Expected output: `Python 3.12.x`

---

## Step 3 — Clone the repository

### Public repository

```powershell
git clone https://github.com/XeonNAS/agileforecasting.git
cd agileforecasting
```

### Private repository

If the repository is private, you need a **GitHub Personal Access Token (PAT)**
with at least `repo` (read) scope. This is a *GitHub* credential, separate from
the Azure DevOps PAT used inside the app itself.

Create one at: **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**

Then clone using HTTPS with your token:

```powershell
git clone https://<your-github-username>:<your-github-token>@github.com/XeonNAS/agileforecasting.git
cd agileforecasting
```

Git for Windows stores these credentials in **Windows Credential Manager**
automatically, so you will not be prompted again on subsequent pulls.

---

## Step 4 — Open PowerShell in the project folder

If you are not already inside the `agileforecasting` folder, open File Explorer,
navigate to the folder, then right-click inside it and choose
**"Open in Terminal"** (Windows 11) or **"Open PowerShell window here"**
(Windows 10 with the PowerShell context menu extension).

Alternatively, open PowerShell anywhere and change to the folder:

```powershell
cd C:\path\to\agileforecasting
```

All commands in the remaining steps must be run from **inside this folder**.

---

## Step 5 — Create the virtual environment

A virtual environment keeps AgileForecasting's dependencies isolated from the
rest of your system.

```powershell
python -m venv .venv
```

This creates a `.venv` folder inside the project directory.

---

## Step 6 — Activate the virtual environment

```powershell
.venv\Scripts\Activate.ps1
```

Your prompt will change to show `(.venv)` at the start, confirming that the
environment is active.

> **Execution policy error?**
> If you see `cannot be loaded because running scripts is disabled on this system`,
> run this once and then try activating again:
>
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```
>
> This allows locally created scripts to run without changing system-wide policy.

---

## Step 7 — Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` installs all dependencies **and** the `agile_mc` package
from the local `src/` folder. Both steps are required; do not skip the
`pip install -r requirements.txt` line.

Installation takes a minute or two on the first run.

---

## Step 8 — Launch AgileForecasting

```powershell
streamlit run streamlit_app\app.py
```

Streamlit will print something like:

```
  You can now view your Streamlit app in your browser.
  Local URL: http://localhost:8501
```

Open `http://localhost:8501` in your browser. The app opens automatically in
most setups.

---

## Step 9 — Configure the app (first run)

When the app opens you will see a sidebar on the left. Work through it from top
to bottom.

### Azure DevOps settings

| Field | What to enter |
|---|---|
| **Encryption passphrase** | A password you choose. Used to encrypt saved settings on disk. Not your ADO password — you pick this yourself. |

Click **Save now** after entering the passphrase so your settings are
remembered between sessions.

### Save PAT toggle

The **Save PAT to OS keyring** toggle is on by default. When enabled, your
Azure DevOps PAT is saved to **Windows Credential Manager** after a successful
sync. You will not need to paste the PAT again on future runs.

### Connect to Azure DevOps

Fill in the form fields:

| Field | Example |
|---|---|
| **Org** | `myorg` |
| **Project** | `MyProject` |
| **Team** | `MyProject Team` |
| **PAT** | Your Azure DevOps Personal Access Token |
| **Saved Query URL or GUID** | The GUID or full URL of your "Done by date" saved query |

**Creating an Azure DevOps PAT:**

1. Go to `https://dev.azure.com/<yourorg>` → click your profile picture →
   **Personal access tokens**.
2. Click **New Token**.
3. Give it a name (e.g. `AgileForecasting`).
4. Set **Expiration** to a date that suits your team's rotation policy.
5. Under **Scopes**, select:
   - **Work Items → Read**
   - **Work → Read**
6. Click **Create** and copy the token.

Paste the token into the **PAT** field in the sidebar.

### Click Refresh

Click **Refresh from Azure DevOps** at the bottom of the sidebar form. The app
will connect to Azure DevOps, load your sprint and throughput data, and display
the forecast results.

After a successful sync, the PAT is saved to Windows Credential Manager (if
the toggle is on) and cleared from the browser session.

---

## Step 10 — Stop the app

Press **Ctrl + C** in the PowerShell window where Streamlit is running.

---

## Reopening the app in a future session

Open PowerShell, navigate to the project folder, activate the virtual
environment, and launch:

```powershell
cd C:\path\to\agileforecasting
.venv\Scripts\Activate.ps1
streamlit run streamlit_app\app.py
```

Your PAT will be loaded automatically from Windows Credential Manager.
Your saved settings (org, project, team, query) will be reloaded when you
click **Load saved** in the sidebar.

To deactivate the virtual environment when you are done:

```powershell
deactivate
```

---

## Updating the app

To pull the latest version from GitHub:

```powershell
cd C:\path\to\agileforecasting
git pull
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Always reinstall dependencies after pulling, in case new packages were added.

---

## Where settings are stored

| What | Where on Windows |
|---|---|
| Saved ADO settings (org, project, team, query) — encrypted | `C:\Users\<YourName>\.config\agileforecasting\ado_settings.enc.json` |
| Saved PAT — OS keyring | Windows Credential Manager (search "agileforecasting" in Credential Manager) |
| PAT fallback when keyring unavailable — encrypted | `C:\Users\<YourName>\.config\agileforecasting\pat.enc.json` |

Both files are encrypted. Your encryption passphrase is never written to disk.

---

## Removing saved credentials

### Remove the saved PAT via the sidebar

Click **Forget saved PAT** in the sidebar.

### Remove the saved PAT from Windows Credential Manager manually

1. Open **Start → Credential Manager**.
2. Click **Windows Credentials**.
3. Find the entry named `agileforecasting`.
4. Click the down-arrow then **Remove**.

### Remove the saved PAT via PowerShell (venv active)

```powershell
python -c "from agile_mc.pat_store import forget_pat; forget_pat(); print('done')"
```

### Remove all saved settings

Delete the config folder:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.config\agileforecasting"
```

---

## Optional: set a persistent environment variable

To avoid typing the encryption passphrase every time the app starts, set it as
a **user environment variable** in PowerShell:

```powershell
[System.Environment]::SetEnvironmentVariable("MC_ADO_PASSPHRASE", "your-passphrase", "User")
```

After setting it, close and reopen PowerShell for the change to take effect.
The app will then pick up the passphrase automatically and auto-save settings
after each sync.

To remove it later:

```powershell
[System.Environment]::SetEnvironmentVariable("MC_ADO_PASSPHRASE", $null, "User")
```

---

## Troubleshooting

### `python` is not recognised

The Python installer was run without "Add python.exe to PATH". Options:

- Re-run the Python installer, click **Modify**, and check the PATH option.
- Use `py` instead of `python` for all commands. Windows includes the **Python
  Launcher** (`py`) by default, which finds installed Python versions
  automatically:
  ```powershell
  py -3.12 -m venv .venv
  ```

### Execution policy blocks `.ps1` activation

```
.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled
```

Fix (applies to your user account only, not system-wide):

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### `streamlit` is not recognised after install

This means the virtual environment is not active. Activate it first:

```powershell
.venv\Scripts\Activate.ps1
```

Then run `streamlit run streamlit_app\app.py` again.

### Port 8501 is already in use

Streamlit prints:
```
Address already in use
Port 8501 is unavailable.
```

Either stop the other Streamlit instance (check your taskbar / Task Manager for
other PowerShell windows), or run on a different port:

```powershell
streamlit run streamlit_app\app.py --server.port 8502
```

Then open `http://localhost:8502` in your browser.

### Windows Defender SmartScreen blocks the installer

When running the Python or Git installer for the first time, SmartScreen may
show a blue warning. Click **More info** → **Run anyway**. These are official
installers from python.org and git-scm.com.

### Antivirus quarantines files in `.venv`

Some antivirus products flag newly downloaded Python packages. If files inside
`.venv` are quarantined:

1. Check your antivirus quarantine and restore the files.
2. Add the project folder (e.g. `C:\Users\<YourName>\Projects\agileforecasting`)
   as an exclusion in your antivirus settings.
3. Re-run `pip install -r requirements.txt`.

### Windows Credential Manager / keyring not working

If the **Save PAT to OS keyring** toggle does not appear to persist the PAT,
check that the `keyring` package is installed:

```powershell
pip show keyring
```

If it is missing:

```powershell
pip install keyring
```

If keyring still does not work, the toggle label will change to
**Save PAT (encrypted file, needs passphrase)**. Enter your encryption
passphrase in the sidebar and the PAT will be saved to an encrypted file
instead. This is an automatic fallback — no manual action is needed.

### `ModuleNotFoundError: No module named 'agile_mc'`

The `agile_mc` package was not installed from `src/`. Reinstall with:

```powershell
pip install -r requirements.txt
```

The last line of `requirements.txt` (`-e .`) installs the local package.
If that still fails:

```powershell
pip install -e .
```

### `ModuleNotFoundError: No module named 'streamlit'` (or any other package)

The virtual environment is not active. Run:

```powershell
.venv\Scripts\Activate.ps1
```

Then retry your command.

### Path too long errors during install

Windows has a 260-character path limit by default. If `pip install` fails with
a path-length error:

1. Open **Group Policy Editor** (`gpedit.msc`) or search for
   **"Enable Win32 long paths"** in Windows Settings.
2. Enable the policy: **Local Computer Policy → Computer Configuration →
   Administrative Templates → System → Filesystem → Enable Win32 long paths**.
3. Restart PowerShell and retry.

Alternatively, clone the repo closer to the root of the drive:

```powershell
cd C:\
git clone https://github.com/XeonNAS/agileforecasting.git
```

### Chart export (PNG/SVG) does not work

Chart export uses **Kaleido**, which bundles its own Chromium binary. No
separate Chrome installation is required. If export fails, ensure the venv is
active and that `kaleido` is installed:

```powershell
pip show kaleido
```

If your organisation blocks Chromium execution, set the `BROWSER_PATH`
environment variable to point to an allowed Chrome or Chromium installation:

```powershell
$env:BROWSER_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"
streamlit run streamlit_app\app.py
```
