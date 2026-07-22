import os
import time
import requests
import io
import pandas as pd
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

DB_XLSX = "weer_verificatie_database.xlsx"

LOCATIONS = {
    "Ukkel": {
        "geoid": "6616",
        "official_source": "KMI",
        "sheet_prefix": "Ukkel"
    },
    "DeBilt": {
        "geoid": "6260",
        "official_source": "KNMI",
        "sheet_prefix": "DeBilt",
        "lat": 52.10,
        "lon": 5.18,
        "knmi_ec": True
    }
}

MODELS_CONFIG = {
    "gfs":           {"sheet_name": "GFS",           "display_name": "GFS (US)",         "runs": ["00", "06", "12", "18"]},
    "ecm":           {"sheet_name": "EC",            "display_name": "ECMWF (EU)",       "runs": ["00", "06", "12", "18"]},
    "aifs":          {"sheet_name": "AIFS",          "display_name": "AIFS (AI)",        "runs": ["00", "06", "12", "18"]},
    "ec_experpluim": {"sheet_name": "EC_Experpluim", "display_name": "ECMWF (KNMI)",     "runs": ["00", "12"]}
}

def get_latest_available_runs():
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour

    if hour >= 22 or hour < 4:
        latest_run = "18"
    elif hour >= 16:
        latest_run = "12"
    elif hour >= 10:
        latest_run = "06"
    else:
        latest_run = "00"

    return {
        "gfs": latest_run,
        "aifs": latest_run,
        "ecm": latest_run
    }

def get_active_knmi_ec_run_info():
    now_utc = datetime.now(timezone.utc)
    if 8 <= now_utc.hour < 20 or (now_utc.hour == 8 and now_utc.minute >= 30):
        run_hour = "00z"
        run_date = now_utc.date()
    else:
        run_hour = "12z"
        if now_utc.hour < 8:
            run_date = now_utc.date() - timedelta(days=1)
        else:
            run_date = now_utc.date()

    return run_hour, run_date

# ==========================================
# 1. KNMI / ECMWF OPHALEN VIA OPEN-METEO API
# ==========================================
def fetch_knmi_ecmwf_openmeteo(lat=52.10, lon=5.18):
    print("📥 Bezig met ophalen van ECMWF pluim (Open-Meteo) voor De Bilt...")
    url = f"https://api.open-meteo.com/v1/ecmwf?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min&forecast_days=12&timezone=Europe%2FAmsterdam"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            daily = data.get("daily", {})
            
            dates = daily.get("time", [])
            max_list = daily.get("temperature_2m_max", [])
            min_list = daily.get("temperature_2m_min", [])
            
            parsed = {}
            for idx, d_str in enumerate(dates):
                parsed[d_str] = {
                    "Max": max_list[idx] if idx < len(max_list) else None,
                    "Min": min_list[idx] if idx < len(min_list) else None
                }
            print("  -> Succesvol ECMWF (KNMI) data opgehaald.")
            return parsed
    except Exception as e:
        print(f"⚠️ Fout bij ophalen ECMWF KNMI data: {e}")
        
    return {}

