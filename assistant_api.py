"""Consultas patrimoniales de solo lectura para el asistente privado."""

import re
import unicodedata
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text


assistant_api = Blueprint("assistant_api", __name__)


def normalize(value):
    value = unicodedata.normalize("NFD", str(value or ""))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", value.lower())).strip()


def wants_detail(question):
    return bool(re.search(r"\b(cuales|cual|lista|listado|listalos|listalas|detalle|detalles|mostra|mostrame|mostralos|mostrar|ver|ids?|que bienes)\b", question))


def reply(body, details=None):
    result = {"text": body}
    if details:
        result["details"] = details
    return jsonify(result)


def resolve_place(conn, question, table, marker):
    rows = conn.execute(text(f"SELECT id, nombre FROM {table} ORDER BY nombre")).mappings().all()
    explicit = re.search(r"\b" + marker + r"\s+(.+)$", question)
    tail = explicit.group(1) if explicit else question
    tail = re.sub(r"^(de |del |la |el )+", "", tail)
    matches = []
    for row in rows:
        name = normalize(row["nombre"])
        short = re.split(r"\b(?:calle|dalmacio|copiapo|urquiza|rivadavia)\b", name)[0].strip()
        if (tail == str(row["id"]) or tail.startswith(str(row["id"]) + " ")
                or name in tail or (len(short) >= 5 and short in tail)):
            matches.append(row)
    exact = [row for row in matches if normalize(row["nombre"]) == tail]
    selected = exact[0] if len(exact) == 1 else matches[0] if len(matches) == 1 else None
    return bool(explicit or matches), selected


def count_inventory(conn, question):
    annex_requested, annex = resolve_place(conn, question, "anexos", "anexo")
    if annex_requested and not annex:
        return reply("No pude identificar un anexo único. Indicá el nombre o ID exacto.")
    sub_requested, sub = resolve_place(conn, question, "subdependencias", "subdependencia")
    if sub_requested and not sub:
        return reply("No pude identificar una subdependencia única. Indicá su nombre o ID exacto.")
    if annex and sub:
        belongs = conn.execute(text("SELECT 1 FROM subdependencias WHERE id=:sub AND id_anexo=:annex"), {"sub": sub["id"], "annex": annex["id"]}).first()
        if not belongs:
            return reply("Esa subdependencia no pertenece al anexo indicado.")
    match = re.search(r"(?:cuantos?|cuantas?|cantidad de|total de|hay)\s+(.+?)(?=\s+hay\b|\s+en\b|\s+del?\s+anexo\b|\s+de\s+la\s+subdependencia\b|$)", question)
    phrase = match.group(1) if match else ""
    phrase = re.sub(r"\b(bienes|mobiliarios|registrados|registradas|actualmente|que|con|problema|de|etiqueta|faltantes|para|reparacion|baja)\b", " ", phrase).strip()
    term = phrase.split()[0] if phrase else ""
    if len(term) > 5 and term.endswith("es"):
        term = term[:-2]
    elif len(term) > 4 and term.endswith("s"):
        term = term[:-1]
    flags = {"faltant": "faltante", "reparaci": "para_reparacion", "para baja": "para_baja", "sobrant": "sobrante", "problema de etiquet": "problema_etiqueta"}
    flag = next((column for keyword, column in flags.items() if keyword in question), None)
    filters = []
    params = {}
    if annex:
        filters.append("sd.id_anexo = :annex")
        params["annex"] = annex["id"]
    if sub:
        filters.append("m.ubicacion_id = :sub")
        params["sub"] = sub["id"]
    if flag:
        filters.append(f"m.{flag} = TRUE")
    if term:
        filters.append("(LOWER(COALESCE(m.descripcion,'')) LIKE :term OR LOWER(COALESCE(cb.descripcion,'')) LIKE :term)")
        params["term"] = f"%{term}%"
    where = " AND ".join(filters) if filters else "TRUE"
    base = " FROM mobiliario m LEFT JOIN subdependencias sd ON sd.id=m.ubicacion_id LEFT JOIN clases_bienes cb ON cb.id_clase=m.clase_bien_id WHERE " + where
    total = conn.execute(text("SELECT COUNT(*)" + base), params).scalar_one()
    place = sub["nombre"] if sub else annex["nombre"] if annex else "todo el sistema"
    subject = f"bienes cuya descripción o clase contiene '{term}'" if term else "bienes registrados"
    description = f"Hay {total} {subject}{' marcados como ' + flag.replace('_', ' ') if flag else ''} en {place}. Es un conteo de registros, no una verificación física."
    if not wants_detail(question):
        return reply(description)
    rows = conn.execute(text("SELECT m.id, m.descripcion FROM mobiliario m LEFT JOIN subdependencias sd ON sd.id=m.ubicacion_id LEFT JOIN clases_bienes cb ON cb.id_clase=m.clase_bien_id WHERE " + where + " ORDER BY m.id DESC LIMIT 8"), params).mappings().all()
    details = [{"label": f"ID {row['id']} · {row['descripcion'] or 'Sin descripción'}", "href": f"/patrimonio/guardado/{row['id']}"} for row in rows]
    return reply(description + (" Muestro los primeros 8." if total > 8 else ""), details)


