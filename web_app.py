import os
import sqlite3
import json
import psycopg2
import secrets
import io
import uuid
from datetime import datetime, date
from calendar import monthrange
from flask import Flask, request, render_template_string, session, redirect, url_for, send_file, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A5, LETTER, LEGAL
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from PIL import Image
from functools import wraps

# ========================== BARCODE OPCIONAL ==========================
try:
    import barcode
    from barcode.writer import ImageWriter
    BARCODE_AVAILABLE = True
except ImportError:
    BARCODE_AVAILABLE = False
    print("ADVERTENCIA: 'barcode' no disponible. Se usará generador alternativo.")

# ========================== WEASYPRINT OPCIONAL ==========================
try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError) as e:
    WEASYPRINT_AVAILABLE = False
    print(f"ADVERTENCIA: WeasyPrint no disponible ({e}). Los PDFs se generarán con ReportLab.")

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['UPLOAD_FOLDER'] = 'static'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

# ========================== DECORADOR ==========================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# ========================== BASE DE DATOS ==========================
DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith('postgresql')

TICKET_80MM = (80 * 28.35, 297 * 28.35)
TICKET_80MM_LANDSCAPE = (297 * 28.35, 80 * 28.35)
MEDIA_CARTA = (216 * 28.35 / 2, 279 * 28.35 / 2)
import psycopg2.extensions as _ext

class CustomCursor(_ext.cursor):
    def execute(self, query, vars=None):
        # Convierte automáticamente los ? de SQLite a %s para PostgreSQL
        if '?' in query:
            query = query.replace('?', '%s')
        return super().execute(query, vars)

def get_db_connection():
    if IS_POSTGRES:
        # Usamos este cursor personalizado para que funcione todo sin editar cada consulta
        return psycopg2.connect(DATABASE_URL, cursor_factory=CustomCursor)
    else:
        return sqlite3.connect('sisgaleno2026.db')
