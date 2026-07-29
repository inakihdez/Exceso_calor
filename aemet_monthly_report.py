"""
Informe mensual automático AEMET — temperatura máxima registrada en España
en el mes anterior (todas las estaciones).

Se ejecuta el día 1 de cada mes vía GitHub Actions. Consulta la API de
AEMET OpenData para el mes anterior completo, encuentra la temperatura
máxima diaria más alta de todas las estaciones, actualiza un histórico de
los últimos 24 meses (guardado en JSON dentro del repo) y envía un email
con el dato destacado y la tabla de esos 24 meses.

Requiere la variable de entorno AEMET_API_KEY con una API key de
https://opendata.aemet.es/
"""

import os
import json
import smtplib
import ssl
import calendar
from datetime import date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

BASE_URL = "https://opendata.aemet.es/opendata"
ENDPOINT_DIARIOS = (
    BASE_URL + "/api/valores/climatologicos/diarios/datos/fechaini/"
    "{fecha_ini}/fechafin/{fecha_fin}/todasestaciones"
)

ARCHIVO_HISTORICO = "aemet_temperatura_maxima_mensual.json"
MESES_A_CONSERVAR = 24
URL_EPDATA = "https://www.epdata.es/datos/temperaturas-hoy-espana-historico-calor-maximo-registrado/401/espana/106"

DESTINATARIOS = [
    "inakihernandez@europapress.es",
    "yonrecio@europapress.es",
]

MESES_NOMBRE = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre",
    11: "noviembre", 12: "diciembre",
}


def mes_anterior(hoy=None):
    hoy = hoy or date.today()
    anio, mes = hoy.year, hoy.month
    if mes == 1:
        return anio - 1, 12
    return anio, mes - 1


def obtener_datos_mes(api_key, anio, mes):
    """
    Descarga los datos climatológicos diarios de todas las estaciones para
    un mes completo.

    El endpoint de AEMET no admite rangos de más de 15 días, así que el
    mes se trocea en bloques de como mucho 15 días y se combinan los
    resultados.
    """
    primer_dia = date(anio, mes, 1)
    ultimo_dia = date(anio, mes, calendar.monthrange(anio, mes)[1])

    registros = []
    inicio_bloque = primer_dia
    while inicio_bloque <= ultimo_dia:
        fin_bloque = min(inicio_bloque + timedelta(days=14), ultimo_dia)
        registros.extend(obtener_datos_rango(api_key, inicio_bloque, fin_bloque))
        inicio_bloque = fin_bloque + timedelta(days=1)

    return registros


def obtener_datos_rango(api_key, dia_inicio, dia_fin):
    """Descarga los datos climatológicos diarios de todas las estaciones para un rango de hasta 15 días."""
    fecha_ini = f"{dia_inicio.isoformat()}T00:00:00UTC"
    fecha_fin = f"{dia_fin.isoformat()}T23:59:59UTC"

    url = ENDPOINT_DIARIOS.format(fecha_ini=fecha_ini, fecha_fin=fecha_fin)
    headers = {"api_key": api_key, "Accept": "application/json"}

    resp = session_get_con_reintentos(url, headers=headers)
    payload = resp.json()

    if payload.get("estado") != 200:
        raise RuntimeError(
            f"AEMET devolvió estado {payload.get('estado')}: {payload.get('descripcion')}"
        )

    resp_datos = session_get_con_reintentos(payload["datos"])
    return resp_datos.json()


def session_get_con_reintentos(url, headers=None, intentos=5, timeout=60):
    ultima_excepcion = None
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 401:
                # Error de autenticación: no tiene sentido reintentar, siempre va a fallar igual.
                raise RuntimeError(
                    f"401 Unauthorized de AEMET. Cuerpo de la respuesta: {resp.text[:500]!r}\n"
                    f"Revisa que el Secret AEMET_API_KEY esté bien configurado (no vacío, sin "
                    f"espacios ni comillas de más)."
                )
            resp.raise_for_status()
            return resp
        except RuntimeError:
            raise
        except requests.exceptions.RequestException as e:
            ultima_excepcion = e
            print(f"  Intento {intento} fallido al pedir {url}: {e}")
    raise RuntimeError(f"No se pudo completar la petición tras {intentos} intentos.") from ultima_excepcion


