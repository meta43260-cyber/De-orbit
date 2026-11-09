"""
Thai Re-entry Predictor v6.0 — Advanced Cloud API
Hybrid model: SGP4 entry-interface estimate + MSISE-00 atmospheric flight.
This is an educational/research prototype, not an operational re-entry forecast.
"""
from __future__ import annotations

import math
import json
import os
import urllib.error
import urllib.request
import http.cookiejar
import urllib.parse
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pymsis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sgp4.api import Satrec, SGP4_ERRORS, jday, WGS72
from sgp4.omm import initialize as initialize_omm
from sgp4.exporter import export_tle

APP_VERSION = "6.0.0-advanced-reentry-foundation"
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
    description="Advanced phone-cloud orbital decay and scenario-based re-entry analysis API; research prototype, not an operational forecast.",
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
    tle_line1: Optional[str] = None
    tle_line2: Optional[str] = None
    omm_fields: Optional[dict] = None
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


class V6ReentryScenarioRequest(BaseModel):
    object_type: str = Field(default="satellite", pattern="^(satellite|rocket_body)$")
    material_label: str = Field(default="user_defined", min_length=1, max_length=80)
    tle_line1: Optional[str] = None
    tle_line2: Optional[str] = None
    omm_fields: Optional[dict] = None
    start_time_utc: datetime
    entry_time_utc: Optional[datetime] = None
    entry_altitude_km: float = Field(default=120.0, ge=80, le=150)
    max_search_hours: float = Field(default=168.0, ge=1, le=720)
    max_simulation_seconds: int = Field(default=1800, ge=60, le=3600)
    sample_interval_s: float = Field(default=2.0, ge=0.5, le=30)
    mass_kg: float = Field(gt=0, le=100000)
    reference_area_m2: float = Field(gt=0, le=1000)
    drag_coefficient: float = Field(default=2.2, ge=0.1, le=5.0)
    nose_radius_m: float = Field(default=0.5, gt=0.001, le=20)
    heat_of_ablation_j_kg: float = Field(default=8.0e6, ge=1.0e5, le=5.0e7)
    ablation_efficiency: float = Field(default=0.5, ge=0.0, le=1.0)
    heated_area_factor: float = Field(default=1.5, ge=0.1, le=10.0)
    f107_sfu: float = Field(default=150.0, ge=50, le=400)
    f107a_sfu: float = Field(default=150.0, ge=50, le=400)
    ap: float = Field(default=15.0, ge=0, le=400)


class OrbitalDecayTrendRequest(BaseModel):
    omm_records: list[dict] = Field(min_length=2, max_length=24)


class SGP4ValidationRequest(BaseModel):
    omm_fields: dict
    tle_line1: Optional[str] = None
    tle_line2: Optional[str] = None

class LongTermValidationRequest(BaseModel):
    omm_fields: dict
    offsets_hours: list[float] = Field(default=[0.0, 6.0, 12.0, 24.0], min_length=1, max_length=12)


class OMMQualityRequest(BaseModel):
    omm_fields: dict
    reference_time_utc: Optional[datetime] = None
    stale_after_hours: float = Field(default=24.0, ge=1.0, le=720.0)


class HistoricalOMMSeriesRequest(BaseModel):
    omm_records: list[dict] = Field(min_length=2, max_length=12)
    offsets_hours: list[float] = Field(default=[0.0, 6.0, 12.0, 24.0, 48.0, 72.0, 168.0], min_length=1, max_length=12)


class OrbitReliabilityRequest(BaseModel):
    omm_records: list[dict] = Field(min_length=2, max_length=12)
    offsets_hours: list[float] = Field(default=[0.0, 6.0, 12.0, 24.0, 48.0, 72.0, 168.0], min_length=1, max_length=12)
    stale_after_hours: float = Field(default=24.0, ge=1.0, le=720.0)


class LatestEpochComparisonRequest(BaseModel):
    older_omm_fields: dict
    offsets_hours: list[float] = Field(default=[0.0, 6.0, 12.0, 24.0, 48.0, 72.0, 168.0], min_length=1, max_length=12)


class EpochComparisonValidationRequest(BaseModel):
    older_omm_fields: dict
    newer_omm_fields: dict
    offsets_hours: list[float] = Field(default=[0.0, 6.0, 12.0, 24.0, 48.0, 72.0, 168.0], min_length=1, max_length=12)


class BatchValidationRequest(BaseModel):
    catalog_ids: list[int] = Field(min_length=1, max_length=8)

class AtmosphereSensitivityRequest(BaseModel):
    time_utc: datetime
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    altitudes_km: list[float] = Field(default=[80, 100, 120, 150, 200, 300], min_length=1, max_length=12)
    baseline_f107_sfu: float = Field(default=150.0, ge=50, le=400)
    baseline_f107a_sfu: float = Field(default=150.0, ge=50, le=400)
    baseline_ap: float = Field(default=15.0, ge=0, le=400)


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


def make_satrec_from_request(req: SimulationRequest) -> Satrec:
    if req.omm_fields:
        try:
            fields = {str(k).upper(): v for k, v in req.omm_fields.items()}
            required = ("NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
                        "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")
            missing = [key for key in required if fields.get(key) in (None, "")]
            if missing:
                raise ValueError("OMM fields missing: " + ", ".join(missing))
            fields.setdefault("OBJECT_ID", "0000-000A")
            fields.setdefault("CLASSIFICATION_TYPE", "U")
            fields.setdefault("EPHEMERIS_TYPE", 0)
            fields.setdefault("ELEMENT_SET_NO", 0)
            fields.setdefault("REV_AT_EPOCH", 0)
            fields.setdefault("BSTAR", 0.0)
            fields.setdefault("MEAN_MOTION_DOT", 0.0)
            fields.setdefault("MEAN_MOTION_DDOT", 0.0)
            sat = Satrec()
            initialize_omm(sat, fields)
            if sat.error:
                raise ValueError(SGP4_ERRORS.get(sat.error, "SGP4 OMM initialization error"))
            return sat
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"อ่าน OMM ไม่สำเร็จ: {exc}") from exc
    if req.tle_line1 and req.tle_line2:
        return make_satrec(req.tle_line1, req.tle_line2)
    raise HTTPException(status_code=422, detail="ต้องส่งข้อมูล TLE หรือ OMM อย่างใดอย่างหนึ่ง")


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
    sat = make_satrec_from_request(req)
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
    return {
        "status": "ok", "version": APP_VERSION, "atmosphere_model": "NRLMSISE-00",
        "spacetrack_configured": bool(os.getenv("SPACETRACK_USER") and os.getenv("SPACETRACK_PASS")),
    }


# In-memory cache: CelesTrak asks clients not to poll GP data more often than every 2 hours.
_TLE_SOURCE_CACHE: dict[int, tuple[float, dict]] = {}
_TLE_CACHE_TTL_SECONDS = 7200


def _tle_epoch(line1: str) -> datetime:
    if len(line1) < 32:
        raise ValueError("TLE line 1 is too short to contain an Epoch")
    field = line1[18:32]
    yy = int(field[:2])
    year = 1900 + yy if yy >= 57 else 2000 + yy
    day_of_year = float(field[2:])
    if not 1.0 <= day_of_year < 367.0:
        raise ValueError("TLE Epoch day is outside its valid range")
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1)


def _parse_tle_text(raw: str, norad_id: int) -> tuple[str, str, str]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            l1, l2 = line, lines[i + 1]
            if len(l1) < 69 or len(l2) < 69:
                continue
            try:
                id1, id2 = int(l1[2:7]), int(l2[2:7])
            except ValueError:
                continue
            if id1 == norad_id and id2 == norad_id:
                if not tle_checksum_ok(l1) or not tle_checksum_ok(l2):
                    raise ValueError("TLE checksum mismatch")
                name = lines[i - 1] if i > 0 and not lines[i - 1].startswith(("1 ", "2 ")) else f"NORAD {norad_id}"
                _tle_epoch(l1)
                return name, l1, l2
    raise ValueError(f"No valid 2-line element set for NORAD {norad_id}")


def _fetch_tle_candidate(url: str, source: str, norad_id: int, headers: dict) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=6) as response:
        raw = response.read().decode("utf-8", errors="replace")
    name, line1, line2 = _parse_tle_text(raw, norad_id)
    epoch = _tle_epoch(line1)
    return {
        "norad_id": norad_id, "name": name, "line1": line1, "line2": line2,
        "epoch_utc": epoch.isoformat(), "epoch_timestamp": epoch.timestamp(),
        "source": source, "source_url": url, "validation": "passed",
    }


