"""
Normalizador: MoMo – Mortalidad por temperaturas extremas (CNE/ISCIII)
Fuente: https://momo.isciii.es/

MODO 1 – Serie nacional mensual (uso normal):
  python momo.py archivo_isciii.csv
  → genera momo_exceso_temperatura.csv y momo_deficit_temperatura.csv

MODO 2 – Actualizar CSV de CCAA por mes (para gráfico de barras EpData):
  python momo.py archivo_isciii.csv --ccaa 2026-08 ccaa_existente.csv
  → añade la columna 2026-08 al CSV existente y lo sobreescribe
"""

import pandas as pd
import io

# Mapa nombre CCAA del ISCIII → nombre en el CSV de EpData
# Ajustar si hay diferencias entre versiones del archivo
NOMBRES_CCAA = {
    "Andalucía":                    "Andalucía",
    "Aragón":                       "Aragón",
    "Asturias, Principado de":      "Asturias, Principado de",
    "Balears, Illes":               "Balears, Illes",
    "Canarias":                     "Canarias",
    "Cantabria":                    "Cantabria",
    "Castilla - La Mancha":         "Castilla - La Mancha",
    "Castilla y León":              "Castilla y León",
    "Cataluña":                     "Cataluña",
    "Ceuta":                        "Ceuta",
    "Comunitat Valenciana":         "Comunitat Valenciana",
    "Extremadura":                  "Extremadura",
    "Galicia":                      "Galicia",
    "Madrid, Comunidad de":         "Madrid, Comunidad de",
    "Melilla":                      "Melilla",
    "Murcia, Región de":            "Murcia, Región de",
    "Navarra, Comunidad Foral de":  "Navarra, Comunidad Foral de",
    "País Vasco":                   "País Vasco",
    "Rioja, La":                    "Rioja, La",
}