def maximo_del_mes(registros):
    """Encuentra el registro con la temperatura máxima (tmax) más alta de todas las estaciones/días."""
    mejor = None
    for r in registros:
        valor_str = r.get("tmax")
        if not valor_str:
            continue
        try:
            valor = float(str(valor_str).replace(",", "."))
        except ValueError:
            continue
        if mejor is None or valor > mejor["temperatura"]:
            mejor = {
                "temperatura": valor,
                "estacion": r.get("nombre", "estación desconocida"),
                "provincia": r.get("provincia", ""),
                "fecha": r.get("fecha", ""),
            }

    if mejor is None:
        raise ValueError("No se ha encontrado ningún valor de tmax válido en los datos del mes.")

    return mejor


def cargar_historico():
    if os.path.exists(ARCHIVO_HISTORICO):
        with open(ARCHIVO_HISTORICO, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def actualizar_historico(historico, anio, mes, temperatura):
    # Quitar cualquier entrada previa del mismo año/mes (por si se relanza el workflow)
    historico = [h for h in historico if not (h["anio"] == anio and h["periodo"] == mes)]
    historico.append({"anio": anio, "periodo": mes, "temperatura": round(temperatura, 1)})
    historico.sort(key=lambda h: (h["anio"], h["periodo"]))
    historico = historico[-MESES_A_CONSERVAR:]

    with open(ARCHIVO_HISTORICO, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)

    return historico


def formatear_historico_texto(historico):
    lineas = ["Territorio;Año;Periodo;Temperatura máxima registrada"]
    for h in historico:
        temp_str = str(h["temperatura"]).replace(".", ",")
        lineas.append(f"España;{h['anio']};{h['periodo']};{temp_str}")
    return "\n".join(lineas)


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
    api_key = os.environ.get("AEMET_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "AEMET_API_KEY está vacía o no definida. Comprueba el Secret en "
            "Settings → Secrets and variables → Actions."
        )

    anio, mes = mes_anterior()
    nombre_mes = MESES_NOMBRE[mes]

    print(f"Consultando AEMET para {nombre_mes} de {anio} (todas las estaciones)...")
    registros = obtener_datos_mes(api_key, anio, mes)
    print(f"  {len(registros)} registros diarios recibidos.")

    resultado = maximo_del_mes(registros)
    print(f"  Máxima del mes: {resultado['temperatura']}°C en {resultado['estacion']} "
          f"({resultado['fecha']})")

    historico = cargar_historico()
    historico = actualizar_historico(historico, anio, mes, resultado["temperatura"])
    texto_historico = formatear_historico_texto(historico)

    asunto = (
        f"AEMET {nombre_mes.capitalize()} {anio}: máxima de "
        f"{resultado['temperatura']:.1f}°C en {resultado['estacion']}"
    )
    provincia_txt = f" ({resultado['provincia']})" if resultado["provincia"] else ""
    cuerpo = (
        f"Temperatura máxima registrada en España — {nombre_mes} de {anio}\n"
        f"{'-' * 60}\n\n"
        f"{resultado['temperatura']:.1f}°C en {resultado['estacion']}{provincia_txt}, "
        f"el {resultado['fecha']}\n\n"
        f"Fuente: AEMET OpenData (https://opendata.aemet.es/)\n\n"
        f"Esta tabla actualiza los gráficos de esta plantilla de EpData:\n"
        f"{URL_EPDATA}\n\n"
        f"{'-' * 60}\n"
        f"Temperatura máxima mensual — últimos {len(historico)} meses:\n\n"
        f"{texto_historico}"
    )
    print(cuerpo)

    enviar_email(asunto, cuerpo)
    print("✓ Email enviado.")


if __name__ == "__main__":
    main()
