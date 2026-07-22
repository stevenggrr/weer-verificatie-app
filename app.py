import os
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

# Pagina configuratie
st.set_page_config(
    page_title="Multi-Model Weer Verificatie",
    page_icon="🎯",
    layout="wide"
)

DB_XLSX = "weer_verificatie_database.xlsx"

# Gedeelde modelconfiguratie met display namen
MODELS_CONFIG = {
    "gfs":           {"sheet_name": "GFS",           "display_name": "GFS (US)",         "runs": ["00", "06", "12", "18"]},
    "ecm":           {"sheet_name": "EC",            "display_name": "ECMWF (EU)",       "runs": ["00", "06", "12", "18"]},
    "aifs":          {"sheet_name": "AIFS",          "display_name": "AIFS (AI)",        "runs": ["00", "06", "12", "18"]},
    "ec_experpluim": {"sheet_name": "EC_Experpluim", "display_name": "ECMWF (KNMI)",     "runs": ["00", "12"]}
}

LOCATIONS = {
    "Ukkel": {
        "sheet_prefix": "Ukkel",
        "has_knmi_ec": False
    },
    "DeBilt": {
        "sheet_prefix": "DeBilt",
        "has_knmi_ec": True
    }
}

# --- SIDEBAR / MENU ---
st.sidebar.markdown("## ⚙️ Instellingen")

st.sidebar.markdown("**Selecteer Locatie**")
loc_choice = st.sidebar.radio(
    "Locatie", 
    ["Ukkel (KMI)", "De Bilt (KNMI)"],
    label_visibility="collapsed"
)

selected_location = "Ukkel" if "Ukkel" in loc_choice else "DeBilt"

st.sidebar.markdown("**Selecteer Run Type**")
run_type_choice = st.sidebar.radio(
    "Run Type", 
    ["Operational (Oper)", "Ensemble Pluim (AVG)"],
    label_visibility="collapsed"
)
run_type = "Oper" if "Operational" in run_type_choice else "Ens"

st.sidebar.markdown("**Selecteer Temperatuur**")
temp_choice = st.sidebar.radio(
    "Temperatuur", 
    ["Max (Maximum)", "Min (Minimum)"],
    label_visibility="collapsed"
)
temp_type = "Max" if "Max" in temp_choice else "Min"

st.sidebar.markdown("**Kies Doeldatum (Verificatiedag)**")
default_date = datetime.now().date()
target_date = st.sidebar.date_input(
    "Doeldatum", 
    value=default_date,
    label_visibility="collapsed"
)
target_date_str = target_date.strftime('%Y-%m-%d')

# --- DATA INLADEN ---
@st.cache_data(ttl=60)
def load_database():
    if not os.path.exists(DB_XLSX):
        return None
    try:
        return pd.read_excel(DB_XLSX, sheet_name=None, index_col="Datum")
    except Exception as e:
        st.error(f"Fout bij inlezen Excel: {e}")
        return None

dfs = load_database()

loc_info = LOCATIONS[selected_location]
prefix = loc_info["sheet_prefix"]

# --- HOOFDPAGINA ---
st.markdown(f"# 🎯 Multi-Model Verification ({loc_choice})")
st.markdown(f"Vergelijk hoe GFS, ECMWF en AIFS voorspellen voor {loc_choice} naarmate de datum dichterbij kwam.")

if dfs is None:
    st.warning("⚠️ Geen database gevonden (`weer_verificatie_database.xlsx`). Draai eerst `scraper.py` om data op te halen.")
    st.stop()

# Bepaal actuele temperatuur (Actual)
actual_temp = None
sample_sheet_name = f"{prefix}_GFS_Max_Oper"
if sample_sheet_name in dfs and target_date_str in dfs[sample_sheet_name].index:
    val = dfs[sample_sheet_name].loc[target_date_str, "Actual_Temp"]
    if pd.notna(val):
        actual_temp = float(val)

if actual_temp is not None:
    st.markdown(f"### Officiële Meting ({loc_choice.split(' ')[0]})")
    st.markdown(f"<h2>{actual_temp} °C</h2>", unsafe_allow_html=True)
else:
    st.info(f"ℹ️ Geen officiële meting beschikbaar voor {target_date_str} op locatie {loc_choice}.")

st.markdown("---")

# --- TABEL OPBOUW ---
st.markdown(f"### 📊 Afwijkingen ten opzichte van Actuele Temperatuur (°C) — {run_type_choice}")
st.markdown(f"Daggemiddelde van runs (Fout = Voorspelling - Actueel)")

