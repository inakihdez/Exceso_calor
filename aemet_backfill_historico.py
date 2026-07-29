"""
Backfill del histórico de temperatura máxima AEMET.

Calcula la temperatura máxima nacional de cada uno de los últimos 24
meses completos (sin contar el mes actual, que aún no ha terminado) y
rellena aemet_temperatura_maxima_mensual.json de una sola vez.

Este script es de USO MANUAL, una única vez (o cuando se quiera
reconstruir el histórico desde cero). No está programado con cron: se
lanza a mano desde la pestaña Actions. Una vez completado el histórico,
el informe mensual normal (aemet_monthly_report.py) se encarga de ir
añadiendo un mes nuevo cada vez.
"""

import os
import time
from datetime import date

from aemet_monthly_report import (
    obtener_datos_mes,
    maximo_del_mes,
    cargar_historico,
    actualizar_historico,
    MESES_A_CONSERVAR,
    MESES_NOMBRE,
)


def meses_atras(n, hoy=None):
    """(año, mes) de hace n meses completos respecto a hoy, sin contar el mes actual."""
    hoy = hoy or date.today()
    total = hoy.year * 12 + (hoy.month - 1) - n
    anio = total // 12
    mes = total % 12 + 1
    return anio, mes


def main():
    api_key = os.environ.get("AEMET_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "AEMET_API_KEY está vacía o no definida. Comprueba el Secret en "
            "Settings → Secrets and variables → Actions."
        )

    historico = cargar_historico()
    print(f"Histórico actual: {len(historico)} meses ya guardados.")

    for n in range(MESES_A_CONSERVAR, 0, -1):
        anio, mes = meses_atras(n)
        nombre_mes = MESES_NOMBRE[mes]
        print(f"[{MESES_A_CONSERVAR - n + 1}/{MESES_A_CONSERVAR}] Procesando {nombre_mes} {anio}...")

        try:
            registros = obtener_datos_mes(api_key, anio, mes)
            resultado = maximo_del_mes(registros)
            historico = actualizar_historico(historico, anio, mes, resultado["temperatura"])
            print(f"  -> {resultado['temperatura']}°C en {resultado['estacion']} ({resultado['fecha']})")
        except Exception as e:
            print(f"  ⚠️ Error procesando {nombre_mes} {anio}: {e}. Se omite este mes.")

        time.sleep(2)  # pausa de cortesía entre meses para no saturar la API

    print(f"\n✓ Backfill completado. Histórico con {len(historico)} meses guardado.")


if __name__ == "__main__":
    main()
