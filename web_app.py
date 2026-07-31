import os
import sqlite3
import psycopg2
from dotenv import load_dotenv
from flask import Flask, request, render_template_string, session, redirect, url_for, send_file
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, LETTER, LEGAL
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
import io
from werkzeug.utils import secure_filename

load_dotenv()
app = Flask(__name__)
app.secret_key = 'clave_super_secreta_sisgaleno2026'

DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith('postgresql')

def get_db_connection():
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect('sisgaleno2026.db')

# ==========================================
# BASE DE DATOS (Nuevas tablas: Auditoría y Servicios Médicos)
# ==========================================
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if IS_POSTGRES: 
        auto_inc = "SERIAL"
        conflict = "ON CONFLICT (usuario) DO NOTHING"
    else: 
        auto_inc = "INTEGER PRIMARY KEY AUTOINCREMENT"
        conflict = ""

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS usuarios ( id {auto_inc}, usuario TEXT UNIQUE NOT NULL, password TEXT NOT NULL, rol TEXT NOT NULL )''')
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('admin', 'admin', 'administrador') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('doctor', 'doctor', 'medico') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('lab', 'lab', 'laboratorista') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('nurse', 'nurse', 'enfermera') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('tecnologo', 'tecnologo', 'tecnologo') {conflict}")

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pacientes ( id {auto_inc}, dni TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, apellido TEXT NOT NULL, fecha_nacimiento TEXT, telefono TEXT, direccion TEXT, sexo TEXT DEFAULT '', edad INTEGER DEFAULT 0 )''')
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_secciones ( id {auto_inc}, nombre_seccion TEXT NOT NULL )''')
    cursor.execute("INSERT OR IGNORE INTO examenes_secciones (id, nombre_seccion) VALUES (1, 'HEMATOLOGIA')")
    cursor.execute("INSERT OR IGNORE INTO examenes_secciones (id, nombre_seccion) VALUES (2, 'INMUNOLOGIA')")
    cursor.execute("INSERT OR IGNORE INTO examenes_secciones (id, nombre_seccion) VALUES (3, 'BIOQUIMICA')")
    cursor.execute("INSERT OR IGNORE INTO examenes_secciones (id, nombre_seccion) VALUES (4, 'PARASITOLOGIA')")

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_catalogo ( 
        id {auto_inc}, 
        codigo TEXT,
        descripcion TEXT NOT NULL, 
        id_seccion INTEGER,
        precio REAL DEFAULT 0,
        abreviatura TEXT,
        interviene_reporte TEXT DEFAULT 'SI',
        epidemiologico TEXT DEFAULT 'NO',
        valor_normal TEXT,
        activo INTEGER DEFAULT 1,
        FOREIGN KEY(id_seccion) REFERENCES examenes_secciones(id) 
    )''')
    
    cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, codigo, descripcion, id_seccion, precio, valor_normal) VALUES (1, '145', 'HEMOGRAMA COMPLETO', 1, 50.00, 'Valores de referencia según edad')")
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_parametros (
        id {auto_inc},
        id_examen_catalogo INTEGER,
        nombre_parametro TEXT NOT NULL,
        unidad TEXT,
        valor_normal TEXT,
        orden INTEGER DEFAULT 0,
        FOREIGN KEY(id_examen_catalogo) REFERENCES examenes_catalogo(id)
    )''')
    cursor.execute("INSERT OR IGNORE INTO examenes_parametros (id_examen_catalogo, nombre_parametro, unidad, valor_normal, orden) VALUES (1, 'GLÓBULOS BLANCOS', 'x mmc', '4.0 - 10.0 milones', 1)")
    cursor.execute("INSERT OR IGNORE INTO examenes_parametros (id_examen_catalogo, nombre_parametro, unidad, valor_normal, orden) VALUES (1, 'HEMATOCRITO', '%', '40 - 54%', 2)")
    cursor.execute("INSERT OR IGNORE INTO examenes_parametros (id_examen_catalogo, nombre_parametro, unidad, valor_normal, orden) VALUES (1, 'HEMOGLOBINA', 'g/dl', '14 - 18 g/dl', 3)")

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS atenciones ( 
        id {auto_inc}, 
        id_paciente INTEGER,
        nro_boleta INTEGER UNIQUE,
        secuencia INTEGER DEFAULT 1,
        fecha_atencion TEXT,
        hora_registro TEXT,
        historia_clinica TEXT,
        sexo TEXT,
        edad INTEGER,
        tipo_paciente TEXT,
        detalle_tipo TEXT,
        origen TEXT,
        pst_hosp_origen TEXT,
        servicio TEXT,
        medico TEXT,
        nro_cama TEXT,
        FOREIGN KEY(id_paciente) REFERENCES pacientes(id) 
    )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS ordenes_laboratorio ( id {auto_inc}, id_paciente INTEGER, id_examen INTEGER, id_atencion INTEGER, fecha_emision TEXT, estado TEXT, resultado TEXT, precio REAL DEFAULT 0, FOREIGN KEY(id_paciente) REFERENCES pacientes(id), FOREIGN KEY(id_examen) REFERENCES examenes_catalogo(id), FOREIGN KEY(id_atencion) REFERENCES atenciones(id) )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS resultados_detalles (
        id {auto_inc},
        id_orden_laboratorio INTEGER,
        id_parametro INTEGER,
        resultado TEXT,
        FOREIGN KEY(id_orden_laboratorio) REFERENCES ordenes_laboratorio(id),
        FOREIGN KEY(id_parametro) REFERENCES examenes_parametros(id)
    )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS triaje (
        id {auto_inc},
        id_atencion INTEGER UNIQUE,
        fecha_hora TEXT,
        enfermera TEXT,
        presion_sistolica INTEGER,
        presion_diastolica INTEGER,
        frecuencia_cardiaca INTEGER,
        frecuencia_respiratoria INTEGER,
        temperatura REAL,
        saturacion_oxigeno INTEGER,
        peso REAL,
        talla REAL,
        imc REAL,
        glasgow INTEGER,
        dolor INTEGER,
        observaciones TEXT,
        FOREIGN KEY(id_atencion) REFERENCES atenciones(id)
    )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS reactivos ( id {auto_inc}, nombre TEXT NOT NULL, cantidad REAL NOT NULL, unidad TEXT, fecha_caducidad TEXT, proveedor TEXT )''')

    # NUEVAS TABLAS PARA CONFIGURACIÓN, AUDITORÍA Y SERVICIOS MÉDICOS
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS configuracion_sistema (
        id INTEGER PRIMARY KEY DEFAULT 1,
        nombre_sistema TEXT DEFAULT 'SISGALENO2026',
        tamano_hoja TEXT DEFAULT 'A4',
        logo_path TEXT DEFAULT '',
        encabezado_texto TEXT DEFAULT 'Laboratorio Clínico',
        pie_pagina_texto TEXT DEFAULT 'Generado automáticamente por el sistema.'
    )''')
    cursor.execute("INSERT OR IGNORE INTO configuracion_sistema (id) VALUES (1)")
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS audit_logs (
        id {auto_inc},
        usuario TEXT,
        accion TEXT,
        tabla TEXT,
        registro_id INTEGER,
        fecha_hora TEXT,
        ip_address TEXT,
        detalles TEXT
    )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS medico_servicio (
        id {auto_inc},
        id_medico INTEGER,
        servicio TEXT
    )''')
    
    conn.commit()
    conn.close()

init_db()

# ==========================================
# FUNCIONES AUXILIARES (Configuración y Auditoría)
# ==========================================
def obtener_configuracion():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT nombre_sistema, tamano_hoja, logo_path, encabezado_texto, pie_pagina_texto FROM configuracion_sistema WHERE id = 1")
    config = cursor.fetchone()
    conn.close()
    return config