# ... (tu código siguiente)
# ========================== PLANTILLA DEFAULT ==========================
PLANTILLA_DEFAULT_RESULTADO = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<style>
    body { font-family: Arial, sans-serif; margin: 20mm; }
    .header { text-align: center; border-bottom: 2px solid #1C9CD4; padding-bottom: 10px; }
    .logo { max-height: 80px; }
    .info-paciente { margin: 20px 0; }
    .info-paciente td { padding: 5px 10px; }
    .resultados { width: 100%; border-collapse: collapse; margin: 20px 0; }
    .resultados th { background: #1C9CD4; color: white; padding: 8px; }
    .resultados td { border: 1px solid #ddd; padding: 8px; text-align: center; }
    .footer { margin-top: 40px; font-size: 12px; color: #666; text-align: center; border-top: 1px solid #ccc; padding-top: 15px; }
    .sello { position: absolute; bottom: 50px; right: 50px; width: 120px; }
    @page { margin: 15mm; }
</style>
</head>
<body>
    <div class="header">
        <img class="logo" src="{{ config[2] }}" alt="Logo">
        <h2>{{ nombre_sistema }}</h2>
        <h3>INFORME DE RESULTADOS</h3>
    </div>
    <table class="info-paciente">
        <tr><td><strong>Paciente:</strong></td><td>{{ paciente.nombre }}</td></tr>
        <tr><td><strong>DNI:</strong></td><td>{{ paciente.dni }}</td></tr>
        <tr><td><strong>Edad:</strong></td><td>{{ paciente.edad }} años</td></tr>
        <tr><td><strong>Sexo:</strong></td><td>{{ paciente.sexo }}</td></tr>
        <tr><td><strong>Examen:</strong></td><td>{{ examen }}</td></tr>
        <tr><td><strong>Fecha Resultado:</strong></td><td>{{ fecha_resultado }}</td></tr>
        <tr><td><strong>Tecnólogo:</strong></td><td>{{ tecnologo }}</td></tr>
        <tr><td><strong>Validado:</strong></td><td>{{ validado }}</td></tr>
    </table>
    <table class="resultados">
        <thead><tr><th>Parámetro</th><th>Resultado</th><th>Unidad</th><th>Rango Referencia</th></tr></thead>
        <tbody>
            {% for r in resultados %}
            <tr><td>{{ r.parametro }}</td><td>{{ r.valor }}</td><td>{{ r.unidad }}</td><td>{{ r.rango }}</td></tr>
            {% endfor %}
        </tbody>
    </table>
    <div class="footer">
        <p>{{ footer }}</p>
        <p>Generado por {{ nombre_sistema }}</p>
    </div>
    {% if sello_url %}
    <img class="sello" src="{{ config[7] }}" alt="Sello">
    {% endif %}
</body>
</html>
"""

# ========================== FUNCIONES AUXILIARES ==========================
def obtener_configuracion():
    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute("SELECT nombre_sistema, tamano_hoja, logo_path, encabezado_texto, pie_pagina_texto, report_header, report_footer, sello_path, ticket_size, report_size, result_size, login_background, system_background FROM configuracion_sistema WHERE id=%s", (1,))
    else:
        cur.execute("SELECT nombre_sistema, tamano_hoja, logo_path, encabezado_texto, pie_pagina_texto, report_header, report_footer, sello_path, ticket_size, report_size, result_size, login_background, system_background FROM configuracion_sistema WHERE id=?", (1,))
    row = cur.fetchone()
    conn.close()
    return row
def obtener_tamano_pagina(tipo='default'):
    """Retorna el tamaño de página según la configuración y el tipo de documento."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT boleta_tamano, cita_tamano, resultado_tamano, informe_tamano, receta_tamano, etiqueta_tamano FROM config_impresion WHERE id=1")
    row = cur.fetchone()
    conn.close()
    if row:
        config_sizes = {
            'boleta': row[0] or 'TICKET_80MM',
            'cita': row[1] or 'A4',
            'resultado': row[2] or 'A4',
            'informe': row[3] or 'A4',
            'receta': row[4] or 'A4',
            'etiqueta': row[5] or 'A4',
        }
    else:
        config_sizes = {
            'boleta': 'TICKET_80MM',
            'cita': 'A4',
            'resultado': 'A4',
            'informe': 'A4',
            'receta': 'A4',
            'etiqueta': 'A4',
        }
    
    size_key = config_sizes.get(tipo, 'A4' if tipo not in ['boleta','etiqueta'] else 'TICKET_80MM')
    
    sizes = {
        'A4': A4,
        'A5': A5,
        'LETTER': LETTER,
        'LEGAL': LEGAL,
        'TICKET_80MM': TICKET_80MM,
        'TICKET_80MM_LANDSCAPE': TICKET_80MM_LANDSCAPE,
        'MEDIA_CARTA': MEDIA_CARTA,
    }
    return sizes.get(size_key, A4)

def get_user_modules(rol):
    if not rol: return []
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT modulo FROM permisos_roles WHERE rol=?", (rol,))
    mods = [r[0] for r in cur.fetchall()]
    conn.close()
    return mods

def generar_siguiente_hc():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT historia_clinica FROM pacientes WHERE historia_clinica IS NOT NULL AND deleted=0")
    hs = {r[0] for r in cur.fetchall()}
    conn.close()
    n=1
    while True:
        hc=f"HC-{n:04d}"
        if hc not in hs: return hc
        n+=1

def generar_siguiente_boleta(numero_orden=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT MAX(id) FROM pagos")
    max_id = cur.fetchone()[0]
    conn.close()
    if numero_orden:
        return f"B001-{numero_orden.zfill(8)}"
    else:
        seq = max_id + 1 if max_id else 1
        return f"B001-{seq:08d}"

def generar_numero_orden():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT MAX(CAST(numero_orden AS INTEGER)) FROM citas WHERE numero_orden IS NOT NULL AND numero_orden != ''")
    max_orden = cur.fetchone()[0]
    conn.close()
    if max_orden:
        return f"{int(max_orden)+1:04d}"
    else:
        return "0001"

def calcular_edad(fecha):
    if not fecha: return 0
    try:
        nac = datetime.strptime(fecha, '%Y-%m-%d').date()
        hoy = date.today()
        edad = hoy.year - nac.year - ((hoy.month,hoy.day) < (nac.month,nac.day))
        return edad
    except: return 0

def crear_paciente_sistema(dni, nombre, apellido, fecha_nac='', telefono='', celular='', direccion='', sexo='', nro_afiliacion=''):
    dni = (dni or '').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, historia_clinica FROM pacientes WHERE dni = ? LIMIT 1", (dni,))
    existente = cur.fetchone()
    if existente:
        conn.close()
        return existente[1]
    hc = generar_siguiente_hc()
    edad = calcular_edad(fecha_nac) if fecha_nac else 0
    cur.execute("""INSERT INTO pacientes (historia_clinica, dni, nombre, apellido, fecha_nacimiento, telefono, celular, direccion, sexo, edad, nro_afiliacion, deleted)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
                (hc, dni, nombre, apellido, fecha_nac, telefono, celular, direccion, sexo, edad, nro_afiliacion))
    conn.commit()
    conn.close()
    return hc

def obtener_paciente_por_dni(dni):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, historia_clinica, nombre, apellido, fecha_nacimiento, edad, nro_afiliacion FROM pacientes WHERE dni=? AND deleted=0", (dni,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'id':row[0], 'historia_clinica':row[1], 'nombre':row[2], 'apellido':row[3], 'fecha_nacimiento':row[4], 'edad':row[5], 'nro_afiliacion':row[6]}
    return None

def generar_codigo_muestra():
    hoy = date.today()
    fecha = hoy.strftime("%Y%m%d")
    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute("SELECT COUNT(*) FROM ordenes_laboratorio WHERE fecha_validez = %s", (hoy,))
    else:
        cur.execute("SELECT COUNT(*) FROM ordenes_laboratorio WHERE fecha_validez = ?", (hoy,))
    count = cur.fetchone()[0] + 1
    conn.close()
    return f"MUESTRA-{fecha}-{count:04d}"

def generar_codigo_barras(codigo):
    if BARCODE_AVAILABLE:
        try:
            from barcode import get_barcode_class
            Code128 = get_barcode_class('code128')
            buf = io.BytesIO()
            code = Code128(codigo, writer=ImageWriter())
            code.write(buf, options={'module_width':0.2, 'module_height':15, 'font_size':10, 'text_distance':2, 'background':'white', 'foreground':'black'})
            buf.seek(0)
            return buf
        except Exception as e:
            print(f"Error generando código de barras con barcode: {e}")
    # Fallback
    from PIL import Image, ImageDraw, ImageFont
    width = 400
    height = 100
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    x = 20
    for i, ch in enumerate(codigo):
        val = ord(ch) % 10 + 1
        for j in range(val):
            draw.rectangle([x, 10, x+3, height-20], fill='black')
            x += 4
        x += 4
    try:
        font = ImageFont.truetype("arial.ttf", 12)
    except:
        font = ImageFont.load_default()
    draw.text((10, height-18), codigo, fill='black', font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

def paciente_tiene_pagos(id_paciente):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pagos WHERE id_paciente=? AND estado='Pagado'", (id_paciente,))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0

def generar_pdf_desde_html(html_content, datos, tamaño_pagina='A4'):
    """Convierte HTML a PDF usando WeasyPrint o fallback a ReportLab"""
    if WEASYPRINT_AVAILABLE:
        try:
            from jinja2 import Template
            template = Template(html_content)
            html_rendered = template.render(**datos)
            page_sizes = {
                'A4': 'A4',
                'A5': 'A5',
                'LETTER': 'letter',
                'LEGAL': 'legal',
                'TICKET_80MM': '80mm 297mm',
                'TICKET_80MM_LANDSCAPE': '297mm 80mm',
                'MEDIA_CARTA': '108mm 140mm',
            }
            page_size = page_sizes.get(tamaño_pagina, 'A4')
            css = f"@page {{ size: {page_size}; margin: 15mm; }}"
            html = HTML(string=html_rendered, base_url=os.path.join(os.getcwd(), 'static'))
            pdf_bytes = html.write_pdf(stylesheets=[CSS(string=css)])
            return io.BytesIO(pdf_bytes)
        except Exception as e:
            print(f"Error generando PDF con WeasyPrint: {e}. Usando ReportLab.")
    # Fallback a ReportLab (simple)
    from reportlab.pdfgen import canvas
    sizes = {'A4': A4, 'A5': A5, 'LETTER': LETTER, 'LEGAL': LEGAL}
    size = sizes.get(tamaño_pagina, A4)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    c.setFont("Helvetica", 10)
    y = size[1] - 40
    c.drawString(50, y, "DOCUMENTO GENERADO POR SISGALENO2026")
    y -= 20
    for key, val in datos.items():
        if isinstance(val, str):
            c.drawString(50, y, f"{key}: {val}")
            y -= 15
    c.save()
    buf.seek(0)
    return buf

def obtener_plantilla_pdf(tipo):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT contenido_html FROM plantillas_pdf WHERE tipo = ? AND activo = 1", (tipo,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def guardar_plantilla_pdf(tipo, nombre, contenido_html):
    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute("INSERT INTO plantillas_pdf (nombre, tipo, contenido_html) VALUES (%s,%s,%s) ON CONFLICT (tipo) DO UPDATE SET nombre=EXCLUDED.nombre, contenido_html=EXCLUDED.contenido_html, fecha_actualizacion=CURRENT_TIMESTAMP", (nombre, tipo, contenido_html))
    else:
        cur.execute("INSERT OR REPLACE INTO plantillas_pdf (nombre, tipo, contenido_html) VALUES (?,?,?)", (nombre, tipo, contenido_html))
    conn.commit()
    conn.close()

def generar_pdf_boleta(id_pago):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""SELECT p.id, p.numero_boleta, p.monto, p.fecha_pago, p.descripcion, pa.nombre, pa.apellido, pa.dni, pa.historia_clinica, c.fecha_cita, p.numero_orden
                   FROM pagos p LEFT JOIN pacientes pa ON p.id_paciente = pa.id LEFT JOIN citas c ON p.id_cita = c.id WHERE p.id = ?""", (id_pago,))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    size = obtener_tamano_pagina('boleta')
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    w,h = size
    c.setFillColor(colors.HexColor('#0d2b45'))
    c.rect(10, h-60, w-20, 50, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 12)
    c.drawCentredString(w/2, h-38, 'BOLETA DE PAGO')
    c.setFont('Helvetica', 7)
    c.drawCentredString(w/2, h-52, 'SISGALENO2026')
    c.setFillColor(colors.black)
    c.setFont('Helvetica-Bold', 9)
    c.drawCentredString(w/2, h-85, f'Boleta: {row[1]}')
    c.drawCentredString(w/2, h-100, f'Orden: {row[10] or "N/A"}')
    c.setFont('Helvetica', 8)
    c.drawCentredString(w/2, h-115, f'Fecha: {row[3]}')
    c.drawCentredString(w/2, h-130, f'Paciente: {row[5]} {row[6]}')
    c.drawCentredString(w/2, h-145, f'DNI: {row[7]}')
    c.drawCentredString(w/2, h-160, f'HC: {row[8] or "N/E"}')
    c.drawCentredString(w/2, h-175, f'Concepto: {row[4] or "Sin detalle"}')
    if row[9]: c.drawCentredString(w/2, h-190, f'Cita: {row[9]}')
    c.line(15, h-205, w-15, h-205)
    c.setFont('Helvetica-Bold', 11)
    c.drawCentredString(w/2, h-225, f'Monto: S/ {float(row[2] or 0):.2f}')
    c.setFont('Helvetica', 6)
    c.setFillColor(colors.grey)
    c.drawCentredString(w/2, 15, 'Documento generado por SISGALENO2026')
    c.save()
    buf.seek(0)
    return buf

app.jinja_env.globals.update(paciente_tiene_pagos=paciente_tiene_pagos)

# ========================== MIDDLEWARE ==========================
@app.before_request
def proteger():
    if request.endpoint in {'login','logout','index','static','api_paciente_por_dni','api_procedimientos','api_examenes'}:
        return None
    if not session.get('usuario'):
        return redirect(url_for('login'))
    return None

# ========================== LAYOUT BASE ==========================
LAYOUT_BASE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ nombre_sistema }} - Clínica</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6;
            {% if system_background %} background-image: url('{{ system_background }}'); background-size: cover; background-attachment: fixed; background-position: center; {% endif %}
        }
        .navbar-custom { background: linear-gradient(90deg, #0d2b45 0%, #1a4d70 100%); }
        .navbar-custom .navbar-brand, .navbar-custom .nav-link { color: white; }
        .navbar-custom .nav-link:hover { color: #72c6f7; }
        .container { max-width: 1200px; margin: 30px auto; padding: 20px; background: rgba(255,255,255,0.92); border-radius: 16px; box-shadow: 0 8px 30px rgba(0,0,0,0.08); backdrop-filter: blur(4px); }
        .btn { border-radius: 50px; }
        .badge-pendiente { background: #ffc107; color: #333; }
        .badge-pagado { background: #28a745; color: white; }
        .badge-muestra { background: #17a2b8; color: white; }
        .badge-atendido { background: #6c757d; color: white; }
        .badge-rx { background: #6f42c1; color: white; }
        .badge-ecografia { background: #fd7e14; color: white; }
        .badge-lab { background: #0d6efd; color: white; }
        .menu-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-top: 20px; }
        .menu-item { background: #e9ecef; padding: 20px; text-align: center; border-radius: 12px; color: #333; font-weight: bold; text-decoration: none; display: block; }
        .menu-item:hover { background: #0d2b45; color: white; }
        .adm-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        @media (max-width: 600px) { .adm-form-grid { grid-template-columns: 1fr; } }
        .attribution { text-align: center; font-size: 12px; color: #666; padding: 5px 0; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .autocomplete-suggestions { position: absolute; z-index: 1000; background: white; border: 1px solid #ccc; max-height: 200px; overflow-y: auto; width: 100%; }
        .autocomplete-suggestions div { padding: 8px 12px; cursor: pointer; }
        .autocomplete-suggestions div:hover { background: #f0f0f0; }
        .nav-tabs .nav-link { color: #0d2b45; }
        .nav-tabs .nav-link.active { font-weight: bold; background: #0d2b45; color: white; }
        .orden-card { border-left: 4px solid #0d6efd; padding: 12px; margin-bottom: 12px; background: #f8f9fa; border-radius: 8px; }
        .orden-card.rx { border-left-color: #6f42c1; }
        .orden-card.ecografia { border-left-color: #fd7e14; }
        .orden-card.lab { border-left-color: #0d6efd; }
        .orden-card.completado { opacity: 0.8; background: #e9ecef; }
        .result-image { max-width: 100%; max-height: 400px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); margin: 8px 0; }
        .result-thumb { width: 120px; height: 120px; object-fit: cover; border-radius: 8px; cursor: pointer; border: 2px solid #ddd; transition: 0.2s; }
        .result-thumb:hover { border-color: #0d6efd; transform: scale(1.05); }
        .modal-fullscreen .modal-dialog { max-width: 95vw; margin: 1rem auto; }
        .modal-fullscreen .modal-content { height: 95vh; }
        .modal-fullscreen .modal-body { overflow-y: auto; text-align: center; }
        .modal-fullscreen .modal-body img { max-width: 100%; max-height: 85vh; object-fit: contain; }
        .fondo-login {
            {% if login_background %} background-image: url('{{ login_background }}'); background-size: cover; background-position: center;
            {% else %} background: linear-gradient(135deg, #0d2b45 0%, #1a4d70 100%); {% endif %}
            min-height: 100vh; display: flex; align-items: center; justify-content: center;
        }
        .login-card { background: rgba(255,255,255,0.92); backdrop-filter: blur(8px); padding: 40px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 420px; width: 100%; }
        .login-card h2 { color: #0d2b45; }
        .nav-pills .nav-link { color: #0d2b45; }
        .nav-pills .nav-link.active { background: #0d2b45; color: white; }
        @media print {
            .navbar, .attribution, .no-print, .btn, .nav-tabs, .menu-grid { display: none !important; }
            .container { max-width: 100% !important; margin: 0 !important; padding: 10px !important; background: white !important; box-shadow: none !important; backdrop-filter: none !important; }
            .card { border: none !important; box-shadow: none !important; }
            .table { font-size: 10pt; }
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-custom">
        <div class="container-fluid">
            <span class="navbar-brand">🏥 <span>{{ nombre_sistema }}</span></span>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav ms-auto">
                    {% if session.get('usuario') %}
                        <li class="nav-item"><span class="nav-link">👤 {{ session.get('usuario') }} ({{ session.get('rol') }})</span></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('dashboard') }}">Inicio</a></li>
                        {% if 'Admisión' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('admision') }}">📋 Admisión</a></li>{% endif %}
                        {% if 'Caja' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('caja') }}">💰 Caja</a></li>{% endif %}
                        {% if 'Laboratorio' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('laboratorio') }}">🧪 Laboratorio</a></li>{% endif %}
                        {% if 'Atención Médica' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('atencion_medica') }}">🩺 Atención Médica</a></li>{% endif %}
                        {% if 'Enfermería' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('enfermeria') }}">🏥 Enfermería</a></li>{% endif %}
                        {% if 'Historias Clínicas' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('historias_clinicas') }}">📋 Historias Clínicas</a></li>{% endif %}
                        {% if 'Atención Médica' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('orden_examenes') }}">📋 Solicitar Exámenes</a></li>{% endif %}
                        {% if 'Atención Médica' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('mis_ordenes') }}">📄 Mis Órdenes</a></li>{% endif %}
                        {% if 'Atención Médica' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('recetas') }}">📝 Recetas</a></li>{% endif %}
                        {% if 'Configuración' in user_modules %}<li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema') }}">⚙️ Config</a></li>{% endif %}
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reportes') }}">📊 Reportes</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('logout') }}">Salir</a></li>
                    {% endif %}
                </ul>
            </div>
        </div>
    </nav>
    <div class="attribution">Creado por yonanT.b</div>
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        <!-- CONTENIDO_DINAMICO -->
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

# ========================== RUTAS PRINCIPALES ==========================
@app.route('/')
def index():
    return redirect(url_for('login' if not session.get('usuario') else 'dashboard'))

@app.route('/login', methods=['GET','POST'])
def login():
    if session.get('usuario'):
        return redirect(url_for('dashboard'))
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    login_bg = config[11] if config and len(config) > 11 else ''
    system_bg = config[12] if config and len(config) > 12 else ''
    if request.method == 'POST':
        user = request.form['usuario']; pwd = request.form['password']
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT id, rol, password_hash FROM usuarios WHERE usuario=?", (user,))
        data = cur.fetchone(); conn.close()
        if data and check_password_hash(data[2], pwd):
            session['usuario'] = user; session['rol'] = data[1]; session['id_usuario'] = data[0]
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')
    login_layout = """
    <!DOCTYPE html>
    <html>
    <head><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{{ nombre_sistema }} - Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .fondo-login {
            {% if login_background %} background-image: url('/static/{{ login_background }}'); background-size: cover; background-position: center;
            {% else %} background: linear-gradient(135deg, #0d2b45 0%, #1a4d70 100%); {% endif %}
            min-height: 100vh; display: flex; align-items: center; justify-content: center;
        }
        .login-card { background: rgba(255,255,255,0.92); backdrop-filter: blur(8px); padding: 40px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 420px; width: 100%; }
        .login-card h2 { color: #0d2b45; }
    </style>
    </head>
    <body>
        <div class="fondo-login"><div class="login-card">
            <h2 class="text-center">🏥 {{ nombre_sistema }}</h2><p class="text-center text-muted">Inicio de Sesión</p>
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }}">{{ message }}</div>{% endfor %}{% endif %}
            {% endwith %}
            <form method="POST">
                <div class="mb-3"><label>Usuario</label><input type="text" name="usuario" class="form-control" required></div>
                <div class="mb-3"><label>Contraseña</label><input type="password" name="password" class="form-control" required></div>
                <button type="submit" class="btn btn-primary w-100">Acceder</button>
            </form>
        </div></div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(login_layout, nombre_sistema=nombre_sistema, login_background=login_bg, system_background=system_bg)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    user_modules = get_user_modules(session.get('rol'))
    conn = get_db_connection()
    cur = conn.cursor()
    hoy = date.today().isoformat()
    cur.execute("SELECT COUNT(*) FROM citas WHERE fecha_cita LIKE ?", (hoy+'%',))
    citas_hoy = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM pacientes WHERE deleted=0")
    total_pacientes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM ordenes_laboratorio WHERE estado='Pendiente'")
    pendientes_lab = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE fecha_pago LIKE ? AND estado='Pagado'", (hoy+'%',))
    ingresos_hoy = float(cur.fetchone()[0])
    conn.close()
    contenido = """
    <div style="background:#0d2b45; color:white; padding:30px; border-radius:12px; text-align:center;">
        <h2>🏥 Bienvenido, {{ session.get('usuario') }}</h2><p>{{ nombre_sistema }}</p>
    </div>
    <div class="row mt-4">
        <div class="col-md-3"><div class="card text-center"><div class="card-body"><h5>Citas hoy</h5><h2>{{ citas_hoy }}</h2></div></div></div>
        <div class="col-md-3"><div class="card text-center"><div class="card-body"><h5>Pacientes</h5><h2>{{ total_pacientes }}</h2></div></div></div>
        <div class="col-md-3"><div class="card text-center"><div class="card-body"><h5>Laboratorio pendiente</h5><h2>{{ pendientes_lab }}</h2></div></div></div>
        <div class="col-md-3"><div class="card text-center"><div class="card-body"><h5>Ingresos hoy</h5><h2>S/ {{ "%.2f"|format(ingresos_hoy) }}</h2></div></div></div>
    </div>
    <div class="menu-grid">
        {% if 'Admisión' in user_modules %}<a href="{{ url_for('admision') }}" class="menu-item">📋 Admisión</a>{% endif %}
        {% if 'Caja' in user_modules %}<a href="{{ url_for('caja') }}" class="menu-item">💰 Caja</a>{% endif %}
        {% if 'Laboratorio' in user_modules %}<a href="{{ url_for('laboratorio') }}" class="menu-item">🧪 Laboratorio</a>{% endif %}
        {% if 'Atención Médica' in user_modules %}<a href="{{ url_for('atencion_medica') }}" class="menu-item">🩺 Atención Médica</a>{% endif %}
        {% if 'Enfermería' in user_modules %}<a href="{{ url_for('enfermeria') }}" class="menu-item">🏥 Enfermería</a>{% endif %}
        {% if 'Historias Clínicas' in user_modules %}<a href="{{ url_for('historias_clinicas') }}" class="menu-item">📋 Historias Clínicas</a>{% endif %}
        <a href="{{ url_for('reportes') }}" class="menu-item">📊 Reportes</a>
    </div>
    """
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=user_modules,
                                  citas_hoy=citas_hoy, total_pacientes=total_pacientes,
                                  pendientes_lab=pendientes_lab, ingresos_hoy=ingresos_hoy,
                                  system_background=system_bg, login_background=login_bg)

# ========================== MÓDULO REPORTES ==========================
@app.route('/reportes')
@login_required
def reportes():
    conn = get_db_connection(); cur = conn.cursor()
    fecha = request.args.get('fecha') or datetime.now().strftime('%Y-%m-%d')
    mes = request.args.get('mes') or datetime.now().strftime('%Y-%m')
    fecha_inicio = request.args.get('fecha_inicio') or datetime.now().strftime('%Y-%m-01')
    fecha_fin = request.args.get('fecha_fin') or datetime.now().strftime('%Y-%m-%d')
    year, month = map(int, mes.split('-'))
    ultimo_dia = monthrange(year, month)[1]
    mes_inicio = f'{year:04d}-{month:02d}-01'
    mes_fin = f'{year:04d}-{month:02d}-{ultimo_dia:02d}'
    cur.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita)=?", (fecha,))
    total_citas_dia = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita)=? AND estado='Pagado'", (fecha,))
    citas_pagadas_dia = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM citas c LEFT JOIN diagnosticos d ON d.id_cita = c.id WHERE date(c.fecha_cita)=? AND d.id IS NOT NULL", (fecha,))
    atenciones_dia = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE estado='Pagado' AND date(fecha_pago)=?", (fecha,))
    ingresos_dia = float(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita) BETWEEN ? AND ?", (mes_inicio, mes_fin))
    total_citas_mes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita) BETWEEN ? AND ? AND estado='Pagado'", (mes_inicio, mes_fin))
    citas_pagadas_mes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM citas c LEFT JOIN diagnosticos d ON d.id_cita = c.id WHERE date(c.fecha_cita) BETWEEN ? AND ? AND d.id IS NOT NULL", (mes_inicio, mes_fin))
    atenciones_mes = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE estado='Pagado' AND date(fecha_pago) BETWEEN ? AND ?", (mes_inicio, mes_fin))
    ingresos_mes = float(cur.fetchone()[0] or 0)
    cur.execute("""
        SELECT m.id, m.nombre || ' ' || m.apellido AS medico,
               COUNT(DISTINCT d.id) AS atenciones,
               COUNT(DISTINCT c.id) AS citas_asignadas
        FROM medicos m
        LEFT JOIN citas c ON c.id_medico = m.id AND date(c.fecha_cita) BETWEEN ? AND ?
        LEFT JOIN diagnosticos d ON d.id_cita = c.id
        WHERE m.activo = 1
        GROUP BY m.id
        ORDER BY atenciones DESC, citas_asignadas DESC
    """, (fecha_inicio, fecha_fin))
    rendimiento = cur.fetchall()
    conn.close()
    contenido = """
    <h2>📊 Reportes</h2>
    <div class="d-flex gap-2 flex-wrap mb-3">
        <a href="{{ url_for('reportes', fecha=fecha) }}" class="btn btn-primary">📅 Diario</a>
        <a href="{{ url_for('reportes', mes=mes) }}" class="btn btn-success">📈 Mensual</a>
        <a href="{{ url_for('reportes', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin) }}" class="btn btn-warning">🧑‍⚕️ Médicos</a>
    </div>
    <div class="row g-3 mb-4">
        <div class="col-md-3"><div class="card"><div class="card-body"><h6>Citas día</h6><h3>{{ total_citas_dia }}</h3></div></div></div>
        <div class="col-md-3"><div class="card"><div class="card-body"><h6>Pagadas hoy</h6><h3>{{ citas_pagadas_dia }}</h3></div></div></div>
        <div class="col-md-3"><div class="card"><div class="card-body"><h6>Atenciones</h6><h3>{{ atenciones_dia }}</h3></div></div></div>
        <div class="col-md-3"><div class="card"><div class="card-body"><h6>Ingresos día</h6><h3>S/ {{ "%.2f"|format(ingresos_dia) }}</h3></div></div></div>
    </div>
    <div class="card p-3 mb-3"><h5>📅 Diario</h5><form method="GET" class="row g-2"><div class="col-auto"><input type="date" name="fecha" value="{{ fecha }}" class="form-control"></div><div class="col-auto"><button class="btn btn-primary">Ver</button></div></form></div>
    <div class="card p-3 mb-3"><h5>📈 Mensual</h5><form method="GET" class="row g-2"><div class="col-auto"><input type="month" name="mes" value="{{ mes }}" class="form-control"></div><div class="col-auto"><button class="btn btn-success">Ver</button></div></form></div>
    <div class="card p-3"><h5>🧑‍⚕️ Médicos</h5><form method="GET" class="row g-2"><div class="col-auto"><input type="date" name="fecha_inicio" value="{{ fecha_inicio }}" class="form-control"></div><div class="col-auto"><input type="date" name="fecha_fin" value="{{ fecha_fin }}" class="form-control"></div><div class="col-auto"><button class="btn btn-warning">Ver</button></div></form>
    <table class="table"><thead><tr><th>Médico</th><th>Citas</th><th>Atenciones</th></tr></thead><tbody>{% for m in rendimiento %}<tr><td>{{ m[1] }}</td><td>{{ m[3] }}</td><td>{{ m[2] }}</td></tr>{% else %}<tr><td colspan="3">Sin datos</td></tr>{% endfor %}</tbody></table></div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  fecha=fecha, mes=mes, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
                                  total_citas_dia=total_citas_dia, citas_pagadas_dia=citas_pagadas_dia,
                                  atenciones_dia=atenciones_dia, ingresos_dia=ingresos_dia,
                                  total_citas_mes=total_citas_mes, citas_pagadas_mes=citas_pagadas_mes,
                                  atenciones_mes=atenciones_mes, ingresos_mes=ingresos_mes,
                                  rendimiento=rendimiento, system_background=system_bg, login_background=login_bg)

# ========================== MÓDULO ADMISIÓN ==========================
@app.route('/admision', methods=['GET','POST'])
@login_required
def admision():
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    if request.method == 'POST' and request.form.get('accion') == 'registrar_paciente':
        dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
        fecha_nac = request.form.get('fecha_nacimiento', ''); telefono = request.form.get('telefono', '')
        celular = request.form.get('celular', ''); direccion = request.form.get('direccion', '')
        sexo = request.form.get('sexo', ''); nro_af = request.form.get('nro_afiliacion', '')
        try:
            hc = crear_paciente_sistema(dni, nombre, apellido, fecha_nac, telefono, celular, direccion, sexo, nro_af)
            flash(f"Paciente {nombre} {apellido} registrado (HC: {hc}).", 'success')
        except Exception as e:
            flash(f"Error: {str(e)}", 'danger')
        return redirect(url_for('admision'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes WHERE deleted=0 ORDER BY id DESC")
    pacientes = cur.fetchall()
    cur.execute("SELECT id, nombre FROM servicios")
    servicios = cur.fetchall()
    cur.execute("SELECT id, nombre || ' ' || apellido AS nombre_completo FROM medicos WHERE activo=1 ORDER BY nombre")
    medicos = cur.fetchall()
    cur.execute("""SELECT c.id, p.historia_clinica, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, c.tipo_asegurado, c.numero_boleta, c.numero_orden
                   FROM citas c JOIN pacientes p ON c.id_paciente = p.id JOIN servicios s ON c.id_servicio = s.id WHERE p.deleted=0 ORDER BY c.fecha_cita DESC""")
    citas = cur.fetchall()
    conn.close()
    contenido = """
    <h2>📋 Admisión</h2>
    <ul class="nav nav-tabs" id="admTab" role="tablist">
        <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#pacientes">Pacientes</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#citas">Citas</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#registrar">Registrar Paciente</a></li>
    </ul>
    <div class="tab-content mt-3">
        <div class="tab-pane active" id="pacientes">
            <table class="table"><thead><tr><th>HC</th><th>DNI</th><th>Nombre</th><th>Apellido</th><th>Acciones</th></tr></thead><tbody>{% for p in pacientes %}<tr><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td>{{ p[4] }}</td><td><a href="{{ url_for('editar_paciente_admision', id_paciente=p[0]) }}" class="btn btn-warning btn-sm">✏️</a></td></tr>{% else %}<tr><td colspan="5">Sin pacientes</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="citas">
            <button onclick="toggleForm('form_cita')" class="btn btn-success mb-2">+ Nueva Cita</button>
            <div id="form_cita" style="display:none; margin-top:10px;">
                <form method="POST" action="{{ url_for('crear_cita') }}">
                    <div class="row g-2">
                        <div class="col-md-3"><label>Paciente</label><select name="id_paciente" class="form-control" required>{% for p in pacientes %}<option value="{{ p[0] }}">{{ p[1] }} - {{ p[2] }} {{ p[3] }}</option>{% endfor %}</select></div>
                        <div class="col-md-3"><label>Servicio</label><select name="id_servicio" class="form-control" required>{% for s in servicios %}<option value="{{ s[0] }}">{{ s[1] }}</option>{% endfor %}</select></div>
                        <div class="col-md-3"><label>Médico</label><select name="id_medico" class="form-control" required>{% for m in medicos %}<option value="{{ m[0] }}">{{ m[1] }}</option>{% endfor %}</select></div>
                        <div class="col-md-2"><label>Fecha</label><input type="date" name="fecha_cita" class="form-control" required></div>
                        <div class="col-md-1"><label>Hora</label><input type="time" name="hora_cita" step="60" class="form-control" required></div>
                        <div class="col-md-3"><label>Tipo</label><select name="tipo_asegurado" class="form-control" required><option value="Demanda">Demanda</option><option value="SIS">SIS</option><option value="SOAT">SOAT</option></select></div>
                        <div class="col-md-9"><label>Motivo</label><textarea name="motivo_consulta" class="form-control" rows="1"></textarea></div>
                        <div class="col-md-12"><button class="btn btn-primary">Agendar</button></div>
                    </div>
                </form>
            </div>
            <table class="table"><thead><tr><th>HC</th><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Tipo</th><th>Orden</th><th>Boleta</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{% for c in citas %}<tr><td>{{ c[1] }}</td><td>{{ c[2] }} {{ c[3] }}</td><td>{{ c[4] }}</td><td>{{ c[5] }}</td><td>{{ c[7] }}</td><td>{{ c[9] or 'N/A' }}</td><td>{{ c[8] or 'Pendiente' }}</td><td><span class="badge badge-{{ 'pagado' if c[6]=='Pagado' else 'pendiente' }}">{{ c[6] }}</span></td><td><a href="{{ url_for('imprimir_ficha_admision', id_cita=c[0]) }}" class="btn btn-primary btn-sm">📄</a></td></tr>{% else %}<tr><td colspan="9">Sin citas</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="registrar">
            <form method="POST"><input type="hidden" name="accion" value="registrar_paciente">
                <div class="row g-2">
                    <div class="col-md-3"><label>DNI *</label><input type="text" name="dni" class="form-control" required></div>
                    <div class="col-md-3"><label>Nombre *</label><input type="text" name="nombre" class="form-control" required></div>
                    <div class="col-md-3"><label>Apellido *</label><input type="text" name="apellido" class="form-control" required></div>
                    <div class="col-md-3"><label>Fecha Nac.</label><input type="date" name="fecha_nacimiento" class="form-control"></div>
                    <div class="col-md-3"><label>Sexo</label><select name="sexo" class="form-control"><option value="">Seleccione</option><option value="Masculino">Masculino</option><option value="Femenino">Femenino</option><option value="Otro">Otro</option></select></div>
                    <div class="col-md-3"><label>Nº Afiliación</label><input type="text" name="nro_afiliacion" class="form-control"></div>
                    <div class="col-md-3"><label>Teléfono</label><input type="text" name="telefono" class="form-control"></div>
                    <div class="col-md-3"><label>Celular</label><input type="text" name="celular" class="form-control"></div>
                    <div class="col-md-12"><label>Dirección</label><input type="text" name="direccion" class="form-control"></div>
                    <div class="col-md-12"><button class="btn btn-success">Guardar</button></div>
                </div>
            </form>
        </div>
    </div>
    <script>function toggleForm(id){var x=document.getElementById(id);x.style.display=x.style.display==='none'?'block':'none'}</script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  pacientes=pacientes, servicios=servicios, medicos=medicos, citas=citas,
                                  system_background=system_bg, login_background=login_bg)

@app.route('/admision/crear_cita', methods=['POST'])
@login_required
def crear_cita():
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    id_pac = request.form['id_paciente']; id_serv = request.form['id_servicio']
    id_med = request.form['id_medico']; fecha_str = request.form['fecha_cita']
    hora_str = request.form['hora_cita']; motivo = request.form.get('motivo_consulta', '')
    tipo = request.form['tipo_asegurado']
    fecha_hora = f"{fecha_str} {hora_str}:00"
    conn = get_db_connection(); cur = conn.cursor()
    estado = 'Pagado' if tipo in ['SIS','SOAT'] else 'Pendiente'
    numero_orden = generar_numero_orden()
    cur.execute("INSERT INTO citas (id_paciente, id_servicio, id_medico, fecha_cita, estado, motivo_consulta, tipo_asegurado, numero_boleta, numero_orden) VALUES (?,?,?,?,?,?,?,?,?)",
                (id_pac, id_serv, id_med, fecha_hora, estado, motivo, tipo, '', numero_orden))
    conn.commit(); conn.close()
    return redirect(url_for('admision'))

@app.route('/admision/imprimir/<int:id_cita>')
@login_required
def imprimir_ficha_admision(id_cita):
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""SELECT p.nombre, p.apellido, p.dni, p.edad, p.sexo, p.historia_clinica, s.nombre, m.nombre||' '||m.apellido, c.fecha_cita, c.tipo_asegurado, c.numero_boleta
                   FROM citas c JOIN pacientes p ON c.id_paciente=p.id JOIN servicios s ON c.id_servicio=s.id JOIN medicos m ON c.id_medico=m.id WHERE c.id=?""", (id_cita,))
    data = cur.fetchone(); conn.close()
    if not data: return "Cita no encontrada", 404
    # Usar plantilla HTML para la ficha
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    logo_path = config[2] if config else ''
    plantilla = """
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>Ficha de Admisión</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20mm; }
        .header { text-align: center; border-bottom: 2px solid #0d2b45; padding-bottom: 10px; }
        .logo { max-height: 80px; }
        .info { margin: 20px 0; }
        .info td { padding: 5px 10px; }
        .footer { margin-top: 40px; text-align: center; font-size: 12px; color: #666; }
        @page { margin: 15mm; }
    </style>
    </head>
    <body>
        <div class="header">
            {% if logo_path %}<img class="logo" src="{{ logo_path }}" alt="Logo">{% endif %}
            <h2>{{ nombre_sistema }}</h2>
            <h3>FICHA DE ADMISIÓN</h3>
        </div>
        <table class="info">
            <tr><td><strong>Paciente:</strong></td><td>{{ data[0] }} {{ data[1] }}</td></tr>
            <tr><td><strong>DNI:</strong></td><td>{{ data[2] }}</td></tr>
            <tr><td><strong>HC:</strong></td><td>{{ data[5] }}</td></tr>
            <tr><td><strong>Servicio:</strong></td><td>{{ data[6] }}</td></tr>
            <tr><td><strong>Médico:</strong></td><td>Dr. {{ data[7] }}</td></tr>
            <tr><td><strong>Fecha:</strong></td><td>{{ data[8] }}</td></tr>
            <tr><td><strong>Tipo:</strong></td><td>{{ data[9] }}</td></tr>
        </table>
        <div class="footer">{{ config[4] if config else '' }}</div>
    </body>
    </html>
    """
    from jinja2 import Template
    html = Template(plantilla).render(data=data, logo_path=url_for('static', filename=logo_path) if logo_path else '', nombre_sistema=nombre_sistema, config=config)
    pdf = generar_pdf_desde_html(html, {}, 'A4')
    return send_file(pdf, as_attachment=True, download_name=f"Ficha_Admision_{id_cita}.pdf", mimetype='application/pdf')

@app.route('/admision/editar_paciente/<int:id_paciente>', methods=['GET','POST'])
@login_required
def editar_paciente_admision(id_paciente):
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    if request.method == 'POST':
        nombre = request.form['nombre']; apellido = request.form['apellido']
        dni = request.form['dni']; fecha_nac = request.form.get('fecha_nacimiento', '')
        telefono = request.form.get('telefono', ''); celular = request.form.get('celular', '')
        direccion = request.form.get('direccion', ''); sexo = request.form.get('sexo', '')
        nro_af = request.form.get('nro_afiliacion', '')
        edad = calcular_edad(fecha_nac) if fecha_nac else 0
        cur.execute("""UPDATE pacientes SET nombre=?, apellido=?, dni=?, fecha_nacimiento=?, telefono=?, celular=?, direccion=?, sexo=?, edad=?, nro_afiliacion=? WHERE id=?""",
                    (nombre, apellido, dni, fecha_nac, telefono, celular, direccion, sexo, edad, nro_af, id_paciente))
        conn.commit(); conn.close()
        flash('Paciente actualizado.', 'success')
        return redirect(url_for('admision'))
    cur.execute("SELECT id, nombre, apellido, dni, fecha_nacimiento, telefono, celular, direccion, sexo, nro_afiliacion FROM pacientes WHERE id=?", (id_paciente,))
    p = cur.fetchone(); conn.close()
    if not p: return "Paciente no encontrado", 404
    contenido = f"""
    <h2>✏️ Editar Paciente - {p[1]} {p[2]}</h2>
    <form method="POST">
        <div class="row g-2">
            <div class="col-md-4"><label>DNI</label><input type="text" name="dni" value="{p[3]}" class="form-control" required></div>
            <div class="col-md-4"><label>Nombre</label><input type="text" name="nombre" value="{p[1]}" class="form-control" required></div>
            <div class="col-md-4"><label>Apellido</label><input type="text" name="apellido" value="{p[2]}" class="form-control" required></div>
            <div class="col-md-4"><label>Fecha Nac.</label><input type="date" name="fecha_nacimiento" value="{p[4] or ''}" class="form-control"></div>
            <div class="col-md-4"><label>Sexo</label><select name="sexo" class="form-control"><option value="">Seleccione</option><option value="Masculino" {'selected' if p[8]=='Masculino' else ''}>Masculino</option><option value="Femenino" {'selected' if p[8]=='Femenino' else ''}>Femenino</option><option value="Otro" {'selected' if p[8]=='Otro' else ''}>Otro</option></select></div>
            <div class="col-md-4"><label>Nº Afiliación</label><input type="text" name="nro_afiliacion" value="{p[9] or ''}" class="form-control"></div>
            <div class="col-md-3"><label>Teléfono</label><input type="text" name="telefono" value="{p[5] or ''}" class="form-control"></div>
            <div class="col-md-3"><label>Celular</label><input type="text" name="celular" value="{p[6] or ''}" class="form-control"></div>
            <div class="col-md-6"><label>Dirección</label><input type="text" name="direccion" value="{p[7] or ''}" class="form-control"></div>
        </div>
        <button class="btn btn-success mt-2">Guardar</button>
        <a href="{{ url_for('admision') }}" class="btn btn-secondary mt-2">Cancelar</a>
    </form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  system_background=system_bg, login_background=login_bg)

# ========================== MÓDULO CAJA ==========================
@app.route('/caja', methods=['GET','POST'])
@login_required
def caja():
    if 'Caja' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'cobrar_orden':
            numero_orden = request.form.get('numero_orden')
            if not numero_orden:
                flash('Ingrese número de orden.', 'danger')
                return redirect(url_for('caja'))
            cur.execute("SELECT id, id_paciente FROM citas WHERE numero_orden = ? AND estado = 'Pagado'", (numero_orden,))
            cita = cur.fetchone()
            if not cita:
                flash('Orden no encontrada o no está pagada.', 'danger')
                return redirect(url_for('caja'))
            id_cita, id_paciente = cita
            cur.execute("""
                SELECT o.id, o.precio_total, o.descripcion_orden
                FROM ordenes_laboratorio o
                WHERE o.numero_orden = ? AND o.estado = 'Pendiente'
            """, (numero_orden,))
            orden_lab = cur.fetchone()
            if not orden_lab:
                flash('No hay orden de laboratorio pendiente para este número.', 'danger')
                return redirect(url_for('caja'))
            id_orden, total, descripcion = orden_lab
            numero_boleta = generar_siguiente_boleta(numero_orden)
            cur.execute("""
                INSERT INTO pagos (id_cita, id_paciente, numero_boleta, numero_orden, monto, fecha_pago, estado, descripcion)
                VALUES (?, ?, ?, ?, ?, ?, 'Pagado', ?)
            """, (id_cita, id_paciente, numero_boleta, numero_orden, total, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), descripcion or 'Orden médica'))
            id_pago = cur.lastrowid
            cur.execute("UPDATE ordenes_laboratorio SET id_pago=?, numero_boleta=?, estado='Pagado' WHERE id=?", (id_pago, numero_boleta, id_orden))
            cur.execute("UPDATE citas SET numero_boleta=? WHERE id=?", (numero_boleta, id_cita))
            conn.commit()
            flash(f'Boleta emitida: {numero_boleta}', 'success')
            return redirect(url_for('caja'))
        elif accion == 'cobro_directo':
            id_paciente = request.form.get('id_paciente'); monto = request.form.get('monto', '').strip()
            descripcion = request.form.get('descripcion', '').strip() or 'Cobro directo'
            if not id_paciente or not monto:
                flash('Complete todos los campos.', 'danger')
                return redirect(url_for('caja'))
            numero_boleta = generar_siguiente_boleta()
            cur.execute("""
                INSERT INTO pagos (id_cita, id_paciente, numero_boleta, monto, fecha_pago, estado, descripcion)
                VALUES (?, ?, ?, ?, ?, 'Pagado', ?)
            """, (None, id_paciente, numero_boleta, float(monto), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), descripcion))
            conn.commit()
            flash(f'Boleta emitida: {numero_boleta}', 'success')
            return redirect(url_for('caja'))
    cur.execute("""
        SELECT o.numero_orden, p.nombre, p.apellido, o.precio_total, o.descripcion_orden, o.id
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        WHERE o.estado = 'Pendiente' AND o.numero_orden IS NOT NULL
    """)
    ordenes_pendientes = cur.fetchall()
    cur.execute("""SELECT p.id, p.numero_boleta, p.monto, p.fecha_pago, p.descripcion, pa.nombre, pa.apellido, pa.dni, p.numero_orden
                   FROM pagos p JOIN pacientes pa ON p.id_paciente = pa.id WHERE pa.deleted=0 ORDER BY p.fecha_pago DESC""")
    historial = cur.fetchall()
    cur.execute("SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes WHERE deleted=0 ORDER BY id DESC")
    pacientes = cur.fetchall()
    conn.close()
    contenido = """
    <h2>💰 Caja</h2>
    <ul class="nav nav-tabs" id="cajaTab" role="tablist">
        <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#cobrar_orden">Cobrar Orden</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#cobro_directo">Cobro Directo</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#historial">Historial</a></li>
    </ul>
    <div class="tab-content mt-3">
        <div class="tab-pane active" id="cobrar_orden">
            <div class="card p-3">
                <form method="POST">
                    <input type="hidden" name="accion" value="cobrar_orden">
                    <div class="row g-2">
                        <div class="col-md-4"><label>Número de Orden</label><input type="text" name="numero_orden" class="form-control" placeholder="ej. 0001" required></div>
                        <div class="col-md-2"><label>&nbsp;</label><button class="btn btn-success w-100">Cobrar</button></div>
                    </div>
                </form>
            </div>
            <h5 class="mt-3">Órdenes Pendientes de Cobro</h5>
            <table class="table"><thead><tr><th>Orden</th><th>Paciente</th><th>Total</th><th>Descripción</th></tr></thead><tbody>
                {% for o in ordenes_pendientes %}
                <tr><td>{{ o[0] }}</td><td>{{ o[1] }} {{ o[2] }}</td><td>S/ {{ o[3] }}</td><td>{{ o[4] }}</td></tr>
                {% else %}<tr><td colspan="4">Sin órdenes pendientes</td></tr>{% endfor %}
            </tbody></table>
        </div>
        <div class="tab-pane" id="cobro_directo">
            <div class="card p-3">
                <form method="POST">
                    <input type="hidden" name="accion" value="cobro_directo">
                    <div class="row g-2">
                        <div class="col-md-4"><label>Paciente</label><select name="id_paciente" class="form-control" required>{% for p in pacientes %}<option value="{{ p[0] }}">{{ p[2] }} - {{ p[1] }}</option>{% endfor %}</select></div>
                        <div class="col-md-3"><label>Monto</label><input type="number" step="0.01" name="monto" class="form-control" required></div>
                        <div class="col-md-3"><label>Descripción</label><input type="text" name="descripcion" class="form-control"></div>
                        <div class="col-md-2"><label>&nbsp;</label><button class="btn btn-success w-100">Emitir</button></div>
                    </div>
                </form>
            </div>
        </div>
        <div class="tab-pane" id="historial">
            <table class="table"><thead><tr><th>Boleta</th><th>Orden</th><th>Paciente</th><th>Concepto</th><th>Fecha</th><th>Monto</th><th>Acciones</th></tr></thead><tbody>
                {% for h in historial %}
                <tr><td>{{ h[1] }}</td><td>{{ h[8] or 'N/A' }}</td><td>{{ h[5] }} {{ h[6] }}</td><td>{{ h[4] }}</td><td>{{ h[3] }}</td><td>S/ {{ h[2] }}</td><td><a href="{{ url_for('imprimir_boleta_pdf', id_pago=h[0]) }}" class="btn btn-warning btn-sm">🖨️</a></td></tr>
                {% else %}<tr><td colspan="7">Sin pagos</td></tr>{% endfor %}
            </tbody></table>
        </div>
    </div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  ordenes_pendientes=ordenes_pendientes, historial=historial, pacientes=pacientes,
                                  system_background=system_bg, login_background=login_bg)

@app.route('/caja/boleta_pdf/<int:id_pago>')
@login_required
def imprimir_boleta_pdf(id_pago):
    buffer = generar_pdf_boleta(id_pago)
    if not buffer:
        flash('Boleta no encontrada', 'danger')
        return redirect(url_for('caja'))
    download = request.args.get('download', '0') == '1'
    return send_file(buffer, as_attachment=download, download_name=f'boleta_{id_pago}.pdf', mimetype='application/pdf')

# ========================== MÓDULO LABORATORIO ==========================
@app.route('/api/paciente_por_dni')
def api_paciente_por_dni():
    dni = request.args.get('dni', '').strip()
    if not dni:
        return jsonify({'error':'DNI requerido'}), 400
    p = obtener_paciente_por_dni(dni)
    if p:
        return jsonify(p)
    return jsonify({'error':'Paciente no encontrado'}), 404

@app.route('/laboratorio', methods=['GET','POST'])
@login_required
def laboratorio():
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'buscar_boleta':
            numero_boleta = request.form.get('numero_boleta')
            if not numero_boleta:
                flash('Ingrese número de boleta.', 'danger')
                return redirect(url_for('laboratorio'))
            cur.execute("""
                SELECT o.id, p.nombre, p.apellido, o.numero_orden, o.descripcion_orden,
                       GROUP_CONCAT(e.descripcion, ', ') AS examenes
                FROM ordenes_laboratorio o
                JOIN pacientes p ON o.id_paciente = p.id
                LEFT JOIN orden_examenes oe ON oe.id_orden = o.id
                LEFT JOIN examenes_catalogo e ON oe.id_examen = e.id
                WHERE o.numero_boleta = ?
                GROUP BY o.id
            """, (numero_boleta,))
            orden = cur.fetchone()
            if not orden:
                flash('No se encontró orden con esa boleta.', 'danger')
                return redirect(url_for('laboratorio'))
            session['orden_lab_boleta'] = orden[0]
            flash(f'Orden encontrada para {orden[1]} {orden[2]}.', 'success')
            return redirect(url_for('laboratorio'))
        elif accion == 'procesar_orden':
            id_orden = session.get('orden_lab_boleta')
            if not id_orden:
                flash('Primero busque una boleta.', 'danger')
                return redirect(url_for('laboratorio'))
            return redirect(url_for('ingresar_resultado', id_orden=id_orden))
        elif accion == 'registrar_paciente_lab':
            dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
            try:
                hc = crear_paciente_sistema(dni, nombre, apellido)
                flash(f"Paciente agregado (HC: {hc}).", 'success')
            except Exception as e:
                flash(f"Error: {str(e)}", 'danger')
            return redirect(url_for('laboratorio'))
        elif accion == 'crear_orden':
            id_paciente = request.form.get('id_paciente')
            if not id_paciente:
                flash("Seleccione paciente.", 'danger')
                return redirect(url_for('laboratorio'))
            tipo_orden = request.form.get('tipo_orden', 'examen')
            codigo_muestra = generar_codigo_muestra()
            fecha_validez = date.today()
            if tipo_orden == 'examen':
                id_examen = request.form.get('id_examen')
                examen_manual = request.form.get('examen_manual', '').strip()
                if id_examen:
                    cur.execute("SELECT precio FROM examenes_catalogo WHERE id=?", (id_examen,))
                    row = cur.fetchone()
                    precio = float(row[0]) if row else 0.0
                elif examen_manual:
                    precio = float(request.form.get('precio_manual', 0))
                else:
                    flash("Seleccione examen o ingrese manual.", 'danger')
                    return redirect(url_for('laboratorio'))
                cur.execute("""INSERT INTO ordenes_laboratorio (id_paciente, id_examen, examen_manual, fecha_emision, estado, precio, codigo_muestra, fecha_validez, tipo_orden)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (id_paciente, id_examen if id_examen else None, examen_manual,
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 'Pendiente', precio,
                             codigo_muestra, fecha_validez, 'examen'))
                flash(f"Orden creada. Código: {codigo_muestra}", 'success')
            else:
                servicio_manual = request.form.get('servicio_manual', '').strip()
                if not servicio_manual:
                    flash("Ingrese nombre del servicio.", 'danger')
                    return redirect(url_for('laboratorio'))
                precio = float(request.form.get('precio_servicio', 0))
                cur.execute("""INSERT INTO ordenes_laboratorio (id_paciente, servicio_manual, fecha_emision, estado, precio, codigo_muestra, fecha_validez, tipo_orden)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (id_paciente, servicio_manual, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             'Pendiente', precio, codigo_muestra, fecha_validez, 'servicio'))
                flash(f"Servicio registrado. Código: {codigo_muestra}", 'success')
            conn.commit(); conn.close()
            return redirect(url_for('laboratorio'))

    cur.execute("SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes WHERE deleted=0 ORDER BY id DESC")
    pacientes = cur.fetchall()
    cur.execute("SELECT id, descripcion, precio FROM examenes_catalogo")
    examenes = cur.fetchall()
    sql_pend = """SELECT o.id, p.nombre, p.apellido, COALESCE(e.descripcion, o.examen_manual, o.servicio_manual) AS descripcion,
                  o.estado, o.codigo_muestra, o.fecha_validez,
                  CASE WHEN EXISTS (SELECT 1 FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%')) THEN 'Pagado' ELSE 'Pendiente' END AS estado_pago,
                  (SELECT pg.numero_boleta FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS numero_boleta,
                  (SELECT pg.monto FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS monto_pago
                  FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id LEFT JOIN examenes_catalogo e ON o.id_examen=e.id
                  WHERE o.estado='Pendiente' ORDER BY o.id DESC"""
    if IS_POSTGRES: sql_pend = sql_pend.replace('?', '%s')
    cur.execute(sql_pend); pendientes_muestra = cur.fetchall()
    sql_proceso = """SELECT o.id, p.nombre, p.apellido, COALESCE(e.descripcion, o.examen_manual, o.servicio_manual) AS descripcion,
                  o.estado, o.codigo_muestra, o.fecha_validez,
                  CASE WHEN EXISTS (SELECT 1 FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%')) THEN 'Pagado' ELSE 'Pendiente' END AS estado_pago,
                  (SELECT pg.numero_boleta FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS numero_boleta,
                  (SELECT pg.monto FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS monto_pago,
                  CASE WHEN EXISTS (SELECT 1 FROM resultados_lab rl WHERE rl.id_orden = o.id) THEN 'Completado' ELSE 'Pendiente' END AS resultado_estado,
                  o.tecnologo_id, (SELECT usuario FROM usuarios WHERE id=o.tecnologo_id) AS tecnologo_nombre,
                  o.fecha_resultado, o.validado, o.tipo_orden, o.tipo_resultado, o.descripcion_orden
                  FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id LEFT JOIN examenes_catalogo e ON o.id_examen=e.id
                  WHERE o.estado != 'Pendiente' ORDER BY o.id DESC"""
    if IS_POSTGRES: sql_proceso = sql_proceso.replace('?', '%s')
    cur.execute(sql_proceso); ordenes_proceso = cur.fetchall()
    conn.close()
    contenido = """
    <h2>🧪 Laboratorio</h2>
    <ul class="nav nav-tabs" id="labTab" role="tablist">
        <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#buscar_boleta">Buscar por Boleta</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#pendientes">Pendientes</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#procesar">Procesar</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#pacientes_lab">Pacientes</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#crear_orden_lab">+ Nueva Orden</a></li>
    </ul>
    <div class="tab-content mt-3">
        <div class="tab-pane active" id="buscar_boleta">
            <div class="card p-3">
                <form method="POST">
                    <input type="hidden" name="accion" value="buscar_boleta">
                    <div class="row g-2">
                        <div class="col-md-4"><label>Número de Boleta</label><input type="text" name="numero_boleta" class="form-control" placeholder="ej. B001-00000001" required></div>
                        <div class="col-md-2"><label>&nbsp;</label><button class="btn btn-primary w-100">Buscar</button></div>
                    </div>
                </form>
                {% if session.get('orden_lab_boleta') %}
                <form method="POST" class="mt-3">
                    <input type="hidden" name="accion" value="procesar_orden">
                    <button class="btn btn-success">Procesar Resultados</button>
                </form>
                {% endif %}
            </div>
        </div>
        <div class="tab-pane" id="pendientes">
            <table class="table"><thead><tr><th>Código</th><th>Paciente</th><th>Descripción</th><th>Pago</th><th>Boleta</th><th>Monto</th><th>Acciones</th></tr></thead><tbody>{% for p in pendientes_muestra %}<tr><td>{{ p[5] }}</td><td>{{ p[1] }} {{ p[2] }}</td><td>{{ p[3] }}</td><td><span class="badge badge-{{ 'pagado' if p[7]=='Pagado' else 'pendiente' }}">{{ p[7] }}</span></td><td>{{ p[8] or '--' }}</td><td>S/ {{ p[9] or '0.00' }}</td><td><a href="{{ url_for('tomar_muestra', id_orden=p[0]) }}" class="btn btn-primary btn-sm">Tomar</a> <a href="{{ url_for('imprimir_etiqueta', id_orden=p[0]) }}" class="btn btn-warning btn-sm">Etiqueta</a></td></tr>{% else %}<tr><td colspan="7">Sin pendientes</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="procesar">
            <table class="table"><thead><tr><th>Código</th><th>Paciente</th><th>Descripción</th><th>Estado</th><th>Pago</th><th>Boleta</th><th>Monto</th><th>Resultado</th><th>Tecnólogo</th><th>Fecha Res.</th><th>Validado</th><th>Tipo</th><th>Acciones</th></tr></thead><tbody>{% for o in ordenes_proceso %}<tr><td>{{ o[5] }}</td><td>{{ o[1] }} {{ o[2] }}</td><td>{{ o[3] }}</td><td><span class="badge badge-{{ 'muestra' if o[4]=='Muestra Tomada' else 'pagado' if o[4]=='Completado' else 'pendiente' }}">{{ o[4] }}</span></td><td><span class="badge badge-{{ 'pagado' if o[7]=='Pagado' else 'pendiente' }}">{{ o[7] }}</span></td><td>{{ o[8] or '--' }}</td><td>S/ {{ o[9] or '0.00' }}</td><td>{{ o[10] or 'Pendiente' }}</td><td>{{ o[12] or 'N/A' }}</td><td>{{ o[13] or 'N/A' }}</td><td>{% if o[14]==1 %}✅{% else %}❌{% endif %}</td><td><span class="badge badge-{{ o[15] or 'lab' }}">{{ o[15] or 'lab' }}</span></td><td>{% if o[4]=='Muestra Tomada' %}<a href="{{ url_for('ingresar_resultado', id_orden=o[0]) }}" class="btn btn-warning btn-sm">Procesar</a>{% elif o[4]=='Completado' %}<a href="{{ url_for('imprimir_resultado_lab', id_orden=o[0]) }}" class="btn btn-primary btn-sm">PDF</a>{% endif %} <a href="{{ url_for('imprimir_etiqueta', id_orden=o[0]) }}" class="btn btn-info btn-sm">Etiqueta</a> {% if o[15] in ('rx','ecografia','tomografia','resonancia') %}<a href="{{ url_for('ver_imagenes_orden', id_orden=o[0]) }}" class="btn btn-secondary btn-sm">🖼️</a>{% endif %}</td></tr>{% else %}<tr><td colspan="13">Sin procesos</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="pacientes_lab">
            <table class="table"><thead><tr><th>HC</th><th>DNI</th><th>Nombre</th><th>Apellido</th></tr></thead><tbody>{% for p in pacientes %}<tr><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td>{{ p[4] }}</td></tr>{% else %}<tr><td colspan="4">Sin pacientes</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="crear_orden_lab">
            <div class="card p-3">
                <form method="POST"><input type="hidden" name="accion" value="crear_orden">
                    <div class="row g-2">
                        <div class="col-md-4"><label>Paciente</label><select name="id_paciente" class="form-control" required><option value="">Seleccione</option>{% for p in pacientes %}<option value="{{ p[0] }}">{{ p[2] }} {{ p[3] }} ({{ p[1] }})</option>{% endfor %}</select></div>
                        <div class="col-md-4"><label>Tipo</label><select name="tipo_orden" class="form-control"><option value="examen">Examen</option><option value="servicio">Servicio</option></select></div>
                        <div class="col-md-4"><label>Examen</label><select name="id_examen" class="form-control"><option value="">-- Manual --</option>{% for e in examenes %}<option value="{{ e[0] }}">{{ e[1] }} (S/ {{ e[2] }})</option>{% endfor %}</select></div>
                        <div class="col-md-4"><label>Manual</label><input type="text" name="examen_manual" class="form-control"></div>
                        <div class="col-md-4"><label>Precio</label><input type="number" step="0.01" name="precio_manual" class="form-control"></div>
                        <div class="col-md-12"><button class="btn btn-success">Crear Orden</button></div>
                    </div>
                </form>
            </div>
        </div>
    </div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  pacientes=pacientes, examenes=examenes, pendientes_muestra=pendientes_muestra,
                                  ordenes_proceso=ordenes_proceso, system_background=system_bg, login_background=login_bg)

@app.route('/laboratorio/tomar_muestra/<int:id_orden>')
@login_required
def tomar_muestra(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE ordenes_laboratorio SET estado='Muestra Tomada' WHERE id=?", (id_orden,))
    conn.commit(); conn.close()
    return redirect(url_for('laboratorio'))

@app.route('/laboratorio/resultado/<int:id_orden>', methods=['GET','POST'])
@login_required
def ingresar_resultado(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""SELECT o.id, p.nombre, p.apellido, e.descripcion, e.id as id_examen_cat, o.examen_manual, o.servicio_manual, o.tecnologo_id, o.fecha_resultado, o.validado, o.tipo_orden, o.tipo_resultado
                   FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id LEFT JOIN examenes_catalogo e ON o.id_examen=e.id WHERE o.id=?""", (id_orden,))
    orden = cur.fetchone()
    if not orden: return "Orden no encontrada", 404
    es_manual = orden[5] is not None or orden[6] is not None
    tipo_resultado = orden[11] or 'lab'
    if request.method == 'POST':
        if tipo_resultado in ('rx', 'ecografia', 'tomografia', 'resonancia'):
            archivos = request.files.getlist('imagenes')
            descripcion = request.form.get('descripcion_imagenes', '')
            for archivo in archivos:
                if archivo and archivo.filename:
                    ext = archivo.filename.rsplit('.',1)[1].lower() if '.' in archivo.filename else 'jpg'
                    nombre = f"orden_{id_orden}_{uuid.uuid4().hex}.{ext}"
                    ruta = os.path.join('static', 'imagenes_resultados')
                    os.makedirs(ruta, exist_ok=True)
                    archivo.save(os.path.join(ruta, nombre))
                    cur.execute("""INSERT INTO imagenes_laboratorio (id_orden, nombre_archivo, ruta_archivo, descripcion, fecha_subida, tipo_imagen)
                                   VALUES (?, ?, ?, ?, ?, 'resultado')""",
                                (id_orden, nombre, f"imagenes_resultados/{nombre}", descripcion, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            cur.execute("UPDATE ordenes_laboratorio SET estado='Completado', tecnologo_id=?, fecha_resultado=?, validado=? WHERE id=?",
                        (session.get('id_usuario'), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 1 if session.get('rol')=='tecnologo' else 0, id_orden))
            conn.commit(); conn.close()
            flash('Imágenes guardadas y orden completada.', 'success')
            return redirect(url_for('laboratorio'))
        else:
            if es_manual:
                res = request.form.get('resultado_general', '')
                cur.execute("INSERT INTO resultados_lab (id_orden, id_parametro, resultado) VALUES (?,NULL,?)", (id_orden, res))
            else:
                cur.execute("SELECT id FROM examenes_parametros WHERE id_examen_catalogo=?", (orden[4],))
                params = cur.fetchall()
                for p in params:
                    val = request.form.get(f'param_{p[0]}', '')
                    cur.execute("INSERT OR REPLACE INTO resultados_lab (id_orden, id_parametro, resultado) VALUES (?,?,?)", (id_orden, p[0], val))
            ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cur.execute("UPDATE ordenes_laboratorio SET estado='Completado', tecnologo_id=?, fecha_resultado=?, validado=? WHERE id=?", 
                        (session.get('id_usuario'), ahora, 1 if session.get('rol')=='tecnologo' else 0, id_orden))
            conn.commit(); conn.close()
            flash('Resultados guardados.', 'success')
            return redirect(url_for('laboratorio'))
    conn.close()
    if es_manual or tipo_resultado in ('rx','ecografia','tomografia','resonancia'):
        contenido = """
        <h2>Procesar Orden #{{ id_orden }}</h2>
        <div class="bg-light p-3"><b>Paciente:</b> {{ orden[1] }} {{ orden[2] }}</div>
        <form method="POST" enctype="multipart/form-data">
            {% if orden[11] in ('rx','ecografia','tomografia','resonancia') %}
            <div class="mb-3"><label>Subir imágenes</label><input type="file" name="imagenes" class="form-control" multiple accept="image/*"></div>
            <div class="mb-3"><label>Descripción</label><input type="text" name="descripcion_imagenes" class="form-control"></div>
            {% else %}
            <div class="mb-3"><label>Resultado general</label><textarea name="resultado_general" class="form-control" rows="4"></textarea></div>
            {% endif %}
            <button class="btn btn-success">Guardar</button>
            <a href="{{ url_for('laboratorio') }}" class="btn btn-danger">Cancelar</a>
        </form>
        """
    else:
        cur = conn.cursor()
        cur.execute("SELECT id, nombre_parametro, unidad, rango_referencia FROM examenes_parametros WHERE id_examen_catalogo=? ORDER BY orden", (orden[4],))
        params = cur.fetchall()
        conn.close()
        contenido = """
        <h2>Procesar Orden #{{ id_orden }}</h2>
        <div class="bg-light p-3"><b>Paciente:</b> {{ orden[1] }} {{ orden[2] }}<br><b>Examen:</b> {{ orden[3] }}</div>
        <form method="POST"><table class="table"><thead><tr><th>Parámetro</th><th>Unidad / Rango</th><th>Resultado</th></tr></thead><tbody>{% for p in params %}<tr><td>{{ p[1] }}</td><td>{{ p[2] or '' }} {{ p[3] or '' }}</td><td><input type="text" name="param_{{ p[0] }}" class="form-control"></td></tr>{% endfor %}</tbody></table><button class="btn btn-success">Guardar</button><a href="{{ url_for('laboratorio') }}" class="btn btn-danger">Cancelar</a></form>
        """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  id_orden=id_orden, orden=orden, params=params,
                                  system_background=system_bg, login_background=login_bg)

@app.route('/laboratorio/imprimir/<int:id_orden>')
@login_required
def imprimir_resultado_lab(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""SELECT p.nombre, p.apellido, p.dni, COALESCE(e.descripcion, o.examen_manual, o.servicio_manual) AS descripcion,
                   o.fecha_emision, p.edad, p.sexo, o.fecha_resultado, o.validado, o.tecnologo_id, u.usuario AS tecnologo_nombre
                   FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id LEFT JOIN examenes_catalogo e ON o.id_examen=e.id LEFT JOIN usuarios u ON o.tecnologo_id=u.id WHERE o.id=?""", (id_orden,))
    orden_data = cur.fetchone()
    if not orden_data: return "Orden no encontrada", 404
    cur.execute("""SELECT ep.nombre_parametro, ep.unidad, ep.rango_referencia, rl.resultado
                   FROM resultados_lab rl LEFT JOIN examenes_parametros ep ON rl.id_parametro=ep.id
                   WHERE rl.id_orden=? ORDER BY ep.orden ASC""", (id_orden,))
    resultados = cur.fetchall()
    conn.close()
    if not resultados:
        conn = get_db_connection(); cur=conn.cursor()
        cur.execute("SELECT resultado FROM resultados_lab WHERE id_orden=? AND id_parametro IS NULL", (id_orden,))
        row = cur.fetchone(); conn.close()
        if row:
            resultados = [('Resultado General', '', '', row[0])]
    config = obtener_configuracion()
    plantilla_html = obtener_plantilla_pdf('resultado')
    if not plantilla_html:
        plantilla_html = PLANTILLA_DEFAULT_RESULTADO
    datos = {
        'paciente': {
            'nombre': f"{orden_data[0]} {orden_data[1]}",
            'dni': orden_data[2],
            'edad': orden_data[5] or 'N/E',
            'sexo': orden_data[6] or 'N/E'
        },
        'examen': orden_data[3],
        'fecha_emision': orden_data[4],
        'fecha_resultado': orden_data[7] or 'No registrada',
        'tecnologo': orden_data[10] or 'No asignado',
        'validado': 'Sí' if orden_data[8] == 1 else 'No',
        'resultados': [{'parametro': r[0] or 'General', 'valor': r[3] or '', 'unidad': r[1] or '', 'rango': r[2] or ''} for r in resultados],
        'logo_url': url_for('static', filename=config[2]) if config[2] else '',
        'sello_url': url_for('static', filename=config[7]) if config[7] else '',
        'nombre_sistema': config[0] if config else 'SISGALENO2026',
        'footer': config[4] if config else '',
    }
    tamaño = obtener_tamano_pagina('resultado')
    tamaño_str = 'A4'
    if tamaño == A4: tamaño_str = 'A4'
    elif tamaño == A5: tamaño_str = 'A5'
    elif tamaño == LETTER: tamaño_str = 'LETTER'
    elif tamaño == LEGAL: tamaño_str = 'LEGAL'
    elif tamaño == TICKET_80MM: tamaño_str = 'TICKET_80MM'
    elif tamaño == TICKET_80MM_LANDSCAPE: tamaño_str = 'TICKET_80MM_LANDSCAPE'
    else: tamaño_str = 'A4'
    pdf_buffer = generar_pdf_desde_html(plantilla_html, datos, tamaño_str)
    return send_file(pdf_buffer, as_attachment=True, download_name=f"Resultado_{id_orden}.pdf", mimetype='application/pdf')

@app.route('/laboratorio/imprimir_etiqueta/<int:id_orden>')
@login_required
def imprimir_etiqueta(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT o.codigo_muestra, o.fecha_validez, p.nombre, p.apellido, p.historia_clinica FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id WHERE o.id=?", (id_orden,))
    orden = cur.fetchone(); conn.close()
    if not orden: return "Orden no encontrada", 404
    codigo = orden[0]
    if not codigo: return "Sin código de muestra", 400
    barcode_buffer = generar_codigo_barras(codigo)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w,h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(w/2, h-50, "ETIQUETA DE MUESTRA")
    c.setFont("Helvetica", 12)
    c.drawCentredString(w/2, h-80, f"Paciente: {orden[2]} {orden[3]}")
    c.drawCentredString(w/2, h-100, f"Historia: {orden[4]}")
    c.drawCentredString(w/2, h-120, f"Código: {codigo}")
    c.drawCentredString(w/2, h-140, f"Validez: {orden[1]}")
    img = ImageReader(barcode_buffer)
    c.drawImage(img, w/2-150, h-300, width=300, height=80, preserveAspectRatio=True)
    c.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"etiqueta_{codigo}.pdf", mimetype='application/pdf')

# ========================== MÓDULO ATENCIÓN MÉDICA ==========================
@app.route('/atencion_medica', methods=['GET','POST'])
@login_required
def atencion_medica():
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT especialidad FROM medicos WHERE activo=1 AND especialidad IS NOT NULL AND especialidad != '' ORDER BY especialidad")
    especialidades = [r[0] for r in cur.fetchall()]
    especialidad_seleccionada = request.args.get('especialidad')
    if not especialidad_seleccionada and session.get('rol') == 'medico':
        especialidad_seleccionada = session.get('especialidad_medico')
    if not especialidad_seleccionada and especialidades:
        especialidad_seleccionada = especialidades[0]
    if especialidad_seleccionada:
        cur.execute("""
            SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado,
                   p.historia_clinica, c.numero_boleta, m.especialidad, c.numero_orden
            FROM citas c
            JOIN pacientes p ON c.id_paciente = p.id
            JOIN servicios s ON c.id_servicio = s.id
            JOIN medicos m ON c.id_medico = m.id
            WHERE c.estado = 'Pagado' AND p.deleted = 0
              AND m.especialidad = ?
            ORDER BY c.id ASC
        """, (especialidad_seleccionada,))
    else:
        cur.execute("""
            SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado,
                   p.historia_clinica, c.numero_boleta, m.especialidad, c.numero_orden
            FROM citas c
            JOIN pacientes p ON c.id_paciente = p.id
            JOIN servicios s ON c.id_servicio = s.id
            JOIN medicos m ON c.id_medico = m.id
            WHERE c.estado = 'Pagado' AND p.deleted = 0
            ORDER BY c.id ASC
        """)
    citas = cur.fetchall(); conn.close()
    contenido = """
    <h2>🩺 Atención Médica</h2>
    <div class="card p-3 mb-3">
        <form method="GET" class="row g-2">
            <div class="col-md-4">
                <label>Especialidad</label>
                <select name="especialidad" class="form-control" onchange="this.form.submit()">
                    <option value="">Todas</option>
                    {% for esp in especialidades %}
                        <option value="{{ esp }}" {% if esp == especialidad_seleccionada %}selected{% endif %}>{{ esp }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-2"><label>&nbsp;</label><button type="submit" class="btn btn-primary w-100">Filtrar</button></div>
            <div class="col-md-2"><label>&nbsp;</label><a href="{{ url_for('atencion_medica') }}" class="btn btn-warning w-100">Limpiar</a></div>
        </form>
    </div>
    <div class="table-responsive">
        <table class="table table-hover">
            <thead><tr><th>N° Cita</th><th>HC</th><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Orden</th><th>Boleta</th><th>Acciones</th></tr></thead>
            <tbody>
                {% for c in citas %}
                <tr>
                    <td>{{ c[0] }}</td>
                    <td>{{ c[6] }}</td>
                    <td><a href="{{ url_for('atender_paciente', id_cita=c[0]) }}">{{ c[1] }} {{ c[2] }}</a></td>
                    <td>{{ c[3] }}</td>
                    <td>{{ c[4] }}</td>
                    <td>{{ c[9] or 'N/A' }}</td>
                    <td>{{ c[7] or '--' }}</td>
                    <td><a href="{{ url_for('atender_paciente', id_cita=c[0]) }}" class="btn btn-primary btn-sm">Atender</a></td>
                </tr>
                {% else %}
                <tr><td colspan="8" class="text-center">No hay citas pagadas para esta especialidad.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  citas=citas, especialidades=especialidades,
                                  especialidad_seleccionada=especialidad_seleccionada,
                                  system_background=system_bg, login_background=login_bg)

@app.route('/atencion_medica/paciente/<int:id_cita>', methods=['GET','POST'])
@login_required
def atender_paciente(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT c.id, p.id, p.nombre, p.apellido, p.dni, p.historia_clinica,
               p.fecha_nacimiento, p.edad, p.sexo, p.nro_afiliacion,
               s.nombre AS servicio, m.nombre||' '||m.apellido AS medico,
               c.fecha_cita, c.motivo_consulta, c.tipo_asegurado, c.numero_boleta, c.numero_orden
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        JOIN medicos m ON c.id_medico = m.id
        WHERE c.id = ?
    """, (id_cita,))
    cita = cur.fetchone()
    if not cita:
        conn.close(); flash('Cita no encontrada', 'danger'); return redirect(url_for('atencion_medica'))
    id_paciente = cita[1]
    numero_orden = cita[16] or generar_numero_orden()
    if request.method == 'POST' and 'diagnostico' in request.form:
        diag = request.form['diagnostico']; trat = request.form['tratamiento']; desc = int(request.form.get('descanso', 0))
        cur.execute("SELECT id FROM diagnosticos WHERE id_cita = ?", (id_cita,))
        existe = cur.fetchone()
        if existe:
            cur.execute("UPDATE diagnosticos SET diagnostico=?, tratamiento=?, descanso_medico_dias=? WHERE id_cita=?", (diag, trat, desc, id_cita))
        else:
            cur.execute("INSERT INTO diagnosticos (id_cita, id_medico, diagnostico, tratamiento, descanso_medico_dias, informe_pdf_path) VALUES (?, ?, ?, ?, ?, '')",
                        (id_cita, cur.execute("SELECT id_medico FROM citas WHERE id=?", (id_cita,)).fetchone()[0], diag, trat, desc))
        conn.commit(); generar_y_guardar_informe_pdf(id_cita); flash('Diagnóstico guardado.', 'success')
        return redirect(url_for('atender_paciente', id_cita=id_cita))
    if request.method == 'POST' and 'eliminar_diagnostico' in request.form:
        cur.execute("DELETE FROM diagnosticos WHERE id_cita = ?", (id_cita,))
        conn.commit(); flash('Diagnóstico eliminado.', 'success'); return redirect(url_for('atender_paciente', id_cita=id_cita))
    if request.method == 'POST' and 'crear_orden' in request.form:
        examenes_ids = request.form.getlist('examenes_ids[]')
        if examenes_ids:
            total = 0.0
            for eid in examenes_ids:
                cur.execute("SELECT precio FROM examenes_catalogo WHERE id=?", (eid,))
                row = cur.fetchone()
                if row: total += float(row[0])
            if not cita[16]:
                numero_orden = generar_numero_orden()
                cur.execute("UPDATE citas SET numero_orden=? WHERE id=?", (numero_orden, id_cita))
            else:
                numero_orden = cita[16]
            cur.execute("INSERT INTO ordenes_laboratorio (id_paciente, id_cita, fecha_emision, estado, precio_total, numero_orden) VALUES (?, ?, ?, 'Pendiente', ?, ?)",
                        (id_paciente, id_cita, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), total, numero_orden))
            id_orden = cur.lastrowid
            for eid in examenes_ids:
                cur.execute("SELECT precio FROM examenes_catalogo WHERE id=?", (eid,))
                row = cur.fetchone()
                precio = float(row[0]) if row else 0.0
                cur.execute("INSERT INTO orden_examenes (id_orden, id_examen, precio) VALUES (?,?,?)", (id_orden, eid, precio))
            conn.commit()
            flash(f'Orden médica #{numero_orden} creada con {len(examenes_ids)} exámenes.', 'success')
            return redirect(url_for('atender_paciente', id_cita=id_cita))
        else:
            flash('Seleccione al menos un examen.', 'danger')
            return redirect(url_for('atender_paciente', id_cita=id_cita))
    cur.execute("SELECT id, diagnostico, tratamiento, descanso_medico_dias, informe_pdf_path FROM diagnosticos WHERE id_cita = ?", (id_cita,))
    diagnostico = cur.fetchone()
    cur.execute("""
        SELECT o.id, o.fecha_emision, o.estado, o.tipo_orden, o.precio_total,
               o.codigo_muestra, o.descripcion_orden, o.numero_orden, o.numero_boleta,
               GROUP_CONCAT(e.descripcion, ', ') AS examenes
        FROM ordenes_laboratorio o
        LEFT JOIN orden_examenes oe ON oe.id_orden = o.id
        LEFT JOIN examenes_catalogo e ON oe.id_examen = e.id
        WHERE o.id_paciente = ?
        GROUP BY o.id
        ORDER BY o.fecha_emision DESC
    """, (id_paciente,))
    ordenes = cur.fetchall()
    fecha_desde = request.args.get('fecha_desde'); fecha_hasta = request.args.get('fecha_hasta')
    tipo = request.args.get('tipo')
    sql_resultados = """
        SELECT o.id, o.fecha_emision, o.tipo_orden, o.estado,
               COALESCE(e.descripcion, o.descripcion_orden) AS descripcion,
               r.resultado, o.fecha_resultado
        FROM ordenes_laboratorio o
        LEFT JOIN examenes_catalogo e ON o.id_examen = e.id
        LEFT JOIN resultados_lab r ON r.id_orden = o.id
        WHERE o.id_paciente = ?
    """
    params = [id_paciente]
    if fecha_desde: sql_resultados += " AND date(o.fecha_emision) >= ?"; params.append(fecha_desde)
    if fecha_hasta: sql_resultados += " AND date(o.fecha_emision) <= ?"; params.append(fecha_hasta)
    if tipo: sql_resultados += " AND o.tipo_orden = ?"; params.append(tipo)
    sql_resultados += " ORDER BY o.fecha_emision DESC"
    cur.execute(sql_resultados, params); resultados = cur.fetchall()
    cur.execute("""
        SELECT r.id, r.numero_cuenta, r.fecha_emision, r.estado, r.diagnostico, r.indicaciones
        FROM recetas r WHERE r.id_paciente = ? ORDER BY r.fecha_emision DESC
    """, (id_paciente,)); recetas = cur.fetchall()
    cur.execute("SELECT id, codigo, descripcion, precio FROM examenes_catalogo WHERE activo=1 ORDER BY descripcion")
    examenes_catalogo = cur.fetchall()
    conn.close()
    contenido = """
    <h2>🩺 Atender Paciente</h2>
    <div class="card p-3 mb-3">
        <div class="row"><div class="col-md-6">
            <p><strong>Paciente:</strong> {{ cita[2] }} {{ cita[3] }}</p>
            <p><strong>DNI:</strong> {{ cita[4] }}</p>
            <p><strong>HC:</strong> {{ cita[5] }}</p>
            <p><strong>Edad:</strong> {{ cita[7] }} años</p>
            <p><strong>Sexo:</strong> {{ cita[8] }}</p>
            <p><strong>Afiliación:</strong> {{ cita[9] or 'N/E' }}</p>
        </div><div class="col-md-6">
            <p><strong>Servicio:</strong> {{ cita[10] }}</p>
            <p><strong>Médico:</strong> {{ cita[11] }}</p>
            <p><strong>Fecha cita:</strong> {{ cita[12] }}</p>
            <p><strong>Motivo:</strong> {{ cita[13] or 'N/E' }}</p>
            <p><strong>Boleta:</strong> {{ cita[15] or '--' }}</p>
            <p><strong>N° Orden:</strong> {{ cita[16] or 'No generada' }}</p>
        </div></div>
    </div>
    <ul class="nav nav-tabs" id="myTab" role="tablist">
        <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#diagnostico">📋 Diagnóstico</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#ordenes">📋 Órdenes</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#resultados">📊 Resultados</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#recetas">📝 Recetas</button></li>
        <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#crear_orden">➕ Crear Orden</button></li>
    </ul>
    <div class="tab-content mt-3">
        <div class="tab-pane fade show active" id="diagnostico">
            <div class="card p-3">
                <form method="POST">
                    <div class="row g-2">
                        <div class="col-md-6"><label>Diagnóstico</label><textarea name="diagnostico" class="form-control" rows="3" required>{{ diagnostico[1] if diagnostico else '' }}</textarea></div>
                        <div class="col-md-6"><label>Tratamiento</label><textarea name="tratamiento" class="form-control" rows="3" required>{{ diagnostico[2] if diagnostico else '' }}</textarea></div>
                        <div class="col-md-3"><label>Días descanso</label><input type="number" name="descanso" class="form-control" value="{{ diagnostico[3] if diagnostico else 0 }}"></div>
                        <div class="col-md-3"><label>&nbsp;</label><button class="btn btn-success w-100">Guardar</button></div>
                        {% if diagnostico and diagnostico[4] %}
                        <div class="col-md-3"><a href="{{ url_for('ver_informe', id_cita=id_cita) }}" class="btn btn-primary w-100" target="_blank">Ver</a></div>
                        <div class="col-md-3"><a href="{{ url_for('exportar_informe', id_cita=id_cita) }}" class="btn btn-warning w-100">Descargar</a></div>
                        {% endif %}
                    </div>
                </form>
                {% if diagnostico %}
                <div class="mt-3">
                    <form method="POST" style="display:inline;" onsubmit="return confirm('¿Eliminar diagnóstico?')">
                        <input type="hidden" name="eliminar_diagnostico" value="1">
                        <button class="btn btn-danger btn-sm">Eliminar</button>
                    </form>
                </div>
                {% endif %}
            </div>
        </div>
        <div class="tab-pane fade" id="ordenes">
            <div class="card p-3">
                <table class="table"><thead><tr><th>N° Orden</th><th>Boleta</th><th>Fecha</th><th>Estado</th><th>Total</th><th>Exámenes</th><th>Acciones</th></tr></thead>
                <tbody>{% for o in ordenes %}
                <tr><td>{{ o[7] or 'N/A' }}</td><td>{{ o[8] or '--' }}</td><td>{{ o[1] }}</td><td><span class="badge badge-{{ 'pagado' if o[2]=='Completado' else 'pendiente' }}">{{ o[2] }}</span></td><td>S/ {{ o[4] }}</td><td>{{ o[9] or '--' }}</td>
                <td><a href="{{ url_for('ver_orden', id_orden=o[0]) }}" class="btn btn-info btn-sm">Ver</a>
                {% if o[2] == 'Pendiente' or o[2] == 'Muestra Tomada' %}
                <a href="{{ url_for('editar_orden', id_orden=o[0]) }}" class="btn btn-warning btn-sm">Editar</a>
                <form style="display:inline;" method="POST" action="{{ url_for('eliminar_orden', id_orden=o[0]) }}" onsubmit="return confirm('¿Eliminar?')"><button class="btn btn-danger btn-sm">Eliminar</button></form>
                {% endif %}
                {% if o[2] == 'Completado' %}<a href="{{ url_for('ver_imagenes_orden', id_orden=o[0]) }}" class="btn btn-secondary btn-sm">🖼️</a>{% endif %}</td></tr>
                {% else %}<tr><td colspan="7">Sin órdenes</td></tr>{% endfor %}</tbody></table>
            </div>
        </div>
        <div class="tab-pane fade" id="resultados">
            <div class="card p-3">
                <form method="GET" class="row g-2 mb-3">
                    <div class="col-md-3"><label>Desde</label><input type="date" name="fecha_desde" class="form-control"></div>
                    <div class="col-md-3"><label>Hasta</label><input type="date" name="fecha_hasta" class="form-control"></div>
                    <div class="col-md-3"><label>Tipo</label>
                        <select name="tipo" class="form-control">
                            <option value="">Todos</option>
                            <option value="examen">Laboratorio</option>
                            <option value="rx">Rayos X</option>
                            <option value="ecografia">Ecografía</option>
                            <option value="tomografia">Tomografía</option>
                            <option value="resonancia">Resonancia</option>
                        </select>
                    </div>
                    <div class="col-md-3"><button class="btn btn-primary w-100">Filtrar</button></div>
                </form>
                <table class="table"><thead><tr><th>Fecha</th><th>Tipo</th><th>Descripción</th><th>Resultado</th><th>Estado</th></tr></thead>
                <tbody>{% for r in resultados %}<tr><td>{{ r[1] }}</td><td>{{ r[2] }}</td><td>{{ r[4] }}</td><td>{{ r[5] or 'Pendiente' }}</td><td>{{ r[3] }}</td></tr>{% else %}<tr><td colspan="5">Sin resultados</td></tr>{% endfor %}</tbody></table>
            </div>
        </div>
        <div class="tab-pane fade" id="recetas">
            <div class="card p-3">
                <a href="{{ url_for('nueva_receta', id_cita=id_cita) }}" class="btn btn-primary mb-3">+ Nueva Receta</a>
                <table class="table"><thead><tr><th>Nº Cuenta</th><th>Fecha</th><th>Diagnóstico</th><th>Estado</th><th>Acciones</th></tr></thead>
                <tbody>{% for rec in recetas %}<tr><td>{{ rec[1] }}</td><td>{{ rec[2] }}</td><td>{{ rec[4] or 'N/E' }}</td><td><span class="badge bg-{{ 'success' if rec[3]=='activa' else 'danger' }}">{{ rec[3] }}</span></td>
                <td><a href="{{ url_for('ver_receta', id_receta=rec[0]) }}" class="btn btn-info btn-sm">Ver</a>
                <a href="{{ url_for('imprimir_receta_pdf', id_receta=rec[0]) }}" class="btn btn-warning btn-sm">PDF</a>
                <form style="display:inline;" method="POST" action="{{ url_for('eliminar_receta', id_receta=rec[0]) }}" onsubmit="return confirm('¿Eliminar?')"><button class="btn btn-danger btn-sm">Eliminar</button></form></td></tr>
                {% else %}<tr><td colspan="5">Sin recetas</td></tr>{% endfor %}</tbody></table>
            </div>
        </div>
        <div class="tab-pane fade" id="crear_orden">
            <div class="card p-3">
                <form method="POST">
                    <input type="hidden" name="crear_orden" value="1">
                    <div class="row">
                        <div class="col-md-6"><div class="border rounded p-2" style="max-height:300px; overflow-y:auto;">
                            {% for e in examenes_catalogo %}
                            <div class="form-check"><input class="form-check-input" type="checkbox" name="examenes_ids[]" value="{{ e[0] }}" id="ex_{{ e[0] }}">
                                <label for="ex_{{ e[0] }}">{{ e[1] }} - {{ e[2] }} (S/ {{ e[3] }})</label></div>
                            {% endfor %}
                        </div></div>
                        <div class="col-md-6"><div class="border rounded p-2 bg-light">
                            <h6>Resumen</h6><div id="resumen_seleccion"><p class="text-muted">Seleccione exámenes</p></div>
                            <p><strong>Total: S/ <span id="total_seleccion">0.00</span></strong></p>
                            <button type="submit" class="btn btn-success">Crear Orden Médica</button>
                        </div></div>
                    </div>
                </form>
            </div>
        </div>
    </div>
    <button onclick="window.print()" class="btn btn-warning mt-3 no-print">🖨️ Imprimir</button>
    <a href="{{ url_for('atencion_medica') }}" class="btn btn-secondary mt-3">Volver</a>
    <script>
        document.querySelectorAll('input[name="examenes_ids[]"]').forEach(function(chk) {
            chk.addEventListener('change', function() {
                var total = 0, resumen = [];
                document.querySelectorAll('input[name="examenes_ids[]"]:checked').forEach(function(c) {
                    var label = c.parentElement.querySelector('label').textContent;
                    var precio = parseFloat(label.split('S/')[1]) || 0;
                    total += precio; resumen.push(label);
                });
                var div = document.getElementById('resumen_seleccion');
                div.innerHTML = resumen.length ? '<ul>' + resumen.map(function(item) { return '<li>' + item + '</li>'; }).join('') + '</ul>' : '<p class="text-muted">Seleccione exámenes</p>';
                document.getElementById('total_seleccion').textContent = total.toFixed(2);
            });
        });
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  cita=cita, id_cita=id_cita, diagnostico=diagnostico,
                                  ordenes=ordenes, resultados=resultados, recetas=recetas,
                                  examenes_catalogo=examenes_catalogo,
                                  system_background=system_bg, login_background=login_bg)

# ========================== FUNCIONES AUXILIARES ATENCIÓN ==========================
def generar_y_guardar_informe_pdf(id_cita):
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""SELECT p.nombre, p.apellido, p.dni, s.nombre, m.nombre||' '||m.apellido, c.fecha_cita
                       FROM citas c JOIN pacientes p ON c.id_paciente=p.id JOIN servicios s ON c.id_servicio=s.id JOIN medicos m ON c.id_medico=m.id WHERE c.id=?""", (id_cita,))
        cita = cur.fetchone()
        cur.execute("SELECT diagnostico, tratamiento, descanso_medico_dias FROM diagnosticos WHERE id_cita=?", (id_cita,))
        diag = cur.fetchone(); conn.close()
        if not cita or not diag: return
        config = obtener_configuracion()
        plantilla = """
        <!DOCTYPE html><html><head><meta charset="UTF-8"><title>Informe Médico</title>
        <style>body { font-family: Arial; margin: 20mm; } .header { text-align: center; border-bottom: 2px solid #0d2b45; } .info { margin: 20px 0; } .footer { margin-top: 40px; text-align: center; }</style></head>
        <body><div class="header"><h2>{{ nombre_sistema }}</h2><h3>INFORME DE ATENCIÓN CLÍNICA</h3></div>
        <div class="info"><p><strong>Paciente:</strong> {{ cita[0] }} {{ cita[1] }}</p>
        <p><strong>DNI:</strong> {{ cita[2] }}</p><p><strong>Servicio:</strong> {{ cita[3] }}</p>
        <p><strong>Médico:</strong> Dr. {{ cita[4] }}</p><p><strong>Fecha:</strong> {{ cita[5] }}</p>
        <p><strong>Diagnóstico:</strong> {{ diag[0] or 'No especificado' }}</p>
        <p><strong>Tratamiento:</strong> {{ diag[1] or 'No especificado' }}</p>
        <p><strong>Descanso:</strong> {{ diag[2] }} días</p></div>
        <div class="footer">{{ footer }}</div></body></html>
        """
        from jinja2 import Template
        html = Template(plantilla).render(nombre_sistema=config[0], cita=cita, diag=diag, footer=config[6])
        pdf = generar_pdf_desde_html(html, {}, 'A4')
        os.makedirs('static/informes_medicos', exist_ok=True)
        filename = f"informe_{id_cita}.pdf"
        with open(os.path.join('static/informes_medicos', filename), 'wb') as f: f.write(pdf.getvalue())
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("UPDATE diagnosticos SET informe_pdf_path=? WHERE id_cita=?", (f"informes_medicos/{filename}", id_cita))
        conn.commit(); conn.close()
    except Exception as e: app.logger.error(f"Error en informe: {e}")

@app.route('/atencion_medica/ver_informe/<int:id_cita>')
@login_required
def ver_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT informe_pdf_path FROM diagnosticos WHERE id_cita=?", (id_cita,))
    res = cur.fetchone(); conn.close()
    if res and res[0]: return send_file(os.path.join('static', res[0]), mimetype='application/pdf')
    return "Informe no encontrado", 404

@app.route('/atencion_medica/exportar_informe/<int:id_cita>')
@login_required
def exportar_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT informe_pdf_path FROM diagnosticos WHERE id_cita=?", (id_cita,))
    res = cur.fetchone(); conn.close()
    if res and res[0]: return send_file(os.path.join('static', res[0]), as_attachment=True, download_name=f"Informe_{id_cita}.pdf", mimetype='application/pdf')
    return "Informe no encontrado", 404

# ========================== MÓDULO ÓRDENES Y RECETAS ==========================
@app.route('/orden_examenes', methods=['GET','POST'])
@login_required
def orden_examenes():
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    # ... (similar al original)
    return "Solicitar Exámenes"

@app.route('/ver_orden/<int:id_orden>')
@login_required
def ver_orden(id_orden):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Ver Orden"

@app.route('/editar_orden/<int:id_orden>', methods=['GET','POST'])
@login_required
def editar_orden(id_orden):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Editar Orden"

@app.route('/eliminar_orden/<int:id_orden>', methods=['POST'])
@login_required
def eliminar_orden(id_orden):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Eliminar Orden"

@app.route('/mis_ordenes')
@login_required
def mis_ordenes():
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Mis Órdenes"

@app.route('/ver_imagenes/<int:id_orden>')
@login_required
def ver_imagenes_orden(id_orden):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Imágenes"

@app.route('/recetas')
@login_required
def recetas():
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Recetas"

@app.route('/recetas/nueva/<int:id_cita>', methods=['GET','POST'])
@login_required
def nueva_receta(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Nueva Receta"

@app.route('/api/procedimientos')
def api_procedimientos():
    q = request.args.get('q', '')
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, codigo, nombre, precio FROM procedimientos WHERE activo=1 AND (codigo LIKE ? OR nombre LIKE ?)", (f'%{q}%', f'%{q}%'))
    res = cur.fetchall(); conn.close()
    return jsonify([{'id':r[0], 'codigo':r[1], 'nombre':r[2], 'precio':r[3]} for r in res])

@app.route('/recetas/ver/<int:id_receta>')
@login_required
def ver_receta(id_receta):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Ver Receta"

@app.route('/recetas/pdf/<int:id_receta>')
@login_required
def imprimir_receta_pdf(id_receta):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "PDF Receta"

@app.route('/eliminar_receta/<int:id_receta>', methods=['POST'])
@login_required
def eliminar_receta(id_receta):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    return "Eliminar Receta"

# ========================== MÓDULO ENFERMERÍA ==========================
@app.route('/enfermeria')
@login_required
def enfermeria():
    if 'Enfermería' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    hoy = date.today().isoformat()
    cur.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita) = ? AND estado = 'Pagado'", (hoy,))
    citas_hoy = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM triaje WHERE date(fecha_hora) = ?", (hoy,))
    triajes_hoy = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM inyectables WHERE date(fecha_hora) = ?", (hoy,))
    inyecciones_hoy = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM pacientes WHERE deleted=0")
    total_pacientes = cur.fetchone()[0]
    conn.close()
    contenido = """
    <h2>🏥 Módulo de Enfermería</h2>
    <div class="row mt-4">
        <div class="col-md-3"><div class="card"><div class="card-body"><h5>Citas hoy</h5><h2>{{ citas_hoy }}</h2></div></div></div>
        <div class="col-md-3"><div class="card"><div class="card-body"><h5>Triajes hoy</h5><h2>{{ triajes_hoy }}</h2></div></div></div>
        <div class="col-md-3"><div class="card"><div class="card-body"><h5>Inyecciones hoy</h5><h2>{{ inyecciones_hoy }}</h2></div></div></div>
        <div class="col-md-3"><div class="card"><div class="card-body"><h5>Pacientes</h5><h2>{{ total_pacientes }}</h2></div></div></div>
    </div>
    <div class="menu-grid">
        <a href="{{ url_for('buscar_paciente') }}" class="menu-item">🔍 Buscar Paciente</a>
        <a href="{{ url_for('registrar_triaje') }}" class="menu-item">📋 Registrar Triaje</a>
        <a href="{{ url_for('registrar_inyectable') }}" class="menu-item">💉 Registrar Inyectable</a>
        <a href="{{ url_for('historias_clinicas') }}" class="menu-item">📄 Historia Clínica</a>
    </div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  citas_hoy=citas_hoy, triajes_hoy=triajes_hoy, inyecciones_hoy=inyecciones_hoy,
                                  total_pacientes=total_pacientes, system_background=system_bg, login_background=login_bg)

@app.route('/enfermeria/buscar_paciente', methods=['GET','POST'])
@login_required
def buscar_paciente():
    if 'Enfermería' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    resultados = []
    if request.method == 'POST':
        busqueda = request.form.get('busqueda', '').strip()
        if busqueda:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes WHERE deleted=0 AND (dni LIKE ? OR historia_clinica LIKE ? OR nombre LIKE ? OR apellido LIKE ?) ORDER BY nombre",
                        (f'%{busqueda}%', f'%{busqueda}%', f'%{busqueda}%', f'%{busqueda}%'))
            resultados = cur.fetchall(); conn.close()
    contenido = """
    <h2>🔍 Buscar Paciente</h2>
    <form method="POST" class="row g-2">
        <div class="col-md-8"><input type="text" name="busqueda" class="form-control" placeholder="DNI, HC, nombre o apellido" required></div>
        <div class="col-md-2"><button class="btn btn-primary w-100">Buscar</button></div>
        <div class="col-md-2"><a href="{{ url_for('enfermeria') }}" class="btn btn-secondary w-100">Volver</a></div>
    </form>
    {% if resultados %}
    <h5 class="mt-3">Resultados</h5>
    <table class="table"><thead><tr><th>HC</th><th>DNI</th><th>Nombre</th><th>Apellido</th><th>Acciones</th></tr></thead>
    <tbody>{% for p in resultados %}<tr><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td>{{ p[4] }}</td>
    <td><a href="{{ url_for('historia_clinica_paciente', id_paciente=p[0]) }}" class="btn btn-primary btn-sm">Ver Historia</a>
    <a href="{{ url_for('registrar_triaje', id_paciente=p[0]) }}" class="btn btn-warning btn-sm">Triaje</a>
    <a href="{{ url_for('registrar_inyectable', id_paciente=p[0]) }}" class="btn btn-info btn-sm">Inyectable</a></td></tr>{% endfor %}</tbody></table>
    {% endif %}
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  resultados=resultados, system_background=system_bg, login_background=login_bg)

@app.route('/enfermeria/triaje', methods=['GET','POST'])
@login_required
def registrar_triaje():
    if 'Enfermería' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    id_paciente_pre = request.args.get('id_paciente')
    conn = get_db_connection(); cur = conn.cursor()
    if id_paciente_pre:
        cur.execute("SELECT id, nombre, apellido, dni FROM pacientes WHERE id=?", (id_paciente_pre,))
        paciente_pre = cur.fetchone()
    else:
        paciente_pre = None
    if request.method == 'POST':
        id_paciente = request.form.get('id_paciente'); id_cita = request.form.get('id_cita') or None
        presion = request.form.get('presion_arterial'); temperatura = request.form.get('temperatura')
        fc = request.form.get('frecuencia_cardiaca'); fr = request.form.get('frecuencia_respiratoria')
        peso = request.form.get('peso'); talla = request.form.get('talla'); imc = request.form.get('imc')
        sintomas = request.form.get('sintomas'); alergias = request.form.get('alergias')
        medicamentos = request.form.get('medicamentos_actuales'); observaciones = request.form.get('observaciones')
        id_enfermera = session.get('id_usuario')
        cur.execute("""INSERT INTO triaje (id_paciente, id_cita, presion_arterial, temperatura,
            frecuencia_cardiaca, frecuencia_respiratoria, peso, talla, imc,
            sintomas, alergias, medicamentos_actuales, observaciones, id_enfermera)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (id_paciente, id_cita, presion, temperatura, fc, fr, peso, talla, imc,
             sintomas, alergias, medicamentos, observaciones, id_enfermera))
        conn.commit(); conn.close(); flash('Triaje registrado correctamente.', 'success')
        return redirect(url_for('enfermeria'))
    citas = []
    if id_paciente_pre:
        cur.execute("SELECT id, fecha_cita, estado FROM citas WHERE id_paciente = ? ORDER BY fecha_cita DESC", (id_paciente_pre,))
        citas = cur.fetchall()
    conn.close()
    contenido = """
    <h2>📋 Registrar Triaje</h2>
    <div class="card p-3">
        <form method="POST">
            <div class="row g-2">
                <div class="col-md-6"><label>Paciente</label><select name="id_paciente" class="form-control" required>
                    <option value="">Seleccione</option>
                    {% if paciente_pre %}<option value="{{ paciente_pre[0] }}" selected>{{ paciente_pre[1] }} {{ paciente_pre[2] }} ({{ paciente_pre[3] }})</option>{% endif %}
                    {% for p in pacientes %}<option value="{{ p[0] }}">{{ p[1] }} {{ p[2] }} ({{ p[3] }})</option>{% endfor %}
                </select></div>
                <div class="col-md-6"><label>Cita (opcional)</label><select name="id_cita" class="form-control"><option value="">Sin cita</option>{% for c in citas %}<option value="{{ c[0] }}">{{ c[1] }} - {{ c[2] }}</option>{% endfor %}</select></div>
                <div class="col-md-4"><label>Presión Arterial</label><input type="text" name="presion_arterial" class="form-control" placeholder="120/80"></div>
                <div class="col-md-2"><label>Temperatura</label><input type="number" step="0.1" name="temperatura" class="form-control"></div>
                <div class="col-md-2"><label>Frec. Cardiaca</label><input type="number" name="frecuencia_cardiaca" class="form-control"></div>
                <div class="col-md-2"><label>Frec. Respiratoria</label><input type="number" name="frecuencia_respiratoria" class="form-control"></div>
                <div class="col-md-2"><label>Peso (kg)</label><input type="number" step="0.1" name="peso" class="form-control"></div>
                <div class="col-md-2"><label>Talla (cm)</label><input type="number" step="0.1" name="talla" class="form-control"></div>
                <div class="col-md-2"><label>IMC</label><input type="number" step="0.1" name="imc" class="form-control" readonly></div>
                <div class="col-md-6"><label>Síntomas</label><textarea name="sintomas" class="form-control" rows="2"></textarea></div>
                <div class="col-md-6"><label>Alergias</label><textarea name="alergias" class="form-control" rows="2"></textarea></div>
                <div class="col-md-6"><label>Medicamentos actuales</label><textarea name="medicamentos_actuales" class="form-control" rows="2"></textarea></div>
                <div class="col-md-6"><label>Observaciones</label><textarea name="observaciones" class="form-control" rows="2"></textarea></div>
                <div class="col-md-12"><button class="btn btn-success">Guardar Triaje</button></div>
            </div>
        </form>
    </div>
    <script>
        document.querySelector('input[name="peso"]').addEventListener('input', calcularIMC);
        document.querySelector('input[name="talla"]').addEventListener('input', calcularIMC);
        function calcularIMC() {
            var peso = parseFloat(document.querySelector('input[name="peso"]').value);
            var talla = parseFloat(document.querySelector('input[name="talla"]').value);
            if (peso && talla && talla > 0) {
                var imc = peso / ((talla/100) * (talla/100));
                document.querySelector('input[name="imc"]').value = imc.toFixed(1);
            }
        }
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  paciente_pre=paciente_pre, citas=citas,
                                  system_background=system_bg, login_background=login_bg)

@app.route('/enfermeria/inyectable', methods=['GET','POST'])
@login_required
def registrar_inyectable():
    if 'Enfermería' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    id_paciente_pre = request.args.get('id_paciente')
    conn = get_db_connection(); cur = conn.cursor()
    if id_paciente_pre:
        cur.execute("SELECT id, nombre, apellido, dni FROM pacientes WHERE id=?", (id_paciente_pre,))
        paciente_pre = cur.fetchone()
    else:
        paciente_pre = None
    if request.method == 'POST':
        id_paciente = request.form.get('id_paciente'); id_cita = request.form.get('id_cita') or None
        medicamento = request.form['medicamento']; dosis = request.form.get('dosis')
        via = request.form.get('via_administracion'); lote = request.form.get('lote')
        observaciones = request.form.get('observaciones'); id_enfermera = session.get('id_usuario')
        cur.execute("INSERT INTO inyectables (id_paciente, id_cita, medicamento, dosis, via_administracion, lote, observaciones, id_enfermera) VALUES (?,?,?,?,?,?,?,?)",
                    (id_paciente, id_cita, medicamento, dosis, via, lote, observaciones, id_enfermera))
        conn.commit(); conn.close(); flash('Inyectable registrado.', 'success')
        return redirect(url_for('enfermeria'))
    citas = []
    if id_paciente_pre:
        cur.execute("SELECT id, fecha_cita, estado FROM citas WHERE id_paciente = ? ORDER BY fecha_cita DESC", (id_paciente_pre,))
        citas = cur.fetchall()
    conn.close()
    contenido = """
    <h2>💉 Registrar Inyectable</h2>
    <div class="card p-3">
        <form method="POST">
            <div class="row g-2">
                <div class="col-md-6"><label>Paciente</label><select name="id_paciente" class="form-control" required>
                    <option value="">Seleccione</option>
                    {% if paciente_pre %}<option value="{{ paciente_pre[0] }}" selected>{{ paciente_pre[1] }} {{ paciente_pre[2] }} ({{ paciente_pre[3] }})</option>{% endif %}
                    {% for p in pacientes %}<option value="{{ p[0] }}">{{ p[1] }} {{ p[2] }} ({{ p[3] }})</option>{% endfor %}
                </select></div>
                <div class="col-md-6"><label>Cita (opcional)</label><select name="id_cita" class="form-control"><option value="">Sin cita</option>{% for c in citas %}<option value="{{ c[0] }}">{{ c[1] }} - {{ c[2] }}</option>{% endfor %}</select></div>
                <div class="col-md-4"><label>Medicamento *</label><input type="text" name="medicamento" class="form-control" required></div>
                <div class="col-md-2"><label>Dosis</label><input type="text" name="dosis" class="form-control" placeholder="ej. 1 ampolla"></div>
                <div class="col-md-3"><label>Vía Administración</label><select name="via_administracion" class="form-control"><option value="">Seleccione</option>
                    <option value="Intramuscular">Intramuscular</option><option value="Intravenosa">Intravenosa</option>
                    <option value="Subcutánea">Subcutánea</option><option value="Oral">Oral</option><option value="Tópica">Tópica</option></select></div>
                <div class="col-md-3"><label>Lote</label><input type="text" name="lote" class="form-control"></div>
                <div class="col-md-12"><label>Observaciones</label><textarea name="observaciones" class="form-control" rows="2"></textarea></div>
                <div class="col-md-12"><button class="btn btn-success">Guardar</button></div>
            </div>
        </form>
    </div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  paciente_pre=paciente_pre, citas=citas,
                                  system_background=system_bg, login_background=login_bg)

# ========================== MÓDULO HISTORIAS CLÍNICAS ==========================
@app.route('/historias_clinicas', methods=['GET', 'POST'])
@login_required
def historias_clinicas():
    if 'Historias Clínicas' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    paciente_info = None; historial = None
    if request.method == 'POST':
        search = request.form.get('search', '').strip()
        if search:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT id, historia_clinica, dni, nombre, apellido, fecha_nacimiento, edad, sexo, nro_afiliacion, telefono, direccion FROM pacientes WHERE (dni = ? OR historia_clinica = ?) AND deleted=0", (search, search))
            row = cur.fetchone()
            if row:
                paciente_info = {'id':row[0], 'historia_clinica':row[1], 'dni':row[2], 'nombre':row[3], 'apellido':row[4],
                                 'fecha_nacimiento':row[5], 'edad':row[6], 'sexo':row[7], 'nro_afiliacion':row[8],
                                 'telefono':row[9], 'direccion':row[10]}
                # Obtener todo el historial
                cur.execute("SELECT id, fecha_cita, estado, motivo_consulta, tipo_asegurado, numero_boleta, numero_orden FROM citas WHERE id_paciente = ? ORDER BY fecha_cita DESC", (row[0],))
                citas = cur.fetchall()
                cur.execute("SELECT d.*, c.fecha_cita FROM diagnosticos d JOIN citas c ON d.id_cita = c.id WHERE c.id_paciente = ? ORDER BY c.fecha_cita DESC", (row[0],))
                diagnosticos = cur.fetchall()
                cur.execute("SELECT * FROM ordenes_laboratorio WHERE id_paciente = ? ORDER BY fecha_emision DESC", (row[0],))
                ordenes = cur.fetchall()
                cur.execute("SELECT r.*, o.fecha_emision, o.tipo_orden, e.descripcion as examen_desc FROM resultados_lab r JOIN ordenes_laboratorio o ON r.id_orden = o.id LEFT JOIN examenes_catalogo e ON r.id_examen = e.id WHERE o.id_paciente = ? ORDER BY r.fecha_creacion DESC", (row[0],))
                resultados_lab = cur.fetchall()
                cur.execute("SELECT r.*, c.fecha_cita FROM recetas r JOIN citas c ON r.id_cita = c.id WHERE r.id_paciente = ? ORDER BY r.fecha_emision DESC", (row[0],))
                recetas = cur.fetchall()
                cur.execute("SELECT i.*, o.fecha_emision FROM imagenes_laboratorio i JOIN ordenes_laboratorio o ON i.id_orden = o.id WHERE o.id_paciente = ? ORDER BY i.fecha_subida DESC", (row[0],))
                imagenes = cur.fetchall()
                cur.execute("SELECT * FROM triaje WHERE id_paciente = ? ORDER BY fecha_hora DESC", (row[0],))
                triajes = cur.fetchall()
                cur.execute("SELECT * FROM inyectables WHERE id_paciente = ? ORDER BY fecha_hora DESC", (row[0],))
                inyectables = cur.fetchall()
                cur.execute("SELECT id, titulo, descripcion, ruta_archivo, tipo_archivo, fecha_subida, usuario_subio FROM anexos_historia WHERE id_paciente = ? ORDER BY fecha_subida DESC", (row[0],))
                anexos = cur.fetchall()
                historial = {'citas':citas, 'diagnosticos':diagnosticos, 'ordenes':ordenes, 'resultados_lab':resultados_lab,
                             'recetas':recetas, 'imagenes':imagenes, 'triajes':triajes, 'inyectables':inyectables, 'anexos':anexos}
            else:
                flash('Paciente no encontrado', 'danger')
            conn.close()
    contenido = """
    <h2>📋 Historias Clínicas</h2>
    <div class="card p-3 mb-3">
        <form method="POST" class="row g-2">
            <div class="col-md-8"><input type="text" name="search" class="form-control" placeholder="Buscar por DNI o Historia Clínica" value="{{ request.form.get('search', '') }}"></div>
            <div class="col-md-2"><button class="btn btn-primary w-100">Buscar</button></div>
            <div class="col-md-2"><a href="{{ url_for('historias_clinicas') }}" class="btn btn-warning w-100">Limpiar</a></div>
        </form>
    </div>
    {% if paciente_info %}
    <div class="card p-3 mb-3">
        <h4>Datos del Paciente</h4>
        <div class="row">
            <div class="col-md-4"><strong>HC:</strong> {{ paciente_info.historia_clinica }}</div>
            <div class="col-md-4"><strong>DNI:</strong> {{ paciente_info.dni }}</div>
            <div class="col-md-4"><strong>Nombre:</strong> {{ paciente_info.nombre }} {{ paciente_info.apellido }}</div>
            <div class="col-md-3"><strong>Edad:</strong> {{ paciente_info.edad }} años</div>
            <div class="col-md-3"><strong>Sexo:</strong> {{ paciente_info.sexo or 'N/E' }}</div>
            <div class="col-md-3"><strong>Afiliación:</strong> {{ paciente_info.nro_afiliacion or 'N/E' }}</div>
            <div class="col-md-3"><strong>Teléfono:</strong> {{ paciente_info.telefono or 'N/E' }}</div>
            <div class="col-md-12"><strong>Dirección:</strong> {{ paciente_info.direccion or 'N/E' }}</div>
        </div>
    </div>
    <ul class="nav nav-tabs" id="historialTab" role="tablist">
        <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#citas">Citas</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#diagnosticos">Diagnósticos</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#ordenes">Órdenes</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#resultados">Resultados</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#recetas">Recetas</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#triajes">Triajes</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#inyectables">Inyectables</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#imagenes">Imágenes</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#anexos">📎 Anexos</a></li>
    </ul>
    <div class="tab-content mt-3">
        <div class="tab-pane active" id="citas">
            <table class="table"><thead><tr><th>Fecha</th><th>Estado</th><th>Motivo</th><th>Boleta</th><th>Orden</th></tr></thead>
            <tbody>{% for c in historial.citas %}<tr><td>{{ c[1] }}</td><td>{{ c[2] }}</td><td>{{ c[3] or '' }}</td><td>{{ c[5] or '--' }}</td><td>{{ c[6] or 'N/A' }}</td></tr>{% else %}<tr><td colspan="5">Sin citas</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="diagnosticos">
            <table class="table"><thead><tr><th>Fecha</th><th>Diagnóstico</th><th>Tratamiento</th><th>Descanso</th></tr></thead>
            <tbody>{% for d in historial.diagnosticos %}<tr><td>{{ d[7] or 'N/A' }}</td><td>{{ d[1] }}</td><td>{{ d[2] }}</td><td>{{ d[3] }}</td></tr>{% else %}<tr><td colspan="4">Sin diagnósticos</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="ordenes">
            <table class="table"><thead><tr><th>N° Orden</th><th>Boleta</th><th>Fecha</th><th>Tipo</th><th>Estado</th><th>Total</th></tr></thead>
            <tbody>{% for o in historial.ordenes %}<tr><td>{{ o[16] or 'N/A' }}</td><td>{{ o[17] or '--' }}</td><td>{{ o[3] }}</td><td>{{ o[10] }}</td><td>{{ o[4] }}</td><td>S/ {{ o[5] }}</td></tr>{% else %}<tr><td colspan="6">Sin órdenes</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="resultados">
            <table class="table"><thead><tr><th>Fecha</th><th>Tipo</th><th>Examen</th><th>Resultado</th></tr></thead>
            <tbody>{% for r in historial.resultados_lab %}<tr><td>{{ r[5] }}</td><td>{{ r[6] }}</td><td>{{ r[7] }}</td><td>{{ r[3] }}</td></tr>{% else %}<tr><td colspan="4">Sin resultados</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="recetas">
            <table class="table"><thead><tr><th>Nº Cuenta</th><th>Fecha</th><th>Diagnóstico</th><th>Estado</th></tr></thead>
            <tbody>{% for r in historial.recetas %}<tr><td>{{ r[3] }}</td><td>{{ r[2] }}</td><td>{{ r[6] }}</td><td>{{ r[7] }}</td></tr>{% else %}<tr><td colspan="4">Sin recetas</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="triajes">
            <table class="table"><thead><tr><th>Fecha</th><th>PA</th><th>T°</th><th>FC</th><th>FR</th><th>Peso</th><th>Talla</th></tr></thead>
            <tbody>{% for t in historial.triajes %}<tr><td>{{ t[2] }}</td><td>{{ t[3] }}</td><td>{{ t[4] }}</td><td>{{ t[5] }}</td><td>{{ t[6] }}</td><td>{{ t[7] }}</td><td>{{ t[8] }}</td></tr>{% else %}<tr><td colspan="7">Sin triajes</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="inyectables">
            <table class="table"><thead><tr><th>Fecha</th><th>Medicamento</th><th>Dosis</th><th>Vía</th><th>Lote</th></tr></thead>
            <tbody>{% for i in historial.inyectables %}<tr><td>{{ i[2] }}</td><td>{{ i[3] }}</td><td>{{ i[4] }}</td><td>{{ i[5] }}</td><td>{{ i[6] }}</td></tr>{% else %}<tr><td colspan="5">Sin inyectables</td></tr>{% endfor %}</tbody></table>
        </div>
        <div class="tab-pane" id="imagenes">
            <div class="row">{% for img in historial.imagenes %}<div class="col-md-3"><div class="card"><img src="/static/{{ img[3] }}" class="card-img-top" style="height:150px;object-fit:cover;"><div class="card-body"><small>{{ img[5] or '' }}</small></div></div></div>{% else %}<p>Sin imágenes</p>{% endfor %}</div>
        </div>
        <div class="tab-pane" id="anexos">
            <div class="card p-3">
                <h5>Subir nuevo documento</h5>
                <form method="POST" action="{{ url_for('anexar_pdf') }}" enctype="multipart/form-data">
                    <input type="hidden" name="id_paciente" value="{{ paciente_info.id }}">
                    <div class="row g-2">
                        <div class="col-md-4"><input type="text" name="titulo" class="form-control" placeholder="Título" required></div>
                        <div class="col-md-4"><input type="text" name="descripcion" class="form-control" placeholder="Descripción"></div>
                        <div class="col-md-3"><input type="file" name="archivo_pdf" class="form-control" accept=".pdf,.png,.jpg,.jpeg,.doc,.docx" required></div>
                        <div class="col-md-1"><button class="btn btn-primary w-100">Subir</button></div>
                    </div>
                </form>
                <hr>
                <h5>Documentos anexados</h5>
                <table class="table"><thead><tr><th>Título</th><th>Descripción</th><th>Fecha</th><th>Subido por</th><th>Acciones</th></tr></thead>
                <tbody>{% for a in historial.anexos %}<tr><td>{{ a[1] }}</td><td>{{ a[2] }}</td><td>{{ a[5] }}</td><td>{{ a[6] }}</td>
                <td><a href="{{ url_for('ver_anexo', id_anexo=a[0]) }}" class="btn btn-primary btn-sm" target="_blank">Ver</a>
                <a href="{{ url_for('ver_anexo', id_anexo=a[0]) }}" download class="btn btn-success btn-sm">Descargar</a></td></tr>{% else %}<tr><td colspan="5">Sin documentos</td></tr>{% endfor %}</tbody></table>
            </div>
        </div>
    </div>
    <button onclick="window.print()" class="btn btn-warning mt-3 no-print">🖨️ Imprimir</button>
    {% endif %}
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  paciente_info=paciente_info, historial=historial,
                                  system_background=system_bg, login_background=login_bg)

@app.route('/historias_clinicas/anexar_pdf', methods=['POST'])
@login_required
def anexar_pdf():
    if 'Historias Clínicas' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    id_paciente = request.form.get('id_paciente')
    if not id_paciente: flash('Paciente no especificado.', 'danger'); return redirect(url_for('historias_clinicas'))
    archivo = request.files.get('archivo_pdf'); titulo = request.form.get('titulo', 'Documento anexo')
    descripcion = request.form.get('descripcion', '')
    if not archivo or not archivo.filename: flash('Seleccione un archivo.', 'danger'); return redirect(url_for('historias_clinicas'))
    filename = secure_filename(archivo.filename); ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    allowed = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx'}
    if ext not in allowed: flash('Formato no permitido.', 'danger'); return redirect(url_for('historias_clinicas'))
    os.makedirs('static/anexos', exist_ok=True)
    nombre_guardado = f"anexo_{id_paciente}_{uuid.uuid4().hex}.{ext}"
    ruta_guardado = os.path.join('static/anexos', nombre_guardado)
    archivo.save(ruta_guardado)
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO anexos_historia (id_paciente, titulo, descripcion, ruta_archivo, tipo_archivo, usuario_subio) VALUES (?, ?, ?, ?, ?, ?)",
                (id_paciente, titulo, descripcion, f"anexos/{nombre_guardado}", ext, session.get('usuario')))
    conn.commit(); conn.close()
    flash('Documento anexado correctamente.', 'success')
    return redirect(url_for('historias_clinicas'))

@app.route('/historias_clinicas/ver_anexo/<int:id_anexo>')
@login_required
def ver_anexo(id_anexo):
    if 'Historias Clínicas' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT ruta_archivo FROM anexos_historia WHERE id=?", (id_anexo,))
    row = cur.fetchone(); conn.close()
    if not row: flash('Anexo no encontrado.', 'danger'); return redirect(url_for('historias_clinicas'))
    return send_file(os.path.join('static', row[0]), as_attachment=False)

# ========================== MÓDULO CONFIGURACIÓN ==========================
@app.route('/configuracion', methods=['GET','POST'])
@login_required
def configuracion_sistema():
    if 'Configuración' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    tab = request.args.get('tab', 'general')
    conn = get_db_connection(); cur = conn.cursor()
    if tab == 'general':
        if request.method == 'POST':
            nombre = request.form['nombre_sistema']; tamano = request.form['tamano_hoja']
            encabezado = request.form['encabezado_texto']; pie = request.form['pie_pagina_texto']
            header = request.form['report_header']; footer = request.form['report_footer']
            ticket = request.form.get('ticket_size', 'TICKET_80MM')
            report = request.form.get('report_size', 'A4'); result = request.form.get('result_size', 'A4')
            cur.execute("""UPDATE configuracion_sistema SET nombre_sistema=?, tamano_hoja=?, encabezado_texto=?, pie_pagina_texto=?, report_header=?, report_footer=?, ticket_size=?, report_size=?, result_size=? WHERE id=1""",
                        (nombre, tamano, encabezado, pie, header, footer, ticket, report, result))
            conn.commit(); flash('Configuración actualizada.', 'success'); return redirect(url_for('configuracion_sistema', tab='general'))
        config = obtener_configuracion()
        contenido = """
        <h2>⚙️ Configuración</h2>
        <ul class="nav nav-tabs">
            <li class="nav-item"><a class="nav-link active" href="{{ url_for('configuracion_sistema', tab='general') }}">General</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='modulos') }}">Módulos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='roles') }}">Roles</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='personal') }}">Personal</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='medicos') }}">Médicos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='medicamentos') }}">Medicamentos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='examenes') }}">Exámenes</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='procedimientos') }}">Procedimientos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='precios') }}">💰 Precios</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='impresion') }}">🖨️ Impresión</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='plantillas') }}">📄 Plantillas</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='examenes_servicios') }}">Exámenes por Servicio</a></li>
        </ul>
        <div class="tab-content active">
            <form method="POST">
                <div class="row g-2"><div class="col-md-6"><label>Nombre</label><input type="text" name="nombre_sistema" value="{{ config[0] }}" class="form-control"></div>
                <div class="col-md-6"><label>Tamaño general</label><select name="tamano_hoja" class="form-control"><option value="A4" {% if config[1]=='A4' %}selected{% endif %}>A4</option><option value="A5" {% if config[1]=='A5' %}selected{% endif %}>A5</option><option value="LETTER" {% if config[1]=='LETTER' %}selected{% endif %}>Carta</option><option value="LEGAL" {% if config[1]=='LEGAL' %}selected{% endif %}>Oficio</option></select></div></div>
                <div class="row g-2 mt-2"><div class="col-md-4"><label>Ticket</label><select name="ticket_size" class="form-control"><option value="TICKET_80MM" {% if config[8]=='TICKET_80MM' %}selected{% endif %}>80x297mm</option><option value="TICKET_80MM_LANDSCAPE" {% if config[8]=='TICKET_80MM_LANDSCAPE' %}selected{% endif %}>297x80mm</option><option value="A4" {% if config[8]=='A4' %}selected{% endif %}>A4</option><option value="A5" {% if config[8]=='A5' %}selected{% endif %}>A5</option></select></div>
                <div class="col-md-4"><label>Informes médicos</label><select name="report_size" class="form-control"><option value="A4" {% if config[9]=='A4' %}selected{% endif %}>A4</option><option value="A5" {% if config[9]=='A5' %}selected{% endif %}>A5</option><option value="LETTER" {% if config[9]=='LETTER' %}selected{% endif %}>Carta</option><option value="LEGAL" {% if config[9]=='LEGAL' %}selected{% endif %}>Oficio</option></select></div>
                <div class="col-md-4"><label>Resultados lab</label><select name="result_size" class="form-control"><option value="A4" {% if config[10]=='A4' %}selected{% endif %}>A4</option><option value="A5" {% if config[10]=='A5' %}selected{% endif %}>A5</option><option value="LETTER" {% if config[10]=='LETTER' %}selected{% endif %}>Carta</option><option value="LEGAL" {% if config[10]=='LEGAL' %}selected{% endif %}>Oficio</option></select></div></div>
                <div class="row g-2 mt-2"><div class="col-md-6"><label>Encabezado</label><input type="text" name="encabezado_texto" value="{{ config[3] }}" class="form-control"></div>
                <div class="col-md-6"><label>Pie</label><input type="text" name="pie_pagina_texto" value="{{ config[4] }}" class="form-control"></div></div>
                <div class="row g-2 mt-2"><div class="col-md-6"><label>Título reporte</label><input type="text" name="report_header" value="{{ config[5] }}" class="form-control"></div>
                <div class="col-md-6"><label>Pie reporte</label><input type="text" name="report_footer" value="{{ config[6] }}" class="form-control"></div></div>
                <div class="mt-3 border-top"><h5>Logo</h5>{% if config[2] %}<img src="/static/{{ config[2] }}" style="max-height:80px;">{% else %}Sin logo{% endif %}<br><a href="{{ url_for('subir_logo') }}" class="btn btn-primary">Subir Logo</a></div>
                <div class="mt-3 border-top"><h5>Sello</h5>{% if config[7] %}<img src="/static/{{ config[7] }}" style="max-height:80px;">{% else %}Sin sello{% endif %}<br><a href="{{ url_for('subir_sello') }}" class="btn btn-primary">Subir Sello</a></div>
                <div class="mt-3 border-top"><h5>Fondo de Login</h5>{% if config[11] %}<img src="/static/{{ config[11] }}" style="max-height:120px;">{% else %}Sin fondo{% endif %}<br><a href="{{ url_for('subir_fondo_login') }}" class="btn btn-primary">Subir Fondo Login</a></div>
                <div class="mt-3 border-top"><h5>Fondo del Sistema</h5>{% if config[12] %}<img src="/static/{{ config[12] }}" style="max-height:120px;">{% else %}Sin fondo{% endif %}<br><a href="{{ url_for('subir_fondo_sistema') }}" class="btn btn-primary">Subir Fondo Sistema</a></div>
                <button class="btn btn-success mt-3">Guardar</button>
            </form>
        </div>
        """
    elif tab == 'precios':
        if request.method == 'POST':
            for key, val in request.form.items():
                if key.startswith('precio_servicio_'):
                    cur.execute("UPDATE servicios SET precio_base=? WHERE id=?", (float(val), int(key.split('_')[2])))
                elif key.startswith('precio_examen_'):
                    cur.execute("UPDATE examenes_catalogo SET precio=? WHERE id=?", (float(val), int(key.split('_')[2])))
                elif key.startswith('precio_proced_'):
                    cur.execute("UPDATE procedimientos SET precio=? WHERE id=?", (float(val), int(key.split('_')[2])))
                elif key.startswith('precio_med_'):
                    cur.execute("UPDATE medicamentos SET precio=? WHERE id=?", (float(val), int(key.split('_')[2])))
            conn.commit(); flash('Precios actualizados.', 'success'); return redirect(url_for('configuracion_sistema', tab='precios'))
        cur.execute("SELECT id, nombre, precio_base FROM servicios ORDER BY nombre")
        servicios = cur.fetchall()
        cur.execute("SELECT id, codigo, descripcion, precio FROM examenes_catalogo WHERE activo=1 ORDER BY descripcion")
        examenes = cur.fetchall()
        cur.execute("SELECT id, codigo, nombre, precio FROM procedimientos WHERE activo=1 ORDER BY nombre")
        procedimientos = cur.fetchall()
        cur.execute("SELECT id, codigo, nombre, precio FROM medicamentos WHERE activo=1 ORDER BY nombre")
        medicamentos = cur.fetchall()
        conn.close()
        contenido = """
        <h2>💰 Catálogo de Precios</h2>
        <form method="POST">
            <h5>Servicios</h5>
            <div class="row">{% for s in servicios %}<div class="col-md-3 mb-2"><label>{{ s[1] }}</label><input type="number" step="0.01" name="precio_servicio_{{ s[0] }}" value="{{ s[2] }}" class="form-control"></div>{% endfor %}</div>
            <hr><h5>Exámenes</h5>
            <div class="row">{% for e in examenes %}<div class="col-md-3 mb-2"><label>{{ e[1] }} - {{ e[2] }}</label><input type="number" step="0.01" name="precio_examen_{{ e[0] }}" value="{{ e[3] }}" class="form-control"></div>{% endfor %}</div>
            <hr><h5>Procedimientos</h5>
            <div class="row">{% for p in procedimientos %}<div class="col-md-3 mb-2"><label>{{ p[1] }} - {{ p[2] }}</label><input type="number" step="0.01" name="precio_proced_{{ p[0] }}" value="{{ p[3] }}" class="form-control"></div>{% endfor %}</div>
            <hr><h5>Medicamentos</h5>
            <div class="row">{% for m in medicamentos %}<div class="col-md-3 mb-2"><label>{{ m[1] }} - {{ m[2] }}</label><input type="number" step="0.01" name="precio_med_{{ m[0] }}" value="{{ m[3] }}" class="form-control"></div>{% endfor %}</div>
            <button class="btn btn-success mt-3">Guardar Precios</button>
        </form>
        """
    elif tab == 'impresion':
        if request.method == 'POST':
            boleta = request.form.get('boleta_tamano'); cita = request.form.get('cita_tamano')
            resultado = request.form.get('resultado_tamano'); informe = request.form.get('informe_tamano')
            receta = request.form.get('receta_tamano'); etiqueta = request.form.get('etiqueta_tamano')
            cur.execute("UPDATE config_impresion SET boleta_tamano=?, cita_tamano=?, resultado_tamano=?, informe_tamano=?, receta_tamano=?, etiqueta_tamano=? WHERE id=1",
                        (boleta, cita, resultado, informe, receta, etiqueta))
            conn.commit(); flash('Configuración de impresión actualizada.', 'success')
            return redirect(url_for('configuracion_sistema', tab='impresion'))
        cur.execute("SELECT boleta_tamano, cita_tamano, resultado_tamano, informe_tamano, receta_tamano, etiqueta_tamano FROM config_impresion WHERE id=1")
        conf = cur.fetchone(); conn.close()
        contenido = """
        <h2>🖨️ Configuración de Impresión</h2>
        <form method="POST">
            <div class="row g-3">
                <div class="col-md-4"><label>Boleta</label><select name="boleta_tamano" class="form-control">
                    <option value="TICKET_80MM" {% if conf[0]=='TICKET_80MM' %}selected{% endif %}>Ticket 80mm</option>
                    <option value="TICKET_80MM_LANDSCAPE" {% if conf[0]=='TICKET_80MM_LANDSCAPE' %}selected{% endif %}>Ticket 80mm horizontal</option>
                    <option value="A4" {% if conf[0]=='A4' %}selected{% endif %}>A4</option>
                    <option value="A5" {% if conf[0]=='A5' %}selected{% endif %}>A5</option>
                    <option value="LETTER" {% if conf[0]=='LETTER' %}selected{% endif %}>Carta</option>
                    <option value="LEGAL" {% if conf[0]=='LEGAL' %}selected{% endif %}>Oficio</option>
                </select></div>
                <div class="col-md-4"><label>Cita</label><select name="cita_tamano" class="form-control">
                    <option value="A4" {% if conf[1]=='A4' %}selected{% endif %}>A4</option>
                    <option value="A5" {% if conf[1]=='A5' %}selected{% endif %}>A5</option>
                    <option value="LETTER" {% if conf[1]=='LETTER' %}selected{% endif %}>Carta</option>
                    <option value="LEGAL" {% if conf[1]=='LEGAL' %}selected{% endif %}>Oficio</option>
                </select></div>
                <div class="col-md-4"><label>Resultados</label><select name="resultado_tamano" class="form-control">
                    <option value="A4" {% if conf[2]=='A4' %}selected{% endif %}>A4</option>
                    <option value="A5" {% if conf[2]=='A5' %}selected{% endif %}>A5</option>
                    <option value="LETTER" {% if conf[2]=='LETTER' %}selected{% endif %}>Carta</option>
                    <option value="LEGAL" {% if conf[2]=='LEGAL' %}selected{% endif %}>Oficio</option>
                </select></div>
                <div class="col-md-4"><label>Informe</label><select name="informe_tamano" class="form-control">
                    <option value="A4" {% if conf[3]=='A4' %}selected{% endif %}>A4</option>
                    <option value="A5" {% if conf[3]=='A5' %}selected{% endif %}>A5</option>
                    <option value="LETTER" {% if conf[3]=='LETTER' %}selected{% endif %}>Carta</option>
                    <option value="LEGAL" {% if conf[3]=='LEGAL' %}selected{% endif %}>Oficio</option>
                </select></div>
                <div class="col-md-4"><label>Receta</label><select name="receta_tamano" class="form-control">
                    <option value="A4" {% if conf[4]=='A4' %}selected{% endif %}>A4</option>
                    <option value="A5" {% if conf[4]=='A5' %}selected{% endif %}>A5</option>
                    <option value="LETTER" {% if conf[4]=='LETTER' %}selected{% endif %}>Carta</option>
                    <option value="LEGAL" {% if conf[4]=='LEGAL' %}selected{% endif %}>Oficio</option>
                </select></div>
                <div class="col-md-4"><label>Etiqueta</label><select name="etiqueta_tamano" class="form-control">
                    <option value="A4" {% if conf[5]=='A4' %}selected{% endif %}>A4</option>
                    <option value="A5" {% if conf[5]=='A5' %}selected{% endif %}>A5</option>
                    <option value="LETTER" {% if conf[5]=='LETTER' %}selected{% endif %}>Carta</option>
                </select></div>
                <div class="col-md-12"><button class="btn btn-success">Guardar</button></div>
            </div>
        </form>
        """
    elif tab == 'plantillas':
        if request.method == 'POST':
            tipo = request.form['tipo']; nombre = request.form['nombre']; contenido = request.form['contenido_html']
            guardar_plantilla_pdf(tipo, nombre, contenido)
            flash('Plantilla guardada correctamente.', 'success')
            return redirect(url_for('configuracion_sistema', tab='plantillas'))
        cur.execute("SELECT id, nombre, tipo, contenido_html, fecha_actualizacion FROM plantillas_pdf ORDER BY tipo")
        plantillas = cur.fetchall(); conn.close()
        contenido = """
        <h2>📄 Plantillas PDF</h2>
        <div class="card p-3">
            <h5>Editar Plantilla</h5>
            <form method="POST">
                <div class="row g-2">
                    <div class="col-md-3"><label>Tipo</label><select name="tipo" class="form-control" required>
                        <option value="resultado">Resultado Laboratorio</option>
                        <option value="informe">Informe Médico</option>
                        <option value="receta">Receta</option>
                        <option value="boleta">Boleta</option>
                        <option value="cita">Cita / Admisión</option>
                    </select></div>
                    <div class="col-md-3"><label>Nombre</label><input type="text" name="nombre" class="form-control" required></div>
                    <div class="col-md-6"><label>&nbsp;</label><button class="btn btn-primary w-100">Cargar Plantilla</button></div>
                    <div class="col-md-12"><label>HTML</label><textarea name="contenido_html" class="form-control" rows="15" style="font-family:monospace;"></textarea></div>
                </div>
            </form>
            <hr>
            <h5>Plantillas guardadas</h5>
            <table class="table"><thead><tr><th>Tipo</th><th>Nombre</th><th>Actualización</th><th>Acciones</th></tr></thead>
            <tbody>{% for p in plantillas %}<tr><td>{{ p[2] }}</td><td>{{ p[1] }}</td><td>{{ p[4] }}</td>
            <td><button onclick="cargarPlantilla('{{ p[2] }}','{{ p[1] }}',{{ p[3]|tojson }})" class="btn btn-warning btn-sm">Cargar</button></td></tr>{% else %}<tr><td colspan="4">Sin plantillas</td></tr>{% endfor %}</tbody></table>
        </div>
        <script>
        function cargarPlantilla(tipo, nombre, contenido) {
            document.querySelector('select[name="tipo"]').value = tipo;
            document.querySelector('input[name="nombre"]').value = nombre;
            document.querySelector('textarea[name="contenido_html"]').value = contenido;
        }
        </script>
        """
    elif tab == 'examenes_servicios':
        if request.method == 'POST' and 'asignar' in request.form:
            id_servicio = request.form['id_servicio']; id_examen = request.form['id_examen']
            activo = 1 if request.form.get('activo') else 0
            if IS_POSTGRES:
                cur.execute("INSERT INTO servicios_examenes (id_servicio, id_examen, activo) VALUES (%s,%s,%s) ON CONFLICT (id_servicio, id_examen) DO UPDATE SET activo=EXCLUDED.activo", (id_servicio, id_examen, activo))
            else:
                cur.execute("INSERT OR REPLACE INTO servicios_examenes (id_servicio, id_examen, activo) VALUES (?,?,?)", (id_servicio, id_examen, activo))
            conn.commit(); flash('Asignación actualizada.', 'success'); return redirect(url_for('configuracion_sistema', tab='examenes_servicios'))
        cur.execute("SELECT id, nombre FROM servicios")
        servicios = cur.fetchall()
        cur.execute("SELECT id, codigo, descripcion, precio FROM examenes_catalogo WHERE activo=1")
        examenes = cur.fetchall()
        cur.execute("SELECT id_servicio, id_examen, activo FROM servicios_examenes")
        asignaciones = cur.fetchall()
        asignaciones_dict = {}
        for row in asignaciones:
            asignaciones_dict[(row[0], row[1])] = row[2]
        conn.close()
        contenido = """
        <h2>Exámenes por Servicio</h2>
        <form method="POST" class="row g-2">
            <div class="col-md-3"><label>Servicio</label><select name="id_servicio" class="form-control" required>{% for s in servicios %}<option value="{{ s[0] }}">{{ s[1] }}</option>{% endfor %}</select></div>
            <div class="col-md-3"><label>Examen</label><select name="id_examen" class="form-control" required>{% for e in examenes %}<option value="{{ e[0] }}">{{ e[1] }} - {{ e[2] }}</option>{% endfor %}</select></div>
            <div class="col-md-2"><label>Activo</label><input type="checkbox" name="activo" checked></div>
            <div class="col-md-2"><label>&nbsp;</label><button type="submit" name="asignar" class="btn btn-primary">Asignar</button></div>
        </form>
        <hr><h5>Asignaciones actuales</h5>
        <table class="table"><thead><tr><th>Servicio</th><th>Examen</th><th>Activo</th></tr></thead>
        <tbody>{% for s in servicios %}{% for e in examenes %}{% set key = (s[0], e[0]) %}{% if asignaciones.get(key, 0) %}<tr><td>{{ s[1] }}</td><td>{{ e[1] }} - {{ e[2] }}</td><td>✅</td></tr>{% endif %}{% endfor %}{% endfor %}</tbody></table>
        """
    else:
        # Otras pestañas (modulos, roles, personal, medicos, medicamentos, examenes, procedimientos)
        # Aquí iría el código de esas pestañas (similar al original)
        return redirect(url_for('configuracion_sistema', tab='general'))

    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  **locals() if 'servicios' in locals() else {},
                                  system_background=system_bg, login_background=login_bg)

# ========================== SUBIR LOGO, SELLO Y FONDOS ==========================
@app.route('/configuracion/subir_logo', methods=['GET','POST'])
@login_required
def subir_logo():
    if 'Configuración' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
     if request.method == 'POST':
        file = request.files.get('logo_archivo')
        if file and file.filename:
            import base64
            allowed = {'png','jpg','jpeg','gif'}
            if '.' in file.filename and file.filename.rsplit('.',1)[1].lower() in allowed:
                file_data = file.read()
                base64_str = base64.b64encode(file_data).decode('utf-8')
                mime_type = file.mimetype or 'image/jpeg'
                data_uri = f"data:{mime_type};base64,{base64_str}"
                
                conn = get_db_connection(); cur = conn.cursor()
                cur.execute("UPDATE configuracion_sistema SET logo_path=? WHERE id=1", (data_uri,))
                conn.commit(); conn.close()
                flash('Logo subido.', 'success')
                return redirect(url_for('configuracion_sistema'))
            else: flash('Formato no permitido.', 'danger')
        else: flash('Seleccione un archivo.', 'danger')
        return redirect(url_for('subir_logo'))
    contenido = """<h2>Subir Logo</h2><form method="POST" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="logo_archivo" accept="image/*" class="form-control" required></div><button class="btn btn-success">Subir</button><a href="{{ url_for('configuracion_sistema') }}" class="btn btn-secondary">Cancelar</a></form>"""
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  system_background=system_bg, login_background=login_bg)

@app.route('/configuracion/subir_sello', methods=['GET','POST'])
@login_required
def subir_sello():
        if request.method == 'POST':
        file = request.files.get('sello_archivo')
        if file and file.filename:
            import base64
            allowed = {'png','jpg','jpeg','gif'}
            if '.' in file.filename and file.filename.rsplit('.',1)[1].lower() in allowed:
                file_data = file.read()
                base64_str = base64.b64encode(file_data).decode('utf-8')
                mime_type = file.mimetype or 'image/jpeg'
                data_uri = f"data:{mime_type};base64,{base64_str}"
                
                conn = get_db_connection(); cur = conn.cursor()
                cur.execute("UPDATE configuracion_sistema SET sello_path=? WHERE id=1", (data_uri,))
                conn.commit(); conn.close()
                flash('Sello subido.', 'success')
                return redirect(url_for('configuracion_sistema'))
            else: flash('Formato no permitido.', 'danger')
        else: flash('Seleccione un archivo.', 'danger')
        return redirect(url_for('subir_sello'))
    contenido = """<h2>Subir Sello</h2><form method="POST" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="sello_archivo" accept="image/*" class="form-control" required></div><button class="btn btn-success">Subir</button><a href="{{ url_for('configuracion_sistema') }}" class="btn btn-secondary">Cancelar</a></form>"""
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  system_background=system_bg, login_background=login_bg)

@app.route('/configuracion/subir_fondo_login', methods=['GET','POST'])
@login_required
def subir_fondo_login():
        if request.method == 'POST':
        file = request.files.get('fondo_archivo')
        if file and file.filename:
            import base64
            allowed = {'png','jpg','jpeg','gif'}
            if '.' in file.filename and file.filename.rsplit('.',1)[1].lower() in allowed:
                file_data = file.read()
                base64_str = base64.b64encode(file_data).decode('utf-8')
                mime_type = file.mimetype or 'image/jpeg'
                data_uri = f"data:{mime_type};base64,{base64_str}"
                
                conn = get_db_connection(); cur = conn.cursor()
                cur.execute("UPDATE configuracion_sistema SET login_background=? WHERE id=1", (data_uri,))
                conn.commit(); conn.close()
                flash('Fondo de login actualizado.', 'success')
                return redirect(url_for('configuracion_sistema'))
            else: flash('Formato no permitido.', 'danger')
        else: flash('Seleccione un archivo.', 'danger')
        return redirect(url_for('subir_fondo_login'))
    contenido = """<h2>Subir Fondo de Login</h2><form method="POST" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="fondo_archivo" accept="image/*" class="form-control" required></div><button class="btn btn-success">Subir</button><a href="{{ url_for('configuracion_sistema') }}" class="btn btn-secondary">Cancelar</a></form>"""
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  system_background=system_bg, login_background=login_bg)

@app.route('/configuracion/subir_fondo_sistema', methods=['GET','POST'])
@login_required
def subir_fondo_sistema():
        if request.method == 'POST':
        file = request.files.get('fondo_archivo')
        if file and file.filename:
            import base64
            allowed = {'png','jpg','jpeg','gif'}
            if '.' in file.filename and file.filename.rsplit('.',1)[1].lower() in allowed:
                file_data = file.read()
                base64_str = base64.b64encode(file_data).decode('utf-8')
                mime_type = file.mimetype or 'image/jpeg'
                data_uri = f"data:{mime_type};base64,{base64_str}"
                
                conn = get_db_connection(); cur = conn.cursor()
                cur.execute("UPDATE configuracion_sistema SET system_background=? WHERE id=1", (data_uri,))
                conn.commit(); conn.close()
                flash('Fondo del sistema actualizado.', 'success')
                return redirect(url_for('configuracion_sistema'))
            else: flash('Formato no permitido.', 'danger')
        else: flash('Seleccione un archivo.', 'danger')
        return redirect(url_for('subir_fondo_sistema'))
    contenido = """<h2>Subir Fondo del Sistema</h2><form method="POST" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="fondo_archivo" accept="image/*" class="form-control" required></div><button class="btn btn-success">Subir</button><a href="{{ url_for('configuracion_sistema') }}" class="btn btn-secondary">Cancelar</a></form>"""
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    system_bg = config[12] if config and len(config) > 12 else ''
    login_bg = config[11] if config and len(config) > 11 else ''
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  system_background=system_bg, login_background=login_bg)

# ========================== API EXÁMENES ==========================
@app.route('/api/examenes')
def api_examenes():
    q = request.args.get('q', '')
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, codigo, descripcion, precio FROM examenes_catalogo WHERE codigo LIKE ? OR descripcion LIKE ?", (f'%{q}%', f'%{q}%'))
    res = cur.fetchall(); conn.close()
    return jsonify([{'id':r[0], 'codigo':r[1], 'descripcion':r[2], 'precio':r[3]} for r in res])

# ========================== INICIO ==========================

def init_db():
    """Inicializa la base de datos creando tablas y el usuario admin en PostgreSQL."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
                
        # Crear TODAS las tablas necesarias para que los módulos funcionen
        cur.execute("""
            CREATE TABLE IF NOT EXISTS configuracion_sistema (
                id SERIAL PRIMARY KEY,
                nombre_sistema TEXT, tamano_hoja TEXT, logo_path TEXT, encabezado_texto TEXT, pie_pagina_texto TEXT,
                report_header TEXT, report_footer TEXT, sello_path TEXT, ticket_size TEXT, report_size TEXT, result_size TEXT,
                login_background TEXT, system_background TEXT
            );
            INSERT INTO configuracion_sistema (id, nombre_sistema) VALUES (1, 'SISGALENO2026') ON CONFLICT (id) DO NOTHING;

            CREATE TABLE IF NOT EXISTS config_impresion (
                id SERIAL PRIMARY KEY,
                boleta_tamano TEXT, cita_tamano TEXT, resultado_tamano TEXT, informe_tamano TEXT, receta_tamano TEXT, etiqueta_tamano TEXT
            );
            INSERT INTO config_impresion (id, boleta_tamano) VALUES (1, 'TICKET_80MM') ON CONFLICT (id) DO NOTHING;

            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                usuario TEXT UNIQUE NOT NULL, rol TEXT NOT NULL, password_hash TEXT NOT NULL
            );
            INSERT INTO usuarios (usuario, rol, password_hash) VALUES ('admin', 'Administrador', %s) ON CONFLICT (usuario) DO NOTHING;

            CREATE TABLE IF NOT EXISTS permisos_roles (
                id SERIAL PRIMARY KEY, rol TEXT NOT NULL, modulo TEXT NOT NULL
            );
            INSERT INTO permisos_roles (rol, modulo) VALUES ('Administrador', 'Admisión'), ('Administrador', 'Caja'), ('Administrador', 'Laboratorio'), ('Administrador', 'Atención Médica'), ('Administrador', 'Enfermería'), ('Administrador', 'Historias Clínicas'), ('Administrador', 'Configuración') ON CONFLICT DO NOTHING;

            CREATE TABLE IF NOT EXISTS pacientes (id SERIAL PRIMARY KEY, historia_clinica TEXT, dni TEXT, nombre TEXT, apellido TEXT, fecha_nacimiento TEXT, telefono TEXT, celular TEXT, direccion TEXT, sexo TEXT, edad INTEGER, nro_afiliacion TEXT, deleted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS servicios (id SERIAL PRIMARY KEY, nombre TEXT, precio_base REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS medicos (id SERIAL PRIMARY KEY, nombre TEXT, apellido TEXT, especialidad TEXT, activo INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS citas (id SERIAL PRIMARY KEY, id_paciente INTEGER, id_servicio INTEGER, id_medico INTEGER, fecha_cita TEXT, estado TEXT, motivo_consulta TEXT, tipo_asegurado TEXT, numero_boleta TEXT, numero_orden TEXT);
            CREATE TABLE IF NOT EXISTS pagos (id SERIAL PRIMARY KEY, id_cita INTEGER, id_paciente INTEGER, numero_boleta TEXT, numero_orden TEXT, monto REAL, fecha_pago TEXT, estado TEXT, descripcion TEXT);
            CREATE TABLE IF NOT EXISTS diagnosticos (id SERIAL PRIMARY KEY, id_cita INTEGER, id_medico INTEGER, diagnostico TEXT, tratamiento TEXT, descanso_medico_dias INTEGER, informe_pdf_path TEXT);

            CREATE TABLE IF NOT EXISTS examenes_catalogo (id SERIAL PRIMARY KEY, codigo TEXT, descripcion TEXT, precio REAL, activo INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS examenes_parametros (id SERIAL PRIMARY KEY, id_examen_catalogo INTEGER, nombre_parametro TEXT, unidad TEXT, rango_referencia TEXT, orden INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ordenes_laboratorio (id SERIAL PRIMARY KEY, id_paciente INTEGER, id_cita INTEGER, id_examen INTEGER, examen_manual TEXT, servicio_manual TEXT, fecha_emision TEXT, fecha_validez TEXT, estado TEXT, precio_total REAL, numero_orden TEXT, numero_boleta TEXT, codigo_muestra TEXT, tipo_orden TEXT, tipo_resultado TEXT, tecnologo_id INTEGER, fecha_resultado TEXT, validado INTEGER, descripcion_orden TEXT);
            CREATE TABLE IF NOT EXISTS orden_examenes (id SERIAL PRIMARY KEY, id_orden INTEGER, id_examen INTEGER, precio REAL);
            CREATE TABLE IF NOT EXISTS resultados_lab (id SERIAL PRIMARY KEY, id_orden INTEGER, id_parametro INTEGER, resultado TEXT, fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS imagenes_laboratorio (id SERIAL PRIMARY KEY, id_orden INTEGER, nombre_archivo TEXT, ruta_archivo TEXT, descripcion TEXT, fecha_subida TEXT, tipo_imagen TEXT);
            CREATE TABLE IF NOT EXISTS plantillas_pdf (id SERIAL PRIMARY KEY, nombre TEXT, tipo TEXT, contenido_html TEXT, fecha_actualizacion TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS procedimientos (id SERIAL PRIMARY KEY, codigo TEXT, nombre TEXT, precio REAL, activo INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS servicios_examenes (id_servicio INTEGER, id_examen INTEGER, activo INTEGER DEFAULT 1);

            CREATE TABLE IF NOT EXISTS triaje (id SERIAL PRIMARY KEY, id_paciente INTEGER, id_cita INTEGER, presion_arterial TEXT, temperatura REAL, frecuencia_cardiaca INTEGER, frecuencia_respiratoria INTEGER, peso REAL, talla REAL, imc REAL, sintomas TEXT, alergias TEXT, medicamentos_actuales TEXT, observaciones TEXT, id_enfermera INTEGER, fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS inyectables (id SERIAL PRIMARY KEY, id_paciente INTEGER, id_cita INTEGER, medicamento TEXT, dosis TEXT, via_administracion TEXT, lote TEXT, observaciones TEXT, id_enfermera INTEGER, fecha_hora TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS anexos_historia (id SERIAL PRIMARY KEY, id_paciente INTEGER, titulo TEXT, descripcion TEXT, ruta_archivo TEXT, tipo_archivo TEXT, fecha_subida TEXT DEFAULT CURRENT_TIMESTAMP, usuario_subio TEXT);
            CREATE TABLE IF NOT EXISTS recetas (id SERIAL PRIMARY KEY, id_paciente INTEGER, id_cita INTEGER, numero_cuenta TEXT, fecha_emision TEXT, estado TEXT, diagnostico TEXT, indicaciones TEXT);
        """, (generate_password_hash('admin123'),))
        
        conn.commit()
        print("Base de datos inicializada/migrada correctamente en PostgreSQL.")
    except Exception as e:
        print(f"Error al inicializar la base de datos: {e}")
        conn.rollback()
    finally:
        conn.close()
# ========================== INICIO DE LA APP ==========================

# Ejecuta la migración automáticamente al importar la app (Clave para Render)
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
