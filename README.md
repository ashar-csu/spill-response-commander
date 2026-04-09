# Spill Response Commander

A Streamlit app for educational spill-response support. The app lets a user select a chemical, review hazard data from a CSV file, estimate a simplified evacuation radius, display a directional plume on a map, and save an incident log.

## Files
- `app.py` — updated Streamlit app with wind direction, plume ellipse, and location geocoding
- `chemicals.csv` — starter chemical safety dataset
- `requirements.txt` — Python dependencies

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Key features
- Chemical lookup from CSV
- Hazard ratings, PPE, first aid, and cleanup actions
- Wind speed slider
- Wind direction / plume direction slider
- Directional plume ellipse on map
- Location geocoding from typed place name or address
- Incident logging to CSV

## Important note
This is an educational demo, not an operational hazmat model. Always validate against official SDS, ERG, and site emergency procedures.
