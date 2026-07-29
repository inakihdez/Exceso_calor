"""
Informe mensual automático MoMo (ISCIII) — exceso/déficit de mortalidad por
temperaturas extremas a nivel nacional.

Se ejecuta el día 1 de cada mes vía GitHub Actions y envía por email el dato
del mes anterior. Reutiliza la lógica de lectura/parseo de momo.py.
"""

import os
import io
import zipfile
import smtplib
import ssl
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

from momo import _leer_isciii

URL_DATOS = "https://momo.isciii.es/public/momo/data"

DESTINATARIOS = [
    "inakihernandez@europapress.es",
    "yonrecio@europapress.es",
]

MESES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre",
    11: "noviembre", 12: "diciembre",
}


def descargar_csv_isciii(url=URL_DATOS):
    """
    Descarga el archivo de datos del ISCIII.

    El endpoint público de MoMo puede devolver el CSV directamente o un ZIP
    que lo contiene, según el momento; se gestionan ambos casos.
    """
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    contenido = resp.content

    if contenido[:2] == b"PK":  # firma de archivo ZIP
        with zipfile.ZipFile(io.BytesIO(contenido)) as z:
            candidatos = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not candidatos:
                raise ValueError(
                    f"El ZIP descargado no contiene ningún CSV. Archivos: {z.namelist()}"
                )
            # Si hay varios CSV, se asume que el de detalle diario es el más grande
            candidatos.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
            with z.open(candidatos[0]) as f:
                return io.BytesIO(f.read())

    return io.BytesIO(contenido)


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
    archivo = descargar_csv_isciii()

    resultado = resumen_mes_nacional(archivo, anio, mes)

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
