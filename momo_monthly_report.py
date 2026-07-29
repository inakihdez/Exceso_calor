"""
Informe mensual automático MoMo (ISCIII) — exceso/déficit de mortalidad por
temperaturas extremas a nivel nacional.

Se ejecuta el día 1 de cada mes vía GitHub Actions y envía por email el dato
del mes anterior. Reutiliza la lógica de lectura/parseo de momo.py.
"""

import os
import time
import zipfile
import smtplib
import ssl
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

from momo import _leer_isciii

URL_DATOS = "https://momo.isciii.es/public/momo/data"
ARCHIVO_DESCARGA = "momo_data.tmp"  # el dataset completo pesa varios cientos de MB
URL_EPDATA = "https://www.epdata.es/datos/muertes-atribuidas-exceso-calor-graficos-estadisticas/679?accion=2"

DESTINATARIOS = [
    "inakihernandez@europapress.es",
    "yonrecio@europapress.es",
]

MESES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre",
    11: "noviembre", 12: "diciembre",
}


def descargar_archivo(url=URL_DATOS, destino=ARCHIVO_DESCARGA, intentos=6):
    """
    Descarga el archivo del ISCIII a disco en streaming, con reintentos
    completos si la conexión se corta.

    El servidor del ISCIII ignora el header Range (siempre devuelve el
    archivo completo aunque se pida un trozo), así que no es posible
    reanudar una descarga parcial: si falla, se reintenta desde cero.
    """
    for intento in range(1, intentos + 1):
        try:
            with requests.Session() as session:
                with session.get(url, stream=True, timeout=(15, 300)) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length", 0))
                    descargado = 0
                    with open(destino, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=5 * 1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                descargado += len(chunk)
                                print(f"  {descargado / 1e6:.1f} MB descargados"
                                      f"{f' / {total / 1e6:.1f} MB' if total else ''}...")

                    if total and descargado < total:
                        raise requests.exceptions.ChunkedEncodingError(
                            f"Descarga incompleta: {descargado} de {total} bytes"
                        )

            print(f"✓ Descarga completa: {os.path.getsize(destino) / 1e6:.1f} MB")
            return destino

        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout) as e:
            print(f"Intento {intento} fallido ({e}). Reintentando desde cero...")
            if os.path.exists(destino):
                os.remove(destino)
            time.sleep(5)

    raise RuntimeError(f"No se pudo completar la descarga tras {intentos} intentos.")


def extraer_csv(ruta_archivo):
    """Si el archivo descargado es un ZIP, extrae el CSV de mayor tamaño dentro. Si no, lo devuelve tal cual."""
    with open(ruta_archivo, "rb") as f:
        firma = f.read(2)

    if firma == b"PK":  # firma de archivo ZIP
        with zipfile.ZipFile(ruta_archivo) as z:
            candidatos = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not candidatos:
                raise ValueError(
                    f"El ZIP descargado no contiene ningún CSV. Archivos: {z.namelist()}"
                )
            candidatos.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
            destino_csv = "momo_data.csv"
            with z.open(candidatos[0]) as origen, open(destino_csv, "wb") as f_out:
                f_out.write(origen.read())
            return destino_csv

    return ruta_archivo


def resumen_mes_nacional(archivo, anio, mes):
    """Suma nacional (sexo=all, edad=all) de exceso y déficit para un año/mes."""
    df = _leer_isciii(archivo)

    df_mes = df[
        (df["ambito"].astype(str).str.strip() == "nacional")
        & (df["cod_sexo"].astype(str).str.strip() == "all")
        & (df["cod_gedad"].astype(str).str.strip() == "all")
        & (df["fecha_defuncion"].dt.year == anio)
        & (df["fecha_defuncion"].dt.month == mes)
    ]

    if len(df_mes) == 0:
        return None

    exceso = round(df_mes["defunciones_atrib_exc_temp"].sum(), 2)
    deficit = round(df_mes["defunciones_atrib_def_temp"].sum(), 2)
    return exceso, deficit


