# Thai Re-entry Predictor 5.0 — Cloud Backend (Alpha)

This folder contains the first cloud-computing backend for the existing GitHub Pages frontend.

## What is implemented

- FastAPI service with \`/health\` and interactive OpenAPI docs at \`/docs\`.
- \`POST /api/v1/atmosphere\`: NRLMSISE-00 mass density and temperature at a requested UTC time and geodetic location.
- \`POST /api/v1/simulate\`: obtains an entry-interface state from TLE/SGP4, then integrates a point-mass trajectory with Earth gravity, J2 and atmospheric drag.
- Atmosphere density is evaluated with PyMSIS using NRLMSISE-00 (version 0).
- The integrator uses an explicit midpoint method and smaller time steps at lower altitudes.

## Deploy from a phone

1. Open the GitHub repository and select branch \`v5-cloud-backend\`.
2. Open Render and choose **New + → Web Service**.
3. Connect GitHub and select \`meta43260-cyber/De-orbit\`.
4. Select branch \`v5-cloud-backend\`.
5. Render should detect the root \`render.yaml\`. If it asks for settings, use:
   - Root Directory: \`backend\`
   - Build Command: \`pip install -r requirements.txt\`
   - Start Command: \`uvicorn main:app --host 0.0.0.0 --port $PORT\`
   - Health Check Path: \`/health\`
6. Choose the Free plan for initial testing and deploy.
7. After deployment, open the service URL followed by \`/health\`; it should return JSON with \`"status": "ok"\`.
8. Open \`/docs\` to test the API from the phone browser.

## Important Render Free-plan behavior

Free web services sleep after 15 minutes without inbound traffic and may take about one minute to wake on the next request. The free filesystem is ephemeral. This backend is stateless, so no persistent disk is required for the current alpha. Free instances are intended for testing/hobby projects, not operational use.

## Model inputs and scientific limitations

- Supply current TLE lines with valid checksums.
- If \`entry_time_utc\` is omitted, the API searches forward from \`start_time_utc\` for an SGP4-predicted crossing of the selected entry altitude (default 120 km), up to \`max_search_hours\`.
- If \`entry_time_utc\` is supplied, the SGP4 state at that time must be between 70 and 180 km altitude.
- The default F10.7, F10.7a and Ap values are scenario assumptions (150, 150 and 15), not live space-weather measurements. Pass actual values when available.
- The atmosphere is assumed to co-rotate with Earth; neutral winds are omitted.
- The object is treated as a rigid point mass with constant mass, reference area and drag coefficient. Ablation, thermal response, breakup, fragmentation and debris dispersion are not implemented.
- The SGP4/TLE uncertainty remains in the initial condition. This does not make an old TLE accurate.
- The Sutton-Graves value is a convective heat-flux estimate, not a full thermal protection or material-survival model.

## API examples

### Health
\`GET /health\`

### Atmosphere
\`POST /api/v1/atmosphere\`

\`\`\`json
{
  "time_utc": "2026-11-09T00:00:00Z",
  "latitude_deg": 13.7,
  "longitude_deg": 100.9,
  "altitude_km": 100,
  "f107_sfu": 150,
  "f107a_sfu": 150,
  "ap": 15
}
\`\`\`

### Simulation
\`POST /api/v1/simulate\`

\`\`\`json
{
  "tle_line1": "PASTE TLE LINE 1",
  "tle_line2": "PASTE TLE LINE 2",
  "start_time_utc": "2026-11-09T00:00:00Z",
  "mass_kg": 500,
  "reference_area_m2": 10,
  "drag_coefficient": 2.2,
  "nose_radius_m": 0.5,
  "f107_sfu": 150,
  "f107a_sfu": 150,
  "ap": 15
}
\`\`\`

Use real TLE lines, not the placeholders above. The API validates line checksums.

## Next development stage

- Connect the v4 frontend to this API through a configurable backend URL.
- Add scenario presets and object-property sources.
- Add uncertainty propagation for TLE, mass, area and drag coefficient.
- Add a separate thermal/ablation/fragmentation model only after validating this baseline against reference cases.