def log_audit(usuario, accion, tabla, registro_id, detalles=""):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ip = request.remote_addr if request else 'Desconocida'
        cursor.execute("""
            INSERT INTO audit_logs (usuario, accion, tabla, registro_id, fecha_hora, ip_address, detalles)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (usuario, accion, tabla, registro_id, fecha_hora, ip, detalles))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error al guardar log de auditoría: {e}")

# ==========================================
# DISEÑO Y ESTILOS CON FONDO DE LABORATORIO
# ==========================================
LAYOUT_BASE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SISGALENO2026 - Laboratorio Clínico</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; color: #333; background-image: url('/static/fondo_lab.jpg'); background-size: cover; background-attachment: fixed; background-position: center; }
        a { text-decoration: none; }
        .navbar { background: linear-gradient(90deg, #0d2b45 0%, #1a4d70 100%); color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .navbar a { color: #f8f9fa; margin: 0 12px; font-weight: 500; transition: 0.3s; }
        .navbar a:hover { color: #72c6f7; transform: translateY(-1px); }
        .navbar .logo { font-size: 1.5rem; font-weight: bold; letter-spacing: 1px; color: white; }
        .navbar .logo span { color: #72c6f7; }
        
        .container { max-width: 1100px; margin: 30px auto; padding: 20px; background: rgba(255, 255, 255, 0.95); border-radius: 16px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.15); }
        
        .btn { display: inline-block; padding: 10px 24px; margin: 4px; border: none; border-radius: 50px; font-weight: 600; color: white; text-align: center; cursor: pointer; transition: all 0.3s ease; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .btn:hover { transform: translateY(-3px); box-shadow: 0 8px 15px rgba(0,0,0,0.2); }
        .btn-primary { background: linear-gradient(135deg, #007bff, #0056b3); }
        .btn-success { background: linear-gradient(135deg, #28a745, #1e7e34); }
        .btn-danger { background: linear-gradient(135deg, #dc3545, #a71d2a); }
        .btn-warning { background: linear-gradient(135deg, #ffc107, #d39e00); color: #212529; }
        .btn-whatsapp { background: linear-gradient(135deg, #25d366, #128C7E); }
        input, select, textarea { width: 100%; padding: 10px; margin: 6px 0 12px 0; border: 1px solid #ced4da; border-radius: 8px; box-sizing: border-box; font-size: 14px; background: #fcfcfc; transition: 0.3s; }
        input:focus, select:focus, textarea:focus { border-color: #007bff; outline: none; background: white; box-shadow: 0 0 0 3px rgba(0,123,255,0.1); }
        label { font-weight: 600; color: #495057; font-size: 13px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        th { background-color: #0d2b45; color: white; font-weight: 600; padding: 14px; text-align: left; }
        td { padding: 14px; background-color: #ffffff; border-bottom: 1px solid #eee; }
        tr:last-child td { border-bottom: none; }
        .estado-verde td { background-color: #eaf6ed !important; }
        .estado-rojo td { background-color: #fdecea !important; }
        .estado-amarillo td { background-color: #fff8e1 !important; }
        .badge { display: inline-block; padding: 5px 12px; border-radius: 50px; font-size: 12px; font-weight: bold; }
        .badge-verde { background: #28a745; color: white; }
        .badge-rojo { background: #dc3545; color: white; }
        .badge-amarillo { background: #ffc107; color: #212529; }

        .tabs { display: flex; border-bottom: 2px solid #ddd; margin-bottom: 20px; }
        .tab-btn { background: #f1f1f1; border: 1px solid #ccc; border-bottom: none; padding: 10px 20px; cursor: pointer; margin-right: 5px; border-radius: 8px 8px 0 0; font-weight: bold; color: #666; }
        .tab-btn.active { background: white; color: #0d2b45; border-bottom: 2px solid white; margin-bottom: -2px; box-shadow: 0 -2px 5px rgba(0,0,0,0.05); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .dashboard-banner { background-image: url('/static/fondo_lab.jpg'); background-size: cover; background-position: center; margin: -20px -20px 20px -20px; padding: 40px 30px; border-radius: 16px 16px 0 0; color: white; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; text-shadow: 2px 2px 4px rgba(0,0,0,0.8); }
        .menu-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-top: 20px; }
        .menu-item { background: #ffffff; padding: 25px 15px; text-align: center; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); color: #333; font-weight: 600; transition: 0.3s; border: 1px solid #eee; }
        .menu-item:hover { background: #0d2b45; color: white; transform: translateY(-5px); box-shadow: 0 8px 25px rgba(13, 43, 69, 0.3); border-color: #0d2b45; }
        .alert { padding: 15px; border-radius: 10px; margin: 15px 0; border-left: 6px solid; }
        .alert-success { background: #eaf6ed; border-color: #28a745; color: #1e7e34; }
        .alert-danger { background: #fdecea; border-color: #dc3545; color: #a71d2a; }
        
        .attribution { text-align: center; font-size: 12px; color: #666; padding: 5px 0; background: #fcfcfc; border-bottom: 1px solid #eee; }
        @media (max-width: 600px) { .navbar { flex-direction: column; align-items: flex-start; padding: 15px; } .navbar a { margin: 5px 0; } .dashboard-banner { padding: 20px 15px; } }
        
        .adm-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        @media (max-width: 600px) { .adm-form-grid { grid-template-columns: 1fr; } }
        .adm-field { background: #f9f9f9; padding: 10px; border-radius: 8px; border: 1px solid #eee; }
        .adm-field label { display: block; margin-bottom: 4px; }
        .toolbar { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; padding-top: 15px; border-top: 2px solid #eee; }
        
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; justify-content: center; align-items: center; }
        .modal-box { background: white; padding: 25px; border-radius: 16px; max-width: 500px; width: 90%; box-shadow: 0 10px 30px rgba(0,0,0,0.3); position: relative; }
        .modal-box h3 { color: #0d2b45; margin-top: 0; text-align: center; }
        .close-modal { position: absolute; top: 10px; right: 15px; font-size: 24px; cursor: pointer; color: #666; }
        .close-modal:hover { color: #000; }
        
        .triaje-view-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; }
        .triaje-item { background: #fcfcfc; border: 1px solid #eee; border-radius: 8px; padding: 10px; text-align: center; }
        .triaje-item .label { font-weight: bold; font-size: 12px; color: #666; display: block; }
        .triaje-item .value { font-size: 18px; font-weight: bold; color: #0d2b45; display: block; margin-top: 5px; }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">🧪 <span>SISGALENO</span>2026</div>
        <div>
            {% if session.get('usuario') %}
                <span>👤 {{ session['usuario'] }} | Rol: {{ session['rol'] }}</span>
                <a href="{{ url_for('dashboard') }}">Inicio</a>
                {% if session['rol'] in ['administrador', 'medico', 'laboratorista', 'enfermera', 'tecnologo'] %}
                    <a href="{{ url_for('pacientes') }}">Pacientes</a>
                {% endif %}
                {% if session['rol'] in ['administrador', 'laboratorista', 'tecnologo'] %}
                    <a href="{{ url_for('laboratorio') }}">Laboratorio</a>
                    <a href="{{ url_for('resultados') }}">📊 Resultados</a>
                {% endif %}
                {% if session['rol'] == 'administrador' %}
                    <a href="{{ url_for('inventario') }}">📦 Inventario</a>
                    <a href="{{ url_for('catalogo_examenes') }}">📋 Catálogo</a>
                    <a href="{{ url_for('gestion_usuarios') }}" style="color: #ffc107;">👥 Usuarios</a>
                    <a href="{{ url_for('servicios_medicos') }}" style="color: #ffc107;">🏥 Médicos</a>
                    <a href="{{ url_for('configuracion_sistema') }}" style="color: #ffc107;">⚙️ Config</a>
                    <a href="{{ url_for('auditoria') }}" style="color: #ffc107;">📜 Auditoría</a>
                {% endif %}
                <a href="{{ url_for('cambiar_contrasena') }}" class="btn btn-warning" style="padding: 5px 15px; margin: 0 5px;">🔑 Cambiar Contraseña</a>
                <a href="{{ url_for('logout') }}" class="btn btn-danger" style="padding: 5px 15px;">Salir</a>
            {% endif %}
        </div>
    </nav>
    
    <div class="attribution">Creado by Yonan T:B</div>
    
    <div class="container">
        <!-- CONTENIDO_DINAMICO -->
    </div>
</body>
</html>
"""

# ==========================================
# LOGICA DE PDF
# ==========================================
def obtener_tamano_pagina():
    config = obtener_configuracion()
    tamano_str = config[1] if config and config[1] else 'A4'
    if tamano_str == 'LETTER': return LETTER
    elif tamano_str == 'LEGAL': return LEGAL
    else: return A4

def generar_reporte_resultados_pdf(id_atencion):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT p.nombre, p.apellido, p.dni, p.fecha_nacimiento, a.nro_boleta, a.fecha_atencion, a.servicio, a.medico
        FROM atenciones a
        JOIN pacientes p ON a.id_paciente = p.id
        WHERE a.id = ?
    """, (id_atencion,))
    datos_atencion = cursor.fetchone()
    
    cursor.execute("""
        SELECT e.descripcion, o.resultado
        FROM ordenes_laboratorio o
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id_atencion = ?
    """, (id_atencion,))
    examenes = cursor.fetchall()
    conn.close()
    
    if not datos_atencion or not examenes: return None
    
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    logo_path = config[2] if config else ''
    encabezado = config[3] if config else 'Laboratorio Clínico'
    pie_pagina = config[4] if config else ''
    
    page_size = obtener_tamano_pagina()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=page_size)
    width, height = page_size

    if logo_path and os.path.exists(os.path.join('static', logo_path)):
        try:
            img = ImageReader(os.path.join('static', logo_path))
            c.drawImage(img, 20, height - 70, width=80, height=60, preserveAspectRatio=True)
        except Exception as e:
            print(f"Error cargando logo: {e}")

    c.setFont("Helvetica-Bold", 16)
    c.drawString(110, height - 40, nombre_sistema)
    c.setFont("Helvetica", 12)
    c.drawString(110, height - 55, encabezado)
    c.line(20, height - 65, width - 20, height - 65)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(20, height - 90, "INFORME DE RESULTADOS")
    c.setFont("Helvetica", 11)
    c.drawString(20, height - 110, f"Paciente: {datos_atencion[0]} {datos_atencion[1]}")
    c.drawString(20, height - 125, f"DNI: {datos_atencion[2]}")
    c.drawString(20, height - 140, f"Fecha de Emisión: {datos_atencion[5]}")
    c.drawString(20, height - 155, f"Nro. Boleta: {datos_atencion[4]}")
    c.drawString(20, height - 170, f"Médico: {datos_atencion[7] if datos_atencion[7] else 'No especificado'}")

    c.setFont("Helvetica-Bold", 11)
    y_pos = height - 200
    c.drawString(20, y_pos, "EXAMEN SOLICITADO")
    c.drawString(300, y_pos, "RESULTADO")
    c.line(20, y_pos - 5, width - 20, y_pos - 5)
    
    y_pos -= 20
    c.setFont("Helvetica", 11)
    for ex in examenes:
        c.drawString(20, y_pos, ex[0])
        c.drawString(300, y_pos, ex[1] if ex[1] else "Pendiente")
        y_pos -= 20
        if y_pos < 50:
            c.showPage()
            y_pos = height - 40

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(20, 30, pie_pagina)
    
    c.save()
    buffer.seek(0)
    return buffer

def generar_ticket_pdf(paciente_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, dni, nombre, apellido FROM pacientes WHERE id=?", (paciente_id,))
    paciente = cursor.fetchone(); conn.close()
    if not paciente: return None
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    
    buffer = io.BytesIO(); c = canvas.Canvas(buffer, pagesize=A4); width, height = A4
    c.setFont("Helvetica-Bold", 18); c.drawString(30, height - 40, nombre_sistema)
    c.setFont("Helvetica", 10); c.drawString(30, height - 60, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    c.line(30, height - 85, width - 30, height - 85)
    c.setFont("Helvetica-Bold", 14); c.drawString(30, height - 110, "TICKET DE REGISTRO DE PACIENTE")
    c.setFont("Helvetica", 12)
    c.drawString(30, height - 140, f"ID: {paciente[0]}"); c.drawString(30, height - 160, f"DNI: {paciente[1]}")
    c.drawString(30, height - 180, f"Nombre: {paciente[2]}"); c.drawString(30, height - 200, f"Apellido: {paciente[3]}")
    c.line(30, height - 220, width - 30, height - 220)
    c.setFont("Helvetica-Oblique", 9); c.drawString(30, height - 250, config[4] if config else 'Generado automáticamente por SISGALENO2026.')
    c.save(); buffer.seek(0); return buffer

def buscar_atenciones_web(dni, fecha_desde, fecha_hasta):
    conn = get_db_connection(); cursor = conn.cursor()
    concat_func = "STRING_AGG" if IS_POSTGRES else "GROUP_CONCAT"
    sql = f"""SELECT a.id, p.nombre, p.apellido, {concat_func}(e.descripcion, ', ') as examenes, a.fecha_atencion, COUNT(CASE WHEN o.estado = 'Completado' THEN 1 END) as completados, COUNT(*) as total_examenes FROM atenciones a JOIN pacientes p ON a.id_paciente = p.id JOIN ordenes_laboratorio o ON o.id_atencion = a.id JOIN examenes_catalogo e ON o.id_examen = e.id WHERE 1=1"""
    params = []
    if dni and dni.strip() != "": sql += " AND p.dni LIKE ?"; params.append(f'%{dni.strip()}%')
    if fecha_desde and fecha_desde.strip() != "": sql += " AND date(a.fecha_atencion) >= ?"; params.append(fecha_desde.strip())
    if fecha_hasta and fecha_hasta.strip() != "": sql += " AND date(a.fecha_atencion) <= ?"; params.append(fecha_hasta.strip())
    if not dni and not fecha_desde and not fecha_hasta: sql += " AND (SELECT count(*) FROM ordenes_laboratorio sub WHERE sub.id_atencion = a.id AND sub.estado = 'Pendiente') > 0"
    sql += " GROUP BY a.id ORDER BY a.fecha_atencion DESC"
    cursor.execute(sql, params); datos = cursor.fetchall(); conn.close(); return datos

def obtener_examenes_por_atencion(id_atencion):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT o.id, e.descripcion, o.estado, o.resultado, o.precio, e.id FROM ordenes_laboratorio o JOIN examenes_catalogo e ON o.id_examen = e.id WHERE o.id_atencion = ?", (id_atencion,))
    examenes = cursor.fetchall(); conn.close(); return examenes

def calcular_total_atencion(id_atencion):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT SUM(precio) FROM ordenes_laboratorio WHERE id_atencion = ?", (id_atencion,))
    total = cursor.fetchone()[0]; conn.close()
    return total if total else 0.0

def obtener_triaje(id_atencion):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM triaje WHERE id_atencion = ?", (id_atencion,))
    datos = cursor.fetchone()
    conn.close()
    return datos

# ==========================================
# RUTAS PRINCIPALES
# ==========================================
@app.route('/')
def index():
    if 'usuario' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""; tipo_mensaje = ""
    if request.method == 'POST':
        user = request.form['usuario']; pwd = request.form['password']
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT rol FROM usuarios WHERE usuario=? AND password=?", (user, pwd))
        data = cursor.fetchone(); conn.close()
        if data:
            session['usuario'] = user; session['rol'] = data[0]; return redirect(url_for('dashboard'))
        else:
            mensaje = "Credenciales incorrectas."; tipo_mensaje = "alert-danger"
    contenido_login = """<h2 style="text-align: center; color: #0d2b45;">Inicio de Sesión</h2>{% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}<form method="POST"><label>Usuario</label><input type="text" name="usuario" required><label>Contraseña</label><input type="password" name="password" required><button type="submit" class="btn btn-primary" style="width: 100%; margin-top: 10px;">Acceder</button></form>"""
    html_login = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_login)
    return render_template_string(html_login, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'usuario' not in session: return redirect(url_for('login'))
    contenido_dashboard = """<div class="dashboard-banner"><div><h2>🔬 Bienvenido, {{ session['usuario'] }}</h2><p>Laboratorio Clínico SISGALENO2026</p></div><div style="font-size: 30px;">🧪💉</div></div><div class="menu-grid">{% if session['rol'] in ['administrador', 'medico', 'laboratorista', 'enfermera', 'tecnologo'] %}<a href="{{ url_for('pacientes') }}" class="menu-item">👤 Pacientes</a>{% endif %}{% if session['rol'] in ['administrador', 'laboratorista', 'tecnologo'] %}<a href="{{ url_for('laboratorio') }}" class="menu-item">🧪 Laboratorio</a><a href="{{ url_for('resultados') }}" class="menu-item">📊 Resultados</a>{% endif %}{% if session['rol'] == 'administrador' %}<a href="{{ url_for('inventario') }}" class="menu-item">📦 Inventario</a><a href="{{ url_for('catalogo_examenes') }}" class="menu-item">📋 Catálogo</a><a href="{{ url_for('gestion_usuarios') }}" class="menu-item">👥 Usuarios</a><a href="{{ url_for('servicios_medicos') }}" class="menu-item">🏥 Médicos</a><a href="{{ url_for('configuracion_sistema') }}" class="menu-item">⚙️ Config</a><a href="{{ url_for('auditoria') }}" class="menu-item">📜 Auditoría</a>{% endif %}</div>"""
    html_dashboard = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_dashboard)
    return render_template_string(html_dashboard)

# ==========================================
# GESTIÓN DE USUARIOS (Rol y Eliminación)
# ==========================================
@app.route('/gestion_usuarios', methods=['GET'])
def gestion_usuarios():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, usuario, rol FROM usuarios ORDER BY id DESC")
    usuarios = cursor.fetchall(); conn.close()
    
    contenido_usuarios = """
    <h2 style="color: #0d2b45;">👥 Gestión de Usuarios</h2>
    <div style="display: flex; gap: 10px; margin-bottom: 15px;">
        <a href="{{ url_for('agregar_usuario') }}" class="btn btn-success">+ Nuevo Usuario</a>
    </div>
    <table>
        <thead><tr><th>ID</th><th>Usuario</th><th>Rol</th><th>Acciones</th></tr></thead>
        <tbody>
            {% for u in usuarios %}
            <tr>
                <td>{{ u[0] }}</td>
                <td><b>{{ u[1] }}</b></td>
                <td><span class="badge badge-amarillo">{{ u[2] }}</span></td>
                <td>
                    <a href="{{ url_for('editar_usuario', id=u[0]) }}" class="btn btn-warning" style="padding: 4px 10px; font-size: 12px;">✏️ Editar Rol</a>
                    <form method="POST" action="{{ url_for('eliminar_usuario', id=u[0]) }}" style="display:inline;" onsubmit="return confirm('¿Seguro que desea eliminar este usuario?');">
                        <button type="submit" class="btn btn-danger" style="padding: 4px 10px; font-size: 12px;">🗑️ Eliminar</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    """
    html_usuarios = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_usuarios)
    return render_template_string(html_usuarios, usuarios=usuarios)

@app.route('/gestion_usuarios/agregar', methods=['GET', 'POST'])
def agregar_usuario():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    if request.method == 'POST':
        usuario = request.form['usuario']; password = request.form['password']; rol = request.form['rol']
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO usuarios (usuario, password, rol) VALUES (?, ?, ?)", (usuario, password, rol))
            conn.commit(); 
            log_audit(session['usuario'], 'INSERT', 'usuarios', cursor.lastrowid, f'Usuario creado: {usuario}, Rol: {rol}')
            conn.close()
            return redirect(url_for('gestion_usuarios'))
        except sqlite3.IntegrityError:
            mensaje = "Error: El nombre de usuario ya existe."; tipo_mensaje = "alert-danger"
        except Exception as e:
            mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
    
    contenido_nuevo = """
    <h2 style="color: #0d2b45;">Agregar Nuevo Usuario</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <div class="adm-field"><label>Nombre de Usuario</label><input type="text" name="usuario" required></div>
            <div class="adm-field"><label>Contraseña</label><input type="password" name="password" required></div>
            <div class="adm-field"><label>Rol</label>
                <select name="rol" required>
                    <option value="">Seleccione</option>
                    <option value="administrador">Administrador</option>
                    <option value="medico">Médico</option>
                    <option value="tecnologo">Tecnólogo</option>
                    <option value="laboratorista">Técnico de Laboratorio</option>
                    <option value="enfermera">Enfermera</option>
                </select>
            </div>
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Crear Usuario</button>
                <a href="{{ url_for('gestion_usuarios') }}" class="btn btn-danger">Cancelar</a>
            </div>
        </form>
    </div>
    """
    html_nuevo = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_nuevo)
    return render_template_string(html_nuevo, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/gestion_usuarios/editar/<int:id>', methods=['GET', 'POST'])
def editar_usuario(id):
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    
    if request.method == 'POST':
        nuevo_rol = request.form['rol']
        try:
            cursor.execute("UPDATE usuarios SET rol=? WHERE id=?", (nuevo_rol, id))
            conn.commit()
            log_audit(session['usuario'], 'UPDATE', 'usuarios', id, f'Rol actualizado a: {nuevo_rol}')
            conn.close()
            return redirect(url_for('gestion_usuarios'))
        except Exception as e:
            conn.close()
            mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
    else:
        cursor.execute("SELECT id, usuario, rol FROM usuarios WHERE id=?", (id,))
        usuario = cursor.fetchone()
        conn.close()
        if not usuario: return "Usuario no encontrado", 404
        mensaje = ""; tipo_mensaje = ""

    contenido_editar = """
    <h2 style="color: #0d2b45;">Editar Rol de Usuario</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <div class="adm-field"><label>Usuario</label><input type="text" value="{{ usuario[1] }}" disabled style="background:#d1d1d1;"></div>
            <div class="adm-field"><label>Nuevo Rol</label>
                <select name="rol" required>
                    <option value="">Seleccione</option>
                    <option value="administrador" {% if usuario[2] == 'administrador' %}selected{% endif %}>Administrador</option>
                    <option value="medico" {% if usuario[2] == 'medico' %}selected{% endif %}>Médico</option>
                    <option value="tecnologo" {% if usuario[2] == 'tecnologo' %}selected{% endif %}>Tecnólogo</option>
                    <option value="laboratorista" {% if usuario[2] == 'laboratorista' %}selected{% endif %}>Técnico de Lab</option>
                    <option value="enfermera" {% if usuario[2] == 'enfermera' %}selected{% endif %}>Enfermera</option>
                </select>
            </div>
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Guardar Cambios</button>
                <a href="{{ url_for('gestion_usuarios') }}" class="btn btn-danger">Cancelar</a>
            </div>
        </form>
    </div>
    """
    html_edit = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_editar)
    return render_template_string(html_edit, usuario=usuario, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/gestion_usuarios/eliminar/<int:id>', methods=['POST'])
def eliminar_usuario(id):
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT usuario FROM usuarios WHERE id=?", (id,))
        res = cursor.fetchone()
        if res:
            log_audit(session['usuario'], 'DELETE', 'usuarios', id, f'Usuario eliminado: {res[0]}')
        cursor.execute("DELETE FROM usuarios WHERE id=?", (id,))
        conn.commit(); conn.close()
    except Exception as e:
        pass
    return redirect(url_for('gestion_usuarios'))

# ==========================================
# GESTIÓN DE SERVICIOS MÉDICOS
# ==========================================
@app.route('/servicios_medicos', methods=['GET'])
def servicios_medicos():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, usuario FROM usuarios WHERE rol='medico' ORDER BY usuario")
    medicos = cursor.fetchall()
    cursor.execute("SELECT id, nombre_seccion FROM examenes_secciones ORDER BY nombre_seccion")
    servicios = cursor.fetchall()
    cursor.execute("SELECT id_medico, servicio FROM medico_servicio")
    asignaciones = cursor.fetchall()
    conn.close()
    
    contenido_servicios = """
    <h2 style="color: #0d2b45;">🏥 Asignación de Servicios Médicos</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        <form method="POST" action="{{ url_for('guardar_servicios_medicos') }}">
            <table>
                <thead><tr><th>Médico</th><th>Servicio Asignado</th></tr></thead>
                <tbody>
                    {% for m in medicos %}
                    <tr>
                        <td><b>{{ m[1] }}</b></td>
                        <td>
                            <select name="servicio_{{ m[0] }}">
                                <option value="">Sin Asignación</option>
                                {% for s in servicios %}
                                <option value="{{ s[1] }}" {% for a in asignaciones %}{% if a[0] == m[0] and a[1] == s[1] %}selected{% endif %}{% endfor %}>{{ s[1] }}</option>
                                {% endfor %}
                            </select>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="2" style="text-align:center;">No hay médicos registrados en el sistema.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Guardar Asignaciones</button>
                <button type="reset" class="btn btn-danger">Cancelar</button>
            </div>
        </form>
    </div>
    """
    html_serv = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_servicios)
    return render_template_string(html_serv, medicos=medicos, servicios=servicios, asignaciones=asignaciones)

@app.route('/servicios_medicos/guardar', methods=['POST'])
def guardar_servicios_medicos():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    # Limpiar asignaciones anteriores
    cursor.execute("DELETE FROM medico_servicio")
    for key, value in request.form.items():
        if key.startswith('servicio_') and value.strip():
            id_medico = int(key.split('_')[1])
            servicio = value.strip()
            cursor.execute("INSERT INTO medico_servicio (id_medico, servicio) VALUES (?, ?)", (id_medico, servicio))
    conn.commit()
    log_audit(session['usuario'], 'UPDATE', 'medico_servicio', 0, 'Asignaciones de servicios médicos actualizadas.')
    conn.close()
    return redirect(url_for('servicios_medicos'))

# ==========================================
# CONFIGURACIÓN Y CAMBIO DE FONDO
# ==========================================
@app.route('/configuracion', methods=['GET', 'POST'])
def configuracion_sistema():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    
    if request.method == 'POST':
        nombre_sistema = request.form['nombre_sistema']
        tamano_hoja = request.form['tamano_hoja']
        encabezado = request.form['encabezado_texto']
        pie_pagina = request.form['pie_pagina_texto']
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("""
                UPDATE configuracion_sistema SET 
                nombre_sistema=?, tamano_hoja=?, encabezado_texto=?, pie_pagina_texto=?
                WHERE id=1
            """, (nombre_sistema, tamano_hoja, encabezado, pie_pagina))
            conn.commit()
            log_audit(session['usuario'], 'UPDATE', 'configuracion_sistema', 1, 'Configuración general actualizada.')
            conn.close()
            mensaje = "Configuración actualizada exitosamente."; tipo_mensaje = "alert-success"
        except Exception as e:
            mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"

    config = obtener_configuracion()
    
    contenido_config = """
    <h2 style="color: #0d2b45;">⚙️ Configuración del Sistema</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <div class="adm-field"><label>Nombre del Sistema</label><input type="text" name="nombre_sistema" value="{{ config[0] }}" required></div>
            <div class="adm-field"><label>Tamaño de Hoja (PDF)</label>
                <select name="tamano_hoja">
                    <option value="A4" {% if config[1] == 'A4' %}selected{% endif %}>A4</option>
                    <option value="LETTER" {% if config[1] == 'LETTER' %}selected{% endif %}>Carta (Letter)</option>
                    <option value="LEGAL" {% if config[1] == 'LEGAL' %}selected{% endif %}>Oficio (Legal)</option>
                </select>
            </div>
            <div class="adm-field"><label>Texto Encabezado</label><input type="text" name="encabezado_texto" value="{{ config[3] }}"></div>
            <div class="adm-field"><label>Texto Pie de Página</label><input type="text" name="pie_pagina_texto" value="{{ config[4] }}"></div>

            <div style="border-top: 1px solid #ddd; margin-top: 20px; padding-top: 20px;">
                <h4 style="color: #0d2b45;">Logo de la Institución</h4>
                {% if config[2] %}
                    <p>Logo actual: <b>{{ config[2] }}</b></p>
                    <img src="/static/{{ config[2] }}" style="max-height: 80px; border: 1px solid #ccc; border-radius: 4px;">
                {% else %}
                    <p>No hay logo configurado.</p>
                {% endif %}
                <div style="margin-top: 10px;">
                    <a href="{{ url_for('subir_logo') }}" class="btn btn-primary">Cambiar / Subir Logo</a>
                </div>
            </div>

            <div style="border-top: 1px solid #ddd; margin-top: 20px; padding-top: 20px;">
                <h4 style="color: #0d2b45;">Imagen de Fondo del Sistema</h4>
                <p>La imagen actual es <b>fondo_lab.jpg</b> en la carpeta static.</p>
                <div style="margin-top: 10px;">
                    <a href="{{ url_for('subir_fondo') }}" class="btn btn-primary">Cambiar Fondo del Sistema</a>
                </div>
            </div>

            <div class="toolbar">
                <button type="submit" class="btn btn-success">Guardar Configuración</button>
                <button type="reset" class="btn btn-danger">Resetear</button>
            </div>
        </form>
    </div>
    """
    html_conf = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_config)
    return render_template_string(html_conf, config=config, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/configuracion/subir_logo', methods=['GET', 'POST'])
def subir_logo():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    
    if request.method == 'POST':
        if 'logo_archivo' not in request.files:
            mensaje = "No se seleccionó ningún archivo."
            tipo_mensaje = "alert-danger"
        else:
            file = request.files['logo_archivo']
            if file.filename == '':
                mensaje = "No se seleccionó ningún archivo."
                tipo_mensaje = "alert-danger"
            elif file:
                filename = secure_filename(file.filename)
                if not os.path.exists('static'):
                    os.makedirs('static')
                # Limpiar la carpeta static de logos antiguos (manteniendo fondo_lab.jpg)
                for f in os.listdir('static'):
                    if f != 'fondo_lab.jpg' and f.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        os.remove(os.path.join('static', f))
                
                file.save(os.path.join('static', filename))
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("UPDATE configuracion_sistema SET logo_path=? WHERE id=1", (filename,))
                conn.commit()
                log_audit(session['usuario'], 'UPDATE', 'configuracion_sistema', 1, f'Nuevo logo subido: {filename}')
                conn.close()
                return redirect(url_for('configuracion_sistema'))

    contenido_logo = """
    <h2 style="color: #0d2b45;">Subir Nuevo Logo</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST" enctype="multipart/form-data">
            <div class="adm-field">
                <label>Seleccionar Imagen (PNG, JPG)</label>
                <input type="file" name="logo_archivo" accept="image/png, image/jpeg" required>
            </div>
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Subir Logo</button>
                <a href="{{ url_for('configuracion_sistema') }}" class="btn btn-danger">Cancelar</a>
            </div>
        </form>
    </div>
    """
    html_logo = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_logo)
    return render_template_string(html_logo, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/configuracion/subir_fondo', methods=['GET', 'POST'])
def subir_fondo():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    
    if request.method == 'POST':
        if 'fondo_archivo' not in request.files:
            mensaje = "No se seleccionó ningún archivo."
            tipo_mensaje = "alert-danger"
        else:
            file = request.files['fondo_archivo']
            if file.filename == '':
                mensaje = "No se seleccionó ningún archivo."
                tipo_mensaje = "alert-danger"
            elif file:
                if not os.path.exists('static'):
                    os.makedirs('static')
                # Sobrescribir fondo_lab.jpg
                file.save(os.path.join('static', 'fondo_lab.jpg'))
                log_audit(session['usuario'], 'UPDATE', 'configuracion_sistema', 1, 'Fondo del sistema actualizado.')
                return redirect(url_for('configuracion_sistema'))

    contenido_fondo = """
    <h2 style="color: #0d2b45;">Cambiar Fondo del Sistema</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        <p>Recomendación: Usa una imagen de al menos 1920x1080 píxeles (JPG o PNG).</p>
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST" enctype="multipart/form-data">
            <div class="adm-field">
                <label>Seleccionar Imagen de Fondo</label>
                <input type="file" name="fondo_archivo" accept="image/png, image/jpeg" required>
            </div>
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Subir y Cambiar Fondo</button>
                <a href="{{ url_for('configuracion_sistema') }}" class="btn btn-danger">Cancelar</a>
            </div>
        </form>
    </div>
    """
    html_fondo = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_fondo)
    return render_template_string(html_fondo, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

# ==========================================
# MÓDULO DE AUDITORÍA
# ==========================================
@app.route('/auditoria', methods=['GET'])
def auditoria():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT id, usuario, accion, tabla, registro_id, fecha_hora, ip_address, detalles 
        FROM audit_logs ORDER BY fecha_hora DESC LIMIT 200
    """)
    logs = cursor.fetchall()
    conn.close()

    contenido_audit = """
    <h2 style="color: #0d2b45;">📜 Registro de Auditoría del Sistema</h2>
    <p style="color:#666; font-size: 13px;">Aquí se registran todas las acciones de creación, modificación y eliminación de los usuarios, pacientes y atenciones.</p>
    <div style="overflow-x: auto;">
        <table>
            <thead><tr><th>Fecha/Hora</th><th>Usuario</th><th>Acción</th><th>Tabla</th><th>ID Reg</th><th>IP</th><th>Detalles</th></tr></thead>
            <tbody>
                {% for l in logs %}
                <tr>
                    <td style="font-size: 12px;">{{ l[5] }}</td>
                    <td><b>{{ l[1] }}</b></td>
                    <td><span class="badge badge-verde" style="font-size: 10px;">{{ l[2] }}</span></td>
                    <td>{{ l[3] }}</td>
                    <td>{{ l[4] }}</td>
                    <td style="font-size: 12px;">{{ l[6] }}</td>
                    <td style="font-size: 12px; font-style: italic;">{{ l[7] }}</td>
                </tr>
                {% else %}
                <tr><td colspan="7" style="text-align:center;">No se han registrado acciones aún.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    html_audit = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_audit)
    return render_template_string(html_audit, logs=logs)

# ==========================================
# RUTAS DE PACIENTES
# ==========================================
@app.route('/pacientes', methods=['GET', 'POST'])
def pacientes():
    if 'usuario' not in session: return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""; nuevo_paciente_id = None
    if request.method == 'POST':
        dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
        fecha_nac = request.form.get('fecha_nacimiento', ''); telefono = request.form.get('telefono', '')
        direccion = request.form.get('direccion', ''); sexo = request.form.get('sexo', ''); edad = request.form.get('edad', 0)
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO pacientes (dni, nombre, apellido, fecha_nacimiento, telefono, direccion, sexo, edad) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
                           (dni, nombre, apellido, fecha_nac, telefono, direccion, sexo, edad))
            nuevo_paciente_id = cursor.lastrowid; conn.commit()
            log_audit(session['usuario'], 'INSERT', 'pacientes', nuevo_paciente_id, f'Paciente registrado: {nombre} {apellido} (DNI: {dni})')
            conn.close()
            mensaje = f"Paciente {nombre} {apellido} registrado."; tipo_mensaje = "alert-success"
        except sqlite3.IntegrityError:
            mensaje = "Error: El DNI ya está registrado."; tipo_mensaje = "alert-danger"
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, dni, nombre, apellido, sexo, edad FROM pacientes ORDER BY id DESC")
    lista_pacientes = cursor.fetchall(); conn.close()
    
    contenido_pacientes = """<div style="display: flex; justify-content: space-between; align-items: center;"><h2 style="color: #0d2b45;">Gestión de Pacientes</h2><button onclick="toggleForm()" class="btn btn-success">+ Nuevo Paciente</button></div><div id="form_registro" style="display: none; background: #f8f9fa; padding: 20px; border-radius: 12px; margin-bottom: 20px;">{% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}<form method="POST"><label>DNI</label><input type="text" name="dni" required><label>Nombre</label><input type="text" name="nombre" required><label>Apellido</label><input type="text" name="apellido" required><label>Fecha Nacimiento</label><input type="date" name="fecha_nacimiento"><label>Teléfono</label><input type="text" name="telefono"><label>Dirección</label><input type="text" name="direccion"><label>Sexo</label><select name="sexo"><option value="">Seleccione</option><option value="MASCULINO">MASCULINO</option><option value="FEMENINO">FEMENINO</option></select><label>Edad</label><input type="number" name="edad"><button type="submit" class="btn btn-primary">Guardar Paciente</button></form>{% if nuevo_paciente_id %}<div style="margin-top: 15px; border-top: 1px solid #ddd; padding-top: 15px;"><a href="{{ url_for('descargar_ticket', paciente_id=nuevo_paciente_id) }}" target="_blank" class="btn btn-primary">📥 Ticket PDF</a><a href="https://wa.me/?text=Hola%2C%20se%20ha%20generado%20el%20ticket%20del%20paciente%20{{ nombre }}%20{{ apellido }}%20para%20su%20atenci%C3%B3n.%20Desc%C3%A1rgalo%20aqu%C3%AD%3A%20{{ url_for('descargar_ticket', paciente_id=nuevo_paciente_id, _external=True) }}" target="_blank" class="btn btn-whatsapp">📱 Enviar por WhatsApp</a></div>{% endif %}</div><table><thead><tr><th>ID</th><th>DNI</th><th>Nombre</th><th>Apellido</th><th>Acciones</th></tr></thead><tbody>{% for p in pacientes %}<tr><td>{{ p[0] }}</td><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td><a href="{{ url_for('editar_paciente', id=p[0]) }}" class="btn btn-warning" style="padding: 4px 10px; font-size: 12px;">✏️ Editar</a></td></tr>{% endfor %}</tbody></table><script>function toggleForm(){ var x = document.getElementById("form_registro"); if (x.style.display === "none") { x.style.display = "block"; } else { x.style.display = "none"; } }</script>"""
    html_pacientes = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_pacientes)
    return render_template_string(html_pacientes, pacientes=lista_pacientes, mensaje=mensaje, tipo_mensaje=tipo_mensaje, nuevo_paciente_id=nuevo_paciente_id, nombre=request.form.get('nombre', ''), apellido=request.form.get('apellido', ''))

@app.route('/ticket/<int:paciente_id>')
def descargar_ticket(paciente_id):
    if 'usuario' not in session: return redirect(url_for('login'))
    pdf_buffer = generar_ticket_pdf(paciente_id)
    if not pdf_buffer: return "Paciente no encontrado", 404
    return send_file(pdf_buffer, as_attachment=True, download_name=f"Ticket_Paciente_{paciente_id}.pdf", mimetype='application/pdf')

@app.route('/pacientes/editar/<int:id>', methods=['GET', 'POST'])
def editar_paciente(id):
    if 'usuario' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    if request.method == 'POST':
        dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
        fecha_nac = request.form.get('fecha_nacimiento', ''); telefono = request.form.get('telefono', '')
        direccion = request.form.get('direccion', ''); sexo = request.form.get('sexo', ''); edad = request.form.get('edad', 0)
        try:
            cursor.execute("UPDATE pacientes SET dni=?, nombre=?, apellido=?, fecha_nacimiento=?, telefono=?, direccion=?, sexo=?, edad=? WHERE id=?", 
                           (dni, nombre, apellido, fecha_nac, telefono, direccion, sexo, edad, id))
            conn.commit()
            log_audit(session['usuario'], 'UPDATE', 'pacientes', id, f'Paciente actualizado: {nombre} {apellido}')
            conn.close(); return redirect(url_for('pacientes'))
        except Exception as e:
            conn.close(); mensaje = f"Error al actualizar: {str(e)}"
            cursor = conn.cursor(); cursor.execute("SELECT * FROM pacientes WHERE id=?", (id,)); paciente = cursor.fetchone(); conn.close()
            return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_editar), paciente=paciente, mensaje=mensaje, tipo_mensaje="alert-danger")
    cursor.execute("SELECT * FROM pacientes WHERE id=?", (id,)); paciente = cursor.fetchone(); conn.close()
    if not paciente: return "Paciente no encontrado", 404
    contenido_editar = """<div style="display: flex; justify-content: space-between; align-items: center;"><h2 style="color: #0d2b45;">Editar Paciente</h2><a href="{{ url_for('pacientes') }}" class="btn btn-danger">Cancelar</a></div><div style="background: #f8f9fa; padding: 20px; border-radius: 12px; margin-bottom: 20px;">{% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}<form method="POST"><label>DNI</label><input type="text" name="dni" value="{{ paciente[1] }}" required><label>Nombre</label><input type="text" name="nombre" value="{{ paciente[2] }}" required><label>Apellido</label><input type="text" name="apellido" value="{{ paciente[3] }}" required><label>Fecha Nacimiento</label><input type="date" name="fecha_nacimiento" value="{{ paciente[4] if paciente[4] else '' }}"><label>Teléfono</label><input type="text" name="telefono" value="{{ paciente[5] if paciente[5] else '' }}"><label>Dirección</label><input type="text" name="direccion" value="{{ paciente[6] if paciente[6] else '' }}"><label>Sexo</label><select name="sexo"><option value="">Seleccione</option><option value="MASCULINO" {% if paciente[7] == 'MASCULINO' %}selected{% endif %}>MASCULINO</option><option value="FEMENINO" {% if paciente[7] == 'FEMENINO' %}selected{% endif %}>FEMENINO</option></select><label>Edad</label><input type="number" name="edad" value="{{ paciente[8] if paciente[8] else 0 }}"><button type="submit" class="btn btn-primary">Guardar Cambios</button></form></div>"""
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_editar), paciente=paciente, mensaje="", tipo_mensaje="")

# ==========================================
# MÓDULO DE ATENCIÓN (F3 / F4 / F5)
# ==========================================
def obtener_siguiente_boleta():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT MAX(nro_boleta) FROM atenciones")
    max_boleta = cursor.fetchone()[0]
    conn.close()
    return (max_boleta + 1) if max_boleta else 100001

@app.route('/atencion/nueva/<int:id_paciente>', methods=['GET'])
def nueva_atencion(id_paciente):
    if 'usuario' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT nombre, apellido, sexo, edad FROM pacientes WHERE id=?", (id_paciente,))
    paciente = cursor.fetchone(); conn.close()
    if not paciente: return "Paciente no encontrado", 404

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    hora_hoy = datetime.now().strftime("%H:%M")
    nro_boleta = obtener_siguiente_boleta()
    
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO atenciones (id_paciente, nro_boleta, secuencia, fecha_atencion, hora_registro, sexo, edad) 
        VALUES (?, ?, 1, ?, ?, ?, ?)
    """, (id_paciente, nro_boleta, fecha_hoy, hora_hoy, paciente[2], paciente[3]))
    id_atencion = cursor.lastrowid
    conn.commit()
    log_audit(session['usuario'], 'INSERT', 'atenciones', id_atencion, f'Nueva atención creada para el paciente ID {id_paciente}')
    conn.close()
    
    return redirect(url_for('gestion_admision', id_atencion=id_atencion))