def serie_historica_nacional(archivo):
    """
    Genera la serie histórica mensual completa a nivel nacional (sexo=all,
    edad=all), en formato ancho: una fila por año/mes con las dos causas
    (exceso de calor / exceso de frío) en columnas.
    """
    df = _leer_isciii(archivo)

    df_filtrado = df[
        (df["ambito"].astype(str).str.strip() == "nacional")
        & (df["cod_sexo"].astype(str).str.strip() == "all")
        & (df["cod_gedad"].astype(str).str.strip() == "all")
    ].copy()

    df_filtrado["Año"] = df_filtrado["fecha_defuncion"].dt.year
    df_filtrado["Periodo"] = df_filtrado["fecha_defuncion"].dt.month

    resumen = (
        df_filtrado.groupby(["Año", "Periodo"], as_index=False)
        .agg(
            exceso=("defunciones_atrib_exc_temp", "sum"),
            deficit=("defunciones_atrib_def_temp", "sum"),
        )
    )
    resumen["exceso"] = resumen["exceso"].round(0).astype(int)
    resumen["deficit"] = resumen["deficit"].round(0).astype(int)
    resumen = resumen.sort_values(["Año", "Periodo"]).reset_index(drop=True)
    resumen.insert(0, "Territorio", "España")
    return resumen


def formatear_serie_texto(resumen):
    """Convierte la serie histórica en texto plano con columnas separadas por ';'."""
    lineas = ["Territorio;Año;Periodo;Muertes;Causa de muerte;Muertes;Causa de muerte"]
    for _, fila in resumen.iterrows():
        lineas.append(
            f"{fila['Territorio']};{fila['Año']};{fila['Periodo']};"
            f"{fila['exceso']};Exceso de calor;{fila['deficit']};Exceso de frío"
        )
    return "\n".join(lineas)


def mes_anterior(hoy=None):
    hoy = hoy or date.today()
    anio, mes = hoy.year, hoy.month
    if mes == 1:
        return anio - 1, 12
    return anio, mes - 1


def enviar_email(asunto, cuerpo):
    remitente = os.environ["EMAIL_USER"]
    password = os.environ["EMAIL_PASS"]

    msg = MIMEMultipart()
    msg["From"] = remitente
    msg["To"] = ", ".join(DESTINATARIOS)
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

    contexto = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=contexto)
        server.login(remitente, password)
        server.sendmail(remitente, DESTINATARIOS, msg.as_string())


def main():
    anio, mes = mes_anterior()
    nombre_mes = MESES_NOMBRE[mes]

    print(f"Descargando datos ISCIII desde {URL_DATOS}...")
    ruta_descargada = descargar_archivo()
    ruta_csv = extraer_csv(ruta_descargada)
    print(f"Archivo listo para procesar: {ruta_csv}")

    resultado = resumen_mes_nacional(ruta_csv, anio, mes)
    serie = serie_historica_nacional(ruta_csv)
    texto_serie = formatear_serie_texto(serie)

    if resultado is None:
        asunto = f"⚠️ MoMo {nombre_mes} {anio}: sin datos todavía"
        cuerpo = (
            f"Todavía no hay datos consolidados de MoMo para {nombre_mes} de {anio}.\n"
            f"Puede que el ISCIII aún no haya publicado el mes completo.\n\n"
            f"Fuente: {URL_DATOS}\n\n"
            f"Esta tabla actualiza los gráficos de esta plantilla de EpData:\n"
            f"{URL_EPDATA}\n\n"
            f"Serie histórica nacional disponible hasta la fecha:\n\n"
            f"{texto_serie}"
        )
        print("Sin datos para el mes. Enviando aviso.")
    else:
        exceso, deficit = resultado
        asunto = (
            f"MoMo {nombre_mes.capitalize()} {anio}: "
            f"exceso {exceso:.0f} / déficit {deficit:.0f}"
        )
        cuerpo = (
            f"Mortalidad atribuida a temperaturas extremas — "
            f"{nombre_mes} de {anio} (España, nacional)\n"
            f"{'-' * 60}\n\n"
            f"Exceso de temperatura:  {exceso:,.0f} defunciones atribuidas\n"
            f"Déficit de temperatura: {deficit:,.0f} defunciones atribuidas\n\n"
            f"Nota: dato provisional del ISCIII, sujeto a revisión en semanas posteriores.\n"
            f"Fuente: {URL_DATOS}\n\n"
            f"Esta tabla actualiza los gráficos de esta plantilla de EpData:\n"
            f"{URL_EPDATA}\n\n"
            f"{'-' * 60}\n"
            f"Serie histórica nacional completa (mensual):\n\n"
            f"{texto_serie}"
        )
        print(f"Exceso {nombre_mes}: {exceso:.0f} / Déficit: {deficit:.0f}")

    enviar_email(asunto, cuerpo)
    print("✓ Email enviado.")


if __name__ == "__main__":
    main()
