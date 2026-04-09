from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import folium
from folium import plugins
from geopy.geocoders import Nominatim
from streamlit_autorefresh import st_autorefresh
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

# Colors for multi-chemical scenarios
PLUME_COLORS = [
    "red",
    "orange",
    "purple",
    "blue",
    "green",
    "darkred",
    "cadetblue",
]


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

    df["Chemical_lower"] = df["Chemical"].astype(str).str.strip().str.lower()
    return df


@st.cache_data(show_spinner=False)
def geocode_location(location_name: str) -> tuple[float, float, str]:
    """
    Geocode a typed location name to lat/lon.
    Falls back to Fort Collins if geocoding fails.
    """
    try:
        geolocator = Nominatim(user_agent="spill_response_commander")
        result = geolocator.geocode(location_name, timeout=10)
        if result:
            return result.latitude, result.longitude, result.address
    except Exception:
        pass

    return DEFAULT_LAT, DEFAULT_LON, "Fort Collins, CO (default fallback)"


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
    Demo formula only.
    This is not a true hazmat plume model.
    """
    return isolation_base_meters + (wind_speed_mph * 10.0) + (spill_volume_liters * 5.0)


def hazard_color(value: int) -> str:
    if value >= 4:
        return "🔴"
    if value >= 2:
        return "🟠"
    return "🟢"


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


def meters_to_latlon(lat: float, lon: float, dx_m: float, dy_m: float) -> tuple[float, float]:
    """
    Convert local meter offsets (dx east-west, dy north-south) to lat/lon.
    """
    dlat = dy_m / 111320.0
    dlon = dx_m / (111320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def rotate_xy(x: float, y: float, direction_deg: float) -> tuple[float, float]:
    """
    Rotate local plume coordinates by wind direction.
    Convention here: 0° = North, 90° = East, 180° = South, 270° = West.
    """
    theta = math.radians(90 - direction_deg)
    xr = x * math.cos(theta) - y * math.sin(theta)
    yr = x * math.sin(theta) + y * math.cos(theta)
    return xr, yr


def gaussian_half_width(x: float, radius_m: float, wind_speed_mph: float) -> float:
    """
    Approximate crosswind spread using a Gaussian-style width that expands downwind.
    This is a visual approximation for demonstration.
    """
    x = max(x, 1.0)
    speed_factor = 1.0 + (wind_speed_mph / 60.0)
    sigma_y = 0.10 * x * (1 + 0.0002 * x) ** (-0.5) * speed_factor
    sigma_y = max(sigma_y, 12.0)

    # Limit excessive widening
    max_width = 0.35 * radius_m
    return min(2.6 * sigma_y, max_width)


def build_gaussian_plume_polygon(
    center_lat: float,
    center_lon: float,
    radius_m: float,
    wind_direction_deg: float,
    wind_speed_mph: float,
    animation_scale: float = 1.0,
) -> list[list[float]]:
    """
    Build a downwind Gaussian-style plume polygon.
    """
    max_x = radius_m * animation_scale
    x_steps = [i * max_x / 22 for i in range(0, 23)]

    upper_edge = []
    lower_edge = []

    for x in x_steps:
        width = gaussian_half_width(x, radius_m, wind_speed_mph)

        # Upper edge
        dx_u, dy_u = rotate_xy(x, width, wind_direction_deg)
        lat_u, lon_u = meters_to_latlon(center_lat, center_lon, dx_u, dy_u)
        upper_edge.append([lat_u, lon_u])

        # Lower edge
        dx_l, dy_l = rotate_xy(x, -width, wind_direction_deg)
        lat_l, lon_l = meters_to_latlon(center_lat, center_lon, dx_l, dy_l)
        lower_edge.append([lat_l, lon_l])

    # Add a slightly wider rounded head
    head_points = []
    head_x = max_x
    head_w = gaussian_half_width(head_x, radius_m, wind_speed_mph) * 1.2
    for angle in range(-90, 91, 15):
        rad = math.radians(angle)
        hx = head_x + (0.12 * radius_m * math.cos(rad))
        hy = head_w * math.sin(rad)
        dx_h, dy_h = rotate_xy(hx, hy, wind_direction_deg)
        lat_h, lon_h = meters_to_latlon(center_lat, center_lon, dx_h, dy_h)
        head_points.append([lat_h, lon_h])

    polygon = upper_edge + head_points + list(reversed(lower_edge))
    return polygon


def add_direction_arrow(
    fmap: folium.Map,
    center_lat: float,
    center_lon: float,
    radius_m: float,
    wind_direction_deg: float,
) -> None:
    """
    Add a black arrow showing plume direction.
    """
    arrow_length = radius_m * 0.9
    dx, dy = rotate_xy(arrow_length, 0, wind_direction_deg)
    end_lat, end_lon = meters_to_latlon(center_lat, center_lon, dx, dy)

    plugins.PolyLineTextPath(
        folium.PolyLine(
            locations=[[center_lat, center_lon], [end_lat, end_lon]],
            color="black",
            weight=4,
            opacity=1.0,
        ),
        "➜",
        repeat=False,
        offset=10,
        attributes={"fill": "black", "font-weight": "bold", "font-size": "20"},
    ).add_to(fmap)


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
# Sidebar inputs
# -----------------------------
st.sidebar.header("Incident Inputs")

chemical_names = chemicals_df["Chemical"].sort_values().tolist()

# Multi-chemical scenario
selected_chemicals = st.sidebar.multiselect(
    "Chemical Name(s)",
    options=chemical_names,
    default=[chemical_names[0]] if chemical_names else [],
    help="Select one or more chemicals for a multi-chemical scenario.",
)

if not selected_chemicals:
    st.warning("Please select at least one chemical.")
    st.stop()

wind_speed = st.sidebar.slider(
    "Wind Speed (mph)",
    min_value=0,
    max_value=60,
    value=10,
    step=1,
)

# New: wind direction
wind_direction = st.sidebar.slider(
    "Wind Direction (degrees)",
    min_value=0,
    max_value=359,
    value=90,
    step=1,
    help="0° = North, 90° = East, 180° = South, 270° = West",
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

# Replace lat/lon with typed location
incident_location_name = st.sidebar.text_input(
    "Incident Location",
    value="Fly High Trampoline Park, Fort Collins, CO",
    help="Type a place name or address.",
)

responder_notes = st.sidebar.text_area(
    "Responder Notes",
    value="",
    height=120,
)

# Animation controls
animate_plume = st.sidebar.checkbox("Animate plume", value=True)
if animate_plume:
    tick = st_autorefresh(interval=1200, key="plume_animation")
else:
    tick = 0

# Geocode typed location
lat, lon, resolved_address = geocode_location(incident_location_name)

# -----------------------------
# Find chemical rows
# -----------------------------
selected_rows = chemicals_df[chemicals_df["Chemical"].isin(selected_chemicals)].copy()

# Compute per-chemical radii
selected_rows["EvacuationRadius_m"] = selected_rows["IsolationBaseMeters"].astype(float).apply(
    lambda isolation_base: calculate_evacuation_radius(
        isolation_base_meters=isolation_base,
        wind_speed_mph=wind_speed,
        spill_volume_liters=spill_volume,
    )
)

# Summary metrics across scenario
max_health_hazard = int(selected_rows["HealthHazard"].max())
max_fire_hazard = int(selected_rows["FireHazard"].max())
max_reactivity = int(selected_rows["Reactivity"].max())
max_radius_m = float(selected_rows["EvacuationRadius_m"].max())

# Animation phase for subtle pulse
pulse_scale = 1.0 + 0.05 * math.sin(tick / 2.0)
fill_opacity = 0.20 + 0.08 * (0.5 + 0.5 * math.sin(tick / 1.8))

# -----------------------------
# Critical alerts
# -----------------------------
for _, row in selected_rows.iterrows():
    chemical_name = row["Chemical"]

    if yes_no(row["Flammable"]):
        warning_banner(
            f"{chemical_name}: Flammable chemical detected. Eliminate all ignition sources immediately."
        )

    if yes_no(row["WaterReactive"]):
        warning_banner(f"{chemical_name}: DO NOT USE WATER. Chemical is water-reactive.")

    if int(row["HealthHazard"]) >= 4:
        warning_banner(
            f"{chemical_name}: Severe health hazard. Immediate respiratory protection may be required."
        )

    if int(row["FireHazard"]) >= 3:
        warning_banner(f"{chemical_name}: High fire risk. Keep away from heat, sparks, and open flame.")

    critical_alert = str(row["CriticalAlert"]).strip()
    if critical_alert:
        warning_banner(f"{chemical_name}: {critical_alert}")

# -----------------------------
# Main layout
# -----------------------------
col1, col2 = st.columns([1.05, 0.95], gap="large")

with col1:
    scenario_title = ", ".join(selected_chemicals)
    st.subheader(f"Chemical Overview: {scenario_title}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Health Hazard", f"{hazard_color(max_health_hazard)} {max_health_hazard}")
    c2.metric("Fire Hazard", f"{hazard_color(max_fire_hazard)} {max_fire_hazard}")
    c3.metric("Reactivity", f"{hazard_color(max_reactivity)} {max_reactivity}")
    c4.metric("Evacuation Radius", f"{max_radius_m:,.0f} m")

    if len(selected_rows) == 1:
        row = selected_rows.iloc[0]

        st.markdown("### PPE Guidance")
        st.info(str(row["PPE"]))

        st.markdown("### First Aid Measures")
        first_aid = split_bullets(row["FirstAid"])
        if first_aid:
            for item in first_aid:
                st.markdown(f"- {item}")
        else:
            st.write("No first aid guidance available.")

        st.markdown("### Cleanup / Neutralization Actions")
        neutralization = split_bullets(row["Neutralization"])
        if neutralization:
            for item in neutralization:
                st.markdown(f"- {item}")
        else:
            st.write("No cleanup guidance available.")
    else:
        st.markdown("### Multi-Chemical Details")
        tabs = st.tabs(selected_rows["Chemical"].tolist())

        for tab, (_, row) in zip(tabs, selected_rows.iterrows()):
            with tab:
                st.write(f"**Evacuation Radius:** {row['EvacuationRadius_m']:.0f} m")
                st.write(f"**PPE:** {row['PPE']}")

                st.write("**First Aid Measures**")
                first_aid = split_bullets(row["FirstAid"])
                if first_aid:
                    for item in first_aid:
                        st.markdown(f"- {item}")

                st.write("**Cleanup / Neutralization Actions**")
                neutralization = split_bullets(row["Neutralization"])
                if neutralization:
                    for item in neutralization:
                        st.markdown(f"- {item}")

    st.markdown("### Incident Summary")
    st.write(f"**Location entered:** {incident_location_name}")
    st.write(f"**Resolved location:** {resolved_address}")
    st.write(f"**Time:** {incident_time}")
    st.write(f"**Wind Speed:** {wind_speed} mph")
    st.write(f"**Wind Direction:** {wind_direction}°")
    st.write(f"**Spill Volume:** {spill_volume} liters")
    st.write(f"**Selected Chemicals:** {', '.join(selected_chemicals)}")

with col2:
    st.subheader("Evacuation Zone Map")

    fmap = folium.Map(location=[lat, lon], zoom_start=14)

    # Main spill marker
    folium.Marker(
        [lat, lon],
        tooltip="Spill Site",
        popup=f"Spill location: {incident_location_name}",
    ).add_to(fmap)

    # Add plumes for each chemical
    for idx, (_, row) in enumerate(selected_rows.iterrows()):
        color = PLUME_COLORS[idx % len(PLUME_COLORS)]
        radius_m = float(row["EvacuationRadius_m"])

        plume_polygon = build_gaussian_plume_polygon(
            center_lat=lat,
            center_lon=lon,
            radius_m=radius_m,
            wind_direction_deg=wind_direction,
            wind_speed_mph=wind_speed,
            animation_scale=pulse_scale if animate_plume else 1.0,
        )

        folium.Polygon(
            locations=plume_polygon,
            color=color,
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=fill_opacity if animate_plume else 0.25,
            popup=f"{row['Chemical']} plume ({radius_m:.0f} m)",
            tooltip=row["Chemical"],
        ).add_to(fmap)

    # Black direction arrow
    add_direction_arrow(
        fmap=fmap,
        center_lat=lat,
        center_lon=lon,
        radius_m=max_radius_m,
        wind_direction_deg=wind_direction,
    )

    st_folium(fmap, width=None, height=500)

    st.markdown("### Compliance Logging")
    st.caption("Log time, location, chemical(s), and notes for reporting.")

    if st.button("Save Incident Log", type="primary"):
        try:
            save_incident_log(
                {
                    "SavedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),