@app.route('/atencion/admision/<int:id_atencion>', methods=['GET', 'POST'])
def gestion_admision(id_atencion):
    if 'usuario' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.id_paciente, p.nombre, p.apellido, p.dni, p.fecha_nacimiento, a.nro_boleta, 
               a.secuencia, a.fecha_atencion, a.hora_registro, a.historia_clinica, a.sexo, a.edad, 
               a.tipo_paciente, a.detalle_tipo, a.origen, a.pst_hosp_origen, a.servicio, a.medico, a.nro_cama
        FROM atenciones a JOIN pacientes p ON a.id_paciente = p.id WHERE a.id = ?
    """, (id_atencion,))
    atencion = cursor.fetchone()
    if not atencion: return "Atención no encontrada", 404

    examenes = obtener_examenes_por_atencion(id_atencion)
    total = calcular_total_atencion(id_atencion)
    catalogo = obtener_examenes_catalogo()
    triaje = obtener_triaje(id_atencion)
    
    mensaje = ""; tipo_mensaje = ""
    
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'guardar_f3':
            try:
                historia = request.form.get('historia_clinica', '')
                sexo = request.form.get('sexo', '')
                edad = request.form.get('edad', 0)
                tipo_pac = request.form.get('tipo_paciente', '')
                detalle = request.form.get('detalle_tipo', '')
                origen = request.form.get('origen', '')
                pst_hosp = request.form.get('pst_hosp_origen', '')
                servicio = request.form.get('servicio', '')
                medico = request.form.get('medico', '')
                nro_cama = request.form.get('nro_cama', '')
                
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("""
                    UPDATE atenciones SET 
                    historia_clinica=?, sexo=?, edad=?, tipo_paciente=?, detalle_tipo=?, origen=?, 
                    pst_hosp_origen=?, servicio=?, medico=?, nro_cama=?
                    WHERE id=?
                """, (historia, sexo, edad, tipo_pac, detalle, origen, pst_hosp, servicio, medico, nro_cama, id_atencion))
                conn.commit()
                log_audit(session['usuario'], 'UPDATE', 'atenciones', id_atencion, f'Datos F3 actualizados. Servicio: {servicio}, Médico: {medico}')
                conn.close()
                mensaje = "Datos de Atención guardados."; tipo_mensaje = "alert-success"
            except Exception as e:
                mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
        elif accion == 'agregar_examen':
            id_examen = int(request.form['id_examen']); precio = float(request.form['precio'])
            try:
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO ordenes_laboratorio (id_paciente, id_examen, id_atencion, fecha_emision, estado, resultado, precio) VALUES (?, ?, ?, ?, 'Pendiente', '', ?)", 
                               (atencion[4], id_examen, id_atencion, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), precio))
                nuevo_id = cursor.lastrowid
                conn.commit()
                log_audit(session['usuario'], 'INSERT', 'ordenes_laboratorio', nuevo_id, f'Examen agregado a atención {id_atencion}')
                conn.close()
                return redirect(url_for('gestion_admision', id_atencion=id_atencion))
            except Exception as e:
                mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
        elif accion == 'eliminar_atencion':
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("DELETE FROM ordenes_laboratorio WHERE id_atencion = ?", (id_atencion,))
            cursor.execute("DELETE FROM atenciones WHERE id = ?", (id_atencion,))
            conn.commit()
            log_audit(session['usuario'], 'DELETE', 'atenciones', id_atencion, 'Atención eliminada completamente.')
            conn.close()
            return redirect(url_for('laboratorio'))
        elif accion == 'buscar_paciente':
            dni_busq = request.form.get('dni_busqueda', '').strip()
            if dni_busq:
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("SELECT id FROM pacientes WHERE dni = ?", (dni_busq,))
                res = cursor.fetchone(); conn.close()
                if res:
                    return redirect(url_for('nueva_atencion', id_paciente=res[0]))
                else:
                    mensaje = "Paciente no encontrado con ese DNI."; tipo_mensaje = "alert-danger"
        elif accion == 'guardar_triaje':
            if session['rol'] not in ['enfermera', 'administrador']:
                mensaje = "No tienes permisos para realizar triaje."
                tipo_mensaje = "alert-danger"
            else:
                try:
                    presion_sis = int(request.form.get('presion_sis', 0))
                    presion_dias = int(request.form.get('presion_dias', 0))
                    fc = int(request.form.get('fc', 0))
                    fr = int(request.form.get('fr', 0))
                    temp = float(request.form.get('temperatura', 0.0))
                    sat = int(request.form.get('saturacion', 0))
                    peso = float(request.form.get('peso', 0.0))
                    talla = float(request.form.get('talla', 0.0))
                    imc = round(peso / (talla * talla), 2) if talla > 0 else 0.0
                    glasgow = int(request.form.get('glasgow', 0))
                    dolor = int(request.form.get('dolor', 0))
                    obs = request.form.get('observaciones', '')
                    fecha_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    enfermera = session['usuario']
                    
                    conn = get_db_connection(); cursor = conn.cursor()
                    cursor.execute("""
                        INSERT OR REPLACE INTO triaje (id_atencion, fecha_hora, enfermera, presion_sistolica, presion_diastolica, frecuencia_cardiaca, frecuencia_respiratoria, temperatura, saturacion_oxigeno, peso, talla, imc, glasgow, dolor, observaciones)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (id_atencion, fecha_hora, enfermera, presion_sis, presion_dias, fc, fr, temp, sat, peso, talla, imc, glasgow, dolor, obs))
                    conn.commit()
                    log_audit(session['usuario'], 'INSERT', 'triaje', id_atencion, f'Triaje registrado por {enfermera}')
                    conn.close()
                    mensaje = "Triaje guardado exitosamente."; tipo_mensaje = "alert-success"
                except Exception as e:
                    mensaje = f"Error al guardar triaje: {str(e)}"; tipo_mensaje = "alert-danger"

    contenido_admision = """
    <script>
        function abrirPestana(nombre) {
            var tabs = document.getElementsByClassName("tab-content");
            var btns = document.getElementsByClassName("tab-btn");
            for(var i=0; i<tabs.length; i++){ tabs[i].classList.remove("active"); btns[i].classList.remove("active"); }
            document.getElementById("tab_"+nombre).classList.add("active");
            document.getElementById("btn_"+nombre).classList.add("active");
        }
        function calcularIMC() {
            var peso = parseFloat(document.getElementById('peso').value);
            var talla = parseFloat(document.getElementById('talla').value);
            var imcInput = document.getElementById('imc');
            if(peso > 0 && talla > 0) {
                var imc = peso / (talla * talla);
                imcInput.value = imc.toFixed(2);
            } else {
                imcInput.value = '';
            }
        }
    </script>

    <div class="tabs">
        <div class="tab-btn active" id="btn_f3" onclick="abrirPestana('f3')">F3 - ATENCIÓN</div>
        <div class="tab-btn" id="btn_f4" onclick="abrirPestana('f4')">F4 - EXÁMENES</div>
        {% if session['rol'] in ['enfermera', 'medico', 'administrador'] %}
            <div class="tab-btn" id="btn_f5" onclick="abrirPestana('f5')">F5 - TRIAJE</div>
        {% endif %}
    </div>

    <div id="tab_f3" class="tab-content active">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        
        <div style="background: #f8f9fa; padding: 15px; border-radius: 12px; border: 1px solid #ddd;">
            <h3 style="color: #0d2b45; margin-top:0;">INGRESO DE PACIENTES PARA EXÁMENES</h3>
            <form method="POST" style="display: flex; gap: 10px; margin-bottom: 15px; align-items: center;">
                <input type="hidden" name="accion" value="buscar_paciente">
                <label style="margin:0;">Buscar Paciente por DNI:</label>
                <input type="text" name="dni_busqueda" style="width: 200px; margin:0; padding: 8px;">
                <button type="submit" class="btn btn-primary" style="padding: 8px 15px;">🔍 Buscar</button>
            </form>

            <form method="POST">
                <input type="hidden" name="accion" value="guardar_f3">
                <div class="adm-form-grid">
                    <div class="adm-field"><label>NRO DE BOLETA:</label><input type="text" value="{{ atencion[6] }}" disabled style="background:#d1d1d1; color:#555; font-weight:bold; border:2px solid #333;"></div>
                    <div class="adm-field"><label>SECUENCIA:</label><input type="text" value="{{ atencion[7] }}" disabled style="background:#d1d1d1; color:#555; font-weight:bold;"></div>
                    <div class="adm-field"><label>FECHA DE REGISTRO:</label><input type="text" value="{{ atencion[8] }}" disabled></div>
                    <div class="adm-field"><label>HORA DE REGISTRO:</label><input type="text" value="{{ atencion[9] }}" disabled></div>
                    <div class="adm-field" style="grid-column: span 2;"><label>APELLIDOS Y NOMBRES:</label><input type="text" value="{{ atencion[2] }} {{ atencion[3] }} (DNI: {{ atencion[4] }})" disabled style="font-weight:bold;"></div>
                    <div class="adm-field"><label>HISTORIA CLÍNICA:</label><input type="text" name="historia_clinica" value="{{ atencion[10] if atencion[10] else '' }}"></div>
                    <div class="adm-field"><label>SEXO:</label>
                        <select name="sexo"><option value="">Seleccione</option><option value="MASCULINO" {% if atencion[11] == 'MASCULINO' %}selected{% endif %}>MASCULINO</option><option value="FEMENINO" {% if atencion[11] == 'FEMENINO' %}selected{% endif %}>FEMENINO</option></select>
                    </div>
                    <div class="adm-field"><label>EDAD (AÑOS):</label><input type="number" name="edad" value="{{ atencion[12] if atencion[12] else 0 }}"></div>
                    <div class="adm-field"><label>TIPO DE PACIENTE:</label>
                        <select name="tipo_paciente"><option value="">Seleccione</option><option value="CONVENIO/DEMANDA" {% if atencion[13] == 'CONVENIO/DEMANDA' %}selected{% endif %}>CONVENIO/DEMANDA</option><option value="SIS" {% if atencion[13] == 'SIS' %}selected{% endif %}>SIS</option><option value="PARTICULAR" {% if atencion[13] == 'PARTICULAR' %}selected{% endif %}>PARTICULAR</option></select>
                    </div>
                    <div class="adm-field"><label>DETALLE DEL TIPO:</label>
                        <select name="detalle_tipo"><option value="">Seleccione</option><option value="102" {% if atencion[14] == '102' %}selected{% endif %}>102 - PARTICULAR</option><option value="SIS" {% if atencion[14] == 'SIS' %}selected{% endif %}>SIS</option></select>
                    </div>
                    <div class="adm-field"><label>ORIGEN:</label>
                        <select name="origen"><option value="">Seleccione</option><option value="COMÚN" {% if atencion[15] == 'COMÚN' %}selected{% endif %}>COMÚN</option><option value="EMERGENCIA" {% if atencion[15] == 'EMERGENCIA' %}selected{% endif %}>EMERGENCIA</option><option value="HOSPITALIZACIÓN" {% if atencion[15] == 'HOSPITALIZACIÓN' %}selected{% endif %}>HOSPITALIZACIÓN</option></select>
                    </div>
                    <div class="adm-field"><label>PST / HOSP. ORIGEN:</label><input type="text" name="pst_hosp_origen" value="{{ atencion[16] if atencion[16] else '' }}"></div>
                    <div class="adm-field"><label>SERVICIO:</label>
                        <select name="servicio"><option value="">Seleccione</option><option value="PEDIATRÍA" {% if atencion[17] == 'PEDIATRÍA' %}selected{% endif %}>PEDIATRÍA</option><option value="MEDICINA GENERAL" {% if atencion[17] == 'MEDICINA GENERAL' %}selected{% endif %}>MEDICINA GENERAL</option><option value="LABORATORIO" {% if atencion[17] == 'LABORATORIO' %}selected{% endif %}>LABORATORIO</option></select>
                    </div>
                    <div class="adm-field"><label>MÉDICO:</label><input type="text" name="medico" value="{{ atencion[18] if atencion[18] else '' }}"></div>
                    <div class="adm-field"><label>NRO. DE CAMA:</label><input type="text" name="nro_cama" value="{{ atencion[19] if atencion[19] else '' }}"></div>
                </div>
                
                <div class="toolbar">
                    <button type="submit" class="btn btn-success">💾 Graba</button>
                    <button type="reset" class="btn btn-danger">✖ Cancela</button>
                    <a href="{{ url_for('pacientes') }}" class="btn btn-warning">📂 Buscar Paciente (Nuevo)</a>
                    <a href="{{ url_for('laboratorio') }}" class="btn btn-secondary" style="background:#555; color:white; text-decoration:none;">🚪 Salir</a>
                </div>
            </form>
        </div>
    </div>

    <div id="tab_f4" class="tab-content">
        <h3 style="color: #0d2b45; font-size: 18px; margin-top:0;">F4 - EXÁMENES SOLICITADOS</h3>
        <div style="overflow-x: auto;">
            <table>
                <thead><tr><th>CÓDIGO</th><th>EXAMEN</th><th>PRECIO</th></tr></thead>
                <tbody>
                    {% for e in examenes %}
                    <tr>
                        <td>{{ e[0] }}</td>
                        <td>{{ e[1] }}</td>
                        <td>S/ {{ e[4] }}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="3" style="text-align:center;">No hay exámenes agregados.</td></tr>
                    {% endfor %}
                </tbody>
                <tfoot>
                    <tr><td colspan="2" style="text-align: right; font-weight: bold;">TOTAL:</td><td><b>S/ {{ total }}</b></td></tr>
                </tfoot>
            </table>
        </div>

        <div style="background: #f8f9fa; padding: 20px; border-radius: 12px; margin-top: 20px; border: 1px solid #ddd;">
            <h4 style="color: #0d2b45; margin-top:0;">Agregar Examen</h4>
            <form method="POST">
                <input type="hidden" name="accion" value="agregar_examen">
                <label>Seleccionar Examen:</label>
                <select id="select_examen" name="id_examen" required>
                    <option value="">Seleccione un examen...</option>
                    {% for c in catalogo %}
                    <option value="{{ c[0] }}" data-precio="{{ c[2] }}">{{ c[1] }} - S/ {{ c[2] }}</option>
                    {% endfor %}
                </select>
                <label>Precio (S/):</label>
                <input type="number" step="0.01" id="precio_examen" name="precio" value="0.00" required>
                <div style="display: flex; gap: 10px; margin-top: 10px;">
                    <button type="submit" class="btn btn-success">✔ Graba</button>
                    <button type="reset" class="btn btn-danger">✖ Cancela</button>
                </div>
            </form>
        </div>
        
        <div class="toolbar">
            <a href="{{ url_for('nueva_atencion', id_paciente=atencion[1]) }}" class="btn btn-warning">📄 Nuevo</a>
            <form method="POST" style="display:inline;" onsubmit="return confirm('¿Seguro que desea eliminar esta atención y sus exámenes?');">
                <input type="hidden" name="accion" value="eliminar_atencion">
                <button type="submit" class="btn btn-danger">🗑️ Elimina</button>
            </form>
            <a href="{{ url_for('laboratorio') }}" class="btn btn-secondary" style="background:#555; color:white; text-decoration:none;">🚪 Salir</a>
        </div>
    </div>

    <div id="tab_f5" class="tab-content">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        
        <h3 style="color: #0d2b45; font-size: 18px; margin-top:0;">F5 - REGISTRO DE TRIAJE</h3>
        
        {% if session['rol'] in ['enfermera', 'administrador'] %}
            <div style="background: #f8f9fa; padding: 20px; border-radius: 12px; border: 1px solid #ddd;">
                <h4 style="color: #0d2b45; margin-top:0;">Ingreso de Signos Vitales</h4>
                <form method="POST">
                    <input type="hidden" name="accion" value="guardar_triaje">
                    <div class="adm-form-grid">
                        <div class="adm-field"><label>Presión Arterial (Sistólica)</label><input type="number" name="presion_sis" placeholder="120" required></div>
                        <div class="adm-field"><label>Presión Arterial (Diastólica)</label><input type="number" name="presion_dias" placeholder="80" required></div>
                        <div class="adm-field"><label>Frecuencia Cardíaca</label><input type="number" name="fc" placeholder="70" required></div>
                        <div class="adm-field"><label>Frecuencia Respiratoria</label><input type="number" name="fr" placeholder="16" required></div>
                        <div class="adm-field"><label>Temperatura (°C)</label><input type="number" step="0.1" name="temperatura" placeholder="36.5" required></div>
                        <div class="adm-field"><label>Saturación de Oxígeno (%)</label><input type="number" name="saturacion" placeholder="98" required></div>
                        <div class="adm-field"><label>Peso (kg)</label><input type="number" step="0.1" id="peso" name="peso" oninput="calcularIMC()" placeholder="70" required></div>
                        <div class="adm-field"><label>Talla (m)</label><input type="number" step="0.01" id="talla" name="talla" oninput="calcularIMC()" placeholder="1.75" required></div>
                        <div class="adm-field"><label>IMC (Calculado)</label><input type="text" id="imc" name="imc" readonly style="background:#d1d1d1;"></div>
                        <div class="adm-field"><label>Escala de Glasgow</label><input type="number" name="glasgow" placeholder="15" required></div>
                        <div class="adm-field"><label>Dolor (Escala 0-10)</label><input type="number" name="dolor" placeholder="0" required></div>
                    </div>
                    <div class="adm-field"><label>Observaciones</label><textarea name="observaciones" rows="3" placeholder="Notas de enfermería..."></textarea></div>
                    
                    <div class="toolbar">
                        <button type="submit" class="btn btn-success">💾 Grabar Triaje</button>
                        <button type="reset" class="btn btn-danger">✖ Limpiar</button>
                    </div>
                </form>
            </div>
        {% endif %}

        <div style="background: #ffffff; padding: 20px; border-radius: 12px; border: 1px solid #ddd; margin-top: 15px;">
            <h4 style="color: #0d2b45; margin-top:0;">📋 Vista de Enfermería</h4>
            {% if triaje %}
                <div style="display: flex; justify-content: flex-end; font-size: 12px; color: #666; margin-bottom: 10px;">
                    Registrado por: <b>{{ triaje[2] }}</b> a las {{ triaje[1] }}
                </div>
                <div class="triaje-view-grid">
                    <div class="triaje-item"><span class="label">Presión Arterial</span><span class="value">{{ triaje[3] }}/{{ triaje[4] }} mmHg</span></div>
                    <div class="triaje-item"><span class="label">Frecuencia Cardíaca</span><span class="value">{{ triaje[5] }} lpm</span></div>
                    <div class="triaje-item"><span class="label">Frecuencia Respiratoria</span><span class="value">{{ triaje[6] }} rpm</span></div>
                    <div class="triaje-item"><span class="label">Temperatura</span><span class="value">{{ triaje[7] }} °C</span></div>
                    <div class="triaje-item"><span class="label">Sat. O₂</span><span class="value">{{ triaje[8] }} %</span></div>
                    <div class="triaje-item"><span class="label">Peso / Talla</span><span class="value">{{ triaje[9] }} kg / {{ triaje[10] }} m</span></div>
                    <div class="triaje-item"><span class="label">IMC</span><span class="value">{{ triaje[11] }}</span></div>
                    <div class="triaje-item"><span class="label">Glasgow</span><span class="value">{{ triaje[12] }} / 15</span></div>
                    <div class="triaje-item"><span class="label">Dolor (EVA)</span><span class="value">{{ triaje[13] }} / 10</span></div>
                </div>
                <div style="margin-top: 15px; background: #f9f9f9; padding: 10px; border-radius: 8px;">
                    <b>Observaciones:</b> <span style="font-style: italic;">{{ triaje[14] if triaje[14] else 'Sin observaciones.' }}</span>
                </div>
            {% else %}
                <div style="text-align: center; padding: 40px; color: #666;">
                    <span style="font-size: 40px;">🩺</span><br>
                    <b>No se ha registrado el triaje.</b><br>
                    <span style="font-size: 13px;">Espere a que la enfermera registre los signos vitales o realice el ingreso.</span>
                </div>
            {% endif %}
        </div>
    </div>
    """
    html_admision = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_admision)
    return render_template_string(html_admision, atencion=atencion, examenes=examenes, total=total, catalogo=catalogo, triaje=triaje, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

# ==========================================
# RESULTADOS E IMPRESIÓN
# ==========================================
@app.route('/resultados', methods=['GET'])
def resultados():
    if 'usuario' not in session or session['rol'] not in ['administrador', 'laboratorista', 'tecnologo']:
        return redirect(url_for('login'))
    
    dni_busqueda = request.args.get('dni', '').strip()
    conn = get_db_connection(); cursor = conn.cursor()
    
    sql = """
        SELECT o.id, p.nombre, p.apellido, e.descripcion, o.estado, a.id as id_atencion
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        JOIN atenciones a ON o.id_atencion = a.id
        WHERE o.estado = 'Pendiente'
    """
    params = []
    if dni_busqueda:
        sql += " AND p.dni LIKE ?"
        params.append(f'%{dni_busqueda}%')
        
    sql += " ORDER BY o.id DESC"
    
    cursor.execute(sql, params)
    pendientes = cursor.fetchall()
    conn.close()

    contenido_res = """
    <h2 style="color: #0d2b45;">📝 Ingreso de Resultados (Pendientes)</h2>
    
    <div style="background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px;">
        <form method="GET" style="display: flex; flex-wrap: wrap; gap: 10px; align-items: end;">
            <div><label>DNI del Paciente</label><input type="text" name="dni" value="{{ dni_busqueda }}" style="width: 180px; margin:0;"></div>
            <div><button type="submit" class="btn btn-primary" style="padding: 8px 20px;">🔍 Buscar</button></div>
            <div><a href="{{ url_for('resultados') }}" class="btn btn-warning" style="padding: 8px 20px;">Limpiar</a></div>
        </form>
    </div>
    
    <div style="overflow-x: auto;">
        <table>
            <thead><tr><th>ID Orden</th><th>Paciente</th><th>Examen</th><th>Estado</th><th>Acciones</th></tr></thead>
            <tbody>
                {% for p in pendientes %}
                <tr>
                    <td>#{{ p[0] }}</td>
                    <td><b>{{ p[1] }} {{ p[2] }}</b></td>
                    <td>{{ p[3] }}</td>
                    <td><span class="badge badge-amarillo">{{ p[4] }}</span></td>
                    <td>
                        <a href="{{ url_for('ingresar_resultados', id_orden=p[0]) }}" class="btn btn-success" style="padding: 5px 15px;">Ingresar</a>
                        <a href="{{ url_for('descargar_reporte_atencion', id_atencion=p[5]) }}" class="btn btn-primary" style="padding: 5px 15px;">📄 Imprimir</a>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="5" style="text-align:center;">No hay exámenes pendientes para ese DNI.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    html_res = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_res)
    return render_template_string(html_res, pendientes=pendientes, dni_busqueda=dni_busqueda)

@app.route('/resultados/ingresar/<int:id_orden>', methods=['GET', 'POST'])
def ingresar_resultados(id_orden):
    if 'usuario' not in session or session['rol'] not in ['administrador', 'laboratorista', 'tecnologo']:
        return redirect(url_for('login'))
    
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT o.id, p.nombre, p.apellido, e.descripcion, e.id, o.estado
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id = ?
    """, (id_orden,))
    orden = cursor.fetchone()
    if not orden: return "Orden no encontrada", 404

    if request.method == 'POST':
        try:
            for key, val in request.form.items():
                if key.startswith('param_'):
                    id_param = int(key.split('_')[1])
                    resultado_val = val.strip()
                    if resultado_val:
                        cursor.execute("INSERT OR REPLACE INTO resultados_detalles (id_orden_laboratorio, id_parametro, resultado) VALUES (?, ?, ?)", 
                                       (id_orden, id_param, resultado_val))
            cursor.execute("UPDATE ordenes_laboratorio SET estado='Completado' WHERE id=?", (id_orden,))
            conn.commit()
            log_audit(session['usuario'], 'UPDATE', 'ordenes_laboratorio', id_orden, f'Resultados guardados para orden {id_orden}')
            conn.close()
            return redirect(url_for('resultados'))
        except Exception as e:
            conn.close()
            mensaje = f"Error al guardar resultados: {str(e)}"; tipo_mensaje = "alert-danger"
    else:
        mensaje = ""; tipo_mensaje = ""

    cursor.execute("""
        SELECT p.id, p.nombre_parametro, p.unidad, p.valor_normal, r.resultado
        FROM examenes_parametros p
        LEFT JOIN resultados_detalles r ON r.id_parametro = p.id AND r.id_orden_laboratorio = ?
        WHERE p.id_examen_catalogo = ?
        ORDER BY p.orden ASC
    """, (id_orden, orden[4]))
    parametros = cursor.fetchall()
    conn.close()

    contenido_ingreso = """
    <h2 style="color: #0d2b45;">Ingresar Resultados: {{ orden[3] }}</h2>
    <div style="background: #e9ecef; padding: 15px; border-radius: 12px; margin-bottom: 20px;">
        <p><b>👤 Paciente:</b> {{ orden[1] }} {{ orden[2] }}</p>
        <p><b>📄 Orden ID:</b> #{{ orden[0] }}</p>
    </div>
    {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
    
    <form method="POST">
        <table>
            <thead><tr><th>Parámetro</th><th>Unidad</th><th>Valor Normal</th><th>Resultado</th></tr></thead>
            <tbody>
                {% for p in parametros %}
                <tr>
                    <td><b>{{ p[1] }}</b></td>
                    <td>{{ p[2] if p[2] else '-' }}</td>
                    <td><i>{{ p[3] if p[3] else 'No especificado' }}</i></td>
                    <td><input type="text" name="param_{{ p[0] }}" value="{{ p[4] if p[4] else '' }}" placeholder="Ingrese resultado..." style="width:100%; margin:0;"></td>
                </tr>
                {% else %}
                <tr><td colspan="4" style="text-align:center;">Este examen no tiene parámetros configurados. Por favor, edite el catálogo.</td></tr>
                {% endfor %}
            </tbody>
        </table>
        <div style="margin-top: 15px;">
            <button type="submit" class="btn btn-success">Grabar Todos</button>
            <a href="{{ url_for('resultados') }}" class="btn btn-danger">Cancelar</a>
        </div>
    </form>
    """
    html_ingreso = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_ingreso)
    return render_template_string(html_ingreso, orden=orden, parametros=parametros, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/reporte/<int:id_atencion>')
def descargar_reporte_atencion(id_atencion):
    if 'usuario' not in session: return redirect(url_for('login'))
    pdf_buffer = generar_reporte_resultados_pdf(id_atencion)
    if not pdf_buffer: return "No se pudo generar el reporte.", 404
    return send_file(pdf_buffer, as_attachment=True, download_name=f"Reporte_Atencion_{id_atencion}.pdf", mimetype='application/pdf')

# ==========================================
# RUTAS DE LABORATORIO
# ==========================================
@app.route('/laboratorio', methods=['GET'])
def laboratorio():
    if 'usuario' not in session or session['rol'] not in ['administrador', 'laboratorista', 'tecnologo']: return redirect(url_for('login'))
    dni = request.args.get('dni', ''); fecha_desde = request.args.get('fecha_desde', ''); fecha_hasta = request.args.get('fecha_hasta', '')
    atenciones = buscar_atenciones_web(dni, fecha_desde, fecha_hasta)
    
    contenido_lab = """
    <h2 style="color: #0d2b45;">Órdenes de Laboratorio</h2>
    <div style="background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px;">
        <form method="GET" style="display: flex; flex-wrap: wrap; gap: 10px; align-items: end;">
            <div><label>DNI</label><input type="text" name="dni" value="{{ dni }}" style="width: 150px; margin:0;"></div>
            <div><label>Desde</label><input type="date" name="fecha_desde" value="{{ fecha_desde }}" style="width: 150px; margin:0;"></div>
            <div><label>Hasta</label><input type="date" name="fecha_hasta" value="{{ fecha_hasta }}" style="width: 150px; margin:0;"></div>
            <div><button type="submit" class="btn btn-primary">🔍 Buscar</button></div>
            <div><a href="{{ url_for('laboratorio') }}" class="btn btn-warning">Limpiar</a></div>
            <div><button type="button" onclick="abrirModalIngreso()" class="btn btn-success" style="background: #17a2b8;">➕ Ingreso por DNI</button></div>
        </form>
    </div>
    
    <div style="overflow-x: auto;">
        <table>
            <thead><tr><th>ID</th><th>Paciente</th><th>Exámenes</th><th>Fecha</th><th>Progreso</th><th>Estado</th><th>Acciones</th></tr></thead>
            <tbody>
                {% for a in atenciones %}
                {% set completados = a[5] %}
                {% set total = a[6] %}
                {% set clase_fila = '' %}{% set clase_badge = '' %}{% set texto_estado = '' %}
                {% if completados == total and total > 0 %}
                    {% set clase_fila = 'estado-verde' %}{% set clase_badge = 'badge-verde' %}{% set texto_estado = 'Completado' %}
                {% elif completados > 0 %}
                    {% set clase_fila = 'estado-rojo' %}{% set clase_badge = 'badge-rojo' %}{% set texto_estado = 'Incompleto' %}
                {% else %}
                    {% set clase_fila = 'estado-amarillo' %}{% set clase_badge = 'badge-amarillo' %}{% set texto_estado = 'Pendiente' %}
                {% endif %}
                <tr class="{{ clase_fila }}"><td><b>#{{ a[0] }}</b></td><td><b>{{ a[1] }} {{ a[2] }}</b></td><td>{{ a[3] }}</td><td>{{ a[4] }}</td><td>{{ completados }}/{{ total }}</td><td><span class="badge {{ clase_badge }}">{{ texto_estado }}</span></td><td><a href="{{ url_for('gestion_admision', id_atencion=a[0]) }}" class="btn btn-success" style="padding: 6px 16px; font-size: 14px;">📝 Ingresar</a></td></tr>
                {% else %}
                <tr><td colspan="7" style="text-align:center; padding: 30px;">🔬 No se encontraron atenciones registradas.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="modal-overlay" id="modalIngreso">
        <div class="modal-box">
            <span class="close-modal" onclick="cerrarModalIngreso()">&times;</span>
            <h3>➕ Ingreso de Paciente por DNI</h3>
            <form method="POST" action="{{ url_for('ingreso_rapido_paciente') }}">
                <div class="adm-field">
                    <label>DNI del Paciente *</label>
                    <input type="text" name="dni" placeholder="Ej: 12345678" required>
                </div>
                <div style="margin: 10px 0; text-align: center; color: #666;">Si el paciente no existe, ingrese Nombre y Apellido para registrarlo.</div>
                <div class="adm-form-grid">
                    <div class="adm-field">
                        <label>Nombre</label>
                        <input type="text" name="nombre" placeholder="Nombre (Nuevo)">
                    </div>
                    <div class="adm-field">
                        <label>Apellido</label>
                        <input type="text" name="apellido" placeholder="Apellido (Nuevo)">
                    </div>
                </div>
                <div class="toolbar" style="justify-content: center; border-top: none; padding-top: 10px;">
                    <button type="submit" class="btn btn-success">🔍 Buscar / Crear Atención</button>
                    <button type="button" class="btn btn-danger" onclick="cerrarModalIngreso()">Cancelar</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        function abrirModalIngreso() {
            document.getElementById('modalIngreso').style.display = 'flex';
        }
        function cerrarModalIngreso() {
            document.getElementById('modalIngreso').style.display = 'none';
        }
        document.getElementById('modalIngreso').addEventListener('click', function(e) {
            if (e.target === this) {
                cerrarModalIngreso();
            }
        });
    </script>
    """
    html_lab = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_lab)
    return render_template_string(html_lab, atenciones=atenciones, dni=dni, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)

