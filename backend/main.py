"""
Thai Re-entry Predictor v5.0 — Cloud API
Hybrid model: SGP4 entry-interface estimate + MSISE-00 atmospheric flight.
This is an educational/research prototype, not an operational re-entry forecast.
"""
from __future__ import annotations

import math
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pymsis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sgp4.api import Satrec, SGP4_ERRORS, jday, WGS72

APP_VERSION = "5.0.0-cloud-alpha"
MU = 3.986004418e14          # m^3/s^2
RE = 6378137.0               # WGS-84 equatorial radius, m
J2 = 1.08262668e-3
EARTH_OMEGA = 7.2921150e-5   # rad/s
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
ENTRY_ALT_DEFAULT_KM = 120.0
MSIS_VERSION = 0             # NRLMSISE-00 (not MSIS 2.x)

app = FastAPI(
    title="Thai Re-entry Predictor API",
    version=APP_VERSION,
    description="Cloud computation API for the Thai Re-entry Predictor v5.0 prototype.",
)

origins = os.getenv("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if origins.strip() == "*" else [x.strip() for x in origins.split(",") if x.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AtmosphereRequest(BaseModel):
    time_utc: datetime
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    altitude_km: float = Field(ge=0, le=1000)
    f107_sfu: float = Field(default=150.0, ge=50, le=400)
    f107a_sfu: float = Field(default=150.0, ge=50, le=400)
    ap: float = Field(default=15.0, ge=0, le=400)


class SimulationRequest(BaseModel):
    tle_line1: str
    tle_line2: str
    start_time_utc: datetime
    entry_time_utc: Optional[datetime] = None
    entry_altitude_km: float = Field(default=ENTRY_ALT_DEFAULT_KM, ge=80, le=150)
    max_search_hours: float = Field(default=168.0, ge=1, le=720)
    max_simulation_seconds: int = Field(default=1800, ge=60, le=3600)
    sample_interval_s: float = Field(default=2.0, ge=0.5, le=30)
    mass_kg: float = Field(gt=0, le=100000)
    reference_area_m2: float = Field(gt=0, le=1000)
    drag_coefficient: float = Field(default=2.2, ge=0.1, le=5.0)
    nose_radius_m: float = Field(default=0.5, gt=0.001, le=20)
    f107_sfu: float = Field(default=150.0, ge=50, le=400)
    f107a_sfu: float = Field(default=150.0, ge=50, le=400)
    ap: float = Field(default=15.0, ge=0, le=400)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def tle_checksum_ok(line: str) -> bool:
    line = line.rstrip()
    if len(line) < 69:
        return False
    total = 0
    for char in line[:68]:
        if char.isdigit():
            total += int(char)
        elif char == "-":
            total += 1
    try:
        return total % 10 == int(line[68])
    except ValueError:
        return False


def make_satrec(line1: str, line2: str) -> Satrec:
    l1, l2 = line1.strip(), line2.strip()
    if not l1.startswith("1 ") or not l2.startswith("2 "):
        raise HTTPException(status_code=422, detail="TLE ต้องประกอบด้วยบรรทัด 1 และ 2")
    if not tle_checksum_ok(l1) or not tle_checksum_ok(l2):
        raise HTTPException(status_code=422, detail="TLE checksum ไม่ถูกต้อง")
    try:
        return Satrec.twoline2rv(l1, l2, WGS72)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"อ่าน TLE ไม่สำเร็จ: {exc}") from exc


def julian_parts(when: datetime) -> tuple[float, float]:
    dt = as_utc(when)
    sec = dt.second + dt.microsecond / 1_000_000.0
    return jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, sec)


def gmst_rad(when: datetime) -> float:
    jd, fr = julian_parts(when)
    jd_full = jd + fr
    t = (jd_full - 2451545.0) / 36525.0
    deg = (
        280.46061837
        + 360.98564736629 * (jd_full - 2451545.0)
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0
    )
    return math.radians(deg % 360.0)


