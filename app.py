from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import streamlit as st
import folium
from geopy.distance import distance as geopy_distance
from geopy.geocoders import Nominatim
from streamlit_folium import st_folium

# -----------------------------
# App config
# -----------------------------
st.set_page_config(
    page_title="Spill Response Commander",
    page_icon="⚠️",
    layout="wide",
)

DATA_PATH = Path("chemicals.csv")
LOG_PATH = Path("incident_log.csv")

# Default map center: Fort Collins
DEFAULT_LAT = 40.5853
DEFAULT_LON = -105.0844
DEFAULT_LOCATION = "Fort Collins, Colorado"

# -----------------------------
# Helpers
# -----------------------------
@st.cache_data

def load_chemical_data(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing data file: {path}")

    df = pd.read_csv(path)

    required_columns = [
        "Chemical",
        "HealthHazard",
        "FireHazard",
        "Reactivity",
        "PPE",
        "FirstAid",
        "Neutralization",
        "Flammable",
        "WaterReactive",
        "CriticalAlert",
        "IsolationBaseMeters",
    ]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    # Normalize search field
    df["Chemical_lower"] = df["Chemical"].astype(str).str.strip().str.lower()
    return df


def yes_no(value: object) -> bool:
    return str(value).strip().lower() in {"yes", "y", "true", "1"}


def split_bullets(text: object) -> list[str]:
    if pd.isna(text):
        return []
    raw = str(text).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def calculate_evacuation_radius(
    isolation_base_meters: float,
    wind_speed_mph: float,
    spill_volume_liters: float,
) -> float:
    """
    Simplified training formula only.
    This is not a real plume dispersion model.
    """
    return isolation_base_meters + (wind_speed_mph * 10.0) + (spill_volume_liters * 5.0)


def plume_axes(radius_m: float, wind_speed_mph: float) -> tuple[float, float]:
    """Return semi-major and semi-minor axes for a simplified plume ellipse."""
    stretch_factor = 1.1 + (wind_speed_mph / 50.0)  # ~1.1x to ~2.3x
    shrink_factor = max(0.45, 0.8 - (wind_speed_mph / 100.0))
    semi_major = radius_m * stretch_factor
    semi_minor = radius_m * shrink_factor
    return semi_major, semi_minor


def hazard_color(value: int) -> str:
    if value >= 4:
        return "🔴"
    if value >= 2:
        return "🟠"
    return "🟢"


def deg_to_cardinal(deg: float) -> str:
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(deg / 45) % 8
    return directions[idx]


@st.cache_data(show_spinner=False)
def geocode_location(location_text: str) -> Tuple[float, float, str] | None:
    if not location_text.strip():
        return None
    geolocator = Nominatim(user_agent="spill-response-commander-app", timeout=8)
    loc = geolocator.geocode(location_text)
    if not loc:
        return None
    return float(loc.latitude), float(loc.longitude), str(loc.address)


def rotated_ellipse_points(
    center_lat: float,
    center_lon: float,
    semi_major_m: float,
    semi_minor_m: float,
    bearing_deg: float,
    n_points: int = 90,
) -> List[List[float]]:
    """Create a rotated ellipse polygon around a center point.

    bearing_deg is the direction the plume travels toward, clockwise from North.
    """
    points: List[List[float]] = []
    theta = math.radians(90 - bearing_deg)  # convert north-clockwise to east-ccw
    lat_factor = 111_320.0
    lon_factor = lat_factor * max(math.cos(math.radians(center_lat)), 1e-6)

    for i in range(n_points):
        t = (2 * math.pi * i) / n_points
        x = semi_major_m * math.cos(t)
        y = semi_minor_m * math.sin(t)

        xr = x * math.cos(theta) - y * math.sin(theta)
        yr = x * math.sin(theta) + y * math.cos(theta)

        dlon = xr / lon_factor
        dlat = yr / lat_factor
        points.append([center_lat + dlat, center_lon + dlon])

    return points


def arrow_endpoint(lat: float, lon: float, bearing_deg: float, length_m: float) -> tuple[float, float]:
    dest = geopy_distance(meters=length_m).destination((lat, lon), bearing_deg)
    return dest.latitude, dest.longitude


def save_incident_log(record: dict) -> None:
    row = pd.DataFrame([record])
    if LOG_PATH.exists():
        existing = pd.read_csv(LOG_PATH)
        updated = pd.concat([existing, row], ignore_index=True)
        updated.to_csv(LOG_PATH, index=False)
    else:
        row.to_csv(LOG_PATH, index=False)
        

def warning_banner(message: str) -> None:
    st.error(f"⚠️ {message}")


# -----------------------------
# Load data
# -----------------------------
st.title("⚠️ Spill Response Commander")
st.caption(
    "Training / decision-support demo only. Always follow official SDS, ERG, and site emergency procedures."
)

try:
    chemicals_df = load_chemical_data(DATA_PATH)
except Exception as exc:
    st.exception(exc)
    st.stop()


# -----------------------------
# Session state defaults
# -----------------------------
if "map_lat" not in st.session_state:
    st.session_state.map_lat = DEFAULT_LAT
if "map_lon" not in st.session_state:
    st.session_state.map_lon = DEFAULT_LON
if "resolved_address" not in st.session_state:
    st.session_state.resolved_address = DEFAULT_LOCATION
if "incident_location_name" not in st.session_state:
    st.session_state.incident_location_name = "Fly High Trampoline Park, Fort Collins, CO"

# -----------------------------
# Sidebar inputs
# -----------------------------
st.sidebar.header("Incident Inputs")

chemical_names = chemicals_df["Chemical"].sort_values().tolist()

selected_chemical = st.sidebar.selectbox(
    "Chemical Name",
    options=chemical_names,
    index=0,
)

wind_speed = st.sidebar.slider(
    "Wind Speed (mph)",
    min_value=0,
    max_value=60,
    value=10,
    step=1,
)

wind_direction = st.sidebar.slider(
    "Wind Direction / Plume Direction (°)",
    min_value=0,
    max_value=359,
    value=90,
    step=1,
    help="Direction the plume is expected to travel toward. 0°=North, 90°=East, 180°=South, 270°=West.",
)

spill_volume = st.sidebar.slider(
    "Spill Volume (liters)",
    min_value=1,
    max_value=500,
    value=20,
    step=1,
)

incident_time = st.sidebar.text_input(
    "Incident Time",
    value=datetime.now().strftime("%Y-%m-%d %H:%M"),
    help="Use local date/time format.",
)

location_text = st.sidebar.text_input(
    "Incident Location / Address",
    key="incident_location_name",
    help="Type a landmark, address, or place name and click Geocode Location.",
)

if st.sidebar.button("Geocode Location", use_container_width=True):
    with st.sidebar:
        with st.spinner("Resolving location..."):
            geo_result = geocode_location(location_text)
    if geo_result:
        st.session_state.map_lat, st.session_state.map_lon, st.session_state.resolved_address = geo_result
        st.sidebar.success("Location found.")
    else:
        st.sidebar.warning("Could not resolve that location. Using the previous map location.")

st.sidebar.caption(f"Resolved map location: {st.session_state.resolved_address}")
st.sidebar.caption(f"Coordinates: {st.session_state.map_lat:.5f}, {st.session_state.map_lon:.5f}")

responder_notes = st.sidebar.text_area(
    "Responder Notes",
    value="",
    height=120,
)

# -----------------------------
# Find chemical
# -----------------------------
row = chemicals_df.loc[
    chemicals_df["Chemical_lower"] == selected_chemical.strip().lower()
].iloc[0]

health_hazard = int(row["HealthHazard"])
fire_hazard = int(row["FireHazard"])
reactivity = int(row["Reactivity"])
ppe = str(row["PPE"])
first_aid = split_bullets(row["FirstAid"])
neutralization = split_bullets(row["Neutralization"])
flammable = yes_no(row["Flammable"])
water_reactive = yes_no(row["WaterReactive"])
critical_alert = str(row["CriticalAlert"]).strip()
isolation_base = float(row["IsolationBaseMeters"])
typical_use = str(row["TypicalUse"]).strip() if "TypicalUse" in row else ""

radius_m = calculate_evacuation_radius(
    isolation_base_meters=isolation_base,
    wind_speed_mph=wind_speed,
    spill_volume_liters=spill_volume,
)

semi_major_m, semi_minor_m = plume_axes(radius_m, wind_speed)
plume_points = rotated_ellipse_points(
    st.session_state.map_lat,
    st.session_state.map_lon,
    semi_major_m,
    semi_minor_m,
    wind_direction,
)
arrow_lat, arrow_lon = arrow_endpoint(
    st.session_state.map_lat,
    st.session_state.map_lon,
    wind_direction,
    semi_major_m,
)

# -----------------------------
# Critical alerts
# -----------------------------
if flammable:
    warning_banner("Flammable chemical detected. Eliminate all ignition sources immediately.")

if water_reactive:
    warning_banner("DO NOT USE WATER. Chemical is water-reactive.")

if health_hazard >= 4:
    warning_banner("Severe health hazard. Immediate respiratory protection may be required.")

if fire_hazard >= 3:
    warning_banner("High fire risk. Keep away from heat, sparks, and open flame.")

if critical_alert:
    warning_banner(critical_alert)

# -----------------------------
# Main layout
# -----------------------------
col1, col2 = st.columns([1.05, 0.95], gap="large")

with col1:
    st.subheader(f"Chemical Overview: {row['Chemical']}")
    if typical_use:
        st.caption(f"Typical use: {typical_use}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Health Hazard", f"{hazard_color(health_hazard)} {health_hazard}")
    c2.metric("Fire Hazard", f"{hazard_color(fire_hazard)} {fire_hazard}")
    c3.metric("Reactivity", f"{hazard_color(reactivity)} {reactivity}")
    c4.metric("Base Radius", f"{radius_m:,.0f} m")

    c5, c6, c7 = st.columns(3)
    c5.metric("Plume Direction", f"{wind_direction}° {deg_to_cardinal(wind_direction)}")
    c6.metric("Semi-major Axis", f"{semi_major_m:,.0f} m")
    c7.metric("Semi-minor Axis", f"{semi_minor_m:,.0f} m")

    st.markdown("### PPE Guidance")
    st.info(ppe)

    st.markdown("### First Aid Measures")
    if first_aid:
        for item in first_aid:
            st.markdown(f"- {item}")
    else:
        st.write("No first aid guidance available.")

    st.markdown("### Cleanup / Neutralization Actions")
    if neutralization:
        for item in neutralization:
            st.markdown(f"- {item}")
    else:
        st.write("No cleanup guidance available.")

    st.markdown("### Incident Summary")
    st.write(f"**Location:** {location_text}")
    st.write(f"**Resolved address:** {st.session_state.resolved_address}")
    st.write(f"**Time:** {incident_time}")
    st.write(f"**Wind Speed:** {wind_speed} mph")
    st.write(f"**Wind Direction / Plume Direction:** {wind_direction}° ({deg_to_cardinal(wind_direction)})")
    st.write(f"**Spill Volume:** {spill_volume} liters")

if "log_saved" not in st.session_state:
    st.session_state.log_saved = False

with col2:
    st.subheader("Evacuation Zone Map")

    fmap = folium.Map(location=[st.session_state.map_lat, st.session_state.map_lon], zoom_start=13)

    folium.Marker(
        [st.session_state.map_lat, st.session_state.map_lon],
        tooltip=f"{row['Chemical']} spill site",
        popup=f"{row['Chemical']} spill at {location_text}",
    ).add_to(fmap)

    # Simplified plume ellipse
    folium.Polygon(
        locations=plume_points,
        color="red",
        weight=2,
        fill=True,
        fill_color="red",
        fill_opacity=0.22,
        tooltip=f"Directional plume footprint ({semi_major_m:.0f}m x {semi_minor_m:.0f}m)",
    ).add_to(fmap)

    # Direction arrow
    folium.PolyLine(
        locations=[
            [st.session_state.map_lat, st.session_state.map_lon],
            [arrow_lat, arrow_lon],
        ],
        color="#ffd23f",
        weight=5,
        tooltip=f"Plume direction: {wind_direction}° {deg_to_cardinal(wind_direction)}",
    ).add_to(fmap)

    folium.CircleMarker(
        [arrow_lat, arrow_lon],
        radius=6,
        color="#ffd23f",
        fill=True,
        fill_color="#ffd23f",
        popup="Plume direction endpoint",
    ).add_to(fmap)

    st_folium(fmap, width=None, height=500)

    st.caption(
        "The plume overlay is a simplified educational visualization. It stretches with wind speed and spill volume and points in the user-selected wind/plume direction."
    )

    st.markdown("### Compliance Logging")
    st.caption("Log time, location, chemical, and notes for reporting.")

    if st.button("Save Incident Log", type="primary"):
        try:
            save_incident_log(
                {
                    "SavedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Chemical": row["Chemical"],
                    "HealthHazard": health_hazard,
                    "FireHazard": fire_hazard,
                    "Reactivity": reactivity,
                    "WindSpeed_mph": wind_speed,
                    "WindDirection_deg": wind_direction,
                    "SpillVolume_liters": spill_volume,
                    "BaseEvacuationRadius_m": round(radius_m, 2),
                    "PlumeSemiMajor_m": round(semi_major_m, 2),
                    "PlumeSemiMinor_m": round(semi_minor_m, 2),
                    "IncidentTime": incident_time,
                    "LocationName": location_text,
                    "ResolvedAddress": st.session_state.resolved_address,
                    "Latitude": st.session_state.map_lat,
                    "Longitude": st.session_state.map_lon,
                    "ResponderNotes": responder_notes,
                }
            )
            st.session_state.log_saved = True
            st.success(f"Incident saved to {LOG_PATH.name}")
        except Exception as exc:
            st.exception(exc)

    if st.session_state.log_saved and LOG_PATH.exists():
        with open(LOG_PATH, "rb") as f:
            st.download_button(
                "Download Incident Log",
                f,
                file_name="incident_log.csv",
                mime="text/csv",
                use_container_width=False,
            )

# -----------------------------
# Footer note
# -----------------------------
st.markdown("---")
st.caption(
    "Disclaimer: This app uses a simplified training formula and plume visualization for evacuation distance and does not replace professional hazmat guidance, SDS documents, ERG procedures, or emergency command decisions."
)