@app.route('/laboratorio/ingreso_rapido', methods=['POST'])
def ingreso_rapido_paciente():
    if 'usuario' not in session: return redirect(url_for('login'))
    
    dni = request.form.get('dni', '').strip()
    nombre = request.form.get('nombre', '').strip()
    apellido = request.form.get('apellido', '').strip()
    
    if not dni:
        return redirect(url_for('laboratorio'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, apellido FROM pacientes WHERE dni = ?", (dni,))
    res = cursor.fetchone()
    
    if res:
        id_paciente = res[0]
        conn.close()
        return redirect(url_for('nueva_atencion', id_paciente=id_paciente))
    else:
        if not nombre or not apellido:
            conn.close()
            return redirect(url_for('pacientes'))
        cursor.execute("INSERT INTO pacientes (dni, nombre, apellido) VALUES (?, ?, ?)", (dni, nombre, apellido))
        id_paciente = cursor.lastrowid
        conn.commit()
        log_audit(session['usuario'], 'INSERT', 'pacientes', id_paciente, f'Paciente registrado vía ingreso rápido: {nombre} {apellido}')
        conn.close()
        return redirect(url_for('nueva_atencion', id_paciente=id_paciente))

# ==========================================
# CAMBIO DE CONTRASEÑA
# ==========================================
@app.route('/cambiar_contrasena', methods=['GET', 'POST'])
def cambiar_contrasena():
    if 'usuario' not in session:
        return redirect(url_for('login'))
    
    mensaje = ""
    tipo_mensaje = ""
    usuario_actual = session['usuario']
    
    if request.method == 'POST':
        pass_anterior = request.form.get('pass_anterior', '')
        pass_nuevo = request.form.get('pass_nuevo', '')
        pass_confirm = request.form.get('pass_confirm', '')
        
        if not pass_anterior or not pass_nuevo or not pass_confirm:
            mensaje = "Todos los campos son obligatorios."
            tipo_mensaje = "alert-danger"
        elif pass_nuevo != pass_confirm:
            mensaje = "La nueva contraseña y la confirmación no coinciden."
            tipo_mensaje = "alert-danger"
        elif pass_nuevo == pass_anterior:
            mensaje = "La nueva contraseña no puede ser igual a la anterior."
            tipo_mensaje = "alert-danger"
        else:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM usuarios WHERE usuario=? AND password=?", (usuario_actual, pass_anterior))
                user = cursor.fetchone()
                if not user:
                    mensaje = "La contraseña anterior es incorrecta."
                    tipo_mensaje = "alert-danger"
                else:
                    cursor.execute("UPDATE usuarios SET password=? WHERE usuario=?", (pass_nuevo, usuario_actual))
                    conn.commit()
                    log_audit(session['usuario'], 'UPDATE', 'usuarios', user[0], 'Contraseña actualizada')
                    mensaje = "¡Contraseña actualizada exitosamente! Por favor, vuelva a iniciar sesión."
                    tipo_mensaje = "alert-success"
                    conn.close()
                    session.clear()
                    return redirect(url_for('login'))
                conn.close()
            except Exception as e:
                mensaje = f"Error al cambiar la contraseña: {str(e)}"
                tipo_mensaje = "alert-danger"

    contenido_contraseña = """
    <div class="pass-dialog">
        <h3>🔐 Cambio de Contraseña</h3>
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <div class="adm-field">
                <label>CONTRASEÑA ANTERIOR</label>
                <input type="password" name="pass_anterior" placeholder="Ingrese su contraseña actual" required>
            </div>
            <div class="adm-field">
                <label>CONTRASEÑA NUEVA</label>
                <input type="password" name="pass_nuevo" placeholder="Ingrese la nueva contraseña" required>
            </div>
            <div class="adm-field">
                <label>CONFIRMAR CONTRASEÑA NUEVA:</label>
                <input type="password" name="pass_confirm" placeholder="Vuelva a escribir la nueva contraseña" required>
            </div>
            <div class="toolbar" style="justify-content: center; border-top: none; padding-top: 10px;">
                <button type="submit" class="btn btn-success">✔ Aceptar</button>
                <a href="{{ url_for('dashboard') }}" class="btn btn-danger">✖ Cancelar</a>
            </div>
        </form>
    </div>
    """
    html_pwd = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_contraseña)
    return render_template_string(html_pwd, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

# ==========================================
# INVENTARIO Y CATÁLOGO
# ==========================================
def obtener_reactivos():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, cantidad, unidad, fecha_caducidad, proveedor FROM reactivos ORDER BY id DESC")
    datos = cursor.fetchall(); conn.close(); return datos

def agregar_reactivo(nombre, cantidad, unidad, caducidad, proveedor):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO reactivos (nombre, cantidad, unidad, fecha_caducidad, proveedor) VALUES (?, ?, ?, ?, ?)", (nombre, cantidad, unidad, caducidad, proveedor))
        conn.commit(); conn.close(); return True, "Reactivo agregado exitosamente."
    except Exception as e: return False, str(e)

def consumir_reactivo(id_reactivo, cantidad_usada):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT cantidad FROM reactivos WHERE id=?", (id_reactivo,))
        stock_actual = cursor.fetchone()[0]
        if stock_actual < cantidad_usada: return False, f"Stock insuficiente. Solo hay {stock_actual}."
        nuevo_stock = stock_actual - cantidad_usada
        cursor.execute("UPDATE reactivos SET cantidad=? WHERE id=?", (nuevo_stock, id_reactivo))
        conn.commit(); conn.close(); return True, f"Consumo registrado. Nuevo stock: {nuevo_stock}"
    except Exception as e: return False, str(e)

@app.route('/inventario', methods=['GET'])
def inventario():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    reactivos = obtener_reactivos()
    contenido_inv = """<h2 style="color: #0d2b45;">📦 Inventario de Reactivos</h2><div style="display: flex; gap: 10px; margin-bottom: 15px;"><a href="{{ url_for('agregar_reactivo') }}" class="btn btn-success">+ Agregar Reactivo</a></div><div style="overflow-x: auto;"><table><thead><tr><th>ID</th><th>Nombre</th><th>Cantidad</th><th>Unidad</th><th>Caducidad</th><th>Proveedor</th><th>Acciones</th></tr></thead><tbody>{% for r in reactivos %}<tr><td>{{ r[0] }}</td><td>{{ r[1] }}</td><td><b>{{ r[2] }}</b></td><td>{{ r[3] }}</td><td>{{ r[4] }}</td><td>{{ r[5] }}</td><td><a href="{{ url_for('consumir_reactivo', id=r[0]) }}" class="btn btn-warning" style="padding: 4px 10px; font-size: 12px;">- Consumir</a></td></tr>{% else %}<tr><td colspan="7" style="text-align:center;">No hay reactivos registrados.</td></tr>{% endfor %}</tbody></table></div>"""
    html_inv = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_inv)
    return render_template_string(html_inv, reactivos=reactivos)

@app.route('/inventario/agregar', methods=['GET', 'POST'])
def agregar_reactivo():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    if request.method == 'POST':
        nombre = request.form['nombre']; cantidad = float(request.form['cantidad']); unidad = request.form['unidad']
        caducidad = request.form['fecha_caducidad']; proveedor = request.form['proveedor']
        exito, msg = agregar_reactivo(nombre, cantidad, unidad, caducidad, proveedor)
        if exito: return redirect(url_for('inventario'))
        else: mensaje = msg; tipo_mensaje = "alert-danger"
    contenido_agregar = """<h2 style="color: #0d2b45;">Agregar Nuevo Reactivo</h2><div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">{% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}<form method="POST"><label>Nombre del Reactivo</label><input type="text" name="nombre" required><label>Cantidad (Número)</label><input type="number" step="0.01" name="cantidad" required><label>Unidad (ml, gr, unid)</label><input type="text" name="unidad" required><label>Fecha de Caducidad (AAAA-MM-DD)</label><input type="date" name="fecha_caducidad"><label>Proveedor</label><input type="text" name="proveedor"><button type="submit" class="btn btn-primary">Guardar Reactivo</button> <a href="{{ url_for('inventario') }}" class="btn btn-danger">Cancelar</a></form></div>"""
    html_agregar = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_agregar)
    return render_template_string(html_agregar, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/inventario/consumir/<int:id>', methods=['GET', 'POST'])
def consumir_reactivo(id):
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, cantidad FROM reactivos WHERE id=?", (id,)); reactivo = cursor.fetchone(); conn.close()
    if not reactivo: return "Reactivo no encontrado", 404
    if request.method == 'POST':
        cantidad_usar = float(request.form['cantidad_usar'])
        exito, msg = consumir_reactivo(id, cantidad_usar)
        if exito: return redirect(url_for('inventario'))
        else: mensaje = msg; tipo_mensaje = "alert-danger"
    contenido_consumir = """<h2 style="color: #0d2b45;">Consumir Reactivo</h2><div style="background: #f8f9fa; padding: 20px; border-radius: 12px;"><p><b>Reactivo:</b> {{ reactivo[1] }}</p><p><b>Stock actual:</b> {{ reactivo[2] }}</p>{% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}<form method="POST"><label>Cantidad a consumir</label><input type="number" step="0.01" name="cantidad_usar" required><button type="submit" class="btn btn-warning">Consumir Stock</button> <a href="{{ url_for('inventario') }}" class="btn btn-danger">Cancelar</a></form></div>"""
    html_consumir = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_consumir)
    return render_template_string(html_consumir, reactivo=reactivo, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

def obtener_examenes_catalogo():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT e.id, e.codigo, e.descripcion, s.nombre_seccion, e.precio, e.activo FROM examenes_catalogo e LEFT JOIN examenes_secciones s ON e.id_seccion = s.id ORDER BY e.id ASC")
    datos = cursor.fetchall(); conn.close(); return datos

def obtener_parametros_por_examen(id_examen):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre_parametro, unidad, valor_normal, orden FROM examenes_parametros WHERE id_examen_catalogo = ? ORDER BY orden ASC", (id_examen,))
    datos = cursor.fetchall(); conn.close(); return datos

@app.route('/catalogo_examenes', methods=['GET'])
def catalogo_examenes():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    examenes = obtener_examenes_catalogo()
    contenido_cat = """<h2 style="color: #0d2b45;">📋 Catálogo de Exámenes</h2><div style="display: flex; gap: 10px; margin-bottom: 15px;"><a href="{{ url_for('nuevo_examen_catalogo') }}" class="btn btn-success">+ Nuevo Examen</a></div><table><thead><tr><th>Código</th><th>Descripción</th><th>Sección</th><th>Precio</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{% for e in examenes %}<tr><td>{{ e[1] }}</td><td>{{ e[2] }}</td><td>{{ e[3] }}</td><td>S/ {{ e[4] }}</td><td>{% if e[5] == 1 %}Activo{% else %}Inactivo{% endif %}</td><td><a href="{{ url_for('editar_examen_catalogo', id=e[0]) }}" class="btn btn-warning" style="padding: 4px 10px; font-size: 12px;">✏️ Editar</a></td></tr>{% else %}<tr><td colspan="6" style="text-align:center;">No hay exámenes.</td></tr>{% endfor %}</tbody></table>"""
    html_cat = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_cat)
    return render_template_string(html_cat, examenes=examenes)