def teme_to_ecef(r_teme_m: np.ndarray, when: datetime) -> np.ndarray:
    theta = gmst_rad(when)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = r_teme_m
    return np.array([c * x + s * y, -s * x + c * y, z], dtype=float)


def ecef_to_geodetic(ecef_m: np.ndarray) -> tuple[float, float, float]:
    x, y, z = [float(v) for v in ecef_m]
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-8:
        lat = math.copysign(math.pi / 2.0, z)
        alt = abs(z) - RE * (1.0 - WGS84_F)
        return math.degrees(lat), math.degrees(lon), alt / 1000.0

    lat = math.atan2(z, p * (1.0 - WGS84_E2))
    alt = 0.0
    for _ in range(8):
        sin_lat = math.sin(lat)
        n = RE / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        cos_lat = math.cos(lat)
        alt = p / max(abs(cos_lat), 1e-12) - n
        denom = p * (1.0 - WGS84_E2 * n / (n + alt))
        lat = math.atan2(z, denom)
    return math.degrees(lat), math.degrees(lon), alt / 1000.0


def propagate_tle(sat: Satrec, when: datetime) -> tuple[np.ndarray, np.ndarray, tuple[float, float, float]]:
    jd, fr = julian_parts(when)
    error, position_km, velocity_km_s = sat.sgp4(jd, fr)
    if error:
        message = SGP4_ERRORS.get(error, "SGP4 propagation error")
        raise HTTPException(status_code=422, detail=f"SGP4: {message}")
    r = np.asarray(position_km, dtype=float) * 1000.0
    v = np.asarray(velocity_km_s, dtype=float) * 1000.0
    lat, lon, alt_km = ecef_to_geodetic(teme_to_ecef(r, when))
    return r, v, (lat, lon, alt_km)


def find_entry_state(req: SimulationRequest, sat: Satrec):
    start = as_utc(req.start_time_utc)
    target = req.entry_altitude_km
    if req.entry_time_utc is not None:
        entry_time = as_utc(req.entry_time_utc)
        r, v, geo = propagate_tle(sat, entry_time)
        if not (70.0 <= geo[2] <= 180.0):
            raise HTTPException(
                status_code=422,
                detail=f"TLE ณ entry_time อยู่ที่ {geo[2]:.1f} km; ต้องอยู่ในช่วง 70–180 km เพื่อเริ่มจำลอง",
            )
        if float(np.dot(r, v)) >= 0.0:
            raise HTTPException(status_code=422, detail="สถานะ ณ entry_time ไม่ได้กำลังลดระดับลง")
        return entry_time, r, v, geo, "user_supplied_entry_time"

    prev_time = start
    prev = propagate_tle(sat, prev_time)
    prev_alt = prev[2][2]
    if 70.0 <= prev_alt <= 180.0:
        if float(np.dot(prev[0], prev[1])) >= 0.0:
            raise HTTPException(status_code=422, detail="วัตถุอยู่ในช่วงความสูง Re-entry แต่กำลังไต่ระดับขึ้น ไม่ใช่ขาลง")
        return prev_time, prev[0], prev[1], prev[2], "start_state_already_in_entry_region"
    if prev_alt < 70.0:
        raise HTTPException(status_code=422, detail="วัตถุต่ำกว่า 70 km แล้ว; ไม่สามารถเริ่มจาก entry interface ได้")

    search_seconds = int(req.max_search_hours * 3600)
    step_seconds = 60
    elapsed = 0
    while elapsed < search_seconds:
        elapsed = min(elapsed + step_seconds, search_seconds)
        current_time = start + timedelta(seconds=elapsed)
        current = propagate_tle(sat, current_time)
        current_alt = current[2][2]
        if prev_alt > target and current_alt <= target:
            lo, hi = elapsed - step_seconds, elapsed
            # Refine the crossing time without assuming a straight ground track.
            for _ in range(18):
                mid = (lo + hi) / 2.0
                mid_state = propagate_tle(sat, start + timedelta(seconds=mid))
                if mid_state[2][2] > target:
                    lo = mid
                else:
                    hi = mid
            entry_time = start + timedelta(seconds=(lo + hi) / 2.0)
            r, v, geo = propagate_tle(sat, entry_time)
            return entry_time, r, v, geo, "sgp4_predicted_entry_interface"
        prev_time, prev, prev_alt = current_time, current, current_alt

    raise HTTPException(
        status_code=422,
        detail=f"ไม่พบการผ่านระดับ {target:.0f} km ภายใน {req.max_search_hours:g} ชั่วโมงจาก TLE ชุดนี้",
    )