table_rows = []

for m_code, m_info in MODELS_CONFIG.items():
    if m_code == "ec_experpluim" and not loc_info["has_knmi_ec"]:
        continue
        
    m_prefix = m_info["sheet_name"]
    sheet_name = f"{prefix}_{m_prefix}_{temp_type}_{run_type}"
    
    row_data = {"Model": m_info["display_name"]}
    
    if sheet_name in dfs and target_date_str in dfs[sheet_name].index:
        df_sheet = dfs[sheet_name]
        row_vals = df_sheet.loc[target_date_str]
        
        d_days = [3, 7, 10, 12]
        diffs = []
        
        for d in d_days:
            col_vals = []
            for z in ["00z", "06z", "12z", "18z"]:
                col_name = f"D-{d}_{z}"
                if col_name in row_vals.index and pd.notna(row_vals[col_name]):
                    if actual_temp is not None:
                        diffs_val = float(row_vals[col_name]) - actual_temp
                        col_vals.append(diffs_val)
            
            if col_vals:
                avg_diff = sum(col_vals) / len(col_vals)
                row_data[f"D-{d} Gem."] = round(avg_diff, 1)
                diffs.append(avg_diff)
            else:
                row_data[f"D-{d} Gem."] = "None"
                
        if diffs:
            row_data["Gemiddelde Afwijking"] = round(sum(diffs) / len(diffs), 1)
        else:
            row_data["Gemiddelde Afwijking"] = "None"
    else:
        for d in [3, 7, 10, 12]:
            row_data[f"D-{d} Gem."] = "None"
        row_data["Gemiddelde Afwijking"] = "None"
        
    table_rows.append(row_data)

df_table = pd.DataFrame(table_rows)
if not df_table.empty:
    df_table = df_table.set_index("Model")
    st.dataframe(df_table, use_container_width=True)
else:
    st.info("Geen data beschikbaar voor deze selectie.")

st.markdown("""
* **Legenda:** 🔴 Rood (> +0.5°C) = Te warm | 🔵 Blauw (< -0.5°C) = Te koud | 🟢 Groen (binnen ±0.5°C) = Juist
""")

# --- LAATSTE UPDATE WEERGAVE ---
if os.path.exists(DB_XLSX):
    mod_time = os.path.getmtime(DB_XLSX)
    last_update_str = datetime.fromtimestamp(mod_time).strftime('%d-%m-%Y om %H:%M')
else:
    last_update_str = "Onbekend"

st.markdown("---")
st.markdown(f"<p style='font-size: 0.85rem; color: #888;'>Laatste update: {last_update_str}</p>", unsafe_allow_html=True)

# --- RUWE DATA SECTIE (13 DAGEN VÓÓR T/M 13 DAGEN NÁ DOELDATUM) ---
st.markdown(f"### 📋 Ruwe Data ({loc_choice} — {run_type_choice})")

active_models = [m for m in MODELS_CONFIG.values() if m["sheet_name"] != "EC_Experpluim" or loc_info["has_knmi_ec"]]
tab_labels = [m["display_name"] for m in active_models]
tabs = st.tabs(tab_labels)

# Bereken de grenzen: 13 dagen vóór én 13 dagen ná de geselecteerde doeldatum
min_allowed_date = target_date - timedelta(days=13)
max_allowed_date = target_date + timedelta(days=13)

for tab, m_info in zip(tabs, active_models):
    with tab:
        m_prefix = m_info["sheet_name"]
        sheet_name = f"{prefix}_{m_prefix}_{temp_type}_{run_type}"
        
        if sheet_name in dfs:
            df_raw = dfs[sheet_name].copy()
            
            if "Datum" not in df_raw.columns:
                df_raw = df_raw.reset_index()
            
            # Converteer Datum naar datetime om te kunnen filteren
            df_raw['Datum_dt'] = pd.to_datetime(df_raw['Datum']).dt.date
            
            # Filter: behoud alleen rijen tussen (Doeldatum - 13 dagen) en (Doeldatum + 13 dagen)
            df_filtered = df_raw[(df_raw['Datum_dt'] >= min_allowed_date) & (df_raw['Datum_dt'] <= max_allowed_date)]
            df_filtered = df_filtered.drop(columns=['Datum_dt']).sort_values(by="Datum", ascending=False)
            
            st.dataframe(df_filtered, use_container_width=True, height=350)
        else:
            st.info(f"Geen ruwe data beschikbaar voor tabblad {sheet_name}.")