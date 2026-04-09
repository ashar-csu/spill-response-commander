from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim

# -----------------------------
# App config
# -----------------------------
st.set_page_config(page_title="Spill Response Commander", layout="wide")

DATA_PATH = Path("chemicals.csv")

# -----------------------------
# Load data
# -----------------------------
df = pd.read_csv(DATA_PATH)
df["Chemical_lower"] = df["Chemical"].str.lower()

# -----------------------------
# Sidebar Inputs
# -----------------------------
st.sidebar.title("Incident Inputs")

chemical = st.sidebar.selectbox("Chemical", df["Chemical"])

wind_speed = st.sidebar.slider("Wind Speed (mph)", 0, 60, 10)
spill_volume = st.sidebar.slider("Spill Volume (liters)", 1, 500, 20)

# NEW: Wind direction
wind_direction = st.sidebar.slider("Wind Direction (degrees)", 0, 360, 90)

# NEW: Location input
location_name = st.sidebar.text_input("Enter Location", "Fort Collins, CO")

# Geocode
geolocator = Nominatim(user_agent="spill_app")
location = geolocator.geocode(location_name)

if location:
    lat, lon = location.latitude, location.longitude
else:
    lat, lon = 40.5853, -105.0844

# -----------------------------
# Get chemical data
# -----------------------------
row = df[df["Chemical"] == chemical].iloc[0]

radius = row["IsolationBaseMeters"] + wind_speed * 10 + spill_volume * 5

# -----------------------------
# Alerts
# -----------------------------
st.title("⚠️ Spill Response Commander")

if str(row["Flammable"]).lower() == "yes":
    st.error("Flammable chemical detected!")

if str(row["WaterReactive"]).lower() == "yes":
    st.error("DO NOT USE WATER!")

# -----------------------------
# Layout
# -----------------------------
col1, col2 = st.columns(2)

with col1:
    st.subheader(f"{chemical} Overview")

    st.metric("Health Hazard", row["HealthHazard"])
    st.metric("Fire Hazard", row["FireHazard"])
    st.metric("Reactivity", row["Reactivity"])

    st.write("### PPE")
    st.info(row["PPE"])

    st.write("### First Aid")
    for item in str(row["FirstAid"]).split(";"):
        st.write("-", item)

# -----------------------------
# Map with plume
# -----------------------------
with col2:
    st.subheader("Evacuation Zone")

    m = folium.Map(location=[lat, lon], zoom_start=13)

    # Draw ellipse (approximation)
    points = []
    for angle in range(0, 360, 10):
        rad = math.radians(angle)

        # Ellipse shape
        x = radius * math.cos(rad)
        y = (radius * 0.4) * math.sin(rad)

        # Rotate ellipse based on wind direction
        theta = math.radians(wind_direction)
        x_rot = x * math.cos(theta) - y * math.sin(theta)
        y_rot = x * math.sin(theta) + y * math.cos(theta)

        lat_offset = y_rot / 111320
        lon_offset = x_rot / (111320 * math.cos(math.radians(lat)))

        points.append([lat + lat_offset, lon + lon_offset])

    folium.Polygon(points, color="red", fill=True, fill_opacity=0.3).add_to(m)

    # Direction arrow
    arrow_lat = lat + (radius / 111320) * math.sin(math.radians(wind_direction))
    arrow_lon = lon + (radius / (111320 * math.cos(math.radians(lat)))) * math.cos(math.radians(wind_direction))

    folium.Marker(
        [arrow_lat, arrow_lon],
        popup="Wind Direction",
        icon=folium.Icon(color="red", icon="arrow-up")
    ).add_to(m)

    st_folium(m, height=500)