def asset_by_id(conn, question):
    asset_id = re.search(r"\b(?:id|bien|mobiliario)\s+(\d{1,8})\b", question).group(1)
    row = conn.execute(text("""
        SELECT m.id, m.descripcion, m.estado_conservacion, m.faltante, m.problema_etiqueta,
               sd.nombre AS subdependencia, a.nombre AS anexo
        FROM mobiliario m LEFT JOIN subdependencias sd ON sd.id=m.ubicacion_id
        LEFT JOIN anexos a ON a.id=sd.id_anexo WHERE m.id=:id
    """), {"id": asset_id}).mappings().first()
    if not row:
        return reply(f"No encontré un bien con ID {asset_id}.")
    return reply(f"ID {asset_id}: {row['descripcion'] or 'Sin descripción'}. Ubicación: {row['subdependencia'] or 'sin subdependencia'}, {row['anexo'] or 'sin anexo'}. Estado: {row['estado_conservacion'] or 'sin cargar'}." + (" Marcado faltante." if row["faltante"] else "") + (" Con problema de etiqueta." if row["problema_etiqueta"] else ""), [{"label": "Abrir ficha del bien", "href": f"/patrimonio/guardado/{asset_id}"}])


def movements(conn, question):
    today = datetime.now().date()
    if "semana" in question:
        since = today - timedelta(days=7)
    elif "mes" in question:
        since = today.replace(day=1)
    elif "hoy" in question:
        since = today
    else:
        since = today - timedelta(days=30)
    rows = conn.execute(text("""
        SELECT id_registro, fecha, datos_anteriores, datos_nuevos FROM auditoria
        WHERE tabla_afectada='mobiliario' AND fecha >= :since
        ORDER BY fecha DESC, id DESC LIMIT 2001
    """), {"since": since}).mappings().all()
    truncated = len(rows) > 2000
    places = {str(row["id"]): row for row in conn.execute(text("SELECT sd.id, sd.nombre, sd.id_anexo FROM subdependencias sd")).mappings()}
    annex_requested, annex = resolve_place(conn, question, "anexos", "anexo")
    sub_requested, sub = resolve_place(conn, question, "subdependencias", "subdependencia")
    if (annex_requested and not annex) or (sub_requested and not sub):
        return reply("No pude identificar un anexo o subdependencia único para filtrar movimientos.")
    found = []
    for row in rows[:2000]:
        before, after = row["datos_anteriores"], row["datos_nuevos"]
        if isinstance(before, str):
            try:
                import json
                before = json.loads(before)
            except (ValueError, TypeError):
                continue
        if isinstance(after, str):
            try:
                import json
                after = json.loads(after)
            except (ValueError, TypeError):
                continue
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        old_id, new_id = str(before.get("ubicacion_id") or ""), str(after.get("ubicacion_id") or "")
        if not old_id or not new_id or old_id == new_id:
            continue
        old, new = places.get(old_id), places.get(new_id)
        if annex and not any(place and place["id_anexo"] == annex["id"] for place in (old, new)):
            continue
        if sub and str(sub["id"]) not in (old_id, new_id):
            continue
        found.append((row, old, new))
    place = sub["nombre"] if sub else annex["nombre"] if annex else "el sistema"
    summary = f"{'Al menos ' if truncated else ''}{len(found)} movimientos de ubicación registrados en {place} desde {since.isoformat()}."
    if truncated:
        summary += " Hay más de 2.000 eventos; pedí un período menor para obtener el total exacto."
    if not wants_detail(question):
        return reply(summary)
    details = [{"label": f"ID {row['id_registro']} · {old['nombre'] if old else 'Origen desconocido'} → {new['nombre'] if new else 'Destino desconocido'} · {row['fecha']}", "href": f"/patrimonio/guardado/{row['id_registro']}"} for row, old, new in found[:10]]
    return reply(summary + (" Muestro los primeros 10." if len(found) > 10 else ""), details)