@app.route('/catalogo_examenes/nuevo', methods=['GET', 'POST'])
def nuevo_examen_catalogo():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    secciones = obtener_secciones()
    mensaje = ""; tipo_mensaje = ""
    
    if request.method == 'POST':
        codigo = request.form['codigo']; descripcion = request.form['descripcion']
        id_seccion = int(request.form['id_seccion']) if request.form['id_seccion'] else None
        precio = float(request.form['precio']) if request.form['precio'] else 0.0
        abreviatura = request.form['abreviatura']; interviene = request.form['interviene_reporte']
        epidemiologico = request.form['epidemiologico']; valor_normal = request.form['valor_normal']
        
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO examenes_catalogo (codigo, descripcion, id_seccion, precio, abreviatura, interviene_reporte, epidemiologico, valor_normal) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (codigo, descripcion, id_seccion, precio, abreviatura, interviene, epidemiologico, valor_normal))
            id_examen = cursor.lastrowid
            
            nombres_param = request.form.getlist('param_nombre[]')
            unidades_param = request.form.getlist('param_unidad[]')
            normales_param = request.form.getlist('param_normal[]')
            
            for i in range(len(nombres_param)):
                if nombres_param[i].strip():
                    cursor.execute("""
                        INSERT INTO examenes_parametros (id_examen_catalogo, nombre_parametro, unidad, valor_normal, orden) 
                        VALUES (?, ?, ?, ?, ?)
                    """, (id_examen, nombres_param[i].strip(), unidades_param[i].strip(), normales_param[i].strip(), i+1))
            
            conn.commit()
            log_audit(session['usuario'], 'INSERT', 'examenes_catalogo', id_examen, f'Nuevo examen creado: {descripcion}')
            conn.close()
            return redirect(url_for('catalogo_examenes'))
        except Exception as e:
            mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"

    contenido_form = """
    <h2 style="color: #0d2b45;">Nuevo Examen de Laboratorio</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <div class="adm-form-grid">
                <div class="adm-field"><label>Código</label><input type="text" name="codigo"></div>
                <div class="adm-field"><label>Descripción</label><input type="text" name="descripcion" required></div>
                <div class="adm-field"><label>Sección</label>
                    <select name="id_seccion"><option value="">Seleccione</option>
                    {% for s in secciones %}<option value="{{ s[0] }}">{{ s[1] }}</option>{% endfor %}
                    </select>
                </div>
                <div class="adm-field"><label>Precio (S/.)</label><input type="number" step="0.01" name="precio"></div>
                <div class="adm-field"><label>Abreviatura</label><input type="text" name="abreviatura"></div>
                <div class="adm-field"><label>Interviene en Reporte</label>
                    <select name="interviene_reporte"><option value="SI">SI</option><option value="NO">NO</option></select>
                </div>
                <div class="adm-field"><label>Epidemiológico</label>
                    <select name="epidemiologico"><option value="NO">NO</option><option value="SI">SI</option></select>
                </div>
            </div>
            <div class="adm-field"><label>Valor Normal General</label><textarea name="valor_normal" rows="3"></textarea></div>
            
            <h4 style="color: #0d2b45; border-top: 1px solid #ddd; padding-top: 15px;">Parámetros / Sub-análisis</h4>
            <div id="params_container">
                <div class="adm-form-grid">
                    <div class="adm-field"><label>Nombre Parámetro</label><input type="text" name="param_nombre[]" required></div>
                    <div class="adm-field"><label>Unidad</label><input type="text" name="param_unidad[]"></div>
                    <div class="adm-field"><label>Valor Normal</label><input type="text" name="param_normal[]"></div>
                </div>
            </div>
            <button type="button" onclick="agregarParam()" class="btn btn-warning" style="margin-bottom: 15px;">+ Agregar Parámetro</button>
            
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Graba</button>
                <button type="reset" class="btn btn-danger">Cancela</button>
                <a href="{{ url_for('catalogo_examenes') }}" class="btn btn-secondary" style="background:#555; color:white;">Salir</a>
            </div>
        </form>
    </div>
    <script>
        function agregarParam() {
            var container = document.getElementById("params_container");
            var newDiv = document.createElement("div");
            newDiv.className = "adm-form-grid";
            newDiv.style.marginTop = "10px";
            newDiv.innerHTML = `
                <div class="adm-field"><label>Nombre Parámetro</label><input type="text" name="param_nombre[]" required></div>
                <div class="adm-field"><label>Unidad</label><input type="text" name="param_unidad[]"></div>
                <div class="adm-field"><label>Valor Normal</label><input type="text" name="param_normal[]"></div>
            `;
            container.appendChild(newDiv);
        }
    </script>
    """
    html_form = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_form)
    return render_template_string(html_form, secciones=secciones, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/catalogo_examenes/editar/<int:id>', methods=['GET', 'POST'])
def editar_examen_catalogo(id):
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    secciones = obtener_secciones()
    conn = get_db_connection(); cursor = conn.cursor()
    
    if request.method == 'POST':
        codigo = request.form['codigo']; descripcion = request.form['descripcion']
        id_seccion = int(request.form['id_seccion']) if request.form['id_seccion'] else None
        precio = float(request.form['precio']) if request.form['precio'] else 0.0
        abreviatura = request.form['abreviatura']; interviene = request.form['interviene_reporte']
        epidemiologico = request.form['epidemiologico']; valor_normal = request.form['valor_normal']
        activo = 1 if request.form.get('activo') else 0
        
        try:
            cursor.execute("""
                UPDATE examenes_catalogo SET codigo=?, descripcion=?, id_seccion=?, precio=?, abreviatura=?, interviene_reporte=?, epidemiologico=?, valor_normal=?, activo=?
                WHERE id=?
            """, (codigo, descripcion, id_seccion, precio, abreviatura, interviene, epidemiologico, valor_normal, activo, id))
            conn.commit(); conn.close()
            return redirect(url_for('catalogo_examenes'))
        except Exception as e:
            mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
            conn.rollback()
        finally:
            conn.close()

    else:
        cursor.execute("SELECT * FROM examenes_catalogo WHERE id=?", (id,))
        examen = cursor.fetchone()
        if not examen: return "Examen no encontrado", 404
        conn.close()
        mensaje = ""; tipo_mensaje = ""

    contenido_editar = """
    <h2 style="color: #0d2b45;">Editar Examen de Laboratorio</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <div class="adm-form-grid">
                <div class="adm-field"><label>Código</label><input type="text" name="codigo" value="{{ examen[1] }}"></div>
                <div class="adm-field"><label>Descripción</label><input type="text" name="descripcion" value="{{ examen[2] }}" required></div>
                <div class="adm-field"><label>Sección</label>
                    <select name="id_seccion"><option value="">Seleccione</option>
                    {% for s in secciones %}<option value="{{ s[0] }}" {% if s[0] == examen[3] %}selected{% endif %}>{{ s[1] }}</option>{% endfor %}
                    </select>
                </div>
                <div class="adm-field"><label>Precio (S/.)</label><input type="number" step="0.01" name="precio" value="{{ examen[4] }}"></div>
                <div class="adm-field"><label>Abreviatura</label><input type="text" name="abreviatura" value="{{ examen[5] }}"></div>
                <div class="adm-field"><label>Interviene en Reporte</label>
                    <select name="interviene_reporte"><option value="SI" {% if examen[6] == 'SI' %}selected{% endif %}>SI</option><option value="NO" {% if examen[6] == 'NO' %}selected{% endif %}>NO</option></select>
                </div>
                <div class="adm-field"><label>Epidemiológico</label>
                    <select name="epidemiologico"><option value="NO" {% if examen[7] == 'NO' %}selected{% endif %}>NO</option><option value="SI" {% if examen[7] == 'SI' %}selected{% endif %}>SI</option></select>
                </div>
                <div class="adm-field"><label>Activo</label>
                    <select name="activo"><option value="1" {% if examen[9] == 1 %}selected{% endif %}>Activo</option><option value="0" {% if examen[9] == 0 %}selected{% endif %}>Inactivo</option></select>
                </div>
            </div>
            <div class="adm-field"><label>Valor Normal General</label><textarea name="valor_normal" rows="3">{{ examen[8] }}</textarea></div>
            
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Graba</button>
                <button type="reset" class="btn btn-danger">Cancela</button>
                <a href="{{ url_for('catalogo_examenes') }}" class="btn btn-secondary" style="background:#555; color:white;">Salir</a>
            </div>
        </form>
    </div>
    """
    html_edit = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_editar)
    return render_template_string(html_edit, examen=examen, secciones=secciones, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

def obtener_secciones():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre_seccion FROM examenes_secciones ORDER BY id ASC")
    datos = cursor.fetchall(); conn.close(); return datos

# ==========================================
# EJECUCIÓN
# ==========================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