@app.get("/api/v1/tle/{norad_id}")
def get_latest_tle(norad_id: int):
    """Compare available public GP/SupGP TLE feeds and select the newest valid Epoch."""
    if not 1 <= norad_id <= 99999:
        raise HTTPException(
            status_code=422,
            detail="TLE แบบ 2-line รองรับ NORAD ID 1–99999 เท่านั้น; วัตถุที่มี Catalog ID 6 หลักต้องใช้ OMM/JSON",
        )

    now = time.time()
    cached = _TLE_SOURCE_CACHE.get(norad_id)
    if cached and now - cached[0] < _TLE_CACHE_TTL_SECONDS:
        result = dict(cached[1])
        result["cache"] = {"hit": True, "age_seconds": round(now - cached[0]), "ttl_seconds": _TLE_CACHE_TTL_SECONDS}
        return result

    headers = {"User-Agent": "Thai-Reentry-Predictor/5.1 (educational research tool)"}
    source_specs = [
        ("CelesTrak GP", f"https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle",
         "CelesTrak data service; primary GP feed"),
        ("CelesTrak SupGP", f"https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?CATNR={norad_id}&FORMAT=tle",
         "CelesTrak supplemental feed; not an independent tracking network"),
    ]
    candidates = []
    source_status = []

    for source, url, relationship in source_specs:
        started = time.monotonic()
        try:
            candidate = _fetch_tle_candidate(url, source, norad_id, headers)
            candidate["relationship"] = relationship
            candidate["latency_ms"] = round((time.monotonic() - started) * 1000)
            candidates.append(candidate)
            source_status.append({
                "source": source, "status": "ok", "epoch_utc": candidate["epoch_utc"],
                "latency_ms": candidate["latency_ms"], "relationship": relationship,
            })
        except urllib.error.HTTPError as exc:
            source_status.append({"source": source, "status": "error", "error": f"HTTP {exc.code}",
                                  "latency_ms": round((time.monotonic() - started) * 1000),
                                  "relationship": relationship})
        except Exception as exc:
            source_status.append({"source": source, "status": "error", "error": str(exc)[:240],
                                  "latency_ms": round((time.monotonic() - started) * 1000),
                                  "relationship": relationship})

    mirror_url = f"https://tle.ivanstanojevic.me/api/tle/{norad_id}"
    started = time.monotonic()
    mirror_relationship = "Community mirror; documented as CelesTrak-derived, not an independent tracking network"
    try:
        request = urllib.request.Request(mirror_url, headers=headers)
        with urllib.request.urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        returned_id = int(payload.get("satelliteId", payload.get("satellite_id", norad_id)))
        if returned_id != norad_id:
            raise ValueError(f"Mirror returned NORAD ID {returned_id}, expected {norad_id}")
        name = str(payload.get("name") or f"NORAD {norad_id}")
        line1, line2 = str(payload.get("line1") or "").strip(), str(payload.get("line2") or "").strip()
        if not tle_checksum_ok(line1) or not tle_checksum_ok(line2):
            raise ValueError("Mirror TLE checksum mismatch")
        if int(line1[2:7]) != norad_id or int(line2[2:7]) != norad_id:
            raise ValueError("Mirror TLE catalog number mismatch")
        epoch = _tle_epoch(line1)
        candidates.append({
            "norad_id": norad_id, "name": name, "line1": line1, "line2": line2,
            "epoch_utc": epoch.isoformat(), "epoch_timestamp": epoch.timestamp(),
            "source": "TLE API mirror", "source_url": mirror_url, "validation": "passed",
            "relationship": mirror_relationship,
            "latency_ms": round((time.monotonic() - started) * 1000),
        })
        source_status.append({"source": "TLE API mirror", "status": "ok", "epoch_utc": epoch.isoformat(),
                              "latency_ms": round((time.monotonic() - started) * 1000),
                              "relationship": mirror_relationship})
    except urllib.error.HTTPError as exc:
        source_status.append({"source": "TLE API mirror", "status": "error", "error": f"HTTP {exc.code}",
                              "latency_ms": round((time.monotonic() - started) * 1000),
                              "relationship": mirror_relationship})
    except Exception as exc:
        source_status.append({"source": "TLE API mirror", "status": "error", "error": str(exc)[:240],
                              "latency_ms": round((time.monotonic() - started) * 1000),
                              "relationship": mirror_relationship})

    if not candidates:        raise HTTPException(status_code=503, detail={
            "message": f"ไม่พบ TLE ที่ผ่านการตรวจสอบสำหรับ NORAD {norad_id}",
            "sources_checked": source_status,
        })

    candidates.sort(key=lambda item: item["epoch_timestamp"], reverse=True)
    selected = candidates[0]
    fetched_at = datetime.now(timezone.utc).isoformat()
    age_hours = (datetime.now(timezone.utc) - _tle_epoch(selected["line1"])).total_seconds() / 3600.0
    result = {
        "norad_id": norad_id, "name": selected["name"],
        "line1": selected["line1"], "line2": selected["line2"],
        "epoch_utc": selected["epoch_utc"], "source": selected["source"],
        "source_url": selected["source_url"], "fetched_at_utc": fetched_at,
        "selected_source": selected["source"],
        "selection_method": "newest_valid_epoch",
        "epoch_age_hours": round(age_hours, 2),
        "freshness": "stale_over_72h" if age_hours > 72 else "stale_over_24h" if age_hours > 24 else "under_24h",
        "candidates": [
            {k: v for k, v in item.items() if k != "epoch_timestamp"}
            for item in candidates
        ],
        "sources_checked": source_status,
        "source_independence_note": "These feeds are not three independent tracking networks. CelesTrak GP and SupGP are CelesTrak products; the community mirror is CelesTrak-derived.",
        "cache": {"hit": False, "ttl_seconds": _TLE_CACHE_TTL_SECONDS},
    }
    _TLE_SOURCE_CACHE[norad_id] = (now, result)
    return result



# OMM catalog feed: accepts five- and six-digit catalog IDs and preserves full OMM fields.
_OMM_SOURCE_CACHE: dict[int, tuple[float, dict]] = {}

# Space-Track uses session-cookie authentication. Keep the authenticated opener in
# process memory; credentials and cookies are never returned by API responses.
_SPACETRACK_LOCK = threading.Lock()
_SPACETRACK_SESSION = {"opener": None, "created_at": 0.0}
_SPACETRACK_SESSION_TTL = 20 * 60


def _spacetrack_opener():
    username = os.getenv("SPACETRACK_USER")
    password = os.getenv("SPACETRACK_PASS")
    if not username or not password:
        raise RuntimeError("credentials_not_configured")
    with _SPACETRACK_LOCK:
        now = time.time()
        opener = _SPACETRACK_SESSION.get("opener")
        if opener is not None and now - _SPACETRACK_SESSION["created_at"] < _SPACETRACK_SESSION_TTL:
            return opener
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        login_data = urllib.parse.urlencode({"identity": username, "password": password}).encode("utf-8")
        login_req = urllib.request.Request(
            "https://www.space-track.org/ajaxauth/login", data=login_data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Thai-Reentry-Predictor/5.4"},
            method="POST",
        )
        with opener.open(login_req, timeout=18) as response:
            login_text = response.read(4096).decode("utf-8", errors="replace").lower()
        if "login failed" in login_text or "invalid username" in login_text:
            raise RuntimeError("authentication_failed")
        _SPACETRACK_SESSION.update({"opener": opener, "created_at": now})
        return opener


def _fetch_spacetrack_omm(catalog_id: int) -> dict:
    opener = _spacetrack_opener()
    url = "https://www.space-track.org/basicspacedata/query/class/gp/NORAD_CAT_ID/" + str(catalog_id) + "/format/json"
    request = urllib.request.Request(url, headers={"User-Agent": "Thai-Reentry-Predictor/5.4"})
    with opener.open(request, timeout=18) as response:
        raw = response.read().decode("utf-8", errors="replace")
    item = _parse_omm_payload(raw, catalog_id)
    item.update({
        "source": "Space-Track GP", "source_url": url,
        "relationship": "18th Space Defense Squadron / Space-Track GP; primary catalog feed, not an independent sensor network",
        "latency_ms": None,
    })
    return item


def _parse_omm_payload(raw: str, catalog_id: int) -> dict:
    payload = json.loads(raw)
    records = payload if isinstance(payload, list) else [payload]
    for record in records:
        if not isinstance(record, dict):
            continue
        fields = {str(k).upper(): v for k, v in record.items()}
        raw_id = fields.get("NORAD_CAT_ID", fields.get("NORAD_CATID", fields.get("CATALOG_NUMBER")))
        if raw_id is None or int(raw_id) != catalog_id:
            continue
        required = ("EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
                    "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")
        if any(fields.get(k) in (None, "") for k in required):
            continue
        epoch = datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00"))
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=timezone.utc)
        else:
            epoch = epoch.astimezone(timezone.utc)
        numeric = {k: float(fields[k]) for k in required if k != "EPOCH"}
        if not (0 < numeric["MEAN_MOTION"] < 20 and 0 <= numeric["ECCENTRICITY"] < 1
                and 0 <= numeric["INCLINATION"] <= 180):
            raise ValueError("OMM orbital elements are outside accepted ranges")
        fields["NORAD_CAT_ID"] = str(catalog_id)
        # sgp4.omm.initialize expects a strict ISO timestamp without a trailing
        # UTC "Z" or timezone suffix. The parsed datetime is already normalized
        # to UTC, so serialize it as a naive UTC timestamp with microseconds.
        fields["EPOCH"] = epoch.strftime("%Y-%m-%dT%H:%M:%S.%f")
        fields.setdefault("OBJECT_ID", "0000-000A")
        fields.setdefault("CLASSIFICATION_TYPE", "U")
        fields.setdefault("EPHEMERIS_TYPE", 0)
        fields.setdefault("ELEMENT_SET_NO", 0)
        fields.setdefault("REV_AT_EPOCH", 0)
        fields.setdefault("BSTAR", 0.0)
        fields.setdefault("MEAN_MOTION_DOT", 0.0)
        fields.setdefault("MEAN_MOTION_DDOT", 0.0)
        sat = Satrec()
        initialize_omm(sat, fields)
        if sat.error:
            raise ValueError(SGP4_ERRORS.get(sat.error, "SGP4 OMM initialization error"))
        tle_lines = None
        if catalog_id <= 99999:
            line1, line2 = export_tle(sat)
            if not tle_checksum_ok(line1) or not tle_checksum_ok(line2):
                raise ValueError("OMM-to-TLE export failed checksum validation")
            tle_lines = {"line1": line1, "line2": line2}
        return {
            "catalog_id": catalog_id,
            "name": str(fields.get("OBJECT_NAME") or fields.get("OBJECT_ID") or f"CATALOG {catalog_id}"),
            "epoch_utc": epoch.isoformat(),
            "epoch_timestamp": epoch.timestamp(),
            "omm_fields": fields,
            "tle_export": tle_lines,
            "validation": "passed",
        }
    raise ValueError(f"No valid OMM record for catalog ID {catalog_id}")