# ==========================================
# 2. OFFICIËLE METINGEN OPHALEN
# ==========================================
def fetch_official_kmi_uccle():
    print("📥 Bezig met ophalen officiële metingen KMI (Ukkel)...")
    try:
        txt_url = "https://www.meteo.be/resources/climatology/uccle_month/Ukkel_waarnemingen.txt"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(txt_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            lines = response.text.strip().split("\n")
            records = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("Dag") or line.startswith("Date"):
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        date_obj = pd.to_datetime(parts[0], dayfirst=True)
                        records.append({
                            "Datum": date_obj.strftime('%Y-%m-%d'),
                            "Max": float(parts[1].replace(',', '.')),
                            "Min": float(parts[2].replace(',', '.'))
                        })
                    except ValueError:
                        continue
            if records:
                print("  -> Ukkel waarnemingen succesvol geladen.")
                return pd.DataFrame(records).set_index("Datum")
        print("⚠️ KMI Ukkel data niet bereikbaar via txt, lege DataFrame.")
        return pd.DataFrame()
    except Exception as e:
        print(f"⚠️ Fout bij KMI ophalen: {e}")
        return pd.DataFrame()

def fetch_official_knmi_debilt():
    print("📥 Bezig met ophalen officiële metingen KNMI (De Bilt)...")
    try:
        url = "https://cdn.knmi.nl/knmi/map/page/klimatologie/gegevens/daggegevens/etmgeg_260.txt"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            lines = response.text.split("\n")
            header_index = -1
            headers_list = []
            for idx, line in enumerate(lines):
                if "YYYYMMDD" in line and ("TN" in line or "TX" in line):
                    clean_line = line.replace("#", "").strip()
                    headers_list = [h.strip() for h in clean_line.split(",")]
                    header_index = idx
                    break
            
            if header_index != -1 and "TN" in headers_list and "TX" in headers_list:
                date_col = headers_list.index("YYYYMMDD")
                tn_col = headers_list.index("TN")
                tx_col = headers_list.index("TX")
                
                records = []
                for line in lines[header_index + 1:]:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) > max(date_col, tn_col, tx_col):
                        try:
                            date_str = parts[date_col]
                            tn_val = parts[tn_col]
                            tx_val = parts[tx_col]
                            
                            if date_str and tn_val and tx_val:
                                date_obj = datetime.strptime(date_str, '%Y%m%d')
                                records.append({
                                    "Datum": date_obj.strftime('%Y-%m-%d'),
                                    "Max": float(tx_val) / 10.0,
                                    "Min": float(tn_val) / 10.0
                                })
                        except (ValueError, IndexError):
                            continue
                if records:
                    print("  -> De Bilt waarnemingen succesvol geladen.")
                    return pd.DataFrame(records).set_index("Datum")
        print("⚠️ KNMI De Bilt data niet bereikbaar, lege DataFrame.")
        return pd.DataFrame()
    except Exception as e:
        print(f"⚠️ Fout bij KNMI De Bilt ophalen: {e}")
        return pd.DataFrame()

