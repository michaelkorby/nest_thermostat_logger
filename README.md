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
   - Under **Authorized redirect URIs**, add `https://www.google.com` (the same value used later in the PCM authorization URL), then click **Create**.
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

1. Create a machine-specific virtual environment and install dependencies. Update the -3.13 version to -3.12 if that's what we have (e.g. on the laptop):

```bash
py -3.13 -m venv .venv_%COMPUTERNAME%
.\.venv_%COMPUTERNAME%\Scripts\Activate
pip install -r requirements.txt
```

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

4. Launch the Streamlit dashboard (scheduled as a service via NSSM, details below):

   ```bash
   streamlit run src/dashboard.py
   ```

   The dashboard reads the CSV files in `logs/` and lets you choose a thermostat, date range, and optional humidity overlay. It plots the ambient temperature, set points, and HVAC activity (shown as a bar when heating or cooling is active).

    > The repository includes `.streamlit/credentials.toml` with an empty email so Streamlit can start headlessly (useful for services). The helper script `start_dashboard.bat` automatically points `STREAMLIT_CONFIG_DIR` to that folder and disables usage-stat prompts.

The helper script `start_dashboard.bat` and the examples below automatically look for `.venv_%COMPUTERNAME%` first and fall back to `.venv`. When configuring Task Scheduler on each computer, point to the matching `python.exe`:

```
Program/script: C:\Users\<you>\...\nest_thermostat_logger\.venv_%COMPUTERNAME%\Scripts\python.exe
```

## Scheduling

To collect data continuously, schedule the poller with Windows Task Scheduler (or your preferred scheduler) to run every five minutes:

1. Open **Task Scheduler** → **Create Task…** (not *Basic Task*, so you can set the repeat interval).
2. On the **General** tab:
   - Name: `Nest Thermostat Logger`
   - Select **Run whether user is logged on or not** and **Run with highest privileges**.
3. On the **Triggers** tab → **New…**:
   - Begin the task: **On a schedule**.
   - Settings: **Daily**, start at 12:00:00 AM.
   - Check **Repeat task every:** `5 minutes`.
   - Set **for a duration of:** `Indefinitely`.
   - Ensure the trigger is **Enabled** and click **OK**.
4. On the **Actions** tab → **New…**, configure the poller via your virtual environment, for example:

   ```
   Program/script: C:\Users\mkorb\My Drive\Code\nest_thermostat_logger\.venv\Scripts\python.exe
   Add arguments: -m src.nest_poller --config config.json --log-file logs\poller.log
   Start in: C:\Users\mkorb\My Drive\Code\nest_thermostat_logger
   ```
5. On the **Conditions** tab, uncheck **Start the task only if the computer is on AC power** (optional).
6. On the **Settings** tab, enable **Allow task to be run on demand** and ensure **If the task is already running, then the following rule applies: Do not start a new instance**.

Click **OK**, supply your account password if prompted, and verify the task appears in the scheduler library. Ensure the account running the task has read access to `config.json` and write access to `logs/`.

**NOTE: It doesn't seem to start running every 5 minutes until it crosses the initial start time boundary. If it's set to midnight, will need to wait until midnight for it start running.**

> **Tip:** The `--log-file` argument overwrites `logs/poller.log` every run, so you always have the latest poll execution details. Remove the flag if you prefer writing logs only to Task Scheduler’s history.

**This is set up on the basement computer**

## Dashboard
Streamlit dashboard is set up via nssm on the basement computer. The name of the service is "NestDashboard". It calls start_dashboard.bat.

## Troubleshooting

- If the script logs `Failed to refresh access token`, verify the client credentials and refresh token.
- `No thermostat devices found` indicates the SDM API returned no thermostat devices. Check the project linkage and API permissions.
- Use `--log-level DEBUG` for detailed logging:

  ```bash
  python -m src.nest_poller --config config.json --log-level DEBUG
  ```