def msis_density(when: datetime, lat: float, lon: float, alt_km: float, req) -> tuple[float, float]:
    # Explicit solar/geomagnetic inputs avoid hidden assumptions about future index data.
    dt = as_utc(when).replace(tzinfo=None)
    dates = np.array([np.datetime64(dt.isoformat())])
    output = pymsis.calculate(
        dates,
        np.array([lon]),
        np.array([lat]),
        np.array([max(0.1, min(1000.0, alt_km))]),
        f107s=np.array([req.f107_sfu]),
        f107as=np.array([req.f107a_sfu]),
        aps=np.array([req.ap]),
        version=MSIS_VERSION,
    )
    flat = np.asarray(output).reshape(-1, 11)[0]
    return max(0.0, float(flat[0])), float(flat[10])


def gravity_j2(r: np.ndarray) -> np.ndarray:
    x, y, z = r
    r2 = float(np.dot(r, r))
    radius = math.sqrt(r2)
    base = -MU / (radius ** 3) * r
    z2_r2 = (z * z) / r2
    factor = 1.5 * J2 * MU * RE * RE / (radius ** 5)
    perturb = factor * np.array([
        x * (5.0 * z2_r2 - 1.0),
        y * (5.0 * z2_r2 - 1.0),
        z * (5.0 * z2_r2 - 3.0),
    ])
    return base + perturb


def state_diagnostics(when: datetime, y: np.ndarray, req):
    r, v = y[:3], y[3:]
    lat, lon, alt_km = ecef_to_geodetic(teme_to_ecef(r, when))
    rho, temp = msis_density(when, lat, lon, alt_km, req)
    v_atm = np.cross(np.array([0.0, 0.0, EARTH_OMEGA]), r)
    v_rel = v - v_atm
    speed_rel = float(np.linalg.norm(v_rel))
    drag_acc = -0.5 * rho * req.drag_coefficient * req.reference_area_m2 / req.mass_kg * speed_rel * v_rel
    decel = float(np.linalg.norm(drag_acc))
    q_dyn = 0.5 * rho * speed_rel * speed_rel
    heat_flux = 1.83e-4 * math.sqrt(max(rho, 0.0) / req.nose_radius_m) * speed_rel ** 3
    return {
        "latitude_deg": lat,
        "longitude_deg": lon,
        "altitude_km": alt_km,
        "speed_inertial_km_s": float(np.linalg.norm(v)) / 1000.0,
        "speed_relative_km_s": speed_rel / 1000.0,
        "density_kg_m3": rho,
        "temperature_K": temp,
        "dynamic_pressure_Pa": q_dyn,
        "drag_deceleration_m_s2": decel,
        "convective_heat_flux_W_m2": heat_flux,
        "drag_acceleration_m_s2": drag_acc.tolist(),
    }


def acceleration(when: datetime, y: np.ndarray, req):
    r, v = y[:3], y[3:]
    diag = state_diagnostics(when, y, req)
    drag = np.asarray(diag["drag_acceleration_m_s2"], dtype=float)
    return np.concatenate((v, gravity_j2(r) + drag)), diag


def choose_step(alt_km: float) -> float:
    if alt_km > 105.0:
        return 1.0
    if alt_km > 85.0:
        return 0.25
    if alt_km > 60.0:
        return 0.05
    return 0.02