# ==========================================
# 3. MATRIX UPDATE LOGICA
# ==========================================
def update_matrix_excel(all_parsed_data, knmi_ec_data=None, include_actuals=False):
    print("💾 Bezig met wegschrijven van alle data naar de Excel-database...")
    existing_sheets = {}
    START_DATE = "2026-01-01"
    current_year = datetime.now(timezone.utc).year
    END_DATE = f"{current_year + 1}-12-31"
    
    full_year_dates = pd.date_range(start=START_DATE, end=END_DATE).strftime('%Y-%m-%d').tolist()
    
    expected_columns = ["Actual_Temp"]
    for d in range(1, 13):
        for z in ["00z", "06z", "12z", "18z"]:
            expected_columns.append(f"D-{d}_{z}")
    
    if os.path.exists(DB_XLSX):
        try:
            with open(DB_XLSX, "rb") as f:
                file_bytes = f.read()
            xls = pd.ExcelFile(io.BytesIO(file_bytes))
            for s in xls.sheet_names:
                df_old = pd.read_excel(xls, sheet_name=s, index_col="Datum")
                df_old.index = df_old.index.astype(str)
                existing_sheets[s] = df_old
        except Exception as e:
            print(f"⚠️ Kon bestaande Excel niet openen: {e}")

    df_kmi, df_knmi = pd.DataFrame(), pd.DataFrame()
    if include_actuals:
        df_kmi = fetch_official_kmi_uccle()
        df_knmi = fetch_official_knmi_debilt()

    sheets_to_process = {}
    for loc_key, loc_info in LOCATIONS.items():
        prefix = loc_info["sheet_prefix"]
        for m_code, m_info in MODELS_CONFIG.items():
            if m_code == "ec_experpluim" and not loc_info.get("knmi_ec"):
                continue

            m_prefix = m_info["sheet_name"]
            sheets_to_process[f"{prefix}_{m_prefix}_Max_Oper"] = (loc_key, m_code, "Max", "Oper")
            sheets_to_process[f"{prefix}_{m_prefix}_Min_Oper"] = (loc_key, m_code, "Min", "Oper")
            sheets_to_process[f"{prefix}_{m_prefix}_Max_Ens"]  = (loc_key, m_code, "Max", "Ens")
            sheets_to_process[f"{prefix}_{m_prefix}_Min_Ens"]  = (loc_key, m_code, "Min", "Ens")

    ec_run_hour, ec_run_date = get_active_knmi_ec_run_info()

    with pd.ExcelWriter(DB_XLSX, engine="openpyxl") as writer:
        for sheet_name, (loc_key, model_code, temp_type, run_type) in sheets_to_process.items():
            df_sheet = existing_sheets.get(sheet_name, pd.DataFrame())
            df_sheet = df_sheet.reindex(index=full_year_dates, columns=expected_columns)
            df_sheet.index.name = "Datum"

            if include_actuals:
                df_actual = df_kmi if loc_key == "Ukkel" else df_knmi
                if not df_actual.empty and temp_type in df_actual.columns:
                    for datum, row in df_actual.iterrows():
                        if datum in df_sheet.index and pd.notna(row[temp_type]):
                            df_sheet.loc[str(datum), "Actual_Temp"] = float(row[temp_type])

            if model_code != "ec_experpluim":
                model_runs = [r for r in all_parsed_data if r["model"] == model_code and r["location"] == loc_key]
                for run_info in model_runs:
                    run_date = run_info["run_date"]
                    run_hour = run_info["run_hour"]
                    data_dict = run_info["data"]

                    for target_date_str, vals in data_dict.items():
                        if target_date_str not in df_sheet.index:
                            continue
                        target_dt = datetime.strptime(target_date_str, '%Y-%m-%d').date()
                        days_ahead = (target_dt - run_date).days

                        if 1 <= days_ahead <= 12:
                            col_name = f"D-{days_ahead}_{run_hour}"
                            val = vals.get(f"{run_type}_{temp_type}")
                            if col_name in df_sheet.columns and val is not None:
                                df_sheet.loc[target_date_str, col_name] = val

            elif knmi_ec_data and loc_key in knmi_ec_data:
                loc_data = knmi_ec_data[loc_key]
                for target_date_str, ec_vals in loc_data.items():
                    if target_date_str in df_sheet.index:
                        target_dt = datetime.strptime(target_date_str, '%Y-%m-%d').date()
                        days_ahead = (target_dt - ec_run_date).days
                        
                        if 1 <= days_ahead <= 12:
                            col_name = f"D-{days_ahead}_{ec_run_hour}"
                            val = ec_vals.get(temp_type)
                            if col_name in df_sheet.columns and val is not None:
                                df_sheet.loc[target_date_str, col_name] = val

            df_sheet = df_sheet.sort_index(ascending=False)
            df_sheet.to_excel(writer, sheet_name=sheet_name)

    print("✅ Excel-database succesvol bijgewerkt en opgeslagen!")