@app.get("/api/v1/omm/{catalog_id}")
def get_latest_omm(catalog_id: int):
    """Prefer Space-Track GP, compare CelesTrak GP/SupGP, and select newest valid Epoch."""
    if not 1 <= catalog_id <= 999999:
        raise HTTPException(status_code=422, detail="Catalog ID ต้องอยู่ในช่วง 1–999999")
    now = time.time()
    cached = _OMM_SOURCE_CACHE.get(catalog_id)
    if cached and now - cached[0] < _TLE_CACHE_TTL_SECONDS:
        result = dict(cached[1])
        result["cache"] = {"hit": True, "age_seconds": round(now - cached[0]), "ttl_seconds": _TLE_CACHE_TTL_SECONDS}
        return result

    headers = {"User-Agent": "Thai-Reentry-Predictor/5.3 (educational research tool)"}
    specs = [
        ("CelesTrak GP OMM", f"https://celestrak.org/NORAD/elements/gp.php?CATNR={catalog_id}&FORMAT=json",
         "CelesTrak GP JSON/OMM"),
        ("CelesTrak SupGP OMM", f"https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?CATNR={catalog_id}&FORMAT=json",
         "CelesTrak supplemental; same provider, not an independent tracking network"),
    ]

    def fetch_one(spec):
        source, url, relationship = spec
        started = time.monotonic()
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
            item = _parse_omm_payload(raw, catalog_id)
            item.update({"source": source, "source_url": url, "relationship": relationship,
                         "latency_ms": round((time.monotonic() - started) * 1000)})
            return item, {"source": source, "status": "ok", "epoch_utc": item["epoch_utc"],
                          "latency_ms": item["latency_ms"], "relationship": relationship}
        except urllib.error.HTTPError as exc:
            return None, {"source": source, "status": "error", "error": f"HTTP {exc.code}",
                          "latency_ms": round((time.monotonic() - started) * 1000),
                          "relationship": relationship}
        except Exception as exc:
            return None, {"source": source, "status": "error", "error": str(exc)[:240],
                          "latency_ms": round((time.monotonic() - started) * 1000),
                          "relationship": relationship}

    candidates, statuses = [], []

    # Space-Track is the preferred catalog source when credentials are configured.
    started = time.monotonic()
    try:
        item = _fetch_spacetrack_omm(catalog_id)
        item["latency_ms"] = round((time.monotonic() - started) * 1000)
        candidates.append(item)
        statuses.append({"source": "Space-Track GP", "status": "ok", "epoch_utc": item["epoch_utc"],
                         "latency_ms": item["latency_ms"], "relationship": item["relationship"]})
    except urllib.error.HTTPError as exc:
        statuses.append({"source": "Space-Track GP", "status": "error", "error": "HTTP " + str(exc.code),
                         "latency_ms": round((time.monotonic() - started) * 1000), "relationship": "Space-Track GP"})
        if exc.code in (401, 403):
            _SPACETRACK_SESSION.update({"opener": None, "created_at": 0.0})
    except Exception as exc:
        reason = str(exc)
        safe_reason = reason if reason in ("credentials_not_configured", "authentication_failed") else reason[:180]
        statuses.append({"source": "Space-Track GP", "status": "error", "error": safe_reason,
                         "latency_ms": round((time.monotonic() - started) * 1000), "relationship": "Space-Track GP"})

    executor = ThreadPoolExecutor(max_workers=2)
    futures = {executor.submit(fetch_one, spec): spec for spec in specs}
    try:
        for future in as_completed(futures, timeout=22):
            item, status = future.result()
            statuses.append(status)
            if item:
                candidates.append(item)
    except TimeoutError:
        pass
    finally:
        for future, spec in futures.items():
            if not future.done():
                source, _url, relationship = spec
                statuses.append({"source": source, "status": "error",
                                 "error": "timeout after 22 seconds (parallel request deadline)",
                                 "latency_ms": 22000, "relationship": relationship})
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    # Keep diagnostics in a stable GP/SupGP order.
    status_by_source = {item["source"]: item for item in statuses}
    # Preserve Space-Track diagnostics; the previous ordering accidentally
    # dropped this source from the response even though it had been queried.
    statuses = [
        status_by_source.get("Space-Track GP", {
            "source": "Space-Track GP", "status": "error",
            "error": "no response from Space-Track", "latency_ms": None,
            "relationship": "Space-Track GP",
        }),
        *[status_by_source.get(spec[0], {
            "source": spec[0], "status": "error", "error": "no response before deadline",
            "latency_ms": 22000, "relationship": spec[2]
        }) for spec in specs],
    ]

    if not candidates:
        raise HTTPException(status_code=503, detail={
            "message": f"ไม่พบ OMM ที่ผ่านการตรวจสอบสำหรับ Catalog ID {catalog_id}",
            "sources_checked": statuses,
            "hint": "ตรวจสอบสถานะ Space-Track GP และการเชื่อมต่อขาออกจาก Render ไปยัง CelesTrak; หาก Space-Track ล้มเหลวให้ดู error ของแหล่งนั้นโดยตรง",
        })
    candidates.sort(key=lambda x: x["epoch_timestamp"], reverse=True)
    selected = candidates[0]
    result = {
        "catalog_id": catalog_id, "norad_id": catalog_id, "format": "OMM",
        "name": selected["name"], "epoch_utc": selected["epoch_utc"],
        "omm_fields": selected["omm_fields"], "tle_export": selected["tle_export"],
        "source": selected["source"], "source_url": selected["source_url"],
        "selected_source": selected["source"], "selection_method": "newest_valid_epoch",
        "candidates": [{k: v for k, v in item.items() if k != "epoch_timestamp"} for item in candidates],
        "sources_checked": statuses,
        "source_independence_note": "Space-Track GP is the primary catalog feed; CelesTrak GP/SupGP and the community mirror are redistributions/derivatives, not independent tracking networks.",
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "cache": {"hit": False, "ttl_seconds": _TLE_CACHE_TTL_SECONDS},
    }
    _OMM_SOURCE_CACHE[catalog_id] = (now, result)
    return result

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


@app.get("/api/v1/validate/sgp4/{catalog_id}")
def validate_latest_catalog_sgp4(catalog_id: int):
    """Fetch the latest validated OMM record and run the SGP4 consistency test in one request."""
    record = get_latest_omm(catalog_id)
    request = SGP4ValidationRequest(
        omm_fields=record["omm_fields"],
        tle_line1=(record.get("tle_export") or {}).get("line1"),
        tle_line2=(record.get("tle_export") or {}).get("line2"),
    )
    result = validate_sgp4(request)
    result["source"] = record.get("source")
    result["source_url"] = record.get("source_url")
    result["fetched_at_utc"] = record.get("fetched_at_utc")
    result["cache"] = record.get("cache")
    return result