def simulate_entry(req: SimulationRequest):
    sat = make_satrec(req.tle_line1, req.tle_line2)
    entry_time, r0, v0, entry_geo, entry_source = find_entry_state(req, sat)
    y = np.concatenate((r0, v0))
    t = 0.0
    points = []
    last_saved = -1e9
    impact = False
    status = "time_limit"
    steps = 0
    max_steps = 25000
    current_time = entry_time
    _, last_diag = acceleration(current_time, y, req)

    while t < req.max_simulation_seconds and steps < max_steps:
        alt = last_diag["altitude_km"]
        dt = min(choose_step(alt), req.max_simulation_seconds - t)

        # Explicit midpoint (RK2) with density recomputed at the midpoint.
        k1, _ = acceleration(current_time, y, req)
        y_mid = y + 0.5 * dt * k1
        mid_time = current_time + timedelta(seconds=0.5 * dt)
        k2, mid_diag = acceleration(mid_time, y_mid, req)
        y_new = y + dt * k2
        next_time = current_time + timedelta(seconds=dt)
        _, next_diag = acceleration(next_time, y_new, req)

        if next_diag["altitude_km"] <= 0.0:
            h0 = max(0.0, last_diag["altitude_km"])
            h1 = next_diag["altitude_km"]
            frac = h0 / max(h0 - h1, 1e-9)
            y = y + frac * (y_new - y)
            t += frac * dt
            current_time = current_time + timedelta(seconds=frac * dt)
            _, last_diag = acceleration(current_time, y, req)
            last_diag["altitude_km"] = 0.0
            impact = True
            status = "ground_intersection"
            steps += 1
            break

        y = y_new
        t += dt
        current_time = next_time
        last_diag = next_diag
        steps += 1

        if t - last_saved >= req.sample_interval_s or steps == 1:
            points.append({
                "elapsed_s": round(t, 3),
                "time_utc": current_time.isoformat(),
                **{k: round(v, 8) if isinstance(v, float) else v for k, v in last_diag.items() if k != "drag_acceleration_m_s2"},
            })
            last_saved = t

    if steps >= max_steps:
        status = "step_limit"
    if not impact and t >= req.max_simulation_seconds:
        status = "time_limit"

    if not points or (impact and points[-1].get("elapsed_s") != round(t, 3)):
        points.append({
            "elapsed_s": round(t, 3),
            "time_utc": current_time.isoformat(),
            **{k: round(v, 8) if isinstance(v, float) else v for k, v in last_diag.items() if k != "drag_acceleration_m_s2"},
        })

    peak_q = max((p["dynamic_pressure_Pa"] for p in points), default=0.0)
    peak_heat = max((p["convective_heat_flux_W_m2"] for p in points), default=0.0)
    peak_decel = max((p["drag_deceleration_m_s2"] for p in points), default=0.0)

    return {
        "api_version": APP_VERSION,
        "model": {
            "orbit_initialization": "SGP4 / TEME",
            "atmosphere": "NRLMSISE-00 (pymsis version=0)",
            "dynamics": "Earth gravity + J2 + aerodynamic drag",
            "integration": "explicit midpoint RK2 with altitude-dependent step",
            "atmosphere_rotation": "rigid co-rotation; winds omitted",
            "mass_model": "constant mass and reference area; ablation/fragmentation not included",
            "heating_model": "Sutton-Graves convective estimate; not a thermal-survival solver",
        },
        "entry_interface": {
            "source": entry_source,
            "time_utc": entry_time.isoformat(),
            "latitude_deg": entry_geo[0],
            "longitude_deg": entry_geo[1],
            "altitude_km": entry_geo[2],
            "velocity_km_s": float(np.linalg.norm(v0)) / 1000.0,
        },
        "inputs": {
            "mass_kg": req.mass_kg,
            "reference_area_m2": req.reference_area_m2,
            "drag_coefficient": req.drag_coefficient,
            "nose_radius_m": req.nose_radius_m,
            "f107_sfu": req.f107_sfu,
            "f107a_sfu": req.f107a_sfu,
            "ap": req.ap,
            "solar_geomagnetic_inputs": "user/default scenario values; not fetched live",
        },
        "status": status,
        "impact": impact,
        "elapsed_simulation_s": round(t, 3),
        "steps": steps,
        "peak_dynamic_pressure_Pa": peak_q,
        "peak_convective_heat_flux_W_m2": peak_heat,
        "peak_drag_deceleration_m_s2": peak_decel,
        "track": points,
        "limitations": [
            "Entry-interface time and state are initialized from the supplied TLE using SGP4.",
            "TLE propagation uncertainty is not removed by the atmospheric integration.",
            "The atmosphere is assumed to co-rotate with Earth; neutral winds are omitted.",
            "Mass, area and drag coefficient are held constant; breakup, ablation and debris clouds are not modeled.",
            "The result is a scenario simulation, not an operational prediction or certified impact point.",
        ],
    }