# ==========================================
# 4. PLAYWRIGHT SCRAPER (WETTERZENTRALE)
# ==========================================
def scrape_single_run(page, loc_key, geoid, model_code, run_code):
    print(f"  -> Bezig met ophalen model **{model_code.upper()}** (Run **{run_code}z**) voor **{loc_key}** via Wetterzentrale...")
    url = f"https://www.wetterzentrale.de/nl/show_diagrams.php?geoid={geoid}&var=5&lid=ENS&model={model_code}&run={run_code}"
    
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(1.5)
    except Exception as e:
        print(f"❌ Time-out of netwerkfout bij laden pagina voor {loc_key} / {model_code} / {run_code}z: {e}")
        return None

    chart_series = page.evaluate("""() => {
        if (typeof Highcharts !== 'undefined' && Highcharts.charts.length > 0) {
            const chart = Highcharts.charts.find(c => c !== undefined);
            if (!chart || !chart.series || chart.series.length === 0) return null;
            return chart.series.map(s => ({
                name: s.name,
                data: s.data.map(d => ({ x: d.x, y: d.y }))
            }));
        }
        return null;
    }""")

    if not chart_series:
        print(f"⚠️ Geen grafiekdata gevonden voor {loc_key} / {model_code} / {run_code}z")
        return None

    oper_raw, avg_raw = {}, {}
    first_timestamp = None

    for series in chart_series:
        name = str(series['name']).strip().upper()
        
        # Wetterzentrale filters: OPER / HAUPTLAUF voor de hoofdrun, AVG / MEAN / GEM / ENSEMBLE voor het gemiddelde
        is_oper = ("OPER" in name or "HAUPTLAUF" in name)
        is_avg  = ("AVG" in name or "GEM" in name or "MEAN" in name or "ENSEMBLE" in name)

        if not (is_oper or is_avg):
            continue

        for point in series['data']:
            ts, val = point['x'], point['y']
            if val is None:
                continue
            if first_timestamp is None:
                first_timestamp = ts

            datum_dag = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
            
            if is_oper:
                oper_raw.setdefault(datum_dag, []).append(val)
            if is_avg:
                avg_raw.setdefault(datum_dag, []).append(val)

    if not first_timestamp or not oper_raw:
        print(f"⚠️ Geen geldige datapunten verwerkt voor {loc_key} / {model_code} / {run_code}z")
        return None

    first_dt_utc = datetime.fromtimestamp(first_timestamp / 1000, tz=timezone.utc)
    run_hour_int = int(run_code)
    run_date = first_dt_utc.date()
    if first_dt_utc.hour < run_hour_int:
        run_date = run_date - timedelta(days=1)

    dag_dict = {}
    all_dates = set(list(oper_raw.keys()) + list(avg_raw.keys()))

    for datum in all_dates:
        oper_vals = oper_raw.get(datum, [])
        avg_vals  = avg_raw.get(datum, [])

        dag_dict[datum] = {
            "Oper_Min": round(min(oper_vals), 1) if len(oper_vals) >= 2 else None,
            "Oper_Max": round(max(oper_vals), 1) if len(oper_vals) >= 2 else None,
            "Ens_Min":  round(min(avg_vals), 1) if len(avg_vals) >= 2 else None,
            "Ens_Max":  round(max(avg_vals), 1) if len(avg_vals) >= 2 else None
        }

    print(f"  -> Succesvol verwerkt: {loc_key} | {model_code.upper()} | {run_code}z")
    return {
        "location": loc_key,
        "model": model_code,
        "run_date": run_date,
        "run_hour": f"{run_code}z",
        "data": dag_dict
    }

def scrape_all():
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour
    
    print(f"🚀 Start van het scraper-proces op {now_utc.strftime('%Y-%m-%d %H:%M')} UTC...")
    
    include_actuals = (current_hour == 5)
    if include_actuals:
        print("🌅 Tijd is 05:00 UTC: Officiële metingen (KMI/KNMI) worden meegenomen.")
    else:
        print(f"ℹ️ Huidig uur is {current_hour} UTC. Metingen worden overgeslagen (draait om 05:00 UTC).")

    latest_runs = get_latest_available_runs()
    print(f"🎯 Berekende meest recente model-runs: {latest_runs}")
    
    knmi_ec_results = {}
    parsed_runs = []

    for loc_key, loc_info in LOCATIONS.items():
        if loc_info.get("knmi_ec"):
            ec_data = fetch_knmi_ecmwf_openmeteo(loc_info["lat"], loc_info["lon"])
            if ec_data:
                knmi_ec_results[loc_key] = ec_data

    print("🌐 Browser (Playwright) wordt gestart op de achtergrond...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for loc_key, loc_info in LOCATIONS.items():
            print(f"\n📍 Huidige locatie in behandeling: **{loc_key}**")
            for model_code in ["gfs", "ecm", "aifs"]:
                target_run = latest_runs[model_code]
                try:
                    run_result = scrape_single_run(page, loc_key, loc_info['geoid'], model_code, target_run)
                    if run_result:
                        parsed_runs.append(run_result)
                except Exception as e:
                    print(f"❌ Fout bij ophalen van {loc_key} voor model {model_code}: {e}")

        browser.close()
        print("🔒 Browser succesvol afgesloten.")

    update_matrix_excel(parsed_runs, knmi_ec_data=knmi_ec_results, include_actuals=include_actuals)
    print("✨ Scraper-taak volledig afgerond!")

if __name__ == "__main__":
    scrape_all()