@app.post("/api/v1/validate/sgp4")
def validate_sgp4(req: SGP4ValidationRequest):
    """Validate OMM SGP4 initialization and propagate at fixed offsets; optionally compare its TLE export."""
    try:
        fields = {str(k).upper(): v for k, v in req.omm_fields.items()}
        required = ("NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
                    "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")
        missing = [key for key in required if fields.get(key) in (None, "")]
        if missing:
            raise HTTPException(status_code=422, detail={"message": "OMM fields missing", "fields": missing})

        catalog_id = int(fields["NORAD_CAT_ID"])
        if not 1 <= catalog_id <= 999999:
            raise HTTPException(status_code=422, detail="OMM NORAD_CAT_ID is outside 1–999999")

        epoch = datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00"))
        epoch = as_utc(epoch)
        fields["EPOCH"] = epoch.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")
        fields.setdefault("OBJECT_ID", "0000-000A")
        fields.setdefault("CLASSIFICATION_TYPE", "U")
        fields.setdefault("EPHEMERIS_TYPE", 0)
        fields.setdefault("ELEMENT_SET_NO", 0)
        fields.setdefault("REV_AT_EPOCH", 0)
        fields.setdefault("BSTAR", 0.0)
        fields.setdefault("MEAN_MOTION_DOT", 0.0)
        fields.setdefault("MEAN_MOTION_DDOT", 0.0)

        omm_sat = Satrec()
        initialize_omm(omm_sat, fields)
        if omm_sat.error:
            raise ValueError(SGP4_ERRORS.get(omm_sat.error, "SGP4 OMM initialization error"))

        has_line1 = bool(req.tle_line1 and req.tle_line1.strip())
        has_line2 = bool(req.tle_line2 and req.tle_line2.strip())
        if has_line1 != has_line2:
            raise HTTPException(status_code=422, detail="ต้องส่ง TLE line 1 และ line 2 พร้อมกัน")
        tle_sat = None
        if has_line1 and has_line2:
            if catalog_id > 99999:
                raise HTTPException(status_code=422, detail="TLE comparison รองรับเฉพาะ Catalog ID ไม่เกิน 99999")
            if int(req.tle_line1[2:7]) != catalog_id or int(req.tle_line2[2:7]) != catalog_id:
                raise HTTPException(status_code=422, detail="TLE Catalog ID ไม่ตรงกับ OMM")
            tle_sat = make_satrec(req.tle_line1, req.tle_line2)

        samples = []
        comparison_available = tle_sat is not None
        for offset_minutes in (0, 10, 30, 60):
            sample_time = epoch + timedelta(minutes=offset_minutes)
            jd, fr = julian_parts(sample_time)
            omm_error, omm_pos, omm_vel = omm_sat.sgp4(jd, fr)
            sample = {
                "offset_minutes": offset_minutes,
                "time_utc": sample_time.isoformat(),
                "omm": {"status": "ok", "position_teme_km": list(omm_pos), "velocity_teme_km_s": list(omm_vel)},
            }
            if omm_error:
                sample["omm"] = {
                    "status": "error",
                    "error_code": int(omm_error),
                    "error": SGP4_ERRORS.get(omm_error, "SGP4 propagation error"),
                }
                sample["status"] = "error"
                samples.append(sample)
                continue

            if tle_sat is not None:
                tle_error, tle_pos, tle_vel = tle_sat.sgp4(jd, fr)
                if tle_error:
                    sample["tle_export"] = {
                        "status": "error",
                        "error_code": int(tle_error),
                        "error": SGP4_ERRORS.get(tle_error, "SGP4 propagation error"),
                    }
                    sample["status"] = "comparison_error"
                else:
                    position_delta_km = float(np.linalg.norm(np.asarray(omm_pos) - np.asarray(tle_pos)))
                    velocity_delta_km_s = float(np.linalg.norm(np.asarray(omm_vel) - np.asarray(tle_vel)))
                    sample["tle_export"] = {
                        "status": "ok",
                        "position_teme_km": list(tle_pos),
                        "velocity_teme_km_s": list(tle_vel),
                    }
                    sample["comparison"] = {
                        "position_difference_m": position_delta_km * 1000.0,
                        "velocity_difference_m_s": velocity_delta_km_s * 1000.0,
                        "note": "Difference reflects TLE text-field rounding and SGP4 initialization representation; it is not an independent accuracy assessment.",
                    }
                    sample["status"] = "ok"
            else:
                sample["status"] = "ok"
            samples.append(sample)

        all_omm_ok = all(s["omm"]["status"] == "ok" for s in samples)
        all_comparisons_ok = (not comparison_available) or all(
            s.get("comparison") is not None and s.get("status") == "ok" for s in samples
        )
        return {
            "api_version": APP_VERSION,
            "validation": "passed" if all_omm_ok and all_comparisons_ok else "failed",
            "catalog_id": catalog_id,
            "epoch_utc": epoch.isoformat(),
            "source_note": "Validation uses the supplied OMM fields. It does not independently verify the catalog observations or orbit accuracy.",
            "sgp4_initialization": "passed",
            "propagation_offsets_minutes": [0, 10, 30, 60],
            "omm_propagation": "passed" if all_omm_ok else "failed",
            "tle_export_comparison": "passed" if comparison_available and all_comparisons_ok else (
                "not_requested" if not comparison_available else "failed"
            ),
            "samples": samples,
            "limitations": [
                "TEME position and velocity are SGP4 outputs, not Earth-fixed coordinates.",
                "OMM and exported TLE originate from the same catalog record; agreement is a format/implementation consistency check, not independent validation.",
                "This test does not validate long-term orbit prediction, atmospheric drag accuracy, or re-entry location.",
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"SGP4 validation failed: {exc}") from exc



@app.post("/api/v1/validate/long-term")
def validate_long_term(req: LongTermValidationRequest):
    """Stress-test SGP4 propagation at longer offsets; not an independent accuracy validation."""
    try:
        fields = {str(k).upper(): v for k, v in req.omm_fields.items()}
        required = ("NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
                    "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")
        missing = [key for key in required if fields.get(key) in (None, "")]
        if missing:
            raise HTTPException(status_code=422, detail={"message": "OMM fields missing", "fields": missing})
        catalog_id = int(fields["NORAD_CAT_ID"])
        if not 1 <= catalog_id <= 999999:
            raise HTTPException(status_code=422, detail="OMM NORAD_CAT_ID is outside 1–999999")
        epoch = as_utc(datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00")))
        fields["EPOCH"] = epoch.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")
        fields.setdefault("OBJECT_ID", "0000-000A")
        fields.setdefault("CLASSIFICATION_TYPE", "U")
        fields.setdefault("EPHEMERIS_TYPE", 0)
        fields.setdefault("ELEMENT_SET_NO", 0)
        fields.setdefault("REV_AT_EPOCH", 0)
        fields.setdefault("BSTAR", 0.0)
        fields.setdefault("MEAN_MOTION_DOT", 0.0)
        fields.setdefault("MEAN_MOTION_DDOT", 0.0)
        sat = Satrec()
        initialize_omm(sat, fields)
        if sat.error:
            raise ValueError(SGP4_ERRORS.get(sat.error, "SGP4 OMM initialization error"))
        offsets = sorted(set(float(x) for x in req.offsets_hours))
        if any(not math.isfinite(x) or x < 0 or x > 168 for x in offsets):
            raise HTTPException(status_code=422, detail="offsets_hours ต้องอยู่ระหว่าง 0 ถึง 168 ชั่วโมง")
        samples = []
        for offset in offsets:
            when = epoch + timedelta(hours=offset)
            jd, fr = julian_parts(when)
            error, pos, vel = sat.sgp4(jd, fr)
            item = {"offset_hours": offset, "time_utc": when.isoformat()}
            if error:
                item.update({"status": "error", "error_code": int(error),
                             "error": SGP4_ERRORS.get(error, "SGP4 propagation error")})
            else:
                item.update({"status": "ok", "position_teme_km": list(pos),
                             "velocity_teme_km_s": list(vel),
                             "geocentric_radius_km": float(np.linalg.norm(pos))})
            samples.append(item)
        all_ok = all(x["status"] == "ok" for x in samples)
        return {
            "api_version": APP_VERSION, "catalog_id": catalog_id, "epoch_utc": epoch.isoformat(),
            "validation": "passed" if all_ok else "failed",
            "test_type": "long_term_sgp4_numerical_propagation",
            "samples": samples,
            "interpretation": "การคำนวณสำเร็จแสดงเพียงว่า SGP4 สามารถ propagate ถึงเวลาที่ทดสอบได้ ไม่ได้ยืนยันว่าตำแหน่งจริงแม่นยำในระยะยาว",
            "limitations": [
                "ทุก sample เป็นการ propagate จาก OMM ชุดเดียวกัน ไม่ใช่การเทียบกับการสังเกตอิสระ",
                "ความคลาดเคลื่อนของวงโคจรและความไม่แน่นอนของ TLE/OMM จะเพิ่มตามเวลา",
                "ผล TEME ไม่ใช่พิกัดภาคพื้นดิน และ geocentric_radius_km ไม่ใช่ geodetic altitude",
                "ไม่ใช้ endpoint นี้ทำนายจุดตกหรือรับรองความแม่นยำของ re-entry",
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Long-term SGP4 validation failed: {exc}") from exc


@app.post("/api/v1/validate/epoch-comparison")
def validate_epoch_comparison(req: EpochComparisonValidationRequest):
    """Compare two differently time-tagged GP/OMM solutions at common future times.

    This measures disagreement between catalog solutions, not error against truth.
    """
    def prepare_omm(raw_fields: dict, label: str):
        fields = {str(k).upper(): v for k, v in raw_fields.items()}
        required = ("NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
                    "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")
        missing = [key for key in required if fields.get(key) in (None, "")]
        if missing:
            raise HTTPException(status_code=422, detail={
                "message": f"{label}: OMM fields missing", "fields": missing
            })
        try:
            catalog_id = int(fields["NORAD_CAT_ID"])
            epoch = as_utc(datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00")))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"{label}: invalid NORAD_CAT_ID or EPOCH: {exc}") from exc
        if not 1 <= catalog_id <= 999999:
            raise HTTPException(status_code=422, detail=f"{label}: OMM NORAD_CAT_ID is outside 1–999999")
        ref_frame = str(fields.get("REF_FRAME", "TEME")).upper()
        theory = str(fields.get("MEAN_ELEMENT_THEORY", "SGP4")).upper()
        if ref_frame != "TEME" or theory != "SGP4":
            raise HTTPException(status_code=422, detail={
                "message": f"{label}: SGP4 comparison requires REF_FRAME=TEME and MEAN_ELEMENT_THEORY=SGP4",
                "ref_frame": ref_frame, "mean_element_theory": theory
            })
        fields["EPOCH"] = epoch.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.%f")
        fields.setdefault("OBJECT_ID", "0000-000A")
        fields.setdefault("CLASSIFICATION_TYPE", "U")
        fields.setdefault("EPHEMERIS_TYPE", 0)
        fields.setdefault("ELEMENT_SET_NO", 0)
        fields.setdefault("REV_AT_EPOCH", 0)
        fields.setdefault("BSTAR", 0.0)
        fields.setdefault("MEAN_MOTION_DOT", 0.0)
        fields.setdefault("MEAN_MOTION_DDOT", 0.0)
        sat = Satrec()
        try:
            initialize_omm(sat, fields)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"{label}: OMM initialization failed: {exc}") from exc
        if sat.error:
            raise HTTPException(status_code=422, detail={
                "message": f"{label}: SGP4 initialization error",
                "error": SGP4_ERRORS.get(sat.error, "SGP4 initialization error")
            })
        return {
            "catalog_id": catalog_id, "epoch": epoch, "sat": sat, "fields": fields
        }

    older = prepare_omm(req.older_omm_fields, "older_omm_fields")
    newer = prepare_omm(req.newer_omm_fields, "newer_omm_fields")
    if older["catalog_id"] != newer["catalog_id"]:
        raise HTTPException(status_code=422, detail="older_omm_fields และ newer_omm_fields ต้องเป็น NORAD_CAT_ID เดียวกัน")
    if older["epoch"] >= newer["epoch"]:
        raise HTTPException(status_code=422, detail="older_omm_fields ต้องมี EPOCH เก่ากว่า newer_omm_fields")
    epoch_gap_hours = (newer["epoch"] - older["epoch"]).total_seconds() / 3600.0
    if epoch_gap_hours > 720.0:
        raise HTTPException(status_code=422, detail="ระยะห่างระหว่าง OMM epochs ต้องไม่เกิน 30 วัน")

    offsets = sorted(set(float(x) for x in req.offsets_hours))
    if any(not math.isfinite(x) or x < 0 or x > 168 for x in offsets):
        raise HTTPException(status_code=422, detail="offsets_hours ต้องอยู่ระหว่าง 0 ถึง 168 ชั่วโมง")

    samples = []
    position_differences = []
    velocity_differences = []
    for offset in offsets:
        when = newer["epoch"] + timedelta(hours=offset)
        jd, fr = julian_parts(when)
        old_error, old_pos, old_vel = older["sat"].sgp4(jd, fr)
        new_error, new_pos, new_vel = newer["sat"].sgp4(jd, fr)
        item = {"offset_hours_from_newer_epoch": offset, "time_utc": when.isoformat()}
        if old_error or new_error:
            item.update({
                "status": "error",
                "older_error": SGP4_ERRORS.get(old_error) if old_error else None,
                "newer_error": SGP4_ERRORS.get(new_error) if new_error else None,
            })
        else:
            pos_delta_km = float(np.linalg.norm(np.asarray(old_pos) - np.asarray(new_pos)))
            vel_delta_km_s = float(np.linalg.norm(np.asarray(old_vel) - np.asarray(new_vel)))
            position_differences.append(pos_delta_km)
            velocity_differences.append(vel_delta_km_s)
            item.update({
                "status": "ok",
                "older_position_teme_km": list(old_pos),
                "newer_position_teme_km": list(new_pos),
                "position_disagreement_km": pos_delta_km,
                "velocity_disagreement_m_s": vel_delta_km_s * 1000.0,
            })
        samples.append(item)

    if not position_differences:
        raise HTTPException(status_code=422, detail="ไม่มี sample ที่คำนวณเปรียบเทียบได้")
    rms_position = math.sqrt(sum(x * x for x in position_differences) / len(position_differences))
    rms_velocity = math.sqrt(sum(x * x for x in velocity_differences) / len(velocity_differences))
    all_ok = all(x["status"] == "ok" for x in samples)
    return {
        "api_version": APP_VERSION,
        "catalog_id": older["catalog_id"],
        "test_type": "two_epoch_omm_solution_disagreement",
        "comparison_status": "completed" if all_ok else "partial",
        "older_epoch_utc": older["epoch"].isoformat(),
        "newer_epoch_utc": newer["epoch"].isoformat(),
        "epoch_separation_hours": epoch_gap_hours,
        "comparison_window": "Both OMM solutions propagated to the same times, beginning at the newer OMM epoch.",
        "sample_count": len(samples),
        "successful_sample_count": len(position_differences),
        "position_disagreement_summary_km": {
            "rms": rms_position,
            "maximum": max(position_differences),
            "minimum": min(position_differences),
        },
        "velocity_disagreement_summary_m_s": {
            "rms": rms_velocity * 1000.0,
            "maximum": max(velocity_differences) * 1000.0,
        },
        "samples": samples,
        "interpretation": "ค่าที่รายงานคือความไม่ตรงกันระหว่างคำตอบจาก OMM สองชุด ไม่ใช่ความคลาดเคลื่อนจากตำแหน่งจริงหรือ truth ephemeris",
        "provenance": {
            "older_originator": older["fields"].get("ORIGINATOR"),
            "newer_originator": newer["fields"].get("ORIGINATOR"),
            "older_creation_date": older["fields"].get("CREATION_DATE"),
            "newer_creation_date": newer["fields"].get("CREATION_DATE"),
            "older_gp_id": older["fields"].get("GP_ID"),
            "newer_gp_id": newer["fields"].get("GP_ID"),
        },
        "limitations": [
            "ต้องเป็น OMM ของวัตถุเดียวกันและใช้ TEME/SGP4",
            "แม้ EPOCH ต่างกัน OMM ทั้งสองชุดอาจมาจาก catalog หรือเครือข่ายติดตามชุดเดียวกัน",
            "ความแตกต่างระหว่างสองคำตอบไม่ใช่ absolute orbit error เพราะไม่มี truth ephemeris หรือข้อมูลสังเกตอิสระ",
            "การ propagate ทั้งสองชุดใช้แบบจำลอง SGP4; ผลอาจได้รับผลจากความไม่แน่นอนของ BSTAR และแรงรบกวน",
            "ผลนี้ไม่ใช่การรับรองความแม่นยำของการทำนาย re-entry",
        ],
    }


@app.post("/api/v1/validate/omm-quality")
def validate_omm_quality(req: OMMQualityRequest):
    """Screen one OMM record for metadata completeness, numerical plausibility and epoch freshness."""
    fields = {str(k).upper(): v for k, v in req.omm_fields.items()}
    required = ("NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
                "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")
    missing = [key for key in required if fields.get(key) in (None, "")]
    if missing:
        raise HTTPException(status_code=422, detail={"message": "OMM fields missing", "fields": missing})
    try:
        catalog_id = int(fields["NORAD_CAT_ID"])
        epoch = as_utc(datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00")))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid NORAD_CAT_ID or EPOCH: {exc}") from exc
    if not 1 <= catalog_id <= 999999:
        raise HTTPException(status_code=422, detail="NORAD_CAT_ID ต้องอยู่ในช่วง 1–999999")

    numeric_fields = ("MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
                      "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR",
                      "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT")
    numeric = {}
    for key in numeric_fields:
        if fields.get(key) in (None, ""):
            if key in ("BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT"):
                continue
            raise HTTPException(status_code=422, detail=f"Missing numeric OMM field: {key}")
        try:
            numeric[key] = float(fields[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid numeric OMM field: {key}") from exc
        if not math.isfinite(numeric[key]):
            raise HTTPException(status_code=422, detail=f"Non-finite OMM field: {key}")

    checks = {
        "mean_motion": 0.0 < numeric["MEAN_MOTION"] < 20.0,
        "eccentricity": 0.0 <= numeric["ECCENTRICITY"] < 1.0,
        "inclination": 0.0 <= numeric["INCLINATION"] <= 180.0,
        "angular_elements_finite": all(math.isfinite(numeric[k]) for k in
            ("RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY")),
    }
    if not all(checks.values()):
        raise HTTPException(status_code=422, detail={"message": "OMM orbital elements failed plausibility checks", "checks": checks})

    reference_time = as_utc(req.reference_time_utc or datetime.now(timezone.utc))
    age_hours = (reference_time - epoch).total_seconds() / 3600.0
    warnings = []
    if age_hours < -1.0:
        warnings.append("OMM EPOCH is more than one hour in the future relative to reference_time_utc")
    elif age_hours > req.stale_after_hours:
        warnings.append(f"OMM is older than the configured freshness threshold ({req.stale_after_hours:g} hours)")
    if age_hours > 72.0:
        warnings.append("OMM EPOCH is older than 72 hours")
    provenance = {
        "originator": fields.get("ORIGINATOR"),
        "creation_date": fields.get("CREATION_DATE"),
        "gp_id": fields.get("GP_ID"),
        "object_id": fields.get("OBJECT_ID"),
        "classification_type": fields.get("CLASSIFICATION_TYPE"),
    }
    missing_provenance = [key for key, value in provenance.items() if value in (None, "")]
    if missing_provenance:
        warnings.append("Some provenance metadata fields are absent; traceability is limited")

    return {
        "api_version": APP_VERSION,
        "catalog_id": catalog_id,
        "object_name": fields.get("OBJECT_NAME"),
        "epoch_utc": epoch.isoformat(),
        "reference_time_utc": reference_time.isoformat(),
        "epoch_age_hours": round(age_hours, 3),
        "freshness_threshold_hours": req.stale_after_hours,
        "quality_screen": "review_required" if warnings else "passed",
        "checks": checks,
        "numeric_elements": numeric,
        "provenance": provenance,
        "missing_provenance_fields": missing_provenance,
        "warnings": warnings,
        "interpretation": "เป็นการคัดกรองความสมบูรณ์ ความสมเหตุสมผลเชิงตัวเลข และอายุข้อมูลเท่านั้น ไม่ใช่การยืนยันความแม่นยำของวงโคจร",
        "limitations": [
            "BSTAR ไม่มีเกณฑ์ผ่าน/ไม่ผ่านแบบสากลใน endpoint นี้; ค่าขึ้นกับวัตถุและการ fit ของ catalog",
            "ความสดใหม่ของ EPOCH ไม่เท่ากับความแม่นยำของวงโคจร",
            "การมี provenance ไม่ได้พิสูจน์ว่าแหล่งข้อมูลเป็นอิสระจากเครือข่ายอื่น",
            "ไม่มี truth ephemeris หรือ observation residuals สำหรับคำนวณ absolute orbit error",
        ],
    }


@app.post("/api/v1/validate/historical-series")
def validate_historical_omm_series(req: HistoricalOMMSeriesRequest):
    """Compare a chronological sequence of saved OMM snapshots using adjacent-epoch disagreements."""
    prepared = []
    for index, raw in enumerate(req.omm_records):
        fields = {str(k).upper(): v for k, v in raw.items()}
        try:
            catalog_id = int(fields.get("NORAD_CAT_ID", 0))
            epoch = as_utc(datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00")))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"omm_records[{index}] requires valid NORAD_CAT_ID and EPOCH: {exc}") from exc
        if not 1 <= catalog_id <= 999999:
            raise HTTPException(status_code=422, detail=f"omm_records[{index}] has invalid NORAD_CAT_ID")
        prepared.append({"catalog_id": catalog_id, "epoch": epoch, "fields": raw})

    catalog_ids = {item["catalog_id"] for item in prepared}
    if len(catalog_ids) != 1:
        raise HTTPException(status_code=422, detail="OMM records ต้องเป็นวัตถุ NORAD_CAT_ID เดียวกัน")
    prepared.sort(key=lambda item: item["epoch"])
    epochs = [item["epoch"] for item in prepared]
    if len(set(epochs)) != len(epochs):
        raise HTTPException(status_code=422, detail="OMM records ต้องมี EPOCH ไม่ซ้ำกัน")

    comparisons = []
    for index in range(len(prepared) - 1):
        older, newer = prepared[index], prepared[index + 1]
        comparison = validate_epoch_comparison(EpochComparisonValidationRequest(
            older_omm_fields=older["fields"],
            newer_omm_fields=newer["fields"],
            offsets_hours=req.offsets_hours,
        ))
        comparisons.append({
            "pair_index": index + 1,
            "older_epoch_utc": comparison["older_epoch_utc"],
            "newer_epoch_utc": comparison["newer_epoch_utc"],
            "epoch_separation_hours": comparison["epoch_separation_hours"],
            "position_disagreement_summary_km": comparison["position_disagreement_summary_km"],
            "velocity_disagreement_summary_m_s": comparison["velocity_disagreement_summary_m_s"],
            "samples": comparison["samples"],
            "provenance": comparison["provenance"],
            "comparison_status": comparison["comparison_status"],
        })

    return {
        "api_version": APP_VERSION,
        "catalog_id": prepared[0]["catalog_id"],
        "test_type": "historical_adjacent_omm_solution_disagreement",
        "record_count": len(prepared),
        "comparison_count": len(comparisons),
        "records_chronological": [
            {"epoch_utc": item["epoch"].isoformat(),
             "originator": {str(k).upper(): v for k, v in item["fields"].items()}.get("ORIGINATOR"),
             "creation_date": {str(k).upper(): v for k, v in item["fields"].items()}.get("CREATION_DATE"),
             "gp_id": {str(k).upper(): v for k, v in item["fields"].items()}.get("GP_ID")}
            for item in prepared
        ],
        "adjacent_comparisons": comparisons,
        "interpretation": "แต่ละคู่เป็นความไม่ตรงกันของ OMM สอง Epoch ที่ propagate ไปยังเวลาเดียวกัน ไม่ใช่ absolute orbit error; ใช้ดูแนวโน้มการเปลี่ยนแปลงของ catalog solution เท่านั้น",
        "limitations": [
            "ต้องเก็บ OMM snapshots ไว้เองจากการเรียก API ในเวลาต่างกัน; endpoint นี้ไม่สร้างประวัติย้อนหลังจาก Space-Track ให้อัตโนมัติ",
            "OMM หลาย Epoch อาจมาจากเครือข่ายติดตามเดียวกันและไม่เป็นข้อมูลอิสระ",
            "การเปรียบเทียบแต่ละคู่ใช้ SGP4/TEME และจำกัดช่วงห่างของ Epoch ตาม endpoint epoch-comparison",
            "ผล disagreement ไม่ใช่ covariance, confidence interval หรือการรับรองความแม่นยำของ re-entry",
        ],
    }


@app.post("/api/v1/validate/orbit-reliability")
def validate_orbit_reliability(req: OrbitReliabilityRequest):
    """Build a descriptive reliability evidence report from saved OMM snapshots.

    This is an empirical catalog-solution disagreement indicator, not covariance,
    a probability, or an estimate of absolute orbit error.
    """
    history = validate_historical_omm_series(HistoricalOMMSeriesRequest(
        omm_records=req.omm_records,
        offsets_hours=req.offsets_hours,
    ))
    latest_fields = {str(k).upper(): v for k, v in req.omm_records[0].items()}
    for raw in req.omm_records[1:]:
        fields = {str(k).upper(): v for k, v in raw.items()}
        try:
            if as_utc(datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00"))) > as_utc(
                datetime.fromisoformat(str(latest_fields["EPOCH"]).replace("Z", "+00:00"))
            ):
                latest_fields = fields
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid OMM EPOCH: {exc}") from exc

    quality = validate_omm_quality(OMMQualityRequest(
        omm_fields=latest_fields,
        stale_after_hours=req.stale_after_hours,
    ))

    by_horizon = {}
    for offset in sorted(set(float(x) for x in req.offsets_hours)):
        if not math.isfinite(offset) or offset < 0 or offset > 168:
            raise HTTPException(status_code=422, detail="offsets_hours ต้องอยู่ระหว่าง 0 ถึง 168 ชั่วโมง")
        positions, velocities = [], []
        for comparison in history["adjacent_comparisons"]:
            sample = next((x for x in comparison["samples"]
                           if x.get("status") == "ok"
                           and abs(float(x["offset_hours_from_newer_epoch"]) - offset) < 1e-9), None)
            if sample:
                positions.append(float(sample["position_disagreement_km"]))
                velocities.append(float(sample["velocity_disagreement_m_s"]))
        by_horizon[str(offset)] = {
            "offset_hours": offset,
            "available_pair_count": len(positions),
            "position_disagreement_rms_across_pairs_km": (
                math.sqrt(sum(x*x for x in positions) / len(positions)) if positions else None
            ),
            "position_disagreement_max_across_pairs_km": max(positions) if positions else None,
            "velocity_disagreement_rms_across_pairs_m_s": (
                math.sqrt(sum(x*x for x in velocities) / len(velocities)) if velocities else None
            ),
            "velocity_disagreement_max_across_pairs_m_s": max(velocities) if velocities else None,
        }

    position_rms_values = [
        item["position_disagreement_rms_across_pairs_km"]
        for item in by_horizon.values()
        if item["position_disagreement_rms_across_pairs_km"] is not None
    ]
    if len(position_rms_values) >= 2:
        if position_rms_values[-1] > position_rms_values[0] * 1.05:
            divergence_pattern = "increasing_across_requested_horizons"
        elif position_rms_values[-1] < position_rms_values[0] * 0.95:
            divergence_pattern = "decreasing_across_requested_horizons"
        else:
            divergence_pattern = "approximately_flat_across_requested_horizons"
    else:
        divergence_pattern = "insufficient_comparable_horizons"

    metadata_complete = len(quality["missing_provenance_fields"]) == 0
    fresh = quality["epoch_age_hours"] <= req.stale_after_hours and quality["epoch_age_hours"] >= -1.0
    evidence_gaps = [
        "ไม่มี covariance matrix จากผู้ให้บริการวงโคจร",
        "ไม่มี truth ephemeris หรือ residuals จากการติดตามอิสระ",
        "ไม่สามารถยืนยันความเป็นอิสระของ OMM snapshots จาก provenance เพียงอย่างเดียว",
        "ยังไม่ได้ประเมินพารามิเตอร์กายภาพและความไม่แน่นอนของวัตถุสำหรับ re-entry",
    ]
    return {
        "api_version": APP_VERSION,
        "catalog_id": history["catalog_id"],
        "assessment_type": "descriptive_orbit_prediction_reliability_evidence",
        "evidence_status": "limited" if fresh and metadata_complete else "review_required",
        "latest_omm_quality_screen": quality,
        "snapshot_count": history["record_count"],
        "adjacent_comparison_count": history["comparison_count"],
        "divergence_pattern": divergence_pattern,
        "empirical_disagreement_by_horizon": list(by_horizon.values()),
        "reentry_readiness": "not_assessed_insufficient_uncertainty_and_independent_tracking_evidence",
        "evidence_gaps": evidence_gaps,
        "interpretation": (
            "รายงานนี้สรุปความไม่ตรงกันเชิงประจักษ์ระหว่าง OMM snapshots ที่ propagate ด้วย SGP4 "
            "ใช้เป็นสัญญาณเตือนและติดตามแนวโน้มเท่านั้น ไม่ใช่ขอบเขตความเชื่อมั่นหรือค่าความคลาดเคลื่อนจริง"
        ),
        "limitations": [
            "ค่ามากสุด/ RMS เป็นสถิติของความต่างระหว่าง catalog solutions ในชุดข้อมูลที่ส่งมา ไม่ใช่ uncertainty covariance",
            "แนวโน้มขึ้นกับจำนวน snapshots, ระยะห่าง epochs และ horizons ที่ผู้ใช้เลือก",
            "ห้ามตีความเป็นเปอร์เซ็นต์ความแม่นยำหรือความน่าจะเป็นที่วัตถุจะตก ณ ตำแหน่งใด",
            "การประเมิน re-entry ต้องใช้แบบจำลองพลวัต/บรรยากาศและข้อมูลวัตถุเพิ่มเติม พร้อม validation กับข้อมูลอิสระ",
        ],
    }


@app.post("/api/v1/validate/epoch-comparison/latest/{catalog_id}")
def validate_epoch_comparison_with_latest(catalog_id: int, req: LatestEpochComparisonRequest):
    """Compare a saved older OMM snapshot against the latest validated catalog OMM."""
    if not 1 <= catalog_id <= 999999:
        raise HTTPException(status_code=422, detail="Catalog ID ต้องอยู่ในช่วง 1–999999")
    try:
        older_id = int(req.older_omm_fields.get("NORAD_CAT_ID", req.older_omm_fields.get("norad_cat_id", 0)))
    except Exception:
        older_id = 0
    if older_id != catalog_id:
        raise HTTPException(status_code=422, detail="NORAD_CAT_ID ใน older_omm_fields ต้องตรงกับ catalog_id ใน URL")
    latest_record = get_latest_omm(catalog_id)
    result = validate_epoch_comparison(EpochComparisonValidationRequest(
        older_omm_fields=req.older_omm_fields,
        newer_omm_fields=latest_record["omm_fields"],
        offsets_hours=req.offsets_hours,
    ))
    result["newer_source"] = latest_record.get("source")
    result["newer_source_url"] = latest_record.get("source_url")
    result["newer_fetched_at_utc"] = latest_record.get("fetched_at_utc")
    result["newer_cache"] = latest_record.get("cache")
    return result


@app.post("/api/v1/validate/batch")
def validate_batch(req: BatchValidationRequest):
    """Run the existing OMM/TLE consistency validation for up to eight catalog IDs."""
    if len(set(req.catalog_ids)) != len(req.catalog_ids):
        raise HTTPException(status_code=422, detail="catalog_ids ต้องไม่มีรหัสซ้ำ")
    results = []
    for catalog_id in req.catalog_ids:
        if not 1 <= catalog_id <= 999999:
            results.append({"catalog_id": catalog_id, "validation": "rejected",
                            "error": "Catalog ID ต้องอยู่ในช่วง 1–999999"})
            continue
        try:
            record = get_latest_omm(catalog_id)
            check = validate_sgp4(SGP4ValidationRequest(
                omm_fields=record["omm_fields"],
                tle_line1=(record.get("tle_export") or {}).get("line1"),
                tle_line2=(record.get("tle_export") or {}).get("line2"),
            ))
            results.append({
                "catalog_id": catalog_id, "name": record.get("name"),
                "source": record.get("source"), "epoch_utc": record.get("epoch_utc"),
                "validation": check.get("validation"), "sgp4_initialization": check.get("sgp4_initialization"),
                "omm_propagation": check.get("omm_propagation"),
                "tle_export_comparison": check.get("tle_export_comparison"),
                "samples": check.get("samples"),
                "limitations": check.get("limitations"),
            })
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            results.append({"catalog_id": catalog_id, "validation": "unavailable", "error": str(detail)[:300]})
    passed = sum(1 for x in results if x.get("validation") == "passed")
    return {
        "api_version": APP_VERSION, "requested_count": len(req.catalog_ids),
        "passed_count": passed, "failed_or_unavailable_count": len(results) - passed,
        "results": results,
        "source_note": "Batch นี้ทดสอบความสอดคล้องของ OMM/TLE จาก catalog feed ไม่ใช่การเทียบกับเครือข่ายติดตามอิสระ",
    }



@app.post("/api/v1/validate/atmosphere-sensitivity")
def validate_atmosphere_sensitivity(req: AtmosphereSensitivityRequest):
    """Compare NRLMSISE-00 density under explicitly defined quiet, baseline and active space-weather scenarios."""
    altitudes = sorted(set(float(x) for x in req.altitudes_km))
    if any(not math.isfinite(x) or x < 80 or x > 1000 for x in altitudes):
        raise HTTPException(status_code=422, detail="altitudes_km ต้องอยู่ระหว่าง 80 ถึง 1000 km")
    scenarios = {
        "quiet": {"f107": 70.0, "f107a": 70.0, "ap": 4.0},
        "baseline": {"f107": req.baseline_f107_sfu, "f107a": req.baseline_f107a_sfu,
                     "ap": req.baseline_ap},
        "active": {"f107": 250.0, "f107a": 200.0, "ap": 100.0},
    }
    results = {}
    try:
        for name, values in scenarios.items():
            rows = []
            for altitude in altitudes:
                model_req = AtmosphereRequest(
                    time_utc=req.time_utc, latitude_deg=req.latitude_deg,
                    longitude_deg=req.longitude_deg, altitude_km=altitude,
                    f107_sfu=values["f107"], f107a_sfu=values["f107a"], ap=values["ap"],
                )
                rho, temp = msis_density(as_utc(req.time_utc), req.latitude_deg,
                                          req.longitude_deg, altitude, model_req)
                rows.append({"altitude_km": altitude, "density_kg_m3": rho, "temperature_K": temp})
            results[name] = {"inputs": values, "profile": rows}
        baseline = {row["altitude_km"]: row["density_kg_m3"] for row in results["baseline"]["profile"]}
        for name in ("quiet", "active"):
            for row in results[name]["profile"]:
                ref = baseline[row["altitude_km"]]
                row["density_ratio_vs_baseline"] = row["density_kg_m3"] / ref if ref > 0 else None
        return {
            "api_version": APP_VERSION, "model": "NRLMSISE-00",
            "time_utc": as_utc(req.time_utc).isoformat(),
            "location": {"latitude_deg": req.latitude_deg, "longitude_deg": req.longitude_deg},
            "scenarios": results,
            "interpretation": "เปรียบเทียบความไวของความหนาแน่นบรรยากาศต่อชุดค่า F10.7/F10.7a/Ap ที่กำหนด ไม่ใช่การจำลองการกลับเข้าสู่บรรยากาศทั้งวิถี",
            "limitations": [
                "quiet และ active เป็นกรณีสมมติ ไม่ใช่ข้อมูล space weather ที่วัดได้ ณ เวลานั้น",
                "ผลขึ้นกับตำแหน่ง เวลา และความสูงที่ส่งเข้ามา",
                "ยังไม่รวมลมบรรยากาศ การแตกตัว การระเหย หรือการเปลี่ยนพื้นที่หน้าตัดของวัตถุ",
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Atmosphere sensitivity calculation failed: {exc}") from exc




@app.post("/api/v1/v6/orbit/decay-trend")
def v6_orbital_decay_trend(req: OrbitalDecayTrendRequest):
    """Descriptive trend of mean-motion-derived Keplerian semi-major-axis proxies."""
    prepared = []
    for index, raw in enumerate(req.omm_records):
        fields = {str(k).upper(): v for k, v in raw.items()}
        try:
            catalog_id = int(fields["NORAD_CAT_ID"])
            epoch = as_utc(datetime.fromisoformat(str(fields["EPOCH"]).replace("Z", "+00:00")))
            mean_motion = float(fields["MEAN_MOTION"])
            bstar = float(fields.get("BSTAR", 0.0) or 0.0)
            if not math.isfinite(mean_motion) or not (0.0 < mean_motion < 20.0):
                raise ValueError("MEAN_MOTION ต้องเป็น finite value ในช่วง 0–20 rev/day")
            if not math.isfinite(bstar):
                raise ValueError("BSTAR ต้องเป็น finite number")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"OMM record {index + 1} ไม่ถูกต้อง: {exc}") from exc
        n_rad_s = mean_motion * 2.0 * math.pi / 86400.0
        a_m = (MU / (n_rad_s * n_rad_s)) ** (1.0 / 3.0)
        prepared.append({
            "catalog_id": catalog_id, "epoch": epoch,
            "mean_motion_rev_day": mean_motion, "bstar": bstar,
            "period_minutes": 1440.0 / mean_motion,
            "keplerian_semimajor_axis_proxy_km": a_m / 1000.0,
            "mean_motion_altitude_proxy_km": (a_m - 6371008.8) / 1000.0,
        })
    if len({x["catalog_id"] for x in prepared}) != 1:
        raise HTTPException(status_code=422, detail="OMM ทุกชุดต้องเป็น NORAD_CAT_ID เดียวกัน")
    if len({x["epoch"] for x in prepared}) != len(prepared):
        raise HTTPException(status_code=422, detail="OMM EPOCH ต้องไม่ซ้ำกัน")
    prepared.sort(key=lambda x: x["epoch"])
    first, last = prepared[0], prepared[-1]
    days = (last["epoch"] - first["epoch"]).total_seconds() / 86400.0
    if days <= 0:
        raise HTTPException(status_code=422, detail="ต้องมี Epoch ต่างกันอย่างน้อยสองเวลา")
    delta_a = last["keplerian_semimajor_axis_proxy_km"] - first["keplerian_semimajor_axis_proxy_km"]
    delta_n = last["mean_motion_rev_day"] - first["mean_motion_rev_day"]
    trend = ("decaying_semimajor_axis_proxy" if delta_a < 0 and delta_n > 0 else
             "increasing_semimajor_axis_proxy" if delta_a > 0 and delta_n < 0 else
             "no_clear_monotonic_decay_signal")
    return {
        "api_version": APP_VERSION, "catalog_id": first["catalog_id"],
        "analysis_type": "mean_motion_derived_orbital_decay_trend",
        "snapshot_count": len(prepared), "epoch_span_days": days, "trend": trend,
        "delta_mean_motion_rev_day": delta_n, "delta_semimajor_axis_proxy_km": delta_a,
        "semimajor_axis_proxy_rate_km_day": delta_a / days,
        "records_chronological": [{**x, "epoch_utc": x["epoch"].isoformat()} for x in prepared],
        "interpretation": "ใช้ Kepler two-body relation แปลง Mean Motion เป็นกึ่งแกนเอกโดยประมาณ ไม่ใช่ความสูง geodetic หรือการ fit วงโคจรเต็มรูปแบบ",
        "limitations": [
            "Mean Motion ของ GP/SGP4 เป็น mean element; a ที่คำนวณได้เป็น proxy ไม่ใช่ osculating state",
            "ไม่รวม covariance, maneuver detection, atmospheric density history หรือ independent tracking residuals",
            "BSTAR เป็นพารามิเตอร์ fit ของ SGP4 ไม่ใช่ ballistic coefficient ทางกายภาพโดยตรง",
            "snapshots จำนวนน้อยไม่เพียงพอสำหรับคำนวณวัน re-entry",
        ],
    }


@app.post("/api/v1/v6/reentry/scenario")
def v6_reentry_scenario(req: V6ReentryScenarioRequest):
    """Scenario-only variable-mass entry model with a lumped effective ablation proxy."""
    base = SimulationRequest(
        tle_line1=req.tle_line1, tle_line2=req.tle_line2, omm_fields=req.omm_fields,
        start_time_utc=req.start_time_utc, entry_time_utc=req.entry_time_utc,
        entry_altitude_km=req.entry_altitude_km, max_search_hours=req.max_search_hours,
        max_simulation_seconds=req.max_simulation_seconds, sample_interval_s=req.sample_interval_s,
        mass_kg=req.mass_kg, reference_area_m2=req.reference_area_m2,
        drag_coefficient=req.drag_coefficient, nose_radius_m=req.nose_radius_m,
        f107_sfu=req.f107_sfu, f107a_sfu=req.f107a_sfu, ap=req.ap,
    )
    try:
        sat = make_satrec_from_request(base)
        entry_time, r0, v0, entry_geo, entry_source = find_entry_state(base, sat)
        y, mass = np.concatenate((r0, v0)), float(req.mass_kg)
        initial_mass, elapsed, current_time = mass, 0.0, entry_time
        points, last_saved = [], -1e9
        status, impact, steps = "time_limit", False, 0
        max_steps = 30000

        def mdot(diag):
            power = (req.ablation_efficiency * diag["convective_heat_flux_W_m2"]
                     * req.reference_area_m2 * req.heated_area_factor)
            return -power / req.heat_of_ablation_j_kg

        def save(diag, t, when, m):
            points.append({
                "elapsed_s": round(t, 3), "time_utc": when.isoformat(),
                "mass_kg": round(max(0.0, m), 6),
                "mass_remaining_fraction": round(max(0.0, m) / initial_mass, 8),
                "mass_loss_kg": round(initial_mass - max(0.0, m), 6),
                **{k: round(v, 8) if isinstance(v, float) else v
                   for k, v in diag.items() if k != "drag_acceleration_m_s2"},
            })

        base.mass_kg = mass
        _, last_diag = acceleration(current_time, y, base)
        while elapsed < req.max_simulation_seconds and steps < max_steps:
            dt = min(choose_step(last_diag["altitude_km"]), 1.0,
                     req.max_simulation_seconds - elapsed)
            base.mass_kg = max(mass, 1e-6)
            k1, diag1 = acceleration(current_time, y, base)
            rate1 = mdot(diag1)
            base.mass_kg = max(1e-6, mass + 0.5 * dt * rate1)
            y_mid = y + 0.5 * dt * k1
            mid_time = current_time + timedelta(seconds=0.5 * dt)
            k2, diag_mid = acceleration(mid_time, y_mid, base)
            rate2 = mdot(diag_mid)
            y_new, new_mass = y + dt * k2, mass + dt * rate2
            next_time = current_time + timedelta(seconds=dt)
            base.mass_kg = max(1e-6, new_mass)
            _, next_diag = acceleration(next_time, y_new, base)

            if next_diag["altitude_km"] <= 0.0:
                h0, h1 = max(0.0, last_diag["altitude_km"]), next_diag["altitude_km"]
                fraction = h0 / max(h0 - h1, 1e-12)
                y = y + fraction * (y_new - y)
                mass = max(0.0, mass + fraction * (new_mass - mass))
                elapsed += fraction * dt
                current_time += timedelta(seconds=fraction * dt)
                base.mass_kg = max(mass, 1e-6)
                _, last_diag = acceleration(current_time, y, base)
                last_diag["altitude_km"] = 0.0
                impact, status = True, "ground_intersection"
                steps += 1
                save(last_diag, elapsed, current_time, mass)
                break
            if new_mass <= 0.001:
                fraction = max(0.0, min(1.0, (mass - 0.001) / max(mass - new_mass, 1e-12)))
                y = y + fraction * (y_new - y)
                elapsed += fraction * dt
                current_time += timedelta(seconds=fraction * dt)
                mass, status = 0.001, "mass_depleted_model_limit"
                base.mass_kg = mass
                _, last_diag = acceleration(current_time, y, base)
                steps += 1
                save(last_diag, elapsed, current_time, mass)
                break

            y, mass = y_new, new_mass
            elapsed += dt
            current_time = next_time
            base.mass_kg = max(mass, 1e-6)
            _, last_diag = acceleration(current_time, y, base)
            steps += 1
            if elapsed - last_saved >= req.sample_interval_s or steps == 1:
                save(last_diag, elapsed, current_time, mass)
                last_saved = elapsed

        if steps >= max_steps:
            status = "step_limit"
        if not points:
            save(last_diag, elapsed, current_time, mass)
        return {
            "api_version": APP_VERSION,
            "model": {
                "orbit_initialization": "SGP4 / TEME", "atmosphere": "NRLMSISE-00 via pymsis",
                "gravity": "Earth central gravity + J2", "drag": "quadratic aerodynamic drag",
                "integration": "explicit midpoint RK2; altitude-dependent step capped at 1 s",
                "ablation": "lumped effective heat-flux-to-mass-loss proxy",
                "fragmentation": "not modeled", "winds": "not modeled",
            },
            "object": {
                "object_type": req.object_type, "material_label": req.material_label,
                "initial_mass_kg": initial_mass, "reference_area_m2": req.reference_area_m2,
                "drag_coefficient": req.drag_coefficient, "nose_radius_m": req.nose_radius_m,
                "heat_of_ablation_j_kg": req.heat_of_ablation_j_kg,
                "ablation_efficiency": req.ablation_efficiency,
                "heated_area_factor": req.heated_area_factor,
            },
            "entry_interface": {
                "source": entry_source, "time_utc": entry_time.isoformat(),
                "latitude_deg": entry_geo[0], "longitude_deg": entry_geo[1],
                "altitude_km": entry_geo[2], "velocity_km_s": float(np.linalg.norm(v0)) / 1000.0,
            },
            "status": status, "impact": impact, "elapsed_simulation_s": round(elapsed, 3),
            "steps": steps, "final_mass_kg": round(mass, 6),
            "mass_loss_kg": round(initial_mass - mass, 6),
            "mass_remaining_fraction": round(mass / initial_mass, 8),
            "peak_dynamic_pressure_Pa": max((p["dynamic_pressure_Pa"] for p in points), default=0.0),
            "peak_convective_heat_flux_W_m2": max((p["convective_heat_flux_W_m2"] for p in points), default=0.0),
            "peak_drag_deceleration_m_s2": max((p["drag_deceleration_m_s2"] for p in points), default=0.0),
            "track": points,
            "limitations": [
                "ผลเป็น scenario simulation จาก TLE/OMM ที่ผู้ใช้ส่ง ไม่ใช่ operational re-entry forecast",
                "ablation เป็น lumped effective proxy ไม่ใช่ thermal conduction หรือ material-response solver",
                "พื้นที่หน้าตัดและ drag coefficient คงที่; ไม่มี tumble หรือ aerodynamic shape change",
                "ยังไม่มี fragmentation, fragment cloud, lift, wind field หรือ debris survival model",
                "ไม่มี covariance หรือ uncertainty propagation; track ไม่ใช่ impact corridor",
                "คุณสมบัติวัสดุและพารามิเตอร์การระเหยต้องมาจากข้อมูลหรือสมมติฐานที่ผู้ใช้ระบุ",
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"v6 re-entry scenario failed: {str(exc)[:300]}") from exc



@app.post("/api/v1/simulate")
def simulate(req: SimulationRequest):
    try:
        return simulate_entry(req)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {exc}") from exc