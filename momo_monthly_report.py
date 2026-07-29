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

DESTINATARIOS = [
    "inakihernandez@europapress.es",
    "yonrecio@europapress.es",
]

MESES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre",
    11: "noviembre", 12: "diciembre",
}


def descargar_archivo(url=URL_DATOS, destino=ARCHIVO_DESCARGA, intentos=8):
    """
    Descarga el archivo del ISCIII a disco en streaming, con reintentos y
    reanudación (Range) si la conexión se corta a mitad de camino. El
    dataset completo puede pesar varios cientos de MB, así que una única
    petición sin reintentos suele fallar (ChunkedEncodingError/timeout).
    """
    session = requests.Session()

    for intento in range(1, intentos + 1):
        descargado_previo = os.path.getsize(destino) if os.path.exists(destino) else 0
        headers = {"Range": f"bytes={descargado_previo}-"} if descargado_previo else {}
        modo = "ab" if descargado_previo else "wb"

        try:
            with session.get(url, headers=headers, stream=True, timeout=(15, 180)) as resp:
                if resp.status_code == 416:
                    # El servidor dice que ya no queda nada más que descargar: hecho.
                    break
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()
                # Si pedimos Range y el servidor no lo soporta (200 en vez de 206),
                # reiniciamos desde cero para no duplicar contenido.
                if descargado_previo and resp.status_code == 200:
                    modo = "wb"
                    descargado_previo = 0

                with open(destino, modo) as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)

            print(f"  Intento {intento}: {os.path.getsize(destino) / 1e6:.1f} MB descargados hasta ahora.")
            content_length = resp.headers.get("Content-Length")
            content_range = resp.headers.get("Content-Range")  # formato: bytes start-end/total
            if content_range:
                total = int(content_range.split("/")[-1])
                if os.path.getsize(destino) >= total:
                    break
            elif content_length and not descargado_previo:
                break

        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as e:
            print(f"  Intento {intento} interrumpido ({e}). Reintentando desde el byte "
                  f"{os.path.getsize(destino) if os.path.exists(destino) else 0}...")
            time.sleep(5)
            continue
    else:
        raise RuntimeError(f"No se pudo completar la descarga tras {intentos} intentos.")

    return destino


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

    if resultado is None:
        asunto = f"⚠️ MoMo {nombre_mes} {anio}: sin datos todavía"
        cuerpo = (
            f"Todavía no hay datos consolidados de MoMo para {nombre_mes} de {anio}.\n"
            f"Puede que el ISCIII aún no haya publicado el mes completo.\n\n"
            f"Fuente: {URL_DATOS}"
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
            f"Fuente: {URL_DATOS}"
        )
        print(cuerpo)

    enviar_email(asunto, cuerpo)
    print("✓ Email enviado.")


if __name__ == "__main__":
    main()