@app.get("/")
def root():
    return {"name": "Thai Re-entry Predictor API", "version": APP_VERSION, "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION, "atmosphere_model": "NRLMSISE-00"}


@app.get("/api/v1/tle/{norad_id}")
def get_latest_tle(norad_id: int):
    """Fetch and validate the latest public GP TLE from CelesTrak by NORAD catalog ID."""
    if not 1 <= norad_id <= 999999:
        raise HTTPException(status_code=422, detail="NORAD Catalog ID ต้องอยู่ระหว่าง 1–999999")

    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Thai-Reentry-Predictor/5.0 (educational research tool)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"CelesTrak ตอบกลับ HTTP {exc.code}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"ติดต่อ CelesTrak ไม่สำเร็จ: {exc}") from exc

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    name, line1, line2 = None, None, None
    for i, line in enumerate(lines):
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            candidate1, candidate2 = line, lines[i + 1]
            try:
                catalog1 = int(candidate1[2:7])
                catalog2 = int(candidate2[2:7])
            except (ValueError, IndexError):
                continue
            if catalog1 == norad_id and catalog2 == norad_id:
                name = lines[i - 1] if i > 0 and not lines[i - 1].startswith(("1 ", "2 ")) else f"NORAD {norad_id}"
                line1, line2 = candidate1, candidate2
                break

    if not line1 or not line2:
        raise HTTPException(status_code=404, detail=f"ไม่พบ GP TLE สำหรับ NORAD {norad_id}")

    if not tle_checksum_ok(line1) or not tle_checksum_ok(line2):
        raise HTTPException(status_code=502, detail="CelesTrak ส่ง TLE ที่ checksum ไม่ถูกต้อง")

    epoch_field = line1[18:32]
    try:
        yy = int(epoch_field[:2])
        year = 1900 + yy if yy >= 57 else 2000 + yy
        day_of_year = float(epoch_field[2:])
        epoch = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="ไม่สามารถอ่าน TLE Epoch ได้") from exc

    return {
        "norad_id": norad_id,
        "name": name,
        "line1": line1,
        "line2": line2,
        "epoch_utc": epoch.isoformat(),
        "source": "CelesTrak GP",
        "source_url": url,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/atmosphere")
def atmosphere(req: AtmosphereRequest):
    try:
        rho, temp = msis_density(
            as_utc(req.time_utc), req.latitude_deg, req.longitude_deg, req.altitude_km, req
        )
        return {
            "model": "NRLMSISE-00",
            "time_utc": as_utc(req.time_utc).isoformat(),
            "latitude_deg": req.latitude_deg,
            "longitude_deg": req.longitude_deg,
            "altitude_km": req.altitude_km,
            "mass_density_kg_m3": rho,
            "temperature_K": temp,
            "solar_geomagnetic_inputs": {
                "f107_sfu": req.f107_sfu,
                "f107a_sfu": req.f107a_sfu,
                "ap": req.ap,
                "source": "request values / defaults; not live observations",
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MSISE-00 calculation failed: {exc}") from exc


@app.post("/api/v1/simulate")
def simulate(req: SimulationRequest):
    try:
        return simulate_entry(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {exc}") from exc