def _leer_isciii(archivo):
    """Lee el CSV del ISCIII con detección automática de encoding."""
    try:
        df = pd.read_csv(archivo, encoding="latin-1")
    except Exception:
        try:
            archivo.seek(0)
        except AttributeError:
            pass
        df = pd.read_csv(archivo, encoding="utf-8")

    cols_requeridas = {
        "ambito", "nombre_ambito", "cod_sexo", "cod_gedad",
        "fecha_defuncion", "defunciones_atrib_exc_temp", "defunciones_atrib_def_temp"
    }
    faltantes = cols_requeridas - set(df.columns)
    if faltantes:
        raise ValueError(
            f"El archivo no tiene las columnas esperadas: {', '.join(faltantes)}.\n"
            f"Columnas encontradas: {', '.join(df.columns)}"
        )

    df["fecha_defuncion"] = pd.to_datetime(df["fecha_defuncion"])

    for col in ["defunciones_atrib_exc_temp", "defunciones_atrib_def_temp"]:
        df[col] = (
            df[col].astype(str)
            .str.replace(",", ".", regex=False)
            .str.strip()
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def normalizar(archivo):
    """MODO 1: Serie nacional mensual → dos CSVs EpData."""
    df = _leer_isciii(archivo)

    df_filtrado = df[
        (df["ambito"].astype(str).str.strip() == "nacional") &
        (df["cod_sexo"].astype(str).str.strip() == "all") &
        (df["cod_gedad"].astype(str).str.strip() == "all")
    ].copy()

    if len(df_filtrado) == 0:
        raise ValueError(
            "No hay filas con ambito='nacional', cod_sexo='all' y cod_gedad='all'."
        )

    df_filtrado["año"] = df_filtrado["fecha_defuncion"].dt.year
    df_filtrado["periodo"] = df_filtrado["fecha_defuncion"].dt.month
    df_filtrado["territorio"] = "España"

    grupo = ["territorio", "año", "periodo"]

    def build_epdata(df_input, col_dato, nombre_variable):
        df_out = (
            df_input
            .groupby(grupo, as_index=False)[col_dato]
            .sum()
            .rename(columns={col_dato: "dato"})
        )
        df_out["dato"] = df_out["dato"].round(2)
        df_out["tipo de temperatura"] = nombre_variable
        return df_out[["territorio", "año", "periodo", "dato", "tipo de temperatura"]]

    df_exc = build_epdata(df_filtrado, "defunciones_atrib_exc_temp", "exceso de temperatura")
    df_def = build_epdata(df_filtrado, "defunciones_atrib_def_temp", "déficit de temperatura")

    return [
        {"nombre": "momo_exceso_temperatura", "df": df_exc},
        {"nombre": "momo_deficit_temperatura", "df": df_def},
    ]


def extraer_ccaa(archivo_isciii, mes):
    """
    MODO 2: Extrae muertes por exceso de calor por CCAA para un mes concreto
    y genera un CSV nuevo en formato EpData (columnas: Año, Periodo, Parámetro, YYYY-MM).

    mes: string formato 'YYYY-MM', ej. '2026-06'
    """
    anio, num_mes = int(mes.split("-")[0]), int(mes.split("-")[1])

    df = _leer_isciii(archivo_isciii)

    # Filtrar: CCAA, sexo=all, edad=all, mes concreto
    df_ccaa = df[
        (df["ambito"].astype(str).str.strip() == "ccaa") &
        (df["cod_sexo"].astype(str).str.strip() == "all") &
        (df["cod_gedad"].astype(str).str.strip() == "all") &
        (df["fecha_defuncion"].dt.year == anio) &
        (df["fecha_defuncion"].dt.month == num_mes)
    ].copy()

    if len(df_ccaa) == 0:
        raise ValueError(
            f"No hay datos de CCAA para el mes {mes} con los filtros aplicados."
        )

    # Sumar todos los días del mes por CCAA
    df_suma = (
        df_ccaa
        .groupby("nombre_ambito", as_index=False)["defunciones_atrib_exc_temp"]
        .sum()
        .rename(columns={"nombre_ambito": "Parámetro", "defunciones_atrib_exc_temp": mes})
    )
    df_suma[mes] = df_suma[mes].round(0).astype(int)

    # Extraer también el mismo mes del año anterior
    mes_anterior = f"{anio - 1}-{str(num_mes).zfill(2)}"
    df_ccaa_ant = df[
        (df["ambito"].astype(str).str.strip() == "ccaa") &
        (df["cod_sexo"].astype(str).str.strip() == "all") &
        (df["cod_gedad"].astype(str).str.strip() == "all") &
        (df["fecha_defuncion"].dt.year == anio - 1) &
        (df["fecha_defuncion"].dt.month == num_mes)
    ].copy()

    if len(df_ccaa_ant) > 0:
        df_suma_ant = (
            df_ccaa_ant
            .groupby("nombre_ambito", as_index=False)["defunciones_atrib_exc_temp"]
            .sum()
            .rename(columns={"nombre_ambito": "Parámetro", "defunciones_atrib_exc_temp": mes_anterior})
        )
        df_suma_ant[mes_anterior] = df_suma_ant[mes_anterior].round(0).astype(int)
        df_suma = df_suma.merge(df_suma_ant, on="Parámetro", how="left")
        # Reordenar: año anterior primero, luego el actual
        df_suma = df_suma[["Parámetro", mes_anterior, mes]]
    else:
        print(f"⚠️  No hay datos para {mes_anterior} en el archivo.")

    # Añadir columnas de año y periodo en formato EpData
    meses_nombre = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
    }
    df_suma.insert(0, "Año", str(anio))
    df_suma.insert(1, "Periodo", meses_nombre[num_mes])

    # Ordenar por nombre de CCAA
    df_suma = df_suma.sort_values("Parámetro").reset_index(drop=True)

    return df_suma


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    ruta_isciii = Path(sys.argv[1])
    if not ruta_isciii.exists():
        print(f"Error: no se encuentra '{ruta_isciii}'")
        sys.exit(1)

    # ── MODO 2: --ccaa YYYY-MM ──
    if "--ccaa" in sys.argv:
        idx = sys.argv.index("--ccaa")
        if len(sys.argv) < idx + 2:
            print("Uso: python momo.py archivo_isciii.csv --ccaa 2026-06")
            sys.exit(1)

        mes = sys.argv[idx + 1]

        print(f"Extrayendo datos de CCAA para {mes} desde {ruta_isciii.name}...")
        df_nuevo = extraer_ccaa(ruta_isciii, mes)

        nombre_salida = Path(__file__).parent / f"momo_ccaa_{mes}.csv"
        df_nuevo.to_csv(nombre_salida, sep=";", index=False, encoding="utf-8-sig")
        print(df_nuevo.to_string(index=False))
        print(f"\n✓ {nombre_salida.name} — {len(df_nuevo)} CCAA")

    # ── MODO 1: serie nacional mensual ──
    else:
        print(f"Procesando {ruta_isciii.name}...")
        resultados = normalizar(ruta_isciii)
        for tabla in resultados:
            nombre_salida = Path(__file__).parent / f"{tabla['nombre']}.csv"
            tabla["df"].to_csv(nombre_salida, index=False, sep="\t", decimal=",", encoding="utf-8")
            print(f"✓ {nombre_salida.name} — {len(tabla['df'])} filas")