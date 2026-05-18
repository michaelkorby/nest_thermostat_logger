 # Nest Thermostat Logger

This project polls the Google Nest Smart Device Management (SDM) API for thermostat data and writes snapshots to per-device CSV files. You can schedule the poller to run (for example, every five minutes) to build a timeline of ambient temperature, humidity, set points, and HVAC activity for each thermostat in your account.

## Prerequisites

- Python 3.10 or newer.
- Access to the Google Nest **Smart Device Management (SDM) API**.
- OAuth 2.0 Client credentials (client ID and client secret) created for the SDM API.
- A long-lived refresh token that has already been authorized for the SDM API scope (`https://www.googleapis.com/auth/sdm.service`).

> The poller does not perform interactive authorization. You must obtain the refresh token once (for example, using Google's OAuth Playground) and place the token and client credentials in `config.json`. With the refresh token saved, the script automatically exchanges it for new access tokens, so it will not prompt you again.

### How to Enable the SDM API and Create Credentials

1. **Join the Device Access Program**
   - Visit <https://developers.google.com/nest/device-access> and complete the registration (one-time $5 fee).
   - Create a **Device Access Console** project. After setup, note the **Project ID** (also referred to as the `enterpriseId`); you will copy this into `config.json`.

2. **Create OAuth 2.0 Credentials**
   - Open the [Google Cloud Console](https://console.cloud.google.com/apis/credentials) and make sure the Device Access project is selected (the same project ID shown in the Device Access Console).
   - If prompted to configure the OAuth consent screen, choose **External**, complete the required fields, and add your Google account to the list of test users. Save the consent screen.
   - Click **Create Credentials → OAuth client ID**. Select **Web application** for the application type.
   - Under **Authorized redirect URIs**, add both:
     - `https://www.google.com` (used for initial manual authorization)
     - `http://localhost:8085` (used for automatic re-authorization)
   - Click **Create**.
   - The dialog displays your new **client ID** and **client secret**; download the JSON or copy the values. These are the credentials you will paste into `config.json` and use in the PCM authorization URL.

   **These values are stored under API Keys in LastPass**

3. **Link Your Nest Account via Partner Connections Manager (PCM)**
   - Sign in to the [Device Access Console](https://console.nest.google.com/device-access) and open your Device Access project.
   - Copy your **Project ID** (displayed on the project page); you will need it in the authorization URL.
   - In a browser, navigate to  
     `https://nestservices.google.com/partnerconnections/<project-id>/auth?redirect_uri=https://www.google.com&access_type=offline&prompt=consent&client_id=<oauth2-client-id>&response_type=code&scope=https://www.googleapis.com/auth/sdm.service`  
     replacing `<project-id>` with the Device Access project ID and `<oauth2-client-id>` with the OAuth client ID created in step 2.
   - Use the Google account that owns or manages your Nest home. When the PCM permissions screen appears, toggle on your structure and thermostat(s), then continue through the consent prompts until you are redirected to `https://www.google.com?code=...`.
   - Copy the `code` parameter from the redirected URL; you will exchange it for tokens in the next step. 
   
   **The exact URL and authorization code in `private_notes.md`, which is ignored by git.**

4. **Exchange the Authorization Code for Tokens**
   - From a terminal, run (PowerShell syntax shown; adjust quoting as needed for Bash):
     ```
     curl -L -X POST "https://oauth2.googleapis.com/token" ^
       -H "Content-Type: application/x-www-form-urlencoded" ^
       -d "client_id=<oauth2-client-id>&client_secret=<oauth2-client-secret>&code=<authorization-code>&grant_type=authorization_code&redirect_uri=https://www.google.com"
     ```
     replacing the placeholders with your client credentials and the `code` copied from PCM. The `-d` flag ensures the request includes a form body, preventing the HTTP 411 error.
   - The response contains both an `access_token` and `refresh_token`. Store the refresh token (and optionally the access token) securely; you will paste the refresh token into `config.json`.
   - Complete the authorization by making one initial devices list call with the access token:
     ```
     curl -X GET "https://smartdevicemanagement.googleapis.com/v1/enterprises/<project-id>/devices" \
       -H "Content-Type: application/json" \
       -H "Authorization: Bearer <access-token>"
     ```
     This call finalizes the PCM linkage so events and API access remain active.
     Record the exact command, response payload, and device snapshot in `private_notes.md`.

   **Outputs (constructed URLs, token responses, device snapshots) are kept in `private_notes.md`, which is ignored by git**

With these steps complete you have everything required for unattended access to the Nest SDM API.

## Setup

1. Create a machine-specific virtual environment on the C: drive (outside of Google Drive) and install dependencies:

```bash
py -3.12 -m venv C:\venvs\nest_thermostat_logger_%COMPUTERNAME%
C:\venvs\nest_thermostat_logger_%COMPUTERNAME%\Scripts\Activate
pip install -r requirements.txt
```

   > **Note:** Virtual environments are stored in `C:\venvs\` to avoid syncing large files through Google Drive. If you have an existing `.venv*` directory in the project folder, use `migrate_venv.bat` to move it to the new location.

2. Copy `config.sample.json` to `config.json` and update the values:

   ```json
   {
     "project_id": "project-id-123",
     "client_id": "your-client-id.apps.googleusercontent.com",
     "client_secret": "your-client-secret",
     "refresh_token": "your-refresh-token",
     "output_dir": "logs",
     "temperature_scale": "fahrenheit",
     "timezone": "America/New_York",
     "weather": {
       "latitude": 41.158680,
       "longitude": -73.772659,
       "user_agent": "nest-thermostat-logger (you@example.com)"
     }
   }
   ```

   - `project_id` is the Nest SDM project/enterprise ID.
   - `client_id` and `client_secret` are from your OAuth 2.0 credentials.
   - `refresh_token` is the long-lived token you generated during the one-time authorization.
   - `output_dir` is where CSV files will be written (paths relative to the project root are allowed).
   - `temperature_scale` can be `fahrenheit` (default) or `celsius`; set points are rounded to whole degrees.
   - `timezone` (optional) defaults to `America/New_York` and controls how timestamps are split into `date` and `time` columns.
   - `weather` (optional) enables outside-air readings via [weather.gov](https://weather.gov). Supply latitude/longitude and a user agent string that includes contact info per NWS guidelines. The logger will record the latest observation temperature in Fahrenheit.

3. Run the poller:

   ```bash
   python -m src.nest_poller --config config.json
   ```

   The script creates/updates one CSV per thermostat (e.g., `logs/living_room.csv`) and appends a new row containing:

   - Local `date` and `time` (based on the configured timezone)
   - Ambient temperature
   - Humidity
   - Heat and cool set points (if available)
   - Outdoor temperature (if weather configuration is provided)
   - Current HVAC status (`HEATING`, `COOLING`, or `OFF`)

4. Launch the Streamlit dashboard (runs as a Windows service — see [Dashboard](#dashboard) below):

   ```bash
   streamlit run src/dashboard.py
   ```

   The dashboard reads the CSV files in `logs/` and lets you choose a thermostat, date range, and optional humidity overlay. It plots the ambient temperature, set points, and HVAC activity (shown as a bar when heating or cooling is active).

    > The repository includes `.streamlit/credentials.toml` with an empty email so Streamlit can start headlessly (useful for services). The helper script `start_dashboard.bat` automatically points `STREAMLIT_CONFIG_DIR` to that folder and disables usage-stat prompts.

## Scheduling (Poller Service)

The poller runs as a Windows service managed by **NSSM** so it polls every five minutes automatically, starts with Windows, and keeps running without anyone logged in.

**This is set up on the basement computer.**

### How it works

`start_poller.bat` calls `poller_scheduler.py --duration 0` (run forever), which loops every 5 minutes polling the Nest API. The scheduler handles `SIGTERM` gracefully — NSSM sends this signal when the service is stopped, so the current poll finishes cleanly before the process exits.

### Service account (important for Google Drive)

If the project files live in your Google Drive folder (e.g. `C:\Users\mkorb\My Drive\...`), the service must run as **your Windows user account**, not Local System. Local System cannot access user-profile paths. `install_poller_service.bat` prompts for your username and password and grants the account the *Log on as a service* right automatically.

> **Google Drive sync note:** Google Drive for Desktop must be configured in **Mirror** mode (files stored locally) for the poller to read/write CSV files while no user is interactively logged in. In Stream mode, files are fetched on demand and may not be available without an active user session.

### Installing the poller service

1. Download and install NSSM from <https://nssm.cc/download>. Place `nssm.exe` at `C:\nssm\win64\nssm.exe`.
2. Run `install_poller_service.bat` from an **elevated** (Run as Administrator) Command Prompt:

   ```bat
   install_poller_service.bat
   ```

   The script will:
   - Install the `NestPoller` service pointing to `start_poller.bat`
   - Prompt for your Windows username/password to run as your user account
   - Configure stdout/stderr log rotation (~5 MB cap)
   - Set the service to auto-start with Windows
   - Automatically remove the old `Nest Thermostat Logger` Task Scheduler task (if present)
   - Start the service immediately

3. Confirm it’s running:

   ```bat
   nssm status NestPoller
   ```

   You should see `SERVICE_RUNNING`. Check `logs\poller_service.log` to confirm polls are succeeding.

### Common management commands

| Goal | Command |
|---|---|
| Stop the poller | `nssm stop NestPoller` |
| Restart the poller | `nssm restart NestPoller` |
| Open the GUI editor | `nssm edit NestPoller` |
| Remove the service | `nssm remove NestPoller confirm` |
| View service status | `nssm status NestPoller` |

### Troubleshooting

- **Service starts but no data in `logs/`:** Check `logs\poller_service_error.log`. Most likely the virtual environment path is wrong or `config.json` is missing.
- **Access denied errors:** The service is running as Local System but the project is in a Google Drive folder. Re-run `install_poller_service.bat` and supply your Windows credentials when prompted.
- **Service stops after a poll and doesn’t restart:** This should not happen — `--duration 0` runs forever. If it does, open `nssm edit NestPoller` → **Exit actions** tab and set **Restart if exit code is** to `0` and `1`.

## Dashboard

The Streamlit dashboard runs as a Windows service managed by **NSSM** (Non-Sucking Service Manager), so it starts automatically with Windows and keeps running without anyone being logged in. The service is named `NestDashboard` and invokes `start_dashboard.bat`.

**This is set up on the basement computer.**

### Installing NSSM

1. Download NSSM from <https://nssm.cc/download> (grab the latest release zip).
2. Extract the zip and copy `nssm.exe` to a permanent location such as `C:\tools\nssm\` or add the `win64` folder from the zip to your `PATH`.

### Creating the Service

Run `install_dashboard_service.bat` from an **elevated** (Run as Administrator) Command Prompt:

```bat
install_dashboard_service.bat
```

Credentials: mkorb / password for live.com

The script will:
- Locate `nssm.exe` (from `PATH` or `C:\tools\nssm\`)
- Remove any existing `NestDashboard` service (safe to re-run)
- Install the service pointing to `start_dashboard.bat` in the project folder
- Configure the working directory, stdout/stderr log rotation, and auto-start
- Start the service immediately

On success you'll see `NestDashboard service installed and started successfully.`

Open a browser to `http://localhost:8501` to verify the dashboard loads.

### Common Management Commands

| Goal | Command |
|---|---|
| Stop the service | `nssm stop NestDashboard` |
| Restart the service | `nssm restart NestDashboard` |
| Open the GUI editor | `nssm edit NestDashboard` |
| Remove the service | `nssm remove NestDashboard confirm` |
| View service status | `nssm status NestDashboard` |

### Troubleshooting

- **Service starts but dashboard is unreachable:** Check `logs\dashboard_error.log` for Python or Streamlit errors. Make sure the virtual environment exists at `C:\venvs\nest_thermostat_logger_%COMPUTERNAME%`.
- **Service won't start:** Run `start_dashboard.bat` manually from the project folder first to confirm it works interactively, then re-check the path passed to `nssm install`.
- **Port conflict:** Streamlit defaults to port `8501`. If another process owns it, add `--server.port=8502` (or any free port) to the `streamlit run` line in `start_dashboard.bat` and update any bookmarks accordingly.
- **Logs keep growing:** `AppRotateBytes 1048576` caps each log at ~1 MB before rotating. Increase or remove that setting if you need more history.

## Automatic Re-authorization

When the refresh token expires (typically after ~7 days for unverified OAuth apps), the poller automatically handles re-authorization:

1. A browser tab opens to the Google/Nest authorization page
2. The poller waits up to **7 days** for you to complete authorization (you don't need to be logged in immediately)
3. When you log in and click "Allow", the poller captures the code and exchanges it for new tokens
4. The new refresh token is saved to `config.json` automatically
5. Polling resumes on the next scheduled run

During this waiting period, subsequent poller runs will detect that re-authorization is already in progress and skip gracefully (no duplicate browser tabs).

**Requirements**: Ensure `http://localhost:8085` is listed in your OAuth client's Authorized Redirect URIs in Google Cloud Console (see step 2 in Prerequisites).

To force re-authorization manually (e.g., to test the flow):

```bash
python -m src.nest_poller --config config.json --reauth
```

## Troubleshooting

- **Refresh token expires after ~7 days**: This is common with unverified OAuth apps in Testing mode. The poller now handles this automatically by opening a browser for re-authorization. If you want to prevent this:
  - **Option 1 (Recommended)**: Get your OAuth app verified in Google Cloud Console. This requires submitting your app for review, but refresh tokens will last much longer (up to 6 months of inactivity).
  - **Option 2**: Keep the app in Testing mode but ensure it runs frequently (your 5-minute schedule should help).
  - When Google provides a new refresh token during normal token refresh, the poller automatically saves it to `config.json`.

- **Browser doesn't open for re-authorization**: If running headless (no user session), the browser cannot open. Run the poller manually with `--reauth` in an interactive session to complete re-authorization.
  
- If the script logs `Failed to refresh access token`, verify the client credentials and refresh token.
- `No thermostat devices found` indicates the SDM API returned no thermostat devices. Check the project linkage and API permissions.
- Use `--log-level DEBUG` for detailed logging:

  ```bash
  python -m src.nest_poller --config config.json --log-level DEBUG
  ```