def reports(conn, question):
    if re.search(r"\b(compra|compras|proveedor|factura)\b", question):
        month_names = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        month = next((index + 1 for index, name in enumerate(month_names) if name in question), None)
        year = re.search(r"\b20\d{2}\b", question)
        params = {"month": month, "year": int(year.group()) if year else None}
        result = conn.execute(text("""
            SELECT COUNT(DISTINCT r.id) AS reports, COUNT(i.id) AS rows, COALESCE(SUM(i.cantidad),0) AS units
            FROM compras_bienes_reportes r LEFT JOIN compras_bienes_items i ON i.reporte_id=r.id
            WHERE (:month IS NULL OR r.mes=:month) AND (:year IS NULL OR r.anio=:year)
        """), params).mappings().one()
        summary = f"{result['reports']} reportes de compras, {result['rows']} filas y {result['units']} unidades."
        if "proveedor" in question:
            providers = conn.execute(text("""
                SELECT COALESCE(NULLIF(TRIM(i.proveedor),''),'Sin proveedor') AS nombre, COUNT(*) AS total
                FROM compras_bienes_items i JOIN compras_bienes_reportes r ON r.id=i.reporte_id
                WHERE (:month IS NULL OR r.mes=:month) AND (:year IS NULL OR r.anio=:year)
                GROUP BY 1 ORDER BY total DESC LIMIT 20
            """), params).mappings().all()
            return reply(summary + " Proveedores (filas): " + ("; ".join(f"{row['nombre']}: {row['total']}" for row in providers) or "ninguno") + ".")
        if not wants_detail(question):
            return reply(summary)
        rows = conn.execute(text("""
            SELECT r.anio, r.mes, r.observaciones, COUNT(i.id) AS filas, COALESCE(SUM(i.cantidad),0) AS unidades
            FROM compras_bienes_reportes r LEFT JOIN compras_bienes_items i ON i.reporte_id=r.id
            WHERE (:month IS NULL OR r.mes=:month) AND (:year IS NULL OR r.anio=:year)
            GROUP BY r.id ORDER BY r.anio DESC, r.mes DESC LIMIT 8
        """), params).mappings().all()
        return reply(summary, [{"label": f"{month_names[row['mes']-1]} {row['anio']}: {row['filas']} filas, {row['unidades']} unidades", "href": "/ComprasBienes"} for row in rows])
    if re.search(r"\b(alta|altas)\b", question):
        year = re.search(r"\b20\d{2}\b", question)
        result = conn.execute(text("SELECT COUNT(*) AS total, COALESCE(SUM(cantidad),0) AS unidades FROM movimientos_altas WHERE (:year IS NULL OR anio_planilla=:year)"), {"year": year.group() if year else None}).mappings().one()
        return reply(f"{result['total']} movimientos de alta y {result['unidades']} unidades registradas" + (f" en {year.group()}" if year else "") + ".")
    if re.search(r"\b(matafuego|matafuegos|extintor|extintores)\b", question):
        annex_requested, annex = resolve_place(conn, question, "anexos", "anexo")
        if annex_requested and not annex:
            return reply("No pude identificar un anexo único para filtrar matafuegos.")
        result = conn.execute(text("""
            SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE estado='activo') AS activos,
                   COUNT(*) FILTER (WHERE estado='activo' AND fecha_vencimiento < CURRENT_DATE) AS vencidos
            FROM matafuegos WHERE (:annex IS NULL OR id_anexo=:annex)
        """), {"annex": annex["id"] if annex else None}).mappings().one()
        return reply(f"{result['total']} matafuegos registrados{' en ' + annex['nombre'] if annex else ' en el sistema'}; {result['activos']} activos y {result['vencidos']} activos vencidos.")
    total = conn.execute(text("SELECT COUNT(*) FROM mobiliario")).scalar_one()
    annexes = conn.execute(text("SELECT COUNT(*) FROM anexos")).scalar_one()
    subs = conn.execute(text("SELECT COUNT(*) FROM subdependencias")).scalar_one()
    return reply(f"Reporte actual: {total} bienes, {annexes} anexos y {subs} subdependencias.", [{"label": "Abrir reportes", "href": "/reportes"}])


def register_assistant(db, admin_required_api, ensure_compras_tables, ensure_matafuegos_tables):
    @assistant_api.post("/api/asistente/consulta")
    @admin_required_api
    def consult():
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question") or "").strip()
        if not question or len(question) > 400:
            return jsonify({"error": "La pregunta debe tener entre 1 y 400 caracteres."}), 400
        normalized = normalize(question)
        try:
            if re.search(r"\b(compra|compras|proveedor|factura)\b", normalized):
                ensure_compras_tables()
            if re.search(r"\b(matafuego|matafuegos|extintor|extintores)\b", normalized):
                ensure_matafuegos_tables()
            with db.engine.connect() as conn:
                if re.search(r"\b(movimientos?|traslados?|pases?|cambios? de ubicacion|movieron|trasladaron)\b", normalized):
                    return movements(conn, normalized)
                if re.search(r"\b(?:id|bien|mobiliario)\s+\d{1,8}\b", normalized):
                    return asset_by_id(conn, normalized)
                if re.search(r"\b(reporte|reportes|compra|compras|proveedor|factura|alta|altas|matafuego|matafuegos|extintor|extintores)\b", normalized):
                    return reports(conn, normalized)
                if re.search(r"\b(cuantos?|cuantas?|cantidad|total|hay|faltantes|etiqueta|reparacion)\b", normalized):
                    return count_inventory(conn, normalized)
            return reply("No puedo verificar esa pregunta con los datos disponibles. Preguntá por inventario, ubicaciones, movimientos, compras, altas, matafuegos o reportes.")
        except Exception:
            current_app.logger.exception("Error en consulta patrimonial")
            return jsonify({"error": "No se pudo consultar el sistema en este momento."}), 500
