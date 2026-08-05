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
import barcode
from barcode.writer import ImageWriter
from PIL import Image
from functools import wraps

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['UPLOAD_FOLDER'] = 'static'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

# Asegurar que la carpeta static existe
if not os.path.exists('static'):
    os.makedirs('static')

# ========================== DECORADOR DE AUTENTICACIÓN ==========================
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

# Tamaños personalizados
TICKET_80MM = (80 * 28.35, 297 * 28.35)
TICKET_80MM_LANDSCAPE = (297 * 28.35, 80 * 28.35)

# ========================== FUNCIÓN UNIVERSAL PARA CONSULTAS ==========================
def ejecutar_consulta(cursor, query, params=None):
    """Ejecuta una consulta SQL adaptando los placeholders según el motor."""
    if IS_POSTGRES:
        query = query.replace('?', '%s')
    if params is None:
        cursor.execute(query)
    else:
        cursor.execute(query, params)

def get_db_connection():
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect('sisgaleno2026.db')

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if IS_POSTGRES:
        auto_inc = "SERIAL PRIMARY KEY"
        text = "TEXT"
    else:
        auto_inc = "INTEGER PRIMARY KEY AUTOINCREMENT"
        text = "TEXT"

    # ---------- CREAR TABLAS ----------
    # Usuarios
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS usuarios (
        id {auto_inc},
        usuario {text} UNIQUE NOT NULL,
        password_hash {text} NOT NULL,
        rol {text} NOT NULL
    )''')
    for user, pwd, rol in [('admin','admin','administrador'),('doctor','doctor','medico'),('lab','lab','laboratorista'),('nurse','nurse','enfermera'),('tecnologo','tecnologo','tecnologo')]:
        h = generate_password_hash(pwd)
        if IS_POSTGRES:
            ejecutar_consulta(cursor, "INSERT INTO usuarios (usuario, password_hash, rol) VALUES (%s,%s,%s) ON CONFLICT (usuario) DO NOTHING", (user,h,rol))
        else:
            ejecutar_consulta(cursor, "INSERT OR IGNORE INTO usuarios (usuario, password_hash, rol) VALUES (?,?,?)", (user,h,rol))

    # Pacientes
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pacientes (
        id {auto_inc},
        historia_clinica {text} UNIQUE,
        dni {text} UNIQUE NOT NULL,
        nombre {text} NOT NULL,
        apellido {text} NOT NULL,
        fecha_nacimiento {text},
        telefono {text},
        celular {text},
        direccion {text},
        sexo {text} DEFAULT '',
        edad INTEGER DEFAULT 0,
        convivientes {text},
        acompanante_nombre {text},
        acompanante_telefono {text},
        acompanante_parentesco {text},
        nro_afiliacion {text},
        deleted INTEGER DEFAULT 0
    )''')
    if not IS_POSTGRES:
        ejecutar_consulta(cursor, "PRAGMA table_info(pacientes)")
        cols = [row[1] for row in cursor.fetchall()]
        for col in ['nro_afiliacion','deleted']:
            if col not in cols:
                if col == 'deleted':
                    cursor.execute("ALTER TABLE pacientes ADD COLUMN deleted INTEGER DEFAULT 0")
                else:
                    cursor.execute("ALTER TABLE pacientes ADD COLUMN nro_afiliacion TEXT")
    else:
        cursor.execute("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS nro_afiliacion TEXT")
        cursor.execute("ALTER TABLE pacientes ADD COLUMN IF NOT EXISTS deleted INTEGER DEFAULT 0")

    # Servicios
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS servicios (
        id {auto_inc},
        nombre {text} NOT NULL,
        precio_base REAL DEFAULT 0
    )''')
    servicios_data = [('MEDICINA GENERAL',50),('MEDICINA INTERNA',60),('MEDICINA FISICA',40),('PEDIATRIA',35),('GINECOLOGIA',70),('TRAUMATOLOGIA',65),('CIRUGIA',100),('OTROS',50)]
    for nombre, precio in servicios_data:
        if IS_POSTGRES:
            ejecutar_consulta(cursor, "SELECT 1 FROM servicios WHERE nombre = %s", (nombre,))
            if not cursor.fetchone():
                ejecutar_consulta(cursor, "INSERT INTO servicios (nombre, precio_base) VALUES (%s,%s)", (nombre, precio))
        else:
            ejecutar_consulta(cursor, "INSERT OR IGNORE INTO servicios (nombre, precio_base) VALUES (?,?)", (nombre, precio))

    # Citas
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS citas (
        id {auto_inc},
        id_paciente INTEGER REFERENCES pacientes(id),
        id_servicio INTEGER REFERENCES servicios(id),
        id_medico INTEGER,
        fecha_cita {text},
        estado {text},
        motivo_consulta {text},
        tipo_asegurado {text},
        numero_boleta {text}
    )''')

    # Pagos
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pagos (
        id {auto_inc},
        id_cita INTEGER REFERENCES citas(id),
        id_paciente INTEGER REFERENCES pacientes(id),
        numero_boleta {text} UNIQUE NOT NULL,
        descripcion {text},
        monto REAL,
        fecha_pago {text},
        estado {text}
    )''')

    # Exámenes catálogo
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_catalogo (
        id {auto_inc},
        codigo {text},
        descripcion {text} NOT NULL,
        precio REAL DEFAULT 0
    )''')
    examenes_data = [(1,'145','HEMOGRAMA COMPLETO',50),(2,'G002','GLUCOSA EN AYUNAS',20)]
    for id_ex, cod, desc, precio in examenes_data:
        if IS_POSTGRES:
            ejecutar_consulta(cursor, "INSERT INTO examenes_catalogo (id,codigo,descripcion,precio) VALUES (%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING", (id_ex, cod, desc, precio))
        else:
            ejecutar_consulta(cursor, "INSERT OR IGNORE INTO examenes_catalogo (id,codigo,descripcion,precio) VALUES (?,?,?,?)", (id_ex, cod, desc, precio))

    # ===== NUEVO: SECCIONES DE PARÁMETROS =====
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS secciones_parametros (
        id {auto_inc},
        nombre {text} UNIQUE NOT NULL,
        orden INTEGER DEFAULT 0
    )''')
    secciones_defecto = ['HEMATOLOGIA', 'BIOQUIMICA', 'INMUNOLOGIA', 'UROANALISIS', 'PARASITOLOGIA', 'MICROBIOLOGIA']
    for idx, nombre in enumerate(secciones_defecto):
        if IS_POSTGRES:
            cursor.execute("INSERT INTO secciones_parametros (nombre, orden) VALUES (%s,%s) ON CONFLICT (nombre) DO NOTHING", (nombre, idx))
        else:
            cursor.execute("INSERT OR IGNORE INTO secciones_parametros (nombre, orden) VALUES (?,?)", (nombre, idx))

    # Parámetros
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_parametros (
        id {auto_inc},
        id_examen_catalogo INTEGER REFERENCES examenes_catalogo(id),
        nombre_parametro {text} NOT NULL,
        unidad {text},
        rango_referencia {text},
        orden INTEGER DEFAULT 0,
        id_seccion INTEGER REFERENCES secciones_parametros(id)
    )''')
    if not IS_POSTGRES:
        cursor.execute("PRAGMA table_info(examenes_parametros)")
        cols = [row[1] for row in cursor.fetchall()]
        if 'id_seccion' not in cols:
            cursor.execute("ALTER TABLE examenes_parametros ADD COLUMN id_seccion INTEGER")
    else:
        cursor.execute("ALTER TABLE examenes_parametros ADD COLUMN IF NOT EXISTS id_seccion INTEGER REFERENCES secciones_parametros(id)")

    # ===== PARÁMETROS EXTRA POR ORDEN =====
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS parametros_extra_orden (
        id {auto_inc},
        id_orden INTEGER REFERENCES ordenes_laboratorio(id) ON DELETE CASCADE,
        nombre_analisis {text} NOT NULL,
        resultado {text},
        rango_referencia {text},
        id_seccion INTEGER REFERENCES secciones_parametros(id)
    )''')

    # Órdenes laboratorio
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS ordenes_laboratorio (
        id {auto_inc},
        id_paciente INTEGER REFERENCES pacientes(id),
        id_examen INTEGER REFERENCES examenes_catalogo(id),
        id_cita INTEGER REFERENCES citas(id),
        fecha_emision {text},
        estado {text},
        precio REAL,
        id_pago INTEGER REFERENCES pagos(id),
        codigo_muestra {text},
        fecha_validez DATE,
        examen_manual {text},
        servicio_manual {text},
        tipo_orden {text} DEFAULT 'examen',
        tecnologo_id INTEGER REFERENCES usuarios(id),
        fecha_resultado {text},
        validado INTEGER DEFAULT 0
    )''')
    if not IS_POSTGRES:
        cursor.execute("PRAGMA table_info(ordenes_laboratorio)")
        cols = [row[1] for row in cursor.fetchall()]
        for col in ['codigo_muestra','fecha_validez','examen_manual','servicio_manual','tipo_orden','tecnologo_id','fecha_resultado','validado']:
            if col not in cols:
                if col == 'validado':
                    cursor.execute("ALTER TABLE ordenes_laboratorio ADD COLUMN validado INTEGER DEFAULT 0")
                else:
                    cursor.execute(f"ALTER TABLE ordenes_laboratorio ADD COLUMN {col} TEXT")
    else:
        for col in ['codigo_muestra','fecha_validez','examen_manual','servicio_manual','tipo_orden','tecnologo_id','fecha_resultado']:
            cursor.execute(f"ALTER TABLE ordenes_laboratorio ADD COLUMN IF NOT EXISTS {col} TEXT")
        cursor.execute("ALTER TABLE ordenes_laboratorio ADD COLUMN IF NOT EXISTS validado INTEGER DEFAULT 0")

    # Resultados lab
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS resultados_lab (
        id {auto_inc},
        id_orden INTEGER REFERENCES ordenes_laboratorio(id),
        id_parametro INTEGER REFERENCES examenes_parametros(id),
        resultado {text}
    )''')

    # Imágenes
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS imagenes_laboratorio (
        id {auto_inc},
        id_orden INTEGER REFERENCES ordenes_laboratorio(id),
        nombre_archivo {text},
        ruta_archivo {text}
    )''')

    # Diagnósticos
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS diagnosticos (
        id {auto_inc},
        id_cita INTEGER REFERENCES citas(id),
        id_medico INTEGER,
        diagnostico {text},
        tratamiento {text},
        descanso_medico_dias INTEGER,
        informe_pdf_path {text}
    )''')

    # Configuración sistema
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS configuracion_sistema (
        id INTEGER PRIMARY KEY DEFAULT 1,
        nombre_sistema {text} DEFAULT 'SISGALENO2026',
        tamano_hoja {text} DEFAULT 'A4',
        logo_path {text} DEFAULT '',
        encabezado_texto {text} DEFAULT 'Laboratorio Clínico',
        pie_pagina_texto {text} DEFAULT 'Generado automáticamente por el sistema.',
        report_header {text} DEFAULT 'INFORME DE ATENCIÓN CLÍNICA',
        report_footer {text} DEFAULT 'Documento generado por SISGALENO2026',
        sello_path {text} DEFAULT '',
        ticket_size {text} DEFAULT 'TICKET_80MM',
        report_size {text} DEFAULT 'A4',
        result_size {text} DEFAULT 'A4'
    )''')
    if not IS_POSTGRES:
        cursor.execute("PRAGMA table_info(configuracion_sistema)")
        cols = [row[1] for row in cursor.fetchall()]
        for col in ['sello_path','ticket_size','report_size','result_size']:
            if col not in cols:
                cursor.execute(f"ALTER TABLE configuracion_sistema ADD COLUMN {col} TEXT DEFAULT ''")
    else:
        for col in ['sello_path','ticket_size','report_size','result_size']:
            cursor.execute(f"ALTER TABLE configuracion_sistema ADD COLUMN IF NOT EXISTS {col} TEXT DEFAULT ''")
    if IS_POSTGRES:
        ejecutar_consulta(cursor, "INSERT INTO configuracion_sistema (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    else:
        ejecutar_consulta(cursor, "INSERT OR IGNORE INTO configuracion_sistema (id) VALUES (1)")

    # Módulos
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS config_modulos (
        id {auto_inc},
        nombre {text} UNIQUE NOT NULL,
        activo INTEGER DEFAULT 1,
        descripcion {text}
    )''')
    modulos_data = [('Admisión','Gestión de pacientes y citas'),('Caja','Cobros y emisión de boletas'),('Laboratorio','Procesamiento de muestras y resultados'),('Atención Médica','Evaluación médica e informes clínicos'),('Configuración','Panel de control del sistema')]
    for nombre, desc in modulos_data:
        if IS_POSTGRES:
            ejecutar_consulta(cursor, "INSERT INTO config_modulos (nombre, descripcion) VALUES (%s,%s) ON CONFLICT (nombre) DO NOTHING", (nombre, desc))
        else:
            ejecutar_consulta(cursor, "INSERT OR IGNORE INTO config_modulos (nombre, descripcion) VALUES (?,?)", (nombre, desc))

    # Médicos
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS medicos (
        id {auto_inc},
        nombre {text} NOT NULL,
        apellido {text} NOT NULL,
        especialidad {text},
        horario {text},
        telefono {text},
        email {text},
        numero_licencia {text},
        activo INTEGER DEFAULT 1
    )''')
    if not IS_POSTGRES:
        cursor.execute("PRAGMA table_info(medicos)")
        cols = [row[1] for row in cursor.fetchall()]
        for col in ['nombre','apellido','especialidad','horario','telefono','email','numero_licencia','activo']:
            if col not in cols:
                if col == 'activo':
                    cursor.execute("ALTER TABLE medicos ADD COLUMN activo INTEGER DEFAULT 1")
                else:
                    cursor.execute(f"ALTER TABLE medicos ADD COLUMN {col} TEXT")
    else:
        for col in ['nombre','apellido','especialidad','horario','telefono','email','numero_licencia']:
            cursor.execute(f"ALTER TABLE medicos ADD COLUMN IF NOT EXISTS {col} TEXT")
        cursor.execute("ALTER TABLE medicos ADD COLUMN IF NOT EXISTS activo INTEGER DEFAULT 1")

    # Permisos roles
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS permisos_roles (
        id {auto_inc},
        rol {text} NOT NULL,
        modulo {text} NOT NULL,
        UNIQUE(rol, modulo)
    )''')
    permisos_data = [('administrador','Admisión'),('administrador','Caja'),('administrador','Laboratorio'),('administrador','Atención Médica'),('administrador','Configuración'),
                    ('medico','Admisión'),('medico','Caja'),('medico','Atención Médica'),('laboratorista','Laboratorio'),('tecnologo','Laboratorio'),('enfermera','Admisión')]
    for rol, modulo in permisos_data:
        if IS_POSTGRES:
            ejecutar_consulta(cursor, "INSERT INTO permisos_roles (rol, modulo) VALUES (%s,%s) ON CONFLICT (rol, modulo) DO NOTHING", (rol, modulo))
        else:
            ejecutar_consulta(cursor, "INSERT OR IGNORE INTO permisos_roles (rol, modulo) VALUES (?,?)", (rol, modulo))

    # Procedimientos
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS procedimientos (
        id {auto_inc},
        codigo {text} UNIQUE NOT NULL,
        nombre {text} NOT NULL,
        tipo {text} CHECK (tipo IN ('examen','servicio','medicamento','procedimiento')),
        precio REAL DEFAULT 0,
        activo BOOLEAN DEFAULT TRUE
    )''')
    procedimientos_data = [('71020','EXAMEN RADIOLOGICO, TORAX, FRONTAL Y LATERAL','examen',50),('71015','EXAMEN RADIOLOGICO, TORAX; ESTEREOTÁCTICO, FRONTAL','examen',45),('71010','EXAMEN RADIOLOGICO, TORAX; INCIDENCIA FRONTAL','examen',40),('ECO01','Ecografía Obstétrica','procedimiento',80),('ECO02','Ecografía General','procedimiento',70),('TOM01','Tomografía Computarizada','procedimiento',150)]
    for cod, nombre, tipo, precio in procedimientos_data:
        if IS_POSTGRES:
            ejecutar_consulta(cursor, "INSERT INTO procedimientos (codigo, nombre, tipo, precio) VALUES (%s,%s,%s,%s) ON CONFLICT (codigo) DO NOTHING", (cod, nombre, tipo, precio))
        else:
            ejecutar_consulta(cursor, "INSERT OR IGNORE INTO procedimientos (codigo, nombre, tipo, precio) VALUES (?,?,?,?)", (cod, nombre, tipo, precio))

    # Recetas
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS recetas (
        id {auto_inc},
        id_cita INTEGER REFERENCES citas(id),
        id_paciente INTEGER REFERENCES pacientes(id),
        id_medico INTEGER REFERENCES medicos(id),
        numero_cuenta {text},
        fecha_emision {text} DEFAULT CURRENT_TIMESTAMP,
        diagnostico {text},
        indicaciones {text},
        estado {text} DEFAULT 'activa'
    )''')
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS receta_detalle (
        id {auto_inc},
        id_receta INTEGER REFERENCES recetas(id) ON DELETE CASCADE,
        id_procedimiento INTEGER REFERENCES procedimientos(id),
        procedimiento_manual {text},
        cantidad INTEGER DEFAULT 1,
        precio_unitario REAL DEFAULT 0,
        observaciones {text}
    )''')
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS farmacias (
        id {auto_inc},
        nombre {text} NOT NULL,
        direccion {text},
        telefono {text}
    )''')

    # Autorizaciones eliminación
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS autorizaciones_eliminacion (
        id {auto_inc},
        id_paciente INTEGER REFERENCES pacientes(id),
        usuario_autoriza {text},
        fecha_autorizacion {text} DEFAULT CURRENT_TIMESTAMP,
        archivo_pdf {text} NOT NULL,
        motivo {text}
    )''')

    # Medicamentos
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS medicamentos (
        id {auto_inc},
        codigo {text} UNIQUE NOT NULL,
        nombre {text} NOT NULL,
        descripcion {text},
        precio REAL DEFAULT 0,
        stock INTEGER DEFAULT 0,
        unidad_medida {text} DEFAULT 'unidad',
        activo INTEGER DEFAULT 1,
        fecha_vencimiento DATE,
        laboratorio {text}
    )''')
    medicamentos_data = [('PARA001','Paracetamol','Analgésico y antipirético',5.50,100,'tableta',1,None,'Bayer'),('IBU001','Ibuprofeno','Antiinflamatorio',8.00,80,'tableta',1,None,'Pfizer'),('AMO001','Amoxicilina','Antibiótico',12.50,50,'cápsula',1,None,'Sandoz')]
    for cod, nombre, desc, precio, stock, unidad, activo, fecha_venc, laboratorio in medicamentos_data:
        if IS_POSTGRES:
            ejecutar_consulta(cursor, "INSERT INTO medicamentos (codigo, nombre, descripcion, precio, stock, unidad_medida, activo, fecha_vencimiento, laboratorio) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (codigo) DO NOTHING", (cod, nombre, desc, precio, stock, unidad, activo, fecha_venc, laboratorio))
        else:
            ejecutar_consulta(cursor, "INSERT OR IGNORE INTO medicamentos (codigo, nombre, descripcion, precio, stock, unidad_medida, activo, fecha_vencimiento, laboratorio) VALUES (?,?,?,?,?,?,?,?,?)", (cod, nombre, desc, precio, stock, unidad, activo, fecha_venc, laboratorio))

    conn.commit()
    conn.close()

init_db()

# ========================== FUNCIONES AUXILIARES ==========================
def obtener_configuracion():
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT nombre_sistema, tamano_hoja, logo_path, encabezado_texto, pie_pagina_texto, report_header, report_footer, sello_path, ticket_size, report_size, result_size FROM configuracion_sistema WHERE id=1")
    row = cur.fetchone()
    conn.close()
    return row

def obtener_tamano_pagina(tipo='default'):
    config = obtener_configuracion()
    if not config:
        return A4
    if tipo == 'ticket':
        s = config[8] if len(config)>8 and config[8] else 'TICKET_80MM'
        if s == 'TICKET_80MM': return TICKET_80MM
        elif s == 'TICKET_80MM_LANDSCAPE': return TICKET_80MM_LANDSCAPE
        elif s == 'A4': return A4
        elif s == 'A5': return A5
        elif s == 'LETTER': return LETTER
        elif s == 'LEGAL': return LEGAL
        else: return A4
    elif tipo == 'report':
        s = config[9] if len(config)>9 and config[9] else 'A4'
    elif tipo == 'result':
        s = config[10] if len(config)>10 and config[10] else 'A4'
    else:
        s = config[1] if config[1] else 'A4'
    return {'A4':A4, 'A5':A5, 'LETTER':LETTER, 'LEGAL':LEGAL}.get(s, A4)

def get_user_modules(rol):
    if not rol: return []
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT modulo FROM permisos_roles WHERE rol=?", (rol,))
    mods = [r[0] for r in cur.fetchall()]
    conn.close()
    return mods

def generar_siguiente_hc():
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT historia_clinica FROM pacientes WHERE historia_clinica IS NOT NULL AND deleted=0")
    hs = {r[0] for r in cur.fetchall()}
    conn.close()
    n = 1
    while True:
        hc = f"HC-{n:04d}"
        if hc not in hs:
            return hc
        n += 1

def generar_siguiente_boleta():
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT MAX(id) FROM pagos")
    max_id = cur.fetchone()[0]
    conn.close()
    return f"B-{max_id+1:04d}" if max_id else "B-0001"

def calcular_edad(fecha):
    if not fecha: return 0
    try:
        nac = datetime.strptime(fecha, '%Y-%m-%d').date()
        hoy = date.today()
        edad = hoy.year - nac.year - ((hoy.month,hoy.day) < (nac.month,nac.day))
        return edad
    except: return 0

def crear_paciente_sistema(dni, nombre, apellido, fecha_nac='', telefono='', celular='', direccion='', sexo='', nro_afiliacion='', historia_manual=None):
    dni = (dni or '').strip()
    conn = get_db_connection()
    cur = conn.cursor()
    
    ejecutar_consulta(cur, "SELECT id, historia_clinica FROM pacientes WHERE dni = ? LIMIT 1", (dni,))
    existente = cur.fetchone()
    if existente:
        conn.close()
        return existente[1]
    
    if historia_manual:
        historia_manual = historia_manual.strip()
        if historia_manual:
            ejecutar_consulta(cur, "SELECT 1 FROM pacientes WHERE historia_clinica = ?", (historia_manual,))
            if cur.fetchone():
                conn.close()
                raise ValueError(f"La historia clínica '{historia_manual}' ya existe.")
            hc = historia_manual
        else:
            hc = generar_siguiente_hc()
    else:
        hc = generar_siguiente_hc()
    
    edad = calcular_edad(fecha_nac) if fecha_nac else 0
    ejecutar_consulta(cur, """INSERT INTO pacientes (historia_clinica, dni, nombre, apellido, fecha_nacimiento, telefono, celular, direccion, sexo, edad, nro_afiliacion, deleted)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
                (hc, dni, nombre, apellido, fecha_nac, telefono, celular, direccion, sexo, edad, nro_afiliacion))
    conn.commit()
    conn.close()
    return hc

def obtener_paciente_por_dni(dni):
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT id, historia_clinica, nombre, apellido, fecha_nacimiento, edad, nro_afiliacion FROM pacientes WHERE dni=? AND deleted=0", (dni,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'id':row[0], 'historia_clinica':row[1], 'nombre':row[2], 'apellido':row[3], 'fecha_nacimiento':row[4], 'edad':row[5], 'nro_afiliacion':row[6]}
    return None

def obtener_paciente_por_boleta(numero_boleta):
    """Busca paciente y servicio asociado a una boleta de pago."""
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, """SELECT p.id_paciente, pa.nombre, pa.apellido, pa.dni, pa.historia_clinica, 
                                    c.id_servicio, s.nombre AS servicio_nombre, p.monto, p.descripcion
                             FROM pagos p
                             JOIN pacientes pa ON p.id_paciente = pa.id
                             LEFT JOIN citas c ON p.id_cita = c.id
                             LEFT JOIN servicios s ON c.id_servicio = s.id
                             WHERE p.numero_boleta = ? AND p.estado = 'Pagado'""", (numero_boleta,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            'id_paciente': row[0],
            'nombre': row[1],
            'apellido': row[2],
            'dni': row[3],
            'historia_clinica': row[4],
            'id_servicio': row[5],
            'servicio_nombre': row[6],
            'monto': row[7],
            'descripcion': row[8]
        }
    return None

def generar_codigo_muestra():
    hoy = date.today()
    fecha = hoy.strftime("%Y%m%d")
    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        ejecutar_consulta(cur, "SELECT COUNT(*) FROM ordenes_laboratorio WHERE fecha_validez = %s", (hoy,))
    else:
        ejecutar_consulta(cur, "SELECT COUNT(*) FROM ordenes_laboratorio WHERE fecha_validez = ?", (hoy,))
    count = cur.fetchone()[0] + 1
    conn.close()
    return f"MUESTRA-{fecha}-{count:04d}"

def generar_pdf_boleta(id_pago):
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, """SELECT p.id, p.numero_boleta, p.monto, p.fecha_pago, p.descripcion, pa.nombre, pa.apellido, pa.dni, pa.historia_clinica, c.fecha_cita
                   FROM pagos p LEFT JOIN pacientes pa ON p.id_paciente = pa.id LEFT JOIN citas c ON p.id_cita = c.id WHERE p.id = ?""", (id_pago,))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    size = obtener_tamano_pagina('ticket')
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    w,h = size
    c.setFillColor(colors.HexColor('#0d2b45'))
    c.rect(10, h-60, w-20, 50, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(20, h-38, 'BOLETA DE PAGO')
    c.setFont('Helvetica', 7)
    c.drawString(20, h-52, 'SISGALENO2026')
    c.setFillColor(colors.black)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(15, h-85, f'Boleta: {row[1]}')
    c.setFont('Helvetica', 8)
    c.drawString(15, h-100, f'Fecha: {row[3]}')
    c.drawString(15, h-115, f'Paciente: {row[5]} {row[6]}')
    c.drawString(15, h-130, f'DNI: {row[7]}')
    c.drawString(15, h-145, f'HC: {row[8] or "N/E"}')
    c.drawString(15, h-160, f'Concepto: {row[4] or "Sin detalle"}')
    if row[9]: c.drawString(15, h-175, f'Cita: {row[9]}')
    c.line(15, h-190, w-15, h-190)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(15, h-210, f'Monto: S/ {float(row[2] or 0):.2f}')
    c.setFont('Helvetica', 6)
    c.setFillColor(colors.grey)
    c.drawString(15, 15, 'Documento generado por SISGALENO2026')
    c.save()
    buf.seek(0)
    return buf

def generar_codigo_barras(codigo):
    from barcode import get_barcode_class
    Code128 = get_barcode_class('code128')
    buf = io.BytesIO()
    code = Code128(codigo, writer=ImageWriter())
    code.write(buf, options={'module_width':0.2, 'module_height':15, 'font_size':10, 'text_distance':2, 'background':'white', 'foreground':'black'})
    buf.seek(0)
    return buf

def paciente_tiene_pagos(id_paciente):
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM pagos WHERE id_paciente=? AND estado='Pagado'", (id_paciente,))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0

app.jinja_env.globals.update(paciente_tiene_pagos=paciente_tiene_pagos)

# ========================== MIDDLEWARE ==========================
@app.before_request
def proteger():
    if request.endpoint in {'login','logout','index','static','api_paciente_por_dni','api_procedimientos','api_buscar_pacientes','api_buscar_por_boleta'}:
        return None
    if not session.get('usuario'):
        return redirect(url_for('login'))
    return None

# ========================== LAYOUT ==========================
LAYOUT_BASE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ nombre_sistema }} - Clínica</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #f4f7f6; }
        .navbar-custom { background: linear-gradient(90deg, #0d2b45 0%, #1a4d70 100%); }
        .navbar-custom .navbar-brand, .navbar-custom .nav-link { color: white; }
        .navbar-custom .nav-link:hover { color: #72c6f7; }
        .container { max-width: 1200px; margin: 30px auto; padding: 20px; background: white; border-radius: 16px; box-shadow: 0 8px 30px rgba(0,0,0,0.08); }
        .btn { border-radius: 50px; }
        .badge-pendiente { background: #ffc107; color: #333; }
        .badge-pagado { background: #28a745; color: white; }
        .badge-muestra { background: #17a2b8; color: white; }
        .badge-atendido { background: #6c757d; color: white; }
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
    if request.method == 'POST':
        user = request.form['usuario']
        pwd = request.form['password']
        conn = get_db_connection()
        cur = conn.cursor()
        if IS_POSTGRES:
            ejecutar_consulta(cur, "SELECT id, rol, password_hash FROM usuarios WHERE usuario=%s", (user,))
        else:
            ejecutar_consulta(cur, "SELECT id, rol, password_hash FROM usuarios WHERE usuario=?", (user,))
        data = cur.fetchone()
        conn.close()
        if data and check_password_hash(data[2], pwd):
            session['usuario'] = user
            session['rol'] = data[1]
            session['id_usuario'] = data[0]
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos', 'danger')
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    contenido = """
    <h2 class="text-center">Inicio de Sesión</h2>
    <form method="POST" style="max-width:400px; margin:auto;">
        <div class="mb-3"><label>Usuario</label><input type="text" name="usuario" class="form-control" required></div>
        <div class="mb-3"><label>Contraseña</label><input type="password" name="password" class="form-control" required></div>
        <button type="submit" class="btn btn-primary w-100">Acceder</button>
    </form>
    """
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=[])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    user_modules = get_user_modules(session.get('rol'))
    conn = get_db_connection()
    cur = conn.cursor()
    hoy = date.today().isoformat()
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM citas WHERE fecha_cita LIKE ?", (hoy+'%',))
    citas_hoy = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM pacientes WHERE deleted=0")
    total_pacientes = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM ordenes_laboratorio WHERE estado='Pendiente'")
    pendientes_lab = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COALESCE(SUM(monto),0) FROM pagos WHERE fecha_pago LIKE ? AND estado='Pagado'", (hoy+'%',))
    ingresos_hoy = float(cur.fetchone()[0])
    conn.close()
    contenido = """
    <div style="background:#0d2b45; color:white; padding:30px; border-radius:12px; text-align:center;">
        <h2>🏥 Bienvenido, {{ session.get('usuario') }}</h2>
        <p>{{ nombre_sistema }}</p>
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
        {% if 'Atención Médica' in user_modules %}<a href="{{ url_for('recetas') }}" class="menu-item">📝 Recetas</a>{% endif %}
        <a href="{{ url_for('reportes') }}" class="menu-item">📊 Reportes</a>
    </div>
    """
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=user_modules,
                                  citas_hoy=citas_hoy, total_pacientes=total_pacientes,
                                  pendientes_lab=pendientes_lab, ingresos_hoy=ingresos_hoy)

# ========================== REPORTES ==========================
@app.route('/reportes')
def reportes():
    if not session.get('usuario'): return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor()
    fecha = request.args.get('fecha') or datetime.now().strftime('%Y-%m-%d')
    mes = request.args.get('mes') or datetime.now().strftime('%Y-%m')
    fecha_inicio = request.args.get('fecha_inicio') or datetime.now().strftime('%Y-%m-01')
    fecha_fin = request.args.get('fecha_fin') or datetime.now().strftime('%Y-%m-%d')
    year, month = map(int, mes.split('-'))
    ultimo_dia = monthrange(year, month)[1]
    mes_inicio = f'{year:04d}-{month:02d}-01'
    mes_fin = f'{year:04d}-{month:02d}-{ultimo_dia:02d}'
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM citas WHERE date(fecha_cita)=?", (fecha,))
    total_citas_dia = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM citas WHERE date(fecha_cita)=? AND estado='Pagado'", (fecha,))
    citas_pagadas_dia = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM citas c LEFT JOIN diagnosticos d ON d.id_cita = c.id WHERE date(c.fecha_cita)=? AND d.id IS NOT NULL", (fecha,))
    atenciones_dia = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COALESCE(SUM(monto),0) FROM pagos WHERE estado='Pagado' AND date(fecha_pago)=?", (fecha,))
    ingresos_dia = float(cur.fetchone()[0] or 0)
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM citas WHERE date(fecha_cita) BETWEEN ? AND ?", (mes_inicio, mes_fin))
    total_citas_mes = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM citas WHERE date(fecha_cita) BETWEEN ? AND ? AND estado='Pagado'", (mes_inicio, mes_fin))
    citas_pagadas_mes = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COUNT(*) FROM citas c LEFT JOIN diagnosticos d ON d.id_cita = c.id WHERE date(c.fecha_cita) BETWEEN ? AND ? AND d.id IS NOT NULL", (mes_inicio, mes_fin))
    atenciones_mes = cur.fetchone()[0]
    ejecutar_consulta(cur, "SELECT COALESCE(SUM(monto),0) FROM pagos WHERE estado='Pagado' AND date(fecha_pago) BETWEEN ? AND ?", (mes_inicio, mes_fin))
    ingresos_mes = float(cur.fetchone()[0] or 0)
    ejecutar_consulta(cur, """
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
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  fecha=fecha, mes=mes, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
                                  total_citas_dia=total_citas_dia, citas_pagadas_dia=citas_pagadas_dia,
                                  atenciones_dia=atenciones_dia, ingresos_dia=ingresos_dia,
                                  total_citas_mes=total_citas_mes, citas_pagadas_mes=citas_pagadas_mes,
                                  atenciones_mes=atenciones_mes, ingresos_mes=ingresos_mes,
                                  rendimiento=rendimiento)

# ========================== ADMISIÓN ==========================
@app.route('/admision', methods=['GET','POST'])
def admision():
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    if request.method == 'POST' and request.form.get('accion') == 'registrar_paciente':
        dni = request.form['dni']
        nombre = request.form['nombre']
        apellido = request.form['apellido']
        fecha_nac = request.form.get('fecha_nacimiento', '')
        telefono = request.form.get('telefono', '')
        celular = request.form.get('celular', '')
        direccion = request.form.get('direccion', '')
        sexo = request.form.get('sexo', '')
        nro_af = request.form.get('nro_afiliacion', '')
        historia_manual = request.form.get('historia_clinica', '').strip()
        if not historia_manual:
            historia_manual = None
        try:
            hc = crear_paciente_sistema(dni, nombre, apellido, fecha_nac, telefono, celular, direccion, sexo, nro_af, historia_manual)
            flash(f"Paciente {nombre} {apellido} registrado (HC: {hc}).", 'success')
        except Exception as e:
            flash(f"Error: {str(e)}", 'danger')
        return redirect(url_for('admision'))

    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes WHERE deleted=0 ORDER BY id DESC")
    pacientes = cur.fetchall()
    ejecutar_consulta(cur, "SELECT id, nombre FROM servicios")
    servicios = cur.fetchall()
    ejecutar_consulta(cur, "SELECT id, nombre || ' ' || apellido AS nombre_completo FROM medicos WHERE activo=1 ORDER BY nombre")
    medicos = cur.fetchall()
    ejecutar_consulta(cur, """SELECT c.id, p.historia_clinica, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, c.tipo_asegurado, c.numero_boleta
                   FROM citas c JOIN pacientes p ON c.id_paciente = p.id JOIN servicios s ON c.id_servicio = s.id WHERE p.deleted=0 ORDER BY c.fecha_cita DESC""")
    citas = cur.fetchall()
    conn.close()
    contenido = """
    <h2>📋 Admisión</h2>
    <div class="card p-3 mb-3">
        <div class="d-flex gap-2 flex-wrap"><button onclick="toggleForm('form_cita')" class="btn btn-success">+ Nueva Cita</button><button onclick="toggleForm('form_paciente')" class="btn btn-primary">+ Registrar Paciente</button></div>
        <div id="form_paciente" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4>Registrar Paciente</h4>
            <form method="POST"><input type="hidden" name="accion" value="registrar_paciente">
                <div class="adm-form-grid">
                    <div><label>DNI *</label><input type="text" name="dni" class="form-control" required></div>
                    <div><label>Nombre *</label><input type="text" name="nombre" class="form-control" required></div>
                    <div><label>Apellido *</label><input type="text" name="apellido" class="form-control" required></div>
                    <div><label>Fecha Nac.</label><input type="date" name="fecha_nacimiento" class="form-control" id="fnac" onchange="calcularEdad()"></div>
                    <div><label>Edad</label><input type="number" name="edad" id="edad" class="form-control" readonly></div>
                    <div><label>Sexo</label><select name="sexo" class="form-control"><option value="">Seleccione</option><option value="Masculino">Masculino</option><option value="Femenino">Femenino</option><option value="Otro">Otro</option></select></div>
                    <div><label>Nº Afiliación</label><input type="text" name="nro_afiliacion" class="form-control"></div>
                    <div><label>Historia Clínica</label><input type="text" name="historia_clinica" class="form-control" placeholder="Opcional - dejar vacío para auto-generar"></div>
                    <div><label>Teléfono</label><input type="text" name="telefono" class="form-control"></div>
                    <div><label>Celular</label><input type="text" name="celular" class="form-control"></div>
                    <div style="grid-column:span 2;"><label>Dirección</label><input type="text" name="direccion" class="form-control"></div>
                </div>
                <button class="btn btn-success mt-2">Guardar</button>
            </form>
        </div>
        <div id="form_cita" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4>Nueva Cita</h4>
            <form method="POST" action="{{ url_for('crear_cita') }}" id="formCita">
                <div class="row g-2">
                    <div class="col-md-6">
                        <label>Buscar Paciente</label>
                        <div style="position:relative;">
                            <input type="text" id="buscar_paciente" class="form-control" placeholder="Escriba DNI, nombre, apellido o HC..." autocomplete="off">
                            <div id="sugerencias_pacientes" class="autocomplete-suggestions" style="display:none; position:absolute; background:white; border:1px solid #ccc; max-height:200px; overflow-y:auto; width:100%; z-index:1000;"></div>
                        </div>
                        <input type="hidden" name="id_paciente" id="id_paciente" required>
                        <small id="paciente_seleccionado" class="text-muted">Ningún paciente seleccionado</small>
                    </div>
                    <div class="col-md-6">
                        <label>Servicio</label>
                        <select name="id_servicio" class="form-control" required>
                            {% for s in servicios %}
                            <option value="{{ s[0] }}">{{ s[1] }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-md-4">
                        <label>Médico</label>
                        <select name="id_medico" class="form-control" required>
                            {% for m in medicos %}
                            <option value="{{ m[0] }}">{{ m[1] }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-md-4">
                        <label>Fecha</label>
                        <input type="date" name="fecha_cita" class="form-control" required>
                    </div>
                    <div class="col-md-4">
                        <label>Hora</label>
                        <input type="time" name="hora_cita" step="60" class="form-control" required>
                    </div>
                    <div class="col-md-6">
                        <label>Tipo</label>
                        <select name="tipo_asegurado" class="form-control" required>
                            <option value="Demanda">Demanda</option>
                            <option value="SIS">SIS</option>
                            <option value="SOAT">SOAT</option>
                        </select>
                    </div>
                    <div class="col-md-12">
                        <label>Motivo</label>
                        <textarea name="motivo_consulta" class="form-control" rows="2"></textarea>
                    </div>
                </div>
                <button class="btn btn-primary mt-2">Agendar</button>
            </form>
        </div>
    </div>
    <h3>Pacientes</h3>
    <table class="table"><thead><tr><th>HC</th><th>DNI</th><th>Nombre</th><th>Apellido</th><th>Acciones</th></tr></thead><tbody>{% for p in pacientes %}<tr><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td>{{ p[4] }}</td><td><a href="{{ url_for('editar_paciente_admision', id_paciente=p[0]) }}" class="btn btn-warning btn-sm">✏️</a></td></tr>{% else %}<tr><td colspan="5">Sin pacientes</td></tr>{% endfor %}</tbody></table>
    <h3>Citas</h3>
    <table class="table"><thead><tr><th>HC</th><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Tipo</th><th>Boleta</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{% for c in citas %}<tr><td>{{ c[1] }}</td><td>{{ c[2] }} {{ c[3] }}</td><td>{{ c[4] }}</td><td>{{ c[5] }}</td><td>{{ c[7] }}</td><td>{{ c[8] or 'Pendiente' }}</td><td><span class="badge badge-{{ 'pagado' if c[6]=='Pagado' else 'pendiente' }}">{{ c[6] }}</span></td><td><a href="{{ url_for('imprimir_ficha_admision', id_cita=c[0]) }}" class="btn btn-primary btn-sm">📄</a></td></tr>{% else %}<tr><td colspan="8">Sin citas</td></tr>{% endfor %}</tbody></table>
    <script>
        function toggleForm(id){var x=document.getElementById(id);x.style.display=x.style.display==='none'?'block':'none';}
        function calcularEdad(){var fn=document.getElementById('fnac').value;if(fn){var hoy=new Date();var nac=new Date(fn);var edad=hoy.getFullYear()-nac.getFullYear();var m=hoy.getMonth()-nac.getMonth();if(m<0||(m===0&&hoy.getDate()<nac.getDate()))edad--;document.getElementById('edad').value=edad;}else{document.getElementById('edad').value='';}}
        // Búsqueda de pacientes para nueva cita
        const inputBuscarPaciente = document.getElementById('buscar_paciente');
        const sugerenciasDiv = document.getElementById('sugerencias_pacientes');
        const idPacienteHidden = document.getElementById('id_paciente');
        const pacienteSeleccionado = document.getElementById('paciente_seleccionado');
        let timeoutBusqueda = null;
        if (inputBuscarPaciente) {
            inputBuscarPaciente.addEventListener('input', function() {
                const q = this.value.trim();
                if (q.length < 2) { sugerenciasDiv.style.display = 'none'; return; }
                clearTimeout(timeoutBusqueda);
                timeoutBusqueda = setTimeout(() => {
                    fetch('/api/buscar_pacientes?q=' + encodeURIComponent(q))
                        .then(r => r.json())
                        .then(data => {
                            sugerenciasDiv.innerHTML = '';
                            if (data.length === 0) {
                                sugerenciasDiv.innerHTML = '<div class="p-2 text-muted">No se encontraron pacientes</div>';
                            } else {
                                data.forEach(p => {
                                    const div = document.createElement('div');
                                    div.className = 'p-2 hover-bg-light';
                                    div.style.cursor = 'pointer';
                                    div.textContent = `${p.historia_clinica || 'S/HC'} - ${p.dni} - ${p.nombre} ${p.apellido}`;
                                    div.addEventListener('click', function() {
                                        inputBuscarPaciente.value = `${p.historia_clinica || 'S/HC'} - ${p.dni} - ${p.nombre} ${p.apellido}`;
                                        idPacienteHidden.value = p.id;
                                        pacienteSeleccionado.textContent = `Paciente seleccionado: ${p.nombre} ${p.apellido} (${p.dni})`;
                                        pacienteSeleccionado.className = 'text-success';
                                        sugerenciasDiv.style.display = 'none';
                                    });
                                    sugerenciasDiv.appendChild(div);
                                });
                            }
                            sugerenciasDiv.style.display = 'block';
                        });
                }, 300);
            });
            document.addEventListener('click', function(e) {
                if (!e.target.closest('#buscar_paciente') && !e.target.closest('#sugerencias_pacientes')) {
                    sugerenciasDiv.style.display = 'none';
                }
            });
            document.getElementById('formCita').addEventListener('submit', function(e) {
                if (!idPacienteHidden.value) {
                    e.preventDefault();
                    alert('Debe seleccionar un paciente de la lista de búsqueda.');
                    inputBuscarPaciente.focus();
                }
            });
        }
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  pacientes=pacientes, servicios=servicios, medicos=medicos, citas=citas)

@app.route('/admision/crear_cita', methods=['POST'])
def crear_cita():
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    id_pac = request.form['id_paciente']
    id_serv = request.form['id_servicio']
    id_med = request.form['id_medico']
    fecha_str = request.form['fecha_cita']
    hora_str = request.form['hora_cita']
    motivo = request.form.get('motivo_consulta', '')
    tipo = request.form['tipo_asegurado']
    fecha_hora = f"{fecha_str} {hora_str}:00"
    conn = get_db_connection()
    cur = conn.cursor()
    estado = 'Pagado' if tipo in ['SIS','SOAT'] else 'Pendiente'
    ejecutar_consulta(cur, "INSERT INTO citas (id_paciente, id_servicio, id_medico, fecha_cita, estado, motivo_consulta, tipo_asegurado, numero_boleta) VALUES (?,?,?,?,?,?,?,?)",
                (id_pac, id_serv, id_med, fecha_hora, estado, motivo, tipo, ''))
    conn.commit()
    conn.close()
    return redirect(url_for('admision'))

@app.route('/admision/imprimir/<int:id_cita>')
def imprimir_ficha_admision(id_cita):
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, """SELECT p.nombre, p.apellido, p.dni, p.edad, p.sexo, p.historia_clinica, s.nombre, m.nombre||' '||m.apellido, c.fecha_cita, c.tipo_asegurado, c.numero_boleta
                   FROM citas c JOIN pacientes p ON c.id_paciente=p.id JOIN servicios s ON c.id_servicio=s.id JOIN medicos m ON c.id_medico=m.id WHERE c.id=?""", (id_cita,))
    data = cur.fetchone()
    conn.close()
    if not data: return "Cita no encontrada", 404
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    logo_path = config[2] if config else ''
    encabezado = config[3] if config else 'Laboratorio Clínico'
    pie = config[4] if config else ''
    size = obtener_tamano_pagina('report')
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=size, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    elems = []
    if logo_path and os.path.exists(os.path.join('static', logo_path)):
        try:
            img = ImageReader(os.path.join('static', logo_path))
            elems.append(Spacer(1,10))
        except: pass
    title = ParagraphStyle(name='Title', parent=styles['Heading1'], fontSize=16, alignment=1, spaceAfter=20)
    elems.append(Paragraph(f"<b>{nombre_sistema}</b>", title))
    elems.append(Paragraph(f"<i>{encabezado}</i>", styles['Heading2']))
    elems.append(Paragraph("<b>FICHA DE ADMISIÓN</b>", styles['Heading2']))
    elems.append(Spacer(1,15))
    info = f"""<b>Paciente:</b> {data[0]} {data[1]}<br/><b>DNI:</b> {data[2]}<br/><b>HC:</b> {data[5]}<br/><b>Servicio:</b> {data[6]}<br/><b>Médico:</b> Dr. {data[7]}<br/><b>Fecha:</b> {data[8]}<br/><b>Tipo:</b> {data[9]}"""
    elems.append(Paragraph(info, styles['Normal']))
    elems.append(Spacer(1,15))
    elems.append(Paragraph("<b>Motivo:</b>", styles['Normal']))
    elems.append(Paragraph("No especificado" if not data[10] else data[10], styles['Normal']))
    elems.append(Spacer(1,30))
    elems.append(Paragraph(pie, styles['Italic']))
    doc.build(elems)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Ficha_Admision_{id_cita}.pdf", mimetype='application/pdf')

@app.route('/admision/editar_paciente/<int:id_paciente>', methods=['GET','POST'])
def editar_paciente_admision(id_paciente):
    if 'Admisión' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        nombre = request.form['nombre']
        apellido = request.form['apellido']
        dni = request.form['dni']
        fecha_nac = request.form.get('fecha_nacimiento', '')
        telefono = request.form.get('telefono', '')
        celular = request.form.get('celular', '')
        direccion = request.form.get('direccion', '')
        sexo = request.form.get('sexo', '')
        nro_af = request.form.get('nro_afiliacion', '')
        edad = calcular_edad(fecha_nac) if fecha_nac else 0
        ejecutar_consulta(cur, """UPDATE pacientes SET nombre=?, apellido=?, dni=?, fecha_nacimiento=?, telefono=?, celular=?, direccion=?, sexo=?, edad=?, nro_afiliacion=? WHERE id=?""",
                    (nombre, apellido, dni, fecha_nac, telefono, celular, direccion, sexo, edad, nro_af, id_paciente))
        conn.commit()
        conn.close()
        flash('Paciente actualizado.', 'success')
        return redirect(url_for('admision'))
    ejecutar_consulta(cur, "SELECT id, nombre, apellido, dni, fecha_nacimiento, telefono, celular, direccion, sexo, nro_afiliacion FROM pacientes WHERE id=?", (id_paciente,))
    p = cur.fetchone()
    conn.close()
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
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')))

# ========================== API ==========================
@app.route('/api/buscar_pacientes')
def api_buscar_pacientes():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify([])
    
    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        query = """SELECT id, historia_clinica, dni, nombre, apellido
                   FROM pacientes 
                   WHERE deleted=0 AND (
                       dni ILIKE %s OR 
                       nombre ILIKE %s OR 
                       apellido ILIKE %s OR 
                       historia_clinica ILIKE %s
                   )
                   ORDER BY nombre, apellido
                   LIMIT 20"""
        params = (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')
    else:
        query = """SELECT id, historia_clinica, dni, nombre, apellido
                   FROM pacientes 
                   WHERE deleted=0 AND (
                       dni LIKE ? OR 
                       nombre LIKE ? OR 
                       apellido LIKE ? OR 
                       historia_clinica LIKE ?
                   )
                   ORDER BY nombre, apellido
                   LIMIT 20"""
        params = (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%')
    
    ejecutar_consulta(cur, query, params)
    results = cur.fetchall()
    conn.close()
    
    return jsonify([
        {
            'id': r[0],
            'historia_clinica': r[1] or '',
            'dni': r[2],
            'nombre': r[3],
            'apellido': r[4]
        }
        for r in results
    ])

@app.route('/api/paciente_por_dni')
def api_paciente_por_dni():
    dni = request.args.get('dni', '').strip()
    if not dni:
        return jsonify({'error':'DNI requerido'}), 400
    p = obtener_paciente_por_dni(dni)
    if p:
        return jsonify(p)
    return jsonify({'error':'Paciente no encontrado'}), 404

@app.route('/api/buscar_por_boleta')
def api_buscar_por_boleta():
    boleta = request.args.get('boleta', '').strip()
    if not boleta:
        return jsonify({'error':'Número de boleta requerido'}), 400
    data = obtener_paciente_por_boleta(boleta)
    if data:
        return jsonify(data)
    return jsonify({'error':'Boleta no encontrada o no pagada'}), 404

@app.route('/caja', methods=['GET','POST'])
def caja():
    if 'Caja' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'registrar_paciente_caja':
            dni = request.form['dni']
            nombre = request.form['nombre']
            apellido = request.form['apellido']
            historia_manual = request.form.get('historia_clinica', '').strip()
            if not historia_manual:
                historia_manual = None
            try:
                hc = crear_paciente_sistema(dni, nombre, apellido, '', '', '', '', '', '', historia_manual)
                flash(f"Paciente registrado (HC: {hc}).", 'success')
            except Exception as e:
                flash(f"Error: {str(e)}", 'danger')
            return redirect(url_for('caja'))
        elif accion == 'registrar_cobro_directo':
            id_paciente = request.form.get('id_paciente')
            tipo_item = request.form.get('tipo_item', 'servicio')
            monto = request.form.get('monto', '').strip()
            descripcion = request.form.get('descripcion', '').strip() or 'Cobro directo'
            try:
                if tipo_item in ('laboratorio', 'analisis'):
                    id_examenes = request.form.getlist('id_examenes')
                    if not id_examenes:
                        raise ValueError('Seleccione al menos un examen válido.')
                    detalle_examenes = []
                    monto_total = 0.0
                    examenes_validos = []
                    for id_examen in id_examenes:
                        ejecutar_consulta(cur, "SELECT descripcion, precio FROM examenes_catalogo WHERE id=?", (id_examen,))
                        examen = cur.fetchone()
                        if not examen:
                            continue
                        detalle_examenes.append(examen[0])
                        monto_total += float(examen[1] or 0)
                        examenes_validos.append((id_examen, examen[0], float(examen[1] or 0)))
                    if not detalle_examenes:
                        raise ValueError('Seleccione al menos un examen válido.')
                    monto_final = float(monto) if monto else monto_total
                    fecha_emision = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    descripcion_final = f"{'Laboratorio' if tipo_item == 'laboratorio' else 'Análisis'}: {', '.join(detalle_examenes)}"
                    numero_boleta = generar_siguiente_boleta()
                    ejecutar_consulta(cur, """
                        INSERT INTO pagos (id_cita, id_paciente, numero_boleta, monto, fecha_pago, estado, descripcion)
                        VALUES (?, ?, ?, ?, ?, 'Pagado', ?)
                    """, (None, id_paciente, numero_boleta, monto_final, fecha_emision, descripcion or descripcion_final))
                    id_pago = cur.lastrowid
                    for id_examen, _, precio_examen in examenes_validos:
                        ejecutar_consulta(cur, """
                            INSERT INTO ordenes_laboratorio (id_paciente, id_examen, fecha_emision, estado, precio, id_pago)
                            VALUES (?, ?, ?, 'Pendiente', ?, ?)
                        """, (id_paciente, id_examen, fecha_emision, precio_examen, id_pago))
                    id_cita = None
                else:
                    id_servicio = request.form.get('id_servicio')
                    ejecutar_consulta(cur, "SELECT nombre, precio_base FROM servicios WHERE id=?", (id_servicio,))
                    servicio = cur.fetchone()
                    if not servicio:
                        raise ValueError('Seleccione un servicio válido.')
                    monto_final = float(monto) if monto else float(servicio[1] or 0)
                    descripcion_final = f"Servicio: {servicio[0]}"
                    if tipo_item == 'atencion':
                        descripcion_final = f"Atención: {servicio[0]}"
                    ejecutar_consulta(cur, """
                        INSERT INTO citas (id_paciente, id_servicio, fecha_cita, estado, tipo_asegurado, numero_boleta)
                        VALUES (?, ?, ?, 'Pagado', 'Demanda', ?)
                    """, (id_paciente, id_servicio, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ''))
                    id_cita = cur.lastrowid
                if tipo_item not in ('laboratorio', 'analisis'):
                    numero_boleta = generar_siguiente_boleta()
                    ejecutar_consulta(cur, """
                        INSERT INTO pagos (id_cita, id_paciente, numero_boleta, monto, fecha_pago, estado, descripcion)
                        VALUES (?, ?, ?, ?, ?, 'Pagado', ?)
                    """, (id_cita, id_paciente, numero_boleta, monto_final, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), descripcion or descripcion_final))
                conn.commit()
                flash(f"Boleta emitida: {numero_boleta}.", 'success')
            except ValueError as e:
                flash(f"Error: {str(e)}", 'danger')
            except Exception as e:
                flash(f"Error al registrar el cobro: {str(e)}", 'danger')
            return redirect(url_for('caja'))

    ejecutar_consulta(cur, """SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, s.precio_base, c.id_paciente
                   FROM citas c JOIN pacientes p ON c.id_paciente = p.id JOIN servicios s ON c.id_servicio = s.id
                   WHERE c.estado='Pendiente' AND c.tipo_asegurado='Demanda' AND p.deleted=0 ORDER BY c.fecha_cita ASC""")
    pendientes = cur.fetchall()
    ejecutar_consulta(cur, """SELECT p.id, p.numero_boleta, p.monto, p.fecha_pago, p.descripcion, pa.nombre, pa.apellido, pa.dni
                   FROM pagos p JOIN pacientes pa ON p.id_paciente = pa.id WHERE pa.deleted=0 ORDER BY p.fecha_pago DESC""")
    historial = cur.fetchall()
    ejecutar_consulta(cur, "SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes WHERE deleted=0 ORDER BY id DESC")
    pacientes = cur.fetchall()
    ejecutar_consulta(cur, "SELECT id, nombre, precio_base FROM servicios ORDER BY id")
    servicios = cur.fetchall()
    ejecutar_consulta(cur, "SELECT id, descripcion, precio FROM examenes_catalogo ORDER BY id")
    examenes = cur.fetchall()
    ejecutar_consulta(cur, """SELECT id_paciente, descripcion, numero_boleta, monto, fecha_pago FROM pagos
                   WHERE estado='Pagado' AND (LOWER(descripcion) LIKE 'laboratorio:%' OR LOWER(descripcion) LIKE 'análisis:%' OR LOWER(descripcion) LIKE 'analisis:%')
                   ORDER BY fecha_pago DESC""")
    pagos_examenes = cur.fetchall()
    pagos_resumen = {}
    for id_paciente, descripcion, numero_boleta, monto, fecha_pago in pagos_examenes:
        if not descripcion: continue
        if ':' in descripcion:
            detalle = descripcion.split(':',1)[1].strip()
        else:
            detalle = descripcion.strip()
        pagos_resumen.setdefault(id_paciente, []).append({'detalle':detalle, 'boleta':numero_boleta, 'monto':float(monto or 0), 'fecha':fecha_pago})
    conn.close()

    contenido = """
    <h2>💰 Caja</h2>
    <div class="card p-3 mb-3">
        <div class="d-flex gap-2 flex-wrap"><button onclick="toggleForm('form_paciente_caja')" class="btn btn-primary">+ Ingresar Paciente</button><button onclick="toggleForm('form_cobro_directo')" class="btn btn-success">+ Cobrar</button></div>
        <div id="form_paciente_caja" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4>Registrar Paciente</h4>
            <form method="POST"><input type="hidden" name="accion" value="registrar_paciente_caja">
                <div class="row g-2">
                    <div class="col-md-4"><label>DNI</label><input type="text" name="dni" class="form-control" required></div>
                    <div class="col-md-4"><label>Nombre</label><input type="text" name="nombre" class="form-control" required></div>
                    <div class="col-md-4"><label>Apellido</label><input type="text" name="apellido" class="form-control" required></div>
                    <div class="col-md-4"><label>Historia Clínica</label><input type="text" name="historia_clinica" class="form-control" placeholder="Opcional - dejar vacío para auto-generar"></div>
                </div>
                <button class="btn btn-primary mt-2">Guardar</button>
            </form>
        </div>
        <div id="form_cobro_directo" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4>Nuevo Cobro</h4>
            <form method="POST" id="formCobro">
                <input type="hidden" name="accion" value="registrar_cobro_directo">
                <div class="row g-2">
                    <div class="col-md-4">
                        <label>Buscar Paciente por DNI</label>
                        <div style="position:relative;">
                            <input type="text" id="buscar_dni_caja" class="form-control" placeholder="Ingrese DNI..." autocomplete="off">
                            <div id="sugerencias_dni_caja" class="autocomplete-suggestions" style="display:none; position:absolute; background:white; border:1px solid #ccc; max-height:150px; overflow-y:auto; width:100%; z-index:1000;"></div>
                        </div>
                        <input type="hidden" name="id_paciente" id="id_paciente_caja">
                        <small id="info_paciente_caja" class="text-muted">Ingrese DNI para buscar o registre nuevo</small>
                    </div>
                    <div class="col-md-4"><label>Tipo</label><select name="tipo_item" id="tipo_item" class="form-control" required><option value="servicio">Servicio</option><option value="laboratorio" selected>Laboratorio</option><option value="analisis">Análisis</option><option value="atencion">Atención</option></select></div>
                    <div class="col-md-4" id="bloque_servicio" style="display:none;"><label>Servicio</label><select name="id_servicio" id="id_servicio" class="form-control">{% for s in servicios %}<option value="{{ s[0] }}" data-precio="{{ s[2] }}">{{ s[1] }} (S/ {{ s[2] }})</option>{% endfor %}</select></div>
                </div>
                <div id="bloque_examenes" class="mt-2"><label>Exámenes (Ctrl+clic)</label><select name="id_examenes" id="id_examenes" class="form-control" multiple size="6">{% for e in examenes %}<option value="{{ e[0] }}" data-precio="{{ e[2] }}">{{ e[1] }} (S/ {{ e[2] }})</option>{% endfor %}</select></div>
                <div id="resumen-seleccion" class="mt-2" style="display:none;"><table class="table table-sm"><thead><tr><th>Examen</th><th>Precio</th><th>Acción</th></tr></thead><tbody id="cuerpo-seleccion"></tbody><tfoot><tr><td><strong>Total</strong></td><td><strong id="total-seleccion">S/ 0.00</strong></td><td></td></tr></tfoot></table></div>
                <div class="row g-2 mt-2"><div class="col-md-4"><label>Monto</label><input type="number" step="0.01" name="monto" id="monto" class="form-control" readonly></div>
                <div class="col-md-8"><label>Observación</label><input type="text" name="descripcion" class="form-control"></div></div>
                <button class="btn btn-success mt-2">Emitir Boleta</button>
            </form>
        </div>
        <div id="resumen_paciente" class="mt-3 p-3 border rounded"><h5>Resumen paciente</h5><div id="resumen_paciente_contenido" class="text-muted">Seleccione un paciente</div></div>
    </div>
    <h3>Pendientes</h3>
    <table class="table"><thead><tr><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Monto</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{% for c in pendientes %}<tr><td>{{ c[1] }} {{ c[2] }}</td><td>{{ c[3] }}</td><td>{{ c[4] }}</td><td>S/ {{ c[6] }}</td><td><span class="badge badge-pendiente">Pendiente</span></td><td><a href="{{ url_for('generar_boleta', id_cita=c[0]) }}" class="btn btn-success btn-sm">💰</a></td></tr>{% else %}<tr><td colspan="6">Sin pendientes</td></tr>{% endfor %}</tbody></table>
    <h3>Boletas emitidas</h3>
    <table class="table"><thead><tr><th>Boleta</th><th>Paciente</th><th>Concepto</th><th>Fecha</th><th>Monto</th><th>Acciones</th></tr></thead><tbody>{% for h in historial %}<tr><td>{{ h[1] }}</td><td>{{ h[5] }} {{ h[6] }}</td><td>{{ h[4] }}</td><td>{{ h[3] }}</td><td>S/ {{ h[2] }}</td><td><a href="{{ url_for('imprimir_boleta_pdf', id_pago=h[0]) }}" class="btn btn-warning btn-sm">🖨️</a></td></tr>{% else %}<tr><td colspan="6">Sin boletas</td></tr>{% endfor %}</tbody></table>
    <script>
    function toggleForm(id){var x=document.getElementById(id);x.style.display=x.style.display==='none'?'block':'none';}
    // Búsqueda de paciente por DNI en Caja
    const inputDniCaja = document.getElementById('buscar_dni_caja');
    const sugerenciasDniCaja = document.getElementById('sugerencias_dni_caja');
    const idPacienteCaja = document.getElementById('id_paciente_caja');
    const infoPacienteCaja = document.getElementById('info_paciente_caja');
    let timeoutDni = null;
    if (inputDniCaja) {
        inputDniCaja.addEventListener('input', function() {
            const q = this.value.trim();
            if (q.length < 2) { sugerenciasDniCaja.style.display = 'none'; return; }
            clearTimeout(timeoutDni);
            timeoutDni = setTimeout(() => {
                fetch('/api/buscar_pacientes?q=' + encodeURIComponent(q))
                    .then(r => r.json())
                    .then(data => {
                        sugerenciasDniCaja.innerHTML = '';
                        if (data.length === 0) {
                            sugerenciasDniCaja.innerHTML = '<div class="p-2 text-muted">No se encontraron pacientes</div>';
                        } else {
                            data.forEach(p => {
                                const div = document.createElement('div');
                                div.className = 'p-2 hover-bg-light';
                                div.style.cursor = 'pointer';
                                div.textContent = `${p.historia_clinica || 'S/HC'} - ${p.dni} - ${p.nombre} ${p.apellido}`;
                                div.addEventListener('click', function() {
                                    inputDniCaja.value = p.dni;
                                    idPacienteCaja.value = p.id;
                                    infoPacienteCaja.textContent = `Paciente seleccionado: ${p.nombre} ${p.apellido} (${p.dni})`;
                                    infoPacienteCaja.className = 'text-success';
                                    sugerenciasDniCaja.style.display = 'none';
                                });
                                sugerenciasDniCaja.appendChild(div);
                            });
                        }
                        sugerenciasDniCaja.style.display = 'block';
                    });
            }, 300);
        });
        document.addEventListener('click', function(e) {
            if (!e.target.closest('#buscar_dni_caja') && !e.target.closest('#sugerencias_dni_caja')) {
                sugerenciasDniCaja.style.display = 'none';
            }
        });
        document.getElementById('formCobro').addEventListener('submit', function(e) {
            if (!idPacienteCaja.value) {
                e.preventDefault();
                alert('Debe seleccionar o registrar un paciente válido.');
                inputDniCaja.focus();
            }
        });
    }
    var tipoItem=document.getElementById('tipo_item'), bloqueServicio=document.getElementById('bloque_servicio'), bloqueExamenes=document.getElementById('bloque_examenes'), montoInput=document.getElementById('monto'), servicioSelect=document.getElementById('id_servicio'), examenesSelect=document.getElementById('id_examenes'), pacienteSelect=document.querySelector('select[name="id_paciente"]'), resumenPaciente=document.getElementById('resumen_paciente_contenido'), pagosResumen={{ pagos_resumen_json|safe }}, selectExamenes=document.getElementById('id_examenes'), cuerpoSeleccion=document.getElementById('cuerpo-seleccion'), totalSeleccion=document.getElementById('total-seleccion'), resumenDiv=document.getElementById('resumen-seleccion');
    function obtenerSeleccionExamenes(){if(!examenesSelect)return[];return Array.from(examenesSelect.selectedOptions||[]).map(function(o){return{nombre:o.textContent.split('(')[0].trim(),precio:parseFloat(o.getAttribute('data-precio')||0)};});}
    function calcularMonto(){var tipo=tipoItem?tipoItem.value:'';var total=0;if(tipo==='servicio'||tipo==='atencion'){var so=servicioSelect?servicioSelect.options[servicioSelect.selectedIndex]:null;total=so?parseFloat(so.getAttribute('data-precio')||0):0;}else if(tipo==='laboratorio'||tipo==='analisis'){var sel=obtenerSeleccionExamenes();total=sel.reduce(function(s,i){return s+i.precio;},0);}if(montoInput)montoInput.value=total.toFixed(2);actualizarResumenPaciente();}
    function actualizarResumenPaciente(){if(!resumenPaciente)return;var pid=pacienteSelect?pacienteSelect.value:'';var tipo=tipoItem?tipoItem.value:'';var html=[];if(pid){var pp=pagosResumen[pid]||[];if(pp.length>0){html.push('<div><strong>Exámenes ya pagados:</strong></div><ul>');pp.forEach(function(i){html.push('<li>'+i.detalle+' · Boleta '+i.boleta+' · S/ '+i.monto.toFixed(2)+'</li>');});html.push('</ul>');}else{html.push('<div class="text-muted">Sin exámenes pagados.</div>');}}else{html.push('<div class="text-muted">Seleccione un paciente.</div>');}if(tipo==='laboratorio'||tipo==='analisis'){var sel=obtenerSeleccionExamenes();if(sel.length>0){html.push('<div><strong>Resumen actual:</strong></div><ul>');sel.forEach(function(i){html.push('<li>'+i.nombre+' · S/ '+i.precio.toFixed(2)+'</li>');});html.push('</ul><div class="fw-bold">Total: S/ '+(parseFloat(montoInput?montoInput.value:0)||0).toFixed(2)+'</div>');}}resumenPaciente.innerHTML=html.join('');}
    function actualizarTablaSeleccion(){if(!selectExamenes)return;var sel=selectExamenes.selectedOptions;var filas='',total=0;if(sel.length===0){resumenDiv.style.display='none';return;}resumenDiv.style.display='block';for(var i=0;i<sel.length;i++){var o=sel[i];var id=o.value;var nom=o.textContent.split('(')[0].trim();var prec=parseFloat(o.getAttribute('data-precio')||0);total+=prec;filas+='<tr><td>'+nom+'</td><td>S/ '+prec.toFixed(2)+'</td><td><button type="button" class="btn btn-danger btn-sm eliminar-examen" data-id="'+id+'">✖</button></td></tr>';}cuerpoSeleccion.innerHTML=filas;totalSeleccion.textContent='S/ '+total.toFixed(2);if(montoInput)montoInput.value=total.toFixed(2);}
    if(selectExamenes){selectExamenes.addEventListener('change',function(){actualizarTablaSeleccion();actualizarResumenPaciente();});}
    document.addEventListener('click',function(e){if(e.target.classList.contains('eliminar-examen')){var id=e.target.getAttribute('data-id');var opts=selectExamenes.options;for(var i=0;i<opts.length;i++){if(opts[i].value===id){opts[i].selected=false;break;}}actualizarTablaSeleccion();actualizarResumenPaciente();var evt=new Event('change');selectExamenes.dispatchEvent(evt);}});
    if(tipoItem){tipoItem.addEventListener('change',function(){var tipo=this.value;if(bloqueServicio)bloqueServicio.style.display=(tipo==='servicio'||tipo==='atencion')?'block':'none';if(bloqueExamenes)bloqueExamenes.style.display=(tipo==='laboratorio'||tipo==='analisis')?'block':'none';if(tipo==='servicio'||tipo==='atencion'){resumenDiv.style.display='none';if(selectExamenes){for(var i=0;i<selectExamenes.options.length;i++){selectExamenes.options[i].selected=false;}actualizarTablaSeleccion();}}else{actualizarTablaSeleccion();}calcularMonto();});}
    if(servicioSelect)servicioSelect.addEventListener('change',calcularMonto);
    if(pacienteSelect)pacienteSelect.addEventListener('change',actualizarResumenPaciente);
    calcularMonto();setTimeout(actualizarTablaSeleccion,100);
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  pendientes=pendientes, historial=historial, pacientes=pacientes,
                                  servicios=servicios, examenes=examenes,
                                  pagos_resumen_json=json.dumps(pagos_resumen))

@app.route('/caja/generar_boleta/<int:id_cita>')
def generar_boleta(id_cita):
    if 'Caja' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT s.precio_base, c.id_paciente FROM citas c JOIN servicios s ON c.id_servicio = s.id WHERE c.id=?", (id_cita,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return redirect(url_for('caja'))
    monto = row[0]
    id_paciente = row[1]
    numero_boleta = generar_siguiente_boleta()
    descripcion = "Consulta médica"
    ejecutar_consulta(cur, "INSERT INTO pagos (id_cita, id_paciente, numero_boleta, monto, fecha_pago, estado, descripcion) VALUES (?,?,?,?,?, 'Pagado', ?)",
                (id_cita, id_paciente, numero_boleta, monto, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), descripcion))
    ejecutar_consulta(cur, "UPDATE citas SET estado='Pagado', numero_boleta=? WHERE id=?", (numero_boleta, id_cita))
    conn.commit()
    conn.close()
    return redirect(url_for('caja'))

@app.route('/caja/boleta_pdf/<int:id_pago>')
def imprimir_boleta_pdf(id_pago):
    if 'Caja' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    buffer = generar_pdf_boleta(id_pago)
    if not buffer:
        return redirect(url_for('caja'))
    download = request.args.get('download', '0') == '1'
    return send_file(buffer, as_attachment=download, download_name=f'boleta_{id_pago}.pdf', mimetype='application/pdf')

# ========================== LABORATORIO ==========================
@app.route('/laboratorio', methods=['GET','POST'])
def laboratorio():
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'registrar_paciente_lab':
            dni = request.form['dni']
            nombre = request.form['nombre']
            apellido = request.form['apellido']
            historia_manual = request.form.get('historia_clinica', '').strip()
            if not historia_manual:
                historia_manual = None
            try:
                hc = crear_paciente_sistema(dni, nombre, apellido, '', '', '', '', '', '', historia_manual)
                flash(f"Paciente agregado (HC: {hc}).", 'success')
            except Exception as e:
                flash(f"Error: {str(e)}", 'danger')
            return redirect(url_for('laboratorio'))
        elif accion == 'editar_paciente_lab':
            id_pac = request.form['id_paciente']
            nombre = request.form['nombre']
            apellido = request.form['apellido']
            dni = request.form['dni']
            try:
                ejecutar_consulta(cur, "UPDATE pacientes SET nombre=?, apellido=?, dni=? WHERE id=?", (nombre, apellido, dni, id_pac))
                conn.commit()
                flash("Paciente actualizado.", 'success')
            except Exception as e:
                flash(f"Error: {str(e)}", 'danger')
            return redirect(url_for('laboratorio'))
        elif accion == 'eliminar_paciente_lab':
            id_pac = request.form['id_paciente']
            if paciente_tiene_pagos(id_pac):
                flash('Paciente con pagos. Use autorización.', 'danger')
                return redirect(url_for('laboratorio'))
            ejecutar_consulta(cur, "SELECT COUNT(*) FROM ordenes_laboratorio o JOIN resultados_lab r ON r.id_orden = o.id WHERE o.id_paciente=?", (id_pac,))
            tiene = cur.fetchone()[0] > 0
            if tiene:
                flash("Tiene resultados de laboratorio.", 'danger')
            else:
                ejecutar_consulta(cur, "UPDATE pacientes SET deleted=1 WHERE id=?", (id_pac,))
                conn.commit()
                flash("Paciente eliminado.", 'success')
            return redirect(url_for('laboratorio'))
        elif accion == 'eliminar_paciente_con_pdf':
            id_pac = request.form.get('id_paciente')
            motivo = request.form.get('motivo', '')
            archivo = request.files.get('archivo_pdf')
            if not archivo or not archivo.filename:
                flash('Seleccione un PDF.', 'danger')
                return redirect(url_for('laboratorio'))
            if not archivo.filename.lower().endswith('.pdf'):
                flash('Solo PDF.', 'danger')
                return redirect(url_for('laboratorio'))
            rol = session.get('rol')
            if rol not in ['administrador','medico']:
                flash('Solo admin o médico pueden autorizar.', 'danger')
                return redirect(url_for('laboratorio'))
            filename = secure_filename(f"autorizacion_{id_pac}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
            os.makedirs('static/autorizaciones_eliminacion', exist_ok=True)
            archivo.save(os.path.join('static/autorizaciones_eliminacion', filename))
            usuario = session.get('usuario')
            ejecutar_consulta(cur, "INSERT INTO autorizaciones_eliminacion (id_paciente, usuario_autoriza, archivo_pdf, motivo) VALUES (?,?,?,?)",
                        (id_pac, usuario, f"autorizaciones_eliminacion/{filename}", motivo))
            ejecutar_consulta(cur, "UPDATE pacientes SET deleted=1 WHERE id=?", (id_pac,))
            conn.commit()
            flash(f'Paciente eliminado con autorización.', 'success')
            return redirect(url_for('laboratorio'))
        elif accion == 'crear_orden':
            id_paciente = request.form.get('id_paciente')
            if not id_paciente:
                flash("Seleccione paciente.", 'danger')
                return redirect(url_for('laboratorio'))
            
            examenes_ids = request.form.getlist('examenes_ids[]')
            examen_manual = request.form.get('examen_manual', '').strip()
            
            if not examenes_ids and not examen_manual:
                flash("Debe agregar al menos un examen.", 'danger')
                return redirect(url_for('laboratorio'))
            
            codigo_muestra = generar_codigo_muestra()
            fecha_validez = date.today()
            fecha_emision = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ordenes_creadas = []
            
            if examenes_ids:
                for id_examen in examenes_ids:
                    id_examen = int(id_examen)
                    ejecutar_consulta(cur, "SELECT precio FROM examenes_catalogo WHERE id=?", (id_examen,))
                    row = cur.fetchone()
                    precio = float(row[0]) if row else 0.0
                    ejecutar_consulta(cur, """INSERT INTO ordenes_laboratorio 
                                   (id_paciente, id_examen, fecha_emision, estado, precio, codigo_muestra, fecha_validez, tipo_orden)
                                   VALUES (?, ?, ?, 'Pendiente', ?, ?, ?, 'examen')""",
                                (id_paciente, id_examen, fecha_emision, precio, codigo_muestra, fecha_validez))
                    ordenes_creadas.append(cur.lastrowid)
            
            if examen_manual:
                precio_manual = float(request.form.get('precio_manual', 0))
                ejecutar_consulta(cur, """INSERT INTO ordenes_laboratorio 
                               (id_paciente, examen_manual, fecha_emision, estado, precio, codigo_muestra, fecha_validez, tipo_orden)
                               VALUES (?, ?, ?, 'Pendiente', ?, ?, ?, 'examen')""",
                            (id_paciente, examen_manual, fecha_emision, precio_manual, codigo_muestra, fecha_validez))
                ordenes_creadas.append(cur.lastrowid)
            
            conn.commit()
            flash(f"{len(ordenes_creadas)} órdenes creadas. Código de muestra: {codigo_muestra}", 'success')
            return redirect(url_for('laboratorio'))

    ejecutar_consulta(cur, "SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes WHERE deleted=0 ORDER BY id DESC")
    pacientes = cur.fetchall()
    ejecutar_consulta(cur, "SELECT id, descripcion, precio FROM examenes_catalogo")
    examenes = cur.fetchall()
    
    sql_pend = """SELECT o.id, p.nombre, p.apellido, COALESCE(e.descripcion, o.examen_manual, o.servicio_manual) AS descripcion,
                  o.estado, o.codigo_muestra, o.fecha_validez,
                  CASE WHEN EXISTS (SELECT 1 FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%')) THEN 'Pagado' ELSE 'Pendiente' END AS estado_pago,
                  (SELECT pg.numero_boleta FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS numero_boleta,
                  (SELECT pg.monto FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS monto_pago
                  FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id LEFT JOIN examenes_catalogo e ON o.id_examen=e.id
                  WHERE o.estado='Pendiente' ORDER BY o.id DESC"""
    if IS_POSTGRES:
        sql_pend = sql_pend.replace('?', '%s')
    ejecutar_consulta(cur, sql_pend)
    pendientes_muestra = cur.fetchall()

    sql_proceso = """SELECT o.id, p.nombre, p.apellido, COALESCE(e.descripcion, o.examen_manual, o.servicio_manual) AS descripcion,
                  o.estado, o.codigo_muestra, o.fecha_validez,
                  CASE WHEN EXISTS (SELECT 1 FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%')) THEN 'Pagado' ELSE 'Pendiente' END AS estado_pago,
                  (SELECT pg.numero_boleta FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS numero_boleta,
                  (SELECT pg.monto FROM pagos pg WHERE pg.id_paciente = o.id_paciente AND pg.estado='Pagado' AND (LOWER(pg.descripcion) LIKE '%' || LOWER(COALESCE(e.descripcion, o.examen_manual, o.servicio_manual)) || '%' OR LOWER(pg.descripcion) LIKE '%laboratorio%' OR LOWER(pg.descripcion) LIKE '%análisis%') ORDER BY pg.id DESC LIMIT 1) AS monto_pago,
                  CASE WHEN EXISTS (SELECT 1 FROM resultados_lab rl WHERE rl.id_orden = o.id) THEN 'Completado' ELSE 'Pendiente' END AS resultado_estado,
                  o.tecnologo_id, (SELECT usuario FROM usuarios WHERE id=o.tecnologo_id) AS tecnologo_nombre,
                  o.fecha_resultado, o.validado
                  FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id LEFT JOIN examenes_catalogo e ON o.id_examen=e.id
                  WHERE o.estado != 'Pendiente' ORDER BY o.id DESC"""
    if IS_POSTGRES:
        sql_proceso = sql_proceso.replace('?', '%s')
    ejecutar_consulta(cur, sql_proceso)
    ordenes_proceso = cur.fetchall()
    conn.close()

    contenido = """
    <h2>🧪 Laboratorio</h2>
    <div class="card p-3 mb-3">
        <div class="d-flex gap-2 flex-wrap"><button onclick="toggleForm('form_paciente_lab')" class="btn btn-success">+ Paciente</button><button onclick="toggleForm('form_orden_lab')" class="btn btn-primary">+ Nueva Orden</button></div>
        <div id="form_paciente_lab" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4>Registrar Paciente</h4>
            <form method="POST"><input type="hidden" name="accion" value="registrar_paciente_lab">
                <div class="row g-2">
                    <div class="col-md-4"><label>DNI</label><input type="text" name="dni" class="form-control" required></div>
                    <div class="col-md-4"><label>Nombre</label><input type="text" name="nombre" class="form-control" required></div>
                    <div class="col-md-4"><label>Apellido</label><input type="text" name="apellido" class="form-control" required></div>
                    <div class="col-md-4"><label>Historia Clínica</label><input type="text" name="historia_clinica" class="form-control" placeholder="Opcional - dejar vacío para auto-generar"></div>
                </div>
                <button class="btn btn-success mt-2">Guardar</button>
            </form>
        </div>
        <div id="form_orden_lab" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4>Nueva Orden de Laboratorio</h4>
            <div class="p-3 bg-light rounded mb-3">
                <div class="row g-2">
                    <div class="col-md-4"><label>Buscar por Boleta Electrónica</label><input type="text" id="buscar_boleta_lab" class="form-control" placeholder="Ingrese número de boleta..."></div>
                    <div class="col-md-2"><button onclick="buscarPacientePorBoleta()" class="btn btn-info">Buscar</button></div>
                    <div class="col-md-2"><a href="{{ url_for('admision') }}" class="btn btn-warning">Registrar</a></div>
                </div>
                <div id="datos_paciente_boleta" class="mt-2 text-muted">Ingrese número de boleta</div>
            </div>
            <div class="p-3 bg-light rounded mb-3">
                <div class="row g-2">
                    <div class="col-md-4"><label>DNI</label><input type="text" id="dni_auto" class="form-control" placeholder="Buscar"></div>
                    <div class="col-md-2"><button onclick="buscarPaciente()" class="btn btn-info">Buscar</button></div>
                    <div class="col-md-2"><a href="{{ url_for('admision') }}" class="btn btn-warning">Registrar</a></div>
                </div>
                <div id="datos_paciente" class="mt-2 text-muted">Ingrese DNI</div>
            </div>
            <form method="POST" id="formOrdenLab">
                <input type="hidden" name="accion" value="crear_orden">
                <input type="hidden" name="id_paciente" id="paciente_id">
                <div class="row g-2">
                    <div class="col-md-6"><label>Paciente</label><input type="text" id="paciente_nombre" class="form-control" disabled></div>
                    <div class="col-md-6"><label>Código de muestra</label><input type="text" id="codigo_muestra_preview" class="form-control" disabled value="(se generará al guardar)"></div>
                </div>
                <div class="mt-3 border p-3 rounded">
                    <h5>Agregar exámenes</h5>
                    <div class="row g-2">
                        <div class="col-md-8">
                            <select id="select_examen" class="form-control">
                                <option value="">-- Seleccione un examen --</option>
                                {% for e in examenes %}
                                <option value="{{ e[0] }}" data-precio="{{ e[2] }}">{{ e[1] }} (S/ {{ e[2] }})</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-2">
                            <button type="button" class="btn btn-primary" onclick="agregarExamen()">Agregar</button>
                        </div>
                        <div class="col-md-2">
                            <button type="button" class="btn btn-warning" onclick="agregarExamenManual()">Manual</button>
                        </div>
                    </div>
                    <div class="mt-2">
                        <input type="text" id="examen_manual_input" class="form-control" placeholder="Nombre del examen manual" style="display:none;">
                        <input type="number" id="precio_manual_input" class="form-control mt-1" placeholder="Precio manual" step="0.01" style="display:none;">
                    </div>
                </div>
                <div class="mt-3">
                    <h5>Exámenes agregados</h5>
                    <table class="table table-sm" id="tabla_examenes_agregados">
                        <thead>
                            <tr><th>Examen</th><th>Precio</th><th>Acción</th></tr>
                        </thead>
                        <tbody id="cuerpo_examenes_agregados">
                            <tr id="fila_vacia"><td colspan="3" class="text-muted">No hay exámenes agregados</td></tr>
                        </tbody>
                        <tfoot>
                            <tr><td><strong>TOTAL</strong></td><td><strong id="total_agregados">S/ 0.00</strong></td><td></td></tr>
                        </tfoot>
                    </table>
                </div>
                <div id="examenes_ids_container"></div>
                <button class="btn btn-success mt-2" onclick="return validarYEnviar()">Crear Orden</button>
                <button type="button" class="btn btn-secondary mt-2" onclick="toggleForm('form_orden_lab')">Cancelar</button>
            </form>
        </div>
        <h4 class="mt-3">Pacientes</h4>
        <table class="table"><thead><tr><th>HC</th><th>DNI</th><th>Nombre</th><th>Apellido</th><th>Acciones</th></tr></thead><tbody>{% for p in pacientes %}<tr><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td>{{ p[4] }}</td><td><button onclick="editarPacienteLab({{ p[0] }},'{{ p[3] }}','{{ p[4] }}','{{ p[2] }}')" class="btn btn-warning btn-sm">✏️</button>{% if paciente_tiene_pagos(p[0]) %}<button class="btn btn-danger btn-sm" onclick="abrirModalEliminar({{ p[0] }})">🗑️</button>{% else %}<form style="display:inline;" method="POST" onsubmit="return confirm('¿Eliminar?')"><input type="hidden" name="accion" value="eliminar_paciente_lab"><input type="hidden" name="id_paciente" value="{{ p[0] }}"><button class="btn btn-danger btn-sm">🗑️</button></form>{% endif %}</td></tr>{% else %}<tr><td colspan="5">Sin pacientes</td></tr>{% endfor %}</tbody></table>
    </div>
    <div id="form_editar_lab" style="display:none;" class="card p-3 mb-3"><h4>Editar</h4><form method="POST"><input type="hidden" name="accion" value="editar_paciente_lab"><input type="hidden" name="id_paciente" id="edit_id_paciente"><div class="row g-2"><div class="col-md-4"><label>Nombre</label><input type="text" name="nombre" id="edit_nombre" class="form-control" required></div><div class="col-md-4"><label>Apellido</label><input type="text" name="apellido" id="edit_apellido" class="form-control" required></div><div class="col-md-4"><label>DNI</label><input type="text" name="dni" id="edit_dni" class="form-control" required></div></div><button class="btn btn-warning mt-2">Guardar</button><button type="button" onclick="document.getElementById('form_editar_lab').style.display='none'" class="btn btn-secondary mt-2">Cancelar</button></form></div>
    
    <div class="modal fade" id="modalEliminarConPDF" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form method="POST" enctype="multipart/form-data"><div class="modal-header"><h5>Eliminar con autorización</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><p>Paciente con pagos. Adjunte PDF de autorización.</p><input type="hidden" name="accion" value="eliminar_paciente_con_pdf"><input type="hidden" name="id_paciente" id="id_paciente_modal"><div class="mb-3"><label>PDF</label><input type="file" name="archivo_pdf" class="form-control" accept=".pdf" required></div><div class="mb-3"><label>Motivo</label><textarea name="motivo" class="form-control" rows="2"></textarea></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button><button type="submit" class="btn btn-danger">Eliminar</button></div></form></div></div></div>
    
    <h3>Pendientes de muestra</h3>
    <table class="table"><thead><tr><th>Código</th><th>Paciente</th><th>Descripción</th><th>Pago</th><th>Boleta</th><th>Monto</th><th>Acciones</th></tr></thead><tbody>{% for p in pendientes_muestra %}<tr><td>{{ p[5] }}</td><td>{{ p[1] }} {{ p[2] }}</td><td>{{ p[3] }}</td><td><span class="badge badge-{{ 'pagado' if p[7]=='Pagado' else 'pendiente' }}">{{ p[7] }}</span></td><td>{{ p[8] or '--' }}</td><td>S/ {{ p[9] or '0.00' }}</td><td><a href="{{ url_for('tomar_muestra', id_orden=p[0]) }}" class="btn btn-primary btn-sm">Tomar</a> <a href="{{ url_for('imprimir_etiqueta', id_orden=p[0]) }}" class="btn btn-warning btn-sm">Etiqueta</a></td></tr>{% else %}<tr><td colspan="7">Sin pendientes</td></tr>{% endfor %}</tbody></table>
    
    <h3>Procesamiento</h3>
    <table class="table"><thead><tr><th>Código</th><th>Paciente</th><th>Descripción</th><th>Estado</th><th>Pago</th><th>Boleta</th><th>Monto</th><th>Resultado</th><th>Tecnólogo</th><th>Fecha Res.</th><th>Validado</th><th>Acciones</th></tr></thead><tbody>{% for o in ordenes_proceso %}<tr><td>{{ o[5] }}</td><td>{{ o[1] }} {{ o[2] }}</td><td>{{ o[3] }}</td><td><span class="badge badge-{{ 'muestra' if o[4]=='Muestra Tomada' else 'pagado' if o[4]=='Completado' else 'pendiente' }}">{{ o[4] }}</span></td><td><span class="badge badge-{{ 'pagado' if o[7]=='Pagado' else 'pendiente' }}">{{ o[7] }}</span></td><td>{{ o[8] or '--' }}</td><td>S/ {{ o[9] or '0.00' }}</td><td>{{ o[10] or 'Pendiente' }}</td><td>{{ o[12] or 'N/A' }}</td><td>{{ o[13] or 'N/A' }}</td><td>{% if o[14]==1 %}✅{% else %}❌{% endif %}</td><td>{% if o[4]=='Muestra Tomada' %}<a href="{{ url_for('ingresar_resultado', id_orden=o[0]) }}" class="btn btn-warning btn-sm">Procesar</a>{% elif o[4]=='Completado' %}<a href="{{ url_for('imprimir_resultado_lab', id_orden=o[0]) }}" class="btn btn-primary btn-sm">PDF</a>{% endif %} <a href="{{ url_for('imprimir_etiqueta', id_orden=o[0]) }}" class="btn btn-info btn-sm">Etiqueta</a></td></tr>{% else %}<tr><td colspan="12">Sin procesos</td></tr>{% endfor %}</tbody></table>
    
    <script>
    function toggleForm(id){var x=document.getElementById(id);x.style.display=x.style.display==='none'?'block':'none';}
    function editarPacienteLab(id,nombre,apellido,dni){document.getElementById('edit_id_paciente').value=id;document.getElementById('edit_nombre').value=nombre;document.getElementById('edit_apellido').value=apellido;document.getElementById('edit_dni').value=dni;document.getElementById('form_editar_lab').style.display='block';}
    function buscarPaciente(){var dni=document.getElementById('dni_auto').value.trim();if(!dni){alert('Ingrese DNI');return;}fetch('/api/paciente_por_dni?dni='+dni).then(r=>r.json()).then(data=>{if(data.error){document.getElementById('datos_paciente').innerHTML='<div class="alert alert-danger">'+data.error+'</div>';document.getElementById('paciente_id').value='';document.getElementById('paciente_nombre').value='';return;}document.getElementById('paciente_id').value=data.id;document.getElementById('paciente_nombre').value=data.nombre+' '+data.apellido+' (HC: '+data.historia_clinica+')';document.getElementById('datos_paciente').innerHTML='<div class="alert alert-success">Paciente encontrado</div>';});}
    function buscarPacientePorBoleta(){var boleta=document.getElementById('buscar_boleta_lab').value.trim();if(!boleta){alert('Ingrese número de boleta');return;}fetch('/api/buscar_por_boleta?boleta='+encodeURIComponent(boleta)).then(r=>r.json()).then(data=>{if(data.error){document.getElementById('datos_paciente_boleta').innerHTML='<div class="alert alert-danger">'+data.error+'</div>';return;}document.getElementById('paciente_id').value=data.id_paciente;document.getElementById('paciente_nombre').value=data.nombre+' '+data.apellido+' (HC: '+data.historia_clinica+')';document.getElementById('datos_paciente_boleta').innerHTML='<div class="alert alert-success">Paciente: '+data.nombre+' '+data.apellido+'<br>Servicio: '+data.servicio_nombre+'<br>Monto: S/ '+data.monto.toFixed(2)+'</div>';});}
    function abrirModalEliminar(id){document.getElementById('id_paciente_modal').value=id;var modal=new bootstrap.Modal(document.getElementById('modalEliminarConPDF'));modal.show();}
    
    var examenesAgregados = [];
    function agregarExamen() {
        var select = document.getElementById('select_examen');
        var option = select.options[select.selectedIndex];
        if (!option.value) { alert('Seleccione un examen válido.'); return; }
        var id = parseInt(option.value);
        var nombre = option.text.split(' (S/')[0].trim();
        var precio = parseFloat(option.getAttribute('data-precio'));
        if (examenesAgregados.some(e => e.id === id)) {
            alert('Este examen ya fue agregado.');
            return;
        }
        examenesAgregados.push({ id: id, nombre: nombre, precio: precio });
        actualizarTabla();
    }
    function agregarExamenManual() {
        var inputNom = document.getElementById('examen_manual_input');
        var inputPrecio = document.getElementById('precio_manual_input');
        if (inputNom.style.display === 'none') {
            inputNom.style.display = 'block';
            inputPrecio.style.display = 'block';
            return;
        }
        var nombre = inputNom.value.trim();
        var precio = parseFloat(inputPrecio.value);
        if (!nombre || isNaN(precio) || precio <= 0) {
            alert('Ingrese un nombre válido y un precio mayor a 0.');
            return;
        }
        var id = -Date.now();
        examenesAgregados.push({ id: id, nombre: nombre, precio: precio, manual: true });
        inputNom.value = '';
        inputPrecio.value = '';
        inputNom.style.display = 'none';
        inputPrecio.style.display = 'none';
        actualizarTabla();
    }
    function eliminarExamen(index) {
        examenesAgregados.splice(index, 1);
        actualizarTabla();
    }
    function actualizarTabla() {
        var tbody = document.getElementById('cuerpo_examenes_agregados');
        var total = 0;
        var html = '';
        if (examenesAgregados.length === 0) {
            html = '<tr id="fila_vacia"><td colspan="3" class="text-muted">No hay exámenes agregados</td></tr>';
        } else {
            examenesAgregados.forEach((ex, idx) => {
                total += ex.precio;
                html += `<tr>
                            <td>${ex.nombre} ${ex.manual ? '(Manual)' : ''}</td>
                            <td>S/ ${ex.precio.toFixed(2)}</td>
                            <td><button type="button" class="btn btn-danger btn-sm" onclick="eliminarExamen(${idx})">✖</button></td>
                        </tr>`;
            });
        }
        tbody.innerHTML = html;
        document.getElementById('total_agregados').textContent = 'S/ ' + total.toFixed(2);
    }
    function validarYEnviar() {
        var pacienteId = document.getElementById('paciente_id').value;
        if (!pacienteId) {
            alert('Primero debe buscar y seleccionar un paciente.');
            return false;
        }
        if (examenesAgregados.length === 0) {
            alert('Debe agregar al menos un examen.');
            return false;
        }
        var container = document.getElementById('examenes_ids_container');
        container.innerHTML = '';
        examenesAgregados.forEach(function(ex) {
            if (!ex.manual) {
                var input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'examenes_ids[]';
                input.value = ex.id;
                container.appendChild(input);
            } else {
                var inputNom = document.createElement('input');
                inputNom.type = 'hidden';
                inputNom.name = 'examen_manual';
                inputNom.value = ex.nombre;
                container.appendChild(inputNom);
                var inputPrecio = document.createElement('input');
                inputPrecio.type = 'hidden';
                inputPrecio.name = 'precio_manual';
                inputPrecio.value = ex.precio;
                container.appendChild(inputPrecio);
            }
        });
        return true;
    }
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  pacientes=pacientes, examenes=examenes,
                                  pendientes_muestra=pendientes_muestra, ordenes_proceso=ordenes_proceso)

@app.route('/laboratorio/tomar_muestra/<int:id_orden>')
def tomar_muestra(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "UPDATE ordenes_laboratorio SET estado='Muestra Tomada' WHERE id=?", (id_orden,))
    conn.commit()
    conn.close()
    return redirect(url_for('laboratorio'))

@app.route('/laboratorio/resultado/<int:id_orden>', methods=['GET','POST'])
def ingresar_resultado(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    ejecutar_consulta(cur, """SELECT o.id, p.nombre, p.apellido, p.dni, p.edad, p.sexo, 
                                    COALESCE(e.descripcion, o.examen_manual, o.servicio_manual) AS examen,
                                    o.id_examen, o.examen_manual, o.servicio_manual, o.estado
                             FROM ordenes_laboratorio o 
                             JOIN pacientes p ON o.id_paciente=p.id 
                             LEFT JOIN examenes_catalogo e ON o.id_examen=e.id 
                             WHERE o.id=?""", (id_orden,))
    orden = cur.fetchone()
    if not orden:
        conn.close()
        flash('Orden no encontrada', 'danger')
        return redirect(url_for('laboratorio'))
    
    ejecutar_consulta(cur, "SELECT id, nombre FROM secciones_parametros ORDER BY orden")
    lista_secciones = cur.fetchall()
    
    parametros = []
    resultados_existentes = {}
    if orden[7]:
        ejecutar_consulta(cur, """SELECT ep.id, ep.nombre_parametro, ep.unidad, ep.rango_referencia, ep.id_seccion, ep.orden
                                  FROM examenes_parametros ep
                                  WHERE ep.id_examen_catalogo=?
                                  ORDER BY COALESCE(ep.id_seccion, 0), ep.orden""", (orden[7],))
        parametros = cur.fetchall()
        ejecutar_consulta(cur, "SELECT id_parametro, resultado FROM resultados_lab WHERE id_orden=?", (id_orden,))
        resultados_existentes = {r[0]: r[1] for r in cur.fetchall()}
    
    ejecutar_consulta(cur, "SELECT id, nombre_analisis, resultado, rango_referencia, id_seccion FROM parametros_extra_orden WHERE id_orden=?", (id_orden,))
    parametros_extra = cur.fetchall()
    conn.close()
    
    if request.method == 'POST':
        conn = get_db_connection()
        cur = conn.cursor()
        
        for p in parametros:
            id_param = p[0]
            resultado = request.form.get(f'param_{id_param}', '').strip()
            if resultado:
                ejecutar_consulta(cur, """INSERT INTO resultados_lab (id_orden, id_parametro, resultado)
                                          VALUES (?,?,?) ON CONFLICT (id_orden, id_parametro) 
                                          DO UPDATE SET resultado=excluded.resultado""", 
                                 (id_orden, id_param, resultado))
        
        analisis_list = request.form.getlist('extra_analisis[]')
        resultado_list = request.form.getlist('extra_resultado[]')
        rango_list = request.form.getlist('extra_rango[]')
        seccion_list = request.form.getlist('extra_seccion[]')
        extra_ids = request.form.getlist('extra_id[]')
        eliminar_ids = request.form.getlist('eliminar_extra[]')
        
        for eid in eliminar_ids:
            if eid:
                ejecutar_consulta(cur, "DELETE FROM parametros_extra_orden WHERE id=? AND id_orden=?", (eid, id_orden))
        
        for i in range(len(analisis_list)):
            analisis = analisis_list[i].strip()
            if not analisis:
                continue
            resultado = resultado_list[i].strip() if i < len(resultado_list) else ''
            rango = rango_list[i].strip() if i < len(rango_list) else ''
            seccion = seccion_list[i] if i < len(seccion_list) and seccion_list[i] else None
            extra_id = extra_ids[i] if i < len(extra_ids) else ''
            
            if extra_id and extra_id.isdigit():
                ejecutar_consulta(cur, """UPDATE parametros_extra_orden 
                                          SET nombre_analisis=?, resultado=?, rango_referencia=?, id_seccion=?
                                          WHERE id=? AND id_orden=?""",
                                 (analisis, resultado, rango, seccion, extra_id, id_orden))
            else:
                ejecutar_consulta(cur, """INSERT INTO parametros_extra_orden 
                                          (id_orden, nombre_analisis, resultado, rango_referencia, id_seccion)
                                          VALUES (?,?,?,?,?)""",
                                 (id_orden, analisis, resultado, rango, seccion if seccion else None))
        
        ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        validado = 1 if session.get('rol') == 'tecnologo' else 0
        ejecutar_consulta(cur, """UPDATE ordenes_laboratorio 
                                  SET estado='Completado', tecnologo_id=?, fecha_resultado=?, validado=?
                                  WHERE id=?""",
                         (session.get('id_usuario'), ahora, validado, id_orden))
        
        conn.commit()
        conn.close()
        flash('Resultados guardados correctamente.', 'success')
        return redirect(url_for('laboratorio'))
    
    contenido = """
    <h2>Procesar Orden #{{ id_orden }}</h2>
    <div class="bg-light p-3 mb-3">
        <b>Paciente:</b> {{ orden[1] }} {{ orden[2] }}<br>
        <b>DNI:</b> {{ orden[3] }} | <b>Edad:</b> {{ orden[4] or 'N/E' }} | <b>Sexo:</b> {{ orden[5] or 'N/E' }}<br>
        <b>Examen:</b> {{ orden[6] }}
    </div>
    
    <form method="POST" id="formResultados">
        {% if parametros %}
            {% set secciones_dict = {} %}
            {% for p in parametros %}
                {% set id_sec = p[4] %}
                {% if id_sec not in secciones_dict %}
                    {% set _ = secciones_dict.update({id_sec: []}) %}
                {% endif %}
                {% set _ = secciones_dict[id_sec].append(p) %}
            {% endfor %}
            
            {% for id_sec, params in secciones_dict.items() %}
                <div class="card mb-3">
                    <div class="card-header bg-primary text-white">
                        <strong>{{ lista_secciones|selectattr('0', 'equalto', id_sec)|map(attribute='1')|first or 'Sin sección' }}</strong>
                    </div>
                    <div class="card-body">
                        <table class="table table-bordered">
                            <thead>
                                <tr><th>ANÁLISIS</th><th>RESULTADO</th><th>RANGO DE REFERENCIA</th></tr>
                            </thead>
                            <tbody>
                                {% for p in params %}
                                    <tr>
                                        <td>{{ p[1] }} {{ p[2] or '' }}</td>
                                        <td><input type="text" name="param_{{ p[0] }}" class="form-control" value="{{ resultados_existentes.get(p[0], '') }}"></td>
                                        <td>{{ p[3] or '' }}</td>
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <div class="alert alert-info">Este examen no tiene parámetros predefinidos. Use la sección "Parámetros adicionales" para agregar filas manualmente.</div>
        {% endif %}
        
        <div class="card mt-3">
            <div class="card-header bg-success text-white d-flex justify-content-between">
                <strong>Parámetros adicionales</strong>
                <button type="button" class="btn btn-light btn-sm" onclick="agregarFilaExtra()">+ Agregar fila</button>
            </div>
            <div class="card-body">
                <table class="table table-bordered" id="tabla_extra">
                    <thead>
                        <tr><th>ANÁLISIS</th><th>RESULTADO</th><th>RANGO DE REFERENCIA</th><th>SECCIÓN</th><th></th></tr>
                    </thead>
                    <tbody id="cuerpo_extra">
                        {% for ex in parametros_extra %}
                            <tr id="extra_{{ ex[0] }}">
                                <td><input type="text" name="extra_analisis[]" class="form-control" value="{{ ex[1] }}"></td>
                                <td><input type="text" name="extra_resultado[]" class="form-control" value="{{ ex[2] or '' }}"></td>
                                <td><input type="text" name="extra_rango[]" class="form-control" value="{{ ex[3] or '' }}"></td>
                                <td>
                                    <select name="extra_seccion[]" class="form-control">
                                        <option value="">Sin sección</option>
                                        {% for s in lista_secciones %}
                                            <option value="{{ s[0] }}" {% if s[0] == ex[4] %}selected{% endif %}>{{ s[1] }}</option>
                                        {% endfor %}
                                    </select>
                                </td>
                                <td>
                                    <input type="hidden" name="extra_id[]" value="{{ ex[0] }}">
                                    <button type="button" class="btn btn-danger btn-sm" onclick="eliminarFilaExtra(this)">✖</button>
                                </td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="mt-3">
            <button class="btn btn-success" onclick="return validarFormulario()">Guardar Resultados</button>
            <a href="{{ url_for('laboratorio') }}" class="btn btn-danger">Cancelar</a>
        </div>
    </form>
    
    <script>
        function agregarFilaExtra() {
            var tbody = document.getElementById('cuerpo_extra');
            var fila = document.createElement('tr');
            fila.innerHTML = `
                <td><input type="text" name="extra_analisis[]" class="form-control" required></td>
                <td><input type="text" name="extra_resultado[]" class="form-control"></td>
                <td><input type="text" name="extra_rango[]" class="form-control"></td>
                <td>
                    <select name="extra_seccion[]" class="form-control">
                        <option value="">Sin sección</option>
                        {% for s in lista_secciones %}
                            <option value="{{ s[0] }}">{{ s[1] }}</option>
                        {% endfor %}
                    </select>
                </td>
                <td>
                    <input type="hidden" name="extra_id[]" value="">
                    <button type="button" class="btn btn-danger btn-sm" onclick="eliminarFilaExtra(this)">✖</button>
                </td>
            `;
            tbody.appendChild(fila);
        }
        
        function eliminarFilaExtra(btn) {
            var tr = btn.closest('tr');
            var hiddenId = tr.querySelector('input[name="extra_id[]"]');
            if (hiddenId && hiddenId.value) {
                var inputEliminar = document.createElement('input');
                inputEliminar.type = 'hidden';
                inputEliminar.name = 'eliminar_extra[]';
                inputEliminar.value = hiddenId.value;
                document.getElementById('formResultados').appendChild(inputEliminar);
            }
            tr.remove();
        }
        
        function validarFormulario() {
            return true;
        }
    </script>
    """
    
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  id_orden=id_orden, orden=orden, parametros=parametros, 
                                  resultados_existentes=resultados_existentes,
                                  parametros_extra=parametros_extra, lista_secciones=lista_secciones)

@app.route('/laboratorio/imprimir/<int:id_orden>')
def imprimir_resultado_lab(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    ejecutar_consulta(cur, """SELECT p.nombre, p.apellido, p.dni, p.edad, p.sexo, p.historia_clinica,
                                    COALESCE(e.descripcion, o.examen_manual, o.servicio_manual) AS examen,
                                    o.fecha_emision, o.fecha_resultado, o.validado, 
                                    u.usuario AS tecnologo_nombre
                             FROM ordenes_laboratorio o 
                             JOIN pacientes p ON o.id_paciente=p.id 
                             LEFT JOIN examenes_catalogo e ON o.id_examen=e.id 
                             LEFT JOIN usuarios u ON o.tecnologo_id=u.id
                             WHERE o.id=?""", (id_orden,))
    orden_data = cur.fetchone()
    if not orden_data:
        conn.close()
        return "Orden no encontrada", 404
    
    ejecutar_consulta(cur, """SELECT ep.nombre_parametro, ep.unidad, ep.rango_referencia, rl.resultado, s.nombre AS seccion
                              FROM resultados_lab rl
                              LEFT JOIN examenes_parametros ep ON rl.id_parametro=ep.id
                              LEFT JOIN secciones_parametros s ON ep.id_seccion = s.id
                              WHERE rl.id_orden=?
                              ORDER BY COALESCE(s.orden, 999), ep.orden""", (id_orden,))
    resultados = cur.fetchall()
    
    ejecutar_consulta(cur, """SELECT nombre_analisis, resultado, rango_referencia, s.nombre AS seccion
                              FROM parametros_extra_orden pe
                              LEFT JOIN secciones_parametros s ON pe.id_seccion = s.id
                              WHERE pe.id_orden=?
                              ORDER BY COALESCE(s.orden, 999), pe.id""", (id_orden,))
    extra = cur.fetchall()
    conn.close()
    
    items = []
    for r in resultados:
        items.append({
            'analisis': r[0] + (f" ({r[1]})" if r[1] else ''),
            'resultado': r[3] or '',
            'rango': r[2] or '',
            'seccion': r[4] or 'Sin sección'
        })
    for e in extra:
        items.append({
            'analisis': e[0],
            'resultado': e[1] or '',
            'rango': e[2] or '',
            'seccion': e[3] or 'Sin sección'
        })
    
    secciones = {}
    for item in items:
        sec = item['seccion']
        if sec not in secciones:
            secciones[sec] = []
        secciones[sec].append(item)
    
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    logo_path = config[2] if config else ''
    sello_path = config[7] if len(config) > 7 and config[7] else ''
    pie = config[4] if config else ''
    size = obtener_tamano_pagina('result')
    
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    w, h = size
    y = h - 50
    
    if logo_path and os.path.exists(os.path.join('static', logo_path)):
        try:
            c.drawImage(os.path.join('static', logo_path), 50, y-40, width=80, height=50, preserveAspectRatio=True)
        except:
            pass
    c.setFont("Helvetica-Bold", 16)
    c.drawString(150, y, nombre_sistema)
    c.setFont("Helvetica", 10)
    c.drawString(150, y-18, "INFORME DE RESULTADOS DE LABORATORIO")
    y -= 50
    
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Paciente:")
    c.setFont("Helvetica", 10)
    c.drawString(120, y, f"{orden_data[0]} {orden_data[1]}")
    y -= 18
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "DNI:")
    c.drawString(120, y, orden_data[2])
    c.drawString(250, y, "HC:")
    c.drawString(280, y, orden_data[5] or 'N/E')
    y -= 18
    c.drawString(50, y, "Edad:")
    c.drawString(120, y, f"{orden_data[3] or 'N/E'} años")
    c.drawString(250, y, "Sexo:")
    c.drawString(280, y, orden_data[4] or 'N/E')
    y -= 18
    c.drawString(50, y, "Examen:")
    c.drawString(120, y, orden_data[6])
    y -= 18
    c.drawString(50, y, "F. Emisión:")
    c.drawString(120, y, orden_data[7] or '')
    c.drawString(250, y, "F. Resultado:")
    c.drawString(320, y, orden_data[8] or '')
    y -= 30
    
    for sec, items in secciones.items():
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(colors.HexColor("#1C9CD4"))
        c.drawString(50, y, sec)
        c.setFillColor(colors.black)
        y -= 20
        
        table_data = [["ANÁLISIS", "RESULTADO", "RANGO DE REFERENCIA"]]
        for item in items:
            table_data.append([item['analisis'], item['resultado'], item['rango']])
        
        t = Table(table_data, colWidths=[200, 150, 200])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1C9CD4")),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 10),
            ('BOTTOMPADDING', (0,0), (-1,0), 8),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('FONTNAME', (0,1), (-1,-1), 'Helvetica'),
            ('FONTSIZE', (0,1), (-1,-1), 9),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        t.wrapOn(c, w-100, h)
        t.drawOn(c, 50, y - 20 - len(table_data)*20)
        y = y - 20 - len(table_data)*20 - 30
        
        if y < 100:
            c.showPage()
            y = h - 50
            c.setFont("Helvetica-Bold", 16)
            c.drawString(150, y, nombre_sistema)
            c.setFont("Helvetica", 10)
            c.drawString(150, y-18, "INFORME DE RESULTADOS DE LABORATORIO")
            y -= 50
    
    if sello_path and os.path.exists(os.path.join('static', sello_path)):
        try:
            c.drawImage(os.path.join('static', sello_path), w-150, y-80, width=100, height=80, preserveAspectRatio=True)
        except:
            pass
    
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.green if orden_data[9] else colors.red)
    c.drawString(50, y-40, "VALIDADO" if orden_data[9] else "NO VALIDADO")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    c.drawString(50, y-60, f"Responsable: {orden_data[10] or 'No asignado'}")
    c.drawString(50, y-80, f"Fecha: {orden_data[8] or 'N/A'}")
    
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.grey)
    c.drawString(30, 30, pie)
    c.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Resultado_Lab_{id_orden}.pdf", mimetype='application/pdf')

@app.route('/laboratorio/imprimir_etiqueta/<int:id_orden>')
def imprimir_etiqueta(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT o.codigo_muestra, o.fecha_validez, p.nombre, p.apellido, p.historia_clinica FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente=p.id WHERE o.id=?", (id_orden,))
    orden = cur.fetchone()
    conn.close()
    if not orden: return "Orden no encontrada", 404
    codigo = orden[0]
    if not codigo: return "Sin código de muestra", 400
    barcode_buffer = generar_codigo_barras(codigo)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w,h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, h-50, "ETIQUETA DE MUESTRA")
    c.setFont("Helvetica", 12)
    c.drawString(50, h-80, f"Paciente: {orden[2]} {orden[3]}")
    c.drawString(50, h-100, f"Historia: {orden[4]}")
    c.drawString(50, h-120, f"Código: {codigo}")
    c.drawString(50, h-140, f"Validez: {orden[1]}")
    img = ImageReader(barcode_buffer)
    c.drawImage(img, 50, h-300, width=300, height=80, preserveAspectRatio=True)
    c.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"etiqueta_{codigo}.pdf", mimetype='application/pdf')

# ========================== ATENCIÓN MÉDICA ==========================
@app.route('/atencion_medica')
def atencion_medica():
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    q = request.args.get('q', '')
    conn = get_db_connection()
    cur = conn.cursor()
    sql = """SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, d.id as d_id, d.informe_pdf_path, p.historia_clinica, c.numero_boleta
             FROM citas c JOIN pacientes p ON c.id_paciente = p.id JOIN servicios s ON c.id_servicio = s.id LEFT JOIN diagnosticos d ON d.id_cita = c.id
             WHERE c.estado = 'Pagado' AND p.deleted=0"""
    params = []
    if q:
        sql += " AND (p.nombre LIKE ? OR p.apellido LIKE ? OR p.dni LIKE ? OR c.id LIKE ? OR p.historia_clinica LIKE ? OR c.numero_boleta LIKE ?)"
        params = [f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%']
    sql += " ORDER BY c.fecha_cita DESC"
    ejecutar_consulta(cur, sql, params)
    citas = cur.fetchall()
    conn.close()
    contenido = """
    <h2>🩺 Atención Médica</h2>
    <div class="card p-3 mb-3"><form method="GET" class="row g-2"><div class="col-md-8"><input type="text" name="q" value="{{ q }}" class="form-control" placeholder="Buscar..."></div><div class="col-md-2"><button class="btn btn-primary w-100">Buscar</button></div><div class="col-md-2"><a href="{{ url_for('atencion_medica') }}" class="btn btn-warning w-100">Limpiar</a></div></form></div>
    <table class="table"><thead><tr><th>HC</th><th>Boleta</th><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Estado</th><th>Informe</th><th>Acciones</th></tr></thead><tbody>{% for c in citas %}<tr><td>{{ c[8] }}</td><td>{{ c[9] or '--' }}</td><td>{{ c[1] }} {{ c[2] }}</td><td>{{ c[3] }}</td><td>{{ c[4] }}</td><td><span class="badge badge-pagado">{{ c[5] }}</span></td><td>{% if c[6] %}<a href="{{ url_for('ver_informe', id_cita=c[0]) }}" class="btn btn-primary btn-sm">Ver</a> <a href="{{ url_for('exportar_informe', id_cita=c[0]) }}" class="btn btn-success btn-sm">PDF</a>{% else %}<span class="text-muted">Sin informe</span>{% endif %}</td><td>{% if c[6] %}<a href="{{ url_for('editar_informe', id_cita=c[0]) }}" class="btn btn-warning btn-sm">✏️</a> <a href="{{ url_for('nueva_receta', id_cita=c[0]) }}" class="btn btn-info btn-sm">📝</a>{% else %}<a href="{{ url_for('atender_cita', id_cita=c[0]) }}" class="btn btn-primary btn-sm">Atender</a>{% endif %}</td></tr>{% else %}<tr><td colspan="8">No hay citas pagadas.</td></tr>{% endfor %}</tbody></table>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), citas=citas, q=q)

@app.route('/atencion_medica/atender/<int:id_cita>', methods=['GET','POST'])
def atender_cita(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        diag = request.form['diagnostico']
        trat = request.form['tratamiento']
        desc = int(request.form['descanso'])
        ejecutar_consulta(cur, "SELECT id_medico FROM citas WHERE id=?", (id_cita,))
        id_med = cur.fetchone()[0]
        ejecutar_consulta(cur, "INSERT INTO diagnosticos (id_cita, id_medico, diagnostico, tratamiento, descanso_medico_dias, informe_pdf_path) VALUES (?,?,?,?,?,?)",
                    (id_cita, id_med, diag, trat, desc, ''))
        conn.commit()
        generar_y_guardar_informe_pdf(id_cita)
        conn.close()
        flash("Atención registrada. Cree la receta.", 'success')
        return redirect(url_for('nueva_receta', id_cita=id_cita))
    ejecutar_consulta(cur, "SELECT p.nombre, p.apellido, p.dni, s.nombre, c.fecha_cita FROM citas c JOIN pacientes p ON c.id_paciente=p.id JOIN servicios s ON c.id_servicio=s.id WHERE c.id=?", (id_cita,))
    cita = cur.fetchone()
    ejecutar_consulta(cur, """SELECT e.descripcion, CASE WHEN EXISTS (SELECT 1 FROM resultados_lab rl WHERE rl.id_orden=o.id) THEN 'Con resultados' ELSE 'Pendiente' END AS res, o.id, o.estado
                   FROM ordenes_laboratorio o JOIN examenes_catalogo e ON o.id_examen=e.id WHERE o.id_cita=?""", (id_cita,))
    lab_results = cur.fetchall()
    conn.close()
    contenido = """
    <h2>Atender Cita #{{ id_cita }}</h2>
    <div class="bg-light p-3"><b>{{ cita[0] }} {{ cita[1] }}</b> (DNI: {{ cita[2] }})<br><b>Servicio:</b> {{ cita[3] }}<br><b>Fecha:</b> {{ cita[4] }}</div>
    <h3>Resultados Lab</h3><table class="table">{% for lab in lab_results %}<tr><td>{{ lab[0] }}</td><td>{{ lab[1] }}</td></tr>{% else %}<tr><td>Sin exámenes</td></tr>{% endfor %}</table>
    <form method="POST"><div class="row g-2"><div class="col-md-6"><label>Diagnóstico</label><textarea name="diagnostico" class="form-control" rows="3" required></textarea></div><div class="col-md-6"><label>Tratamiento</label><textarea name="tratamiento" class="form-control" rows="3" required></textarea></div><div class="col-md-3"><label>Días descanso</label><input type="number" name="descanso" value="0" class="form-control"></div></div><button class="btn btn-success mt-2">Guardar y Receta</button><a href="{{ url_for('atencion_medica') }}" class="btn btn-danger">Cancelar</a></form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  id_cita=id_cita, cita=cita, lab_results=lab_results)

def generar_y_guardar_informe_pdf(id_cita):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        ejecutar_consulta(cur, """SELECT p.nombre, p.apellido, p.dni, s.nombre, m.nombre||' '||m.apellido, c.fecha_cita
                       FROM citas c JOIN pacientes p ON c.id_paciente=p.id JOIN servicios s ON c.id_servicio=s.id JOIN medicos m ON c.id_medico=m.id WHERE c.id=?""", (id_cita,))
        cita = cur.fetchone()
        ejecutar_consulta(cur, "SELECT diagnostico, tratamiento, descanso_medico_dias FROM diagnosticos WHERE id_cita=?", (id_cita,))
        diag = cur.fetchone()
        conn.close()
        if not cita or not diag: return
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        logo_path = config[2] if config else ''
        header = config[5] if config else 'INFORME DE ATENCIÓN CLÍNICA'
        footer = config[6] if config else ''
        size = obtener_tamano_pagina('report')
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=size, topMargin=30, bottomMargin=30)
        styles = getSampleStyleSheet()
        elems=[]
        if logo_path and os.path.exists(os.path.join('static', logo_path)):
            try: img=ImageReader(os.path.join('static', logo_path)); elems.append(Spacer(1,10))
            except: pass
        title=ParagraphStyle(name='Title', parent=styles['Heading1'], fontSize=14, alignment=1, spaceAfter=20)
        elems.append(Paragraph(f"<b>{nombre_sistema}</b>", title))
        elems.append(Paragraph(f"<i>{header}</i>", styles['Heading2']))
        elems.append(Spacer(1,15))
        info=f"<b>Paciente:</b> {cita[0]} {cita[1]}<br/><b>DNI:</b> {cita[2]}<br/><b>Servicio:</b> {cita[3]}<br/><b>Médico:</b> Dr. {cita[4]}<br/><b>Fecha:</b> {cita[5]}"
        elems.append(Paragraph(info, styles['Normal']))
        elems.append(Spacer(1,15))
        elems.append(Paragraph("<b>Diagnóstico:</b>", styles['Normal']))
        elems.append(Paragraph(diag[0] or "No especificado", styles['Normal']))
        elems.append(Spacer(1,10))
        elems.append(Paragraph("<b>Tratamiento:</b>", styles['Normal']))
        elems.append(Paragraph(diag[1] or "No especificado", styles['Normal']))
        elems.append(Spacer(1,10))
        elems.append(Paragraph(f"<b>Descanso:</b> {diag[2]} días", styles['Normal']))
        elems.append(Spacer(1,30))
        elems.append(Paragraph(footer, styles['Italic']))
        doc.build(elems)
        os.makedirs('static/informes_medicos', exist_ok=True)
        filename=f"informe_{id_cita}.pdf"
        with open(os.path.join('static/informes_medicos', filename), 'wb') as f: f.write(buf.getvalue())
        conn=get_db_connection(); cur=conn.cursor()
        ejecutar_consulta(cur, "UPDATE diagnosticos SET informe_pdf_path=? WHERE id_cita=?", (f"informes_medicos/{filename}", id_cita))
        conn.commit(); conn.close()
    except Exception as e:
        app.logger.error(f"Error en informe: {e}")

@app.route('/atencion_medica/editar_informe/<int:id_cita>', methods=['GET','POST'])
def editar_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'POST':
        diag = request.form['diagnostico']
        trat = request.form['tratamiento']
        desc = int(request.form['descanso'])
        ejecutar_consulta(cur, "UPDATE diagnosticos SET diagnostico=?, tratamiento=?, descanso_medico_dias=? WHERE id_cita=?", (diag, trat, desc, id_cita))
        conn.commit()
        conn.close()
        generar_y_guardar_informe_pdf(id_cita)
        flash("Informe actualizado.", 'success')
        return redirect(url_for('atencion_medica'))
    ejecutar_consulta(cur, "SELECT diagnostico, tratamiento, descanso_medico_dias FROM diagnosticos WHERE id_cita=?", (id_cita,))
    data = cur.fetchone()
    conn.close()
    if not data: return "Informe no encontrado", 404
    contenido = f"""
    <h2>Editar Informe - Cita #{id_cita}</h2>
    <form method="POST"><div class="row g-2"><div class="col-md-6"><label>Diagnóstico</label><textarea name="diagnostico" class="form-control" rows="3" required>{data[0]}</textarea></div><div class="col-md-6"><label>Tratamiento</label><textarea name="tratamiento" class="form-control" rows="3" required>{data[1]}</textarea></div><div class="col-md-3"><label>Días descanso</label><input type="number" name="descanso" value="{data[2]}" class="form-control"></div></div><button class="btn btn-success mt-2">Guardar</button><a href="{{ url_for('atencion_medica') }}" class="btn btn-secondary">Cancelar</a></form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')))

@app.route('/atencion_medica/ver_informe/<int:id_cita>')
def ver_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT informe_pdf_path FROM diagnosticos WHERE id_cita=?", (id_cita,))
    res = cur.fetchone()
    conn.close()
    if res and res[0]:
        return send_file(os.path.join('static', res[0]), mimetype='application/pdf')
    return "Informe no encontrado", 404

@app.route('/atencion_medica/exportar_informe/<int:id_cita>')
def exportar_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT informe_pdf_path FROM diagnosticos WHERE id_cita=?", (id_cita,))
    res = cur.fetchone()
    conn.close()
    if res and res[0]:
        return send_file(os.path.join('static', res[0]), as_attachment=True, download_name=f"Informe_{id_cita}.pdf", mimetype='application/pdf')
    return "Informe no encontrado", 404

# ========================== RECETAS ==========================
@app.route('/recetas')
def recetas():
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    q = request.args.get('q', '')
    conn = get_db_connection()
    cur = conn.cursor()
    sql = """SELECT r.id, r.numero_cuenta, r.fecha_emision, r.estado, p.nombre||' '||p.apellido AS paciente, m.nombre||' '||m.apellido AS medico
             FROM recetas r JOIN pacientes p ON r.id_paciente = p.id JOIN medicos m ON r.id_medico = m.id WHERE p.deleted=0"""
    params=[]
    if q:
        sql += " AND (p.nombre LIKE ? OR p.apellido LIKE ? OR r.numero_cuenta LIKE ?)"
        params = [f'%{q}%', f'%{q}%', f'%{q}%']
    sql += " ORDER BY r.fecha_emision DESC"
    ejecutar_consulta(cur, sql, params)
    recetas = cur.fetchall()
    conn.close()
    contenido = """
    <h2>📝 Recetas</h2>
    <div class="card p-3 mb-3"><form method="GET" class="row g-2"><div class="col-md-8"><input type="text" name="q" value="{{ q }}" class="form-control" placeholder="Buscar..."></div><div class="col-md-2"><button class="btn btn-primary w-100">Buscar</button></div><div class="col-md-2"><a href="{{ url_for('recetas') }}" class="btn btn-warning w-100">Limpiar</a></div></form></div>
    <table class="table"><thead><tr><th>Nº Cuenta</th><th>Paciente</th><th>Médico</th><th>Fecha</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{% for r in recetas %}<tr><td>{{ r[1] }}</td><td>{{ r[4] }}</td><td>{{ r[5] }}</td><td>{{ r[2] }}</td><td><span class="badge bg-{{ 'success' if r[3]=='activa' else 'danger' }}">{{ r[3] }}</span></td><td><a href="{{ url_for('ver_receta', id_receta=r[0]) }}" class="btn btn-primary btn-sm">Ver</a> <a href="{{ url_for('imprimir_receta_pdf', id_receta=r[0]) }}" class="btn btn-warning btn-sm">PDF</a></td></tr>{% else %}<tr><td colspan="6">Sin recetas</td></tr>{% endfor %}</tbody></table>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), recetas=recetas, q=q)

@app.route('/recetas/nueva/<int:id_cita>', methods=['GET','POST'])
def nueva_receta(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, """SELECT c.id_paciente, p.nombre, p.apellido, p.nro_afiliacion, s.nombre, m.nombre||' '||m.apellido, c.fecha_cita
                   FROM citas c JOIN pacientes p ON c.id_paciente=p.id JOIN servicios s ON c.id_servicio=s.id JOIN medicos m ON c.id_medico=m.id WHERE c.id=?""", (id_cita,))
    cita = cur.fetchone()
    if not cita:
        conn.close()
        flash('Cita no encontrada', 'danger')
        return redirect(url_for('atencion_medica'))
    ejecutar_consulta(cur, "SELECT id, codigo, nombre, precio FROM procedimientos WHERE activo=1 ORDER BY nombre")
    procedimientos = cur.fetchall()
    if request.method == 'POST':
        num_cuenta = request.form['numero_cuenta']
        diag = request.form['diagnostico']
        ind = request.form['indicaciones']
        ejecutar_consulta(cur, "SELECT id_medico FROM citas WHERE id=?", (id_cita,))
        id_med = cur.fetchone()[0]
        ejecutar_consulta(cur, "INSERT INTO recetas (id_cita, id_paciente, id_medico, numero_cuenta, diagnostico, indicaciones) VALUES (?,?,?,?,?,?)",
                    (id_cita, cita[0], id_med, num_cuenta, diag, ind))
        id_rec = cur.lastrowid
        ids_proc = request.form.getlist('procedimiento_id[]')
        cant = request.form.getlist('cantidad[]')
        obs = request.form.getlist('observacion[]')
        for i, id_proc in enumerate(ids_proc):
            if id_proc and int(id_proc)>0:
                ejecutar_consulta(cur, "SELECT precio FROM procedimientos WHERE id=?", (id_proc,))
                prec = cur.fetchone()
                pu = float(prec[0]) if prec else 0
                ejecutar_consulta(cur, "INSERT INTO receta_detalle (id_receta, id_procedimiento, cantidad, precio_unitario, observaciones) VALUES (?,?,?,?,?)",
                            (id_rec, id_proc, int(cant[i]), pu, obs[i]))
        conn.commit()
        conn.close()
        flash(f'Receta creada. Nº: {num_cuenta}', 'success')
        return redirect(url_for('recetas'))
    conn.close()
    contenido = """
    <h2>Nueva Receta - Cita #{{ id_cita }}</h2>
    <div class="card p-3 mb-3">
        <div class="bg-light p-2"><b>Paciente:</b> {{ cita[1] }} {{ cita[2] }}<br><b>Afiliación:</b> {{ cita[3] or 'N/E' }}<br><b>Médico:</b> {{ cita[5] }}</div>
        <form method="POST">
            <div class="row g-2"><div class="col-md-6"><label>Nº Cuenta</label><input type="text" name="numero_cuenta" class="form-control" required></div>
            <div class="col-md-6"><label>Fecha</label><input type="date" name="fecha" class="form-control" value="{{ now().strftime('%Y-%m-%d') }}" readonly></div></div>
            <div class="mt-2"><label>Diagnóstico</label><textarea name="diagnostico" class="form-control" rows="2"></textarea></div>
            <div class="mt-2"><label>Indicaciones</label><textarea name="indicaciones" class="form-control" rows="2"></textarea></div>
            <h5 class="mt-3">Procedimientos</h5>
            <div id="proc-container">
                <div class="row proc-row g-2 align-items-end">
                    <div class="col-md-5"><div style="position:relative;"><input type="text" class="form-control proc-buscar" placeholder="Buscar..."><input type="hidden" name="procedimiento_id[]"><div class="autocomplete-suggestions"></div></div></div>
                    <div class="col-md-2"><label>Cant.</label><input type="number" name="cantidad[]" class="form-control" value="1" min="1"></div>
                    <div class="col-md-4"><label>Observación</label><input type="text" name="observacion[]" class="form-control"></div>
                    <div class="col-md-1"><button type="button" class="btn btn-danger eliminar-proc">×</button></div>
                </div>
            </div>
            <button type="button" class="btn btn-secondary mt-2" id="agregar-proc">+ Agregar otro</button>
            <hr>
            <button class="btn btn-primary">Guardar</button>
            <a href="{{ url_for('recetas') }}" class="btn btn-secondary">Cancelar</a>
        </form>
    </div>
    <script>
        document.addEventListener('input', function(e) {
            if (e.target.classList.contains('proc-buscar')) {
                const input = e.target;
                const row = input.closest('.proc-row');
                const sugg = row.querySelector('.autocomplete-suggestions');
                const hidden = row.querySelector('input[name="procedimiento_id[]"]');
                const q = input.value.trim();
                if (q.length < 2) { sugg.style.display='none'; return; }
                fetch('/api/procedimientos?q='+encodeURIComponent(q))
                    .then(r=>r.json())
                    .then(data=>{
                        sugg.innerHTML='';
                        if(data.length===0){sugg.innerHTML='<div class="p-2 text-muted">No encontrado</div>';}
                        else{data.forEach(item=>{const d=document.createElement('div');d.className='p-2 hover-bg-light';d.textContent=item.codigo+' - '+item.nombre+' (S/ '+item.precio+')';d.dataset.id=item.id;d.dataset.nombre=item.nombre;d.style.cursor='pointer';d.addEventListener('click',function(){input.value=this.dataset.nombre;hidden.value=this.dataset.id;sugg.style.display='none';});sugg.appendChild(d);});}
                        sugg.style.display='block';
                    });
            }
        });
        document.addEventListener('click', function(e){if(!e.target.closest('.proc-row')){document.querySelectorAll('.autocomplete-suggestions').forEach(el=>el.style.display='none');}});
        document.getElementById('agregar-proc').addEventListener('click',function(){const c=document.getElementById('proc-container');const first=c.querySelector('.proc-row');const newRow=first.cloneNode(true);newRow.querySelectorAll('input').forEach(inp=>inp.value='');newRow.querySelector('input[name="cantidad[]"]').value=1;newRow.querySelector('.autocomplete-suggestions').innerHTML='';c.appendChild(newRow);});
        document.addEventListener('click',function(e){if(e.target.classList.contains('eliminar-proc')){const row=e.target.closest('.proc-row');if(document.querySelectorAll('.proc-row').length>1){row.remove();}else{alert('Debe haber al menos un procedimiento.');}}});
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  id_cita=id_cita, cita=cita, now=datetime.now)

@app.route('/api/procedimientos')
def api_procedimientos():
    q = request.args.get('q', '')
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, "SELECT id, codigo, nombre, precio FROM procedimientos WHERE activo=1 AND (codigo LIKE ? OR nombre LIKE ?)", (f'%{q}%', f'%{q}%'))
    res = cur.fetchall()
    conn.close()
    return jsonify([{'id':r[0], 'codigo':r[1], 'nombre':r[2], 'precio':r[3]} for r in res])

@app.route('/recetas/ver/<int:id_receta>')
def ver_receta(id_receta):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, """SELECT r.numero_cuenta, r.fecha_emision, r.diagnostico, r.indicaciones, p.nombre, p.apellido, p.nro_afiliacion, m.nombre||' '||m.apellido
                   FROM recetas r JOIN pacientes p ON r.id_paciente=p.id JOIN medicos m ON r.id_medico=m.id WHERE r.id=?""", (id_receta,))
    rec = cur.fetchone()
    if not rec: return "Receta no encontrada", 404
    ejecutar_consulta(cur, """SELECT pr.codigo, pr.nombre, rd.cantidad, rd.precio_unitario, rd.observaciones
                   FROM receta_detalle rd JOIN procedimientos pr ON rd.id_procedimiento=pr.id WHERE rd.id_receta=?""", (id_receta,))
    detalles = cur.fetchall()
    conn.close()
    contenido = """
    <h2>Receta Nº {{ rec[0] }}</h2>
    <div class="card p-3"><p><b>Paciente:</b> {{ rec[4] }} {{ rec[5] }}</p><p><b>Médico:</b> {{ rec[7] }}</p><p><b>Fecha:</b> {{ rec[1] }}</p><p><b>Diagnóstico:</b> {{ rec[2] or 'N/E' }}</p><p><b>Indicaciones:</b> {{ rec[3] or 'N/E' }}</p>
    <h5>Procedimientos</h5><table class="table"><thead><tr><th>Código</th><th>Nombre</th><th>Cant.</th><th>Precio</th><th>Obs.</th></tr></thead><tbody>{% for d in detalles %}<tr><td>{{ d[0] }}</td><td>{{ d[1] }}</td><td>{{ d[2] }}</td><td>S/ {{ d[3] }}</td><td>{{ d[4] or '' }}</td></tr>{% else %}<tr><td colspan="5">Sin procedimientos</td></tr>{% endfor %}</tbody></table>
    <a href="{{ url_for('imprimir_receta_pdf', id_receta=id_receta) }}" class="btn btn-warning">PDF</a>
    <a href="{{ url_for('recetas') }}" class="btn btn-secondary">Volver</a></div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')),
                                  rec=rec, detalles=detalles, id_receta=id_receta)

@app.route('/recetas/pdf/<int:id_receta>')
def imprimir_receta_pdf(id_receta):
    if 'Atención Médica' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    conn = get_db_connection()
    cur = conn.cursor()
    ejecutar_consulta(cur, """SELECT r.numero_cuenta, r.fecha_emision, r.diagnostico, r.indicaciones, p.nombre, p.apellido, p.nro_afiliacion, m.nombre||' '||m.apellido
                   FROM recetas r JOIN pacientes p ON r.id_paciente=p.id JOIN medicos m ON r.id_medico=m.id WHERE r.id=?""", (id_receta,))
    rec = cur.fetchone()
    if not rec: return "Receta no encontrada", 404
    ejecutar_consulta(cur, """SELECT pr.codigo, pr.nombre, rd.cantidad, rd.precio_unitario, rd.observaciones
                   FROM receta_detalle rd JOIN procedimientos pr ON rd.id_procedimiento=pr.id WHERE rd.id_receta=?""", (id_receta,))
    detalles = cur.fetchall()
    conn.close()
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    logo_path = config[2] if config else ''
    pie = config[4] if config else ''
    size = obtener_tamano_pagina('report')
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    w,h = size
    c.setFillColor(colors.HexColor("#0d2b45"))
    c.rect(0, h-80, w, 80, fill=1, stroke=0)
    if logo_path and os.path.exists(os.path.join('static', logo_path)):
        try: c.drawImage(os.path.join('static', logo_path), 30, h-70, width=60, height=50, preserveAspectRatio=True)
        except: pass
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold",18); c.drawString(100, h-45, nombre_sistema)
    c.setFont("Helvetica",10); c.drawString(100, h-65, "RECETA MÉDICA")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold",12); c.drawString(50, h-120, f"Nº Cuenta: {rec[0]}")
    c.setFont("Helvetica",10)
    y = h-140
    for label,val in [("Paciente:", f"{rec[4]} {rec[5]}"), ("Afiliación:", rec[6] or 'N/E'), ("Médico:", rec[7]), ("Fecha:", rec[1]), ("Diagnóstico:", rec[2] or 'N/E'), ("Indicaciones:", rec[3] or 'N/E')]:
        c.drawString(50, y, label); c.drawString(150, y, val); y -= 20
    c.setFont("Helvetica-Bold",12); c.drawString(50, y-10, "Procedimientos:"); y -= 30
    for d in detalles:
        c.setFont("Helvetica",10)
        c.drawString(50, y, f"{d[0]} - {d[1]}  Cant:{d[2]}  Precio:S/ {d[3]:.2f}  Obs:{d[4] or ''}")
        y -= 20
        if y < 50:
            c.showPage(); y = h-50
    c.setFont("Helvetica-Oblique",8); c.setFillColor(colors.grey); c.drawString(30,30,pie)
    c.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Receta_{rec[0]}.pdf", mimetype='application/pdf')

# ========================== CONFIGURACIÓN ==========================
@app.route('/configuracion', methods=['GET','POST'])
@login_required
def configuracion_sistema():
    if 'Configuración' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    tab = request.args.get('tab', 'general')
    conn = get_db_connection()
    cur = conn.cursor()
    if tab == 'general':
        if request.method == 'POST':
            nombre = request.form['nombre_sistema']
            tamano = request.form['tamano_hoja']
            encabezado = request.form['encabezado_texto']
            pie = request.form['pie_pagina_texto']
            header = request.form['report_header']
            footer = request.form['report_footer']
            ticket = request.form.get('ticket_size', 'TICKET_80MM')
            report = request.form.get('report_size', 'A4')
            result = request.form.get('result_size', 'A4')
            ejecutar_consulta(cur, """UPDATE configuracion_sistema SET nombre_sistema=?, tamano_hoja=?, encabezado_texto=?, pie_pagina_texto=?, report_header=?, report_footer=?, ticket_size=?, report_size=?, result_size=? WHERE id=1""",
                        (nombre, tamano, encabezado, pie, header, footer, ticket, report, result))
            conn.commit()
            flash('Configuración actualizada.', 'success')
            return redirect(url_for('configuracion_sistema', tab='general'))
        config = obtener_configuracion()
        contenido = """
        <h2>⚙️ Configuración</h2>
        <ul class="nav nav-tabs">
            <li class="nav-item"><a class="nav-link active" href="{{ url_for('configuracion_sistema', tab='general') }}">General</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='modulos') }}">Módulos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='roles') }}">Roles</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='personal') }}">Personal</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='medicos') }}">👨‍⚕️ Médicos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='medicamentos') }}">💊 Medicamentos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='examenes') }}">Exámenes</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='procedimientos') }}">Procedimientos</a></li>
            <li class="nav-item"><a class="nav-link" href="{{ url_for('configuracion_sistema', tab='secciones') }}">📂 Secciones Lab</a></li>
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
                <div class="mt-3 border-top pt-3"><h5>Logo</h5>{% if config[2] %}<img src="/static/{{ config[2] }}" style="max-height:80px;">{% else %}Sin logo{% endif %}<br><a href="{{ url_for('subir_logo') }}" class="btn btn-primary">Subir Logo</a></div>
                <div class="mt-3 border-top pt-3"><h5>Sello</h5>{% if config[7] %}<img src="/static/{{ config[7] }}" style="max-height:80px;">{% else %}Sin sello{% endif %}<br><a href="{{ url_for('subir_sello') }}" class="btn btn-primary">Subir Sello</a></div>
                <div class="mt-3"><button class="btn btn-success">Guardar</button></div>
            </form>
        </div>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), config=config)

    elif tab == 'modulos':
        if request.method == 'POST':
            for key,val in request.form.items():
                if key.startswith('mod_'):
                    id_mod = int(key.split('_')[1])
                    activo = 1 if val == 'on' else 0
                    ejecutar_consulta(cur, "UPDATE config_modulos SET activo=? WHERE id=?", (activo, id_mod))
            conn.commit()
            flash('Módulos actualizados.', 'success')
            return redirect(url_for('configuracion_sistema', tab='modulos'))
        ejecutar_consulta(cur, "SELECT id, nombre, activo, descripcion FROM config_modulos ORDER BY id")
        modulos = cur.fetchall()
        conn.close()
        contenido = """
        <h2>Módulos</h2>
        <form method="POST"><table class="table"><thead><tr><th>Módulo</th><th>Descripción</th><th>Activo</th></tr></thead><tbody>{% for m in modulos %}<tr><td>{{ m[1] }}</td><td>{{ m[3] }}</td><td><input type="checkbox" name="mod_{{ m[0] }}" {% if m[2] %}checked{% endif %}></td></tr>{% endfor %}</tbody></table><button class="btn btn-success">Guardar</button></form>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), modulos=modulos)

    elif tab == 'roles':
        if request.method == 'POST':
            rol = request.form.get('rol')
            modulo = request.form.get('modulo')
            if rol and modulo:
                if IS_POSTGRES:
                    ejecutar_consulta(cur, "INSERT INTO permisos_roles (rol, modulo) VALUES (%s,%s) ON CONFLICT (rol, modulo) DO NOTHING", (rol, modulo))
                else:
                    ejecutar_consulta(cur, "INSERT OR IGNORE INTO permisos_roles (rol, modulo) VALUES (?,?)", (rol, modulo))
                conn.commit()
                flash('Permiso agregado.', 'success')
            return redirect(url_for('configuracion_sistema', tab='roles'))
        ejecutar_consulta(cur, "SELECT DISTINCT rol FROM permisos_roles ORDER BY rol")
        roles = [r[0] for r in cur.fetchall()]
        ejecutar_consulta(cur, "SELECT nombre FROM config_modulos WHERE activo=1")
        modulos = [m[0] for m in cur.fetchall()]
        ejecutar_consulta(cur, "SELECT rol, modulo FROM permisos_roles ORDER BY rol, modulo")
        permisos = cur.fetchall()
        conn.close()
        contenido = """
        <h2>Roles</h2>
        <form method="POST" class="row g-2 mb-3"><div class="col-md-4"><label>Rol</label><input type="text" name="rol" class="form-control"></div><div class="col-md-4"><label>Módulo</label><select name="modulo" class="form-control"><option value="">Seleccione</option>{% for m in modulos %}<option value="{{ m }}">{{ m }}</option>{% endfor %}</select></div><div class="col-md-2"><button class="btn btn-primary mt-2">Agregar</button></div></form>
        <table class="table"><thead><tr><th>Rol</th><th>Módulo</th></tr></thead><tbody>{% for p in permisos %}<tr><td>{{ p[0] }}</td><td>{{ p[1] }}</td></tr>{% else %}<tr><td colspan="2">Sin permisos</td></tr>{% endfor %}</tbody></table>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), modulos=modulos, permisos=permisos)

    elif tab == 'personal':
        if request.method == 'POST':
            usuario = request.form['usuario']
            password = request.form['password']
            rol = request.form['rol']
            if usuario and password and rol:
                hashed = generate_password_hash(password)
                try:
                    if IS_POSTGRES:
                        ejecutar_consulta(cur, "INSERT INTO usuarios (usuario, password_hash, rol) VALUES (%s,%s,%s) ON CONFLICT (usuario) DO NOTHING", (usuario, hashed, rol))
                    else:
                        ejecutar_consulta(cur, "INSERT OR IGNORE INTO usuarios (usuario, password_hash, rol) VALUES (?,?,?)", (usuario, hashed, rol))
                    conn.commit()
                    flash('Usuario creado.', 'success')
                except Exception as e:
                    flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('configuracion_sistema', tab='personal'))
        ejecutar_consulta(cur, "SELECT id, usuario, rol FROM usuarios ORDER BY id")
        usuarios = cur.fetchall()
        conn.close()
        contenido = """
        <h2>Personal</h2>
        <form method="POST" class="row g-2 mb-3"><div class="col-md-3"><label>Usuario</label><input type="text" name="usuario" class="form-control" required></div><div class="col-md-3"><label>Contraseña</label><input type="password" name="password" class="form-control" required></div><div class="col-md-3"><label>Rol</label><select name="rol" class="form-control"><option value="administrador">Administrador</option><option value="medico">Médico</option><option value="laboratorista">Laboratorista</option><option value="enfermera">Enfermera</option><option value="tecnologo">Tecnólogo</option></select></div><div class="col-md-3"><button class="btn btn-primary mt-2">Crear</button></div></form>
        <table class="table"><thead><tr><th>ID</th><th>Usuario</th><th>Rol</th></tr></thead><tbody>{% for u in usuarios %}<tr><td>{{ u[0] }}</td><td>{{ u[1] }}</td><td>{{ u[2] }}</td></tr>{% else %}<tr><td colspan="3">Sin usuarios</td></tr>{% endfor %}</tbody></table>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), usuarios=usuarios)

    elif tab == 'medicos':
        if request.method == 'POST':
            accion = request.form.get('accion')
            if accion == 'agregar':
                nombre = request.form['nombre']
                apellido = request.form['apellido']
                especialidad = request.form.get('especialidad', '')
                horario = request.form.get('horario', '')
                telefono = request.form.get('telefono', '')
                email = request.form.get('email', '')
                licencia = request.form.get('numero_licencia', '')
                try:
                    ejecutar_consulta(cur, """INSERT INTO medicos (nombre, apellido, especialidad, horario, telefono, email, numero_licencia, activo)
                                   VALUES (?,?,?,?,?,?,?,1)""", (nombre, apellido, especialidad, horario, telefono, email, licencia))
                    conn.commit()
                    flash('Médico agregado.', 'success')
                except Exception as e:
                    flash(f'Error: {str(e)}', 'danger')
            elif accion == 'editar':
                id_med = request.form['id_medico']
                nombre = request.form['nombre']
                apellido = request.form['apellido']
                especialidad = request.form.get('especialidad', '')
                horario = request.form.get('horario', '')
                telefono = request.form.get('telefono', '')
                email = request.form.get('email', '')
                licencia = request.form.get('numero_licencia', '')
                activo = 1 if request.form.get('activo') == 'on' else 0
                try:
                    ejecutar_consulta(cur, """UPDATE medicos SET nombre=?, apellido=?, especialidad=?, horario=?, telefono=?, email=?, numero_licencia=?, activo=? WHERE id=?""",
                                (nombre, apellido, especialidad, horario, telefono, email, licencia, activo, id_med))
                    conn.commit()
                    flash('Médico actualizado.', 'success')
                except Exception as e:
                    flash(f'Error: {str(e)}', 'danger')
            elif accion == 'eliminar':
                id_med = request.form['id_medico']
                try:
                    ejecutar_consulta(cur, "DELETE FROM medicos WHERE id=?", (id_med,))
                    conn.commit()
                    flash('Médico eliminado.', 'success')
                except Exception as e:
                    flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('configuracion_sistema', tab='medicos'))
        ejecutar_consulta(cur, "SELECT id, nombre, apellido, especialidad, horario, telefono, email, numero_licencia, activo FROM medicos ORDER BY nombre")
        medicos = cur.fetchall()
        conn.close()
        contenido = """
        <h2>👨‍⚕️ Médicos</h2>
        <div class="card p-3 mb-3"><h5>Agregar médico</h5><form method="POST" class="row g-2"><input type="hidden" name="accion" value="agregar">
            <div class="col-md-3"><label>Nombre</label><input type="text" name="nombre" class="form-control" required></div>
            <div class="col-md-3"><label>Apellido</label><input type="text" name="apellido" class="form-control" required></div>
            <div class="col-md-3"><label>Especialidad</label><input type="text" name="especialidad" class="form-control"></div>
            <div class="col-md-3"><label>Horario</label><input type="text" name="horario" class="form-control"></div>
            <div class="col-md-3"><label>Teléfono</label><input type="text" name="telefono" class="form-control"></div>
            <div class="col-md-3"><label>Email</label><input type="email" name="email" class="form-control"></div>
            <div class="col-md-3"><label>Nº Licencia</label><input type="text" name="numero_licencia" class="form-control"></div>
            <div class="col-md-3"><button class="btn btn-success mt-2">Agregar</button></div>
        </form></div>
        <table class="table"><thead><tr><th>Nombre</th><th>Apellido</th><th>Especialidad</th><th>Horario</th><th>Teléfono</th><th>Email</th><th>Licencia</th><th>Activo</th><th>Acciones</th></tr></thead><tbody>{% for m in medicos %}<tr><td>{{ m[1] }}</td><td>{{ m[2] }}</td><td>{{ m[3] }}</td><td>{{ m[4] }}</td><td>{{ m[5] }}</td><td>{{ m[6] }}</td><td>{{ m[7] }}</td><td>{% if m[8]==1 %}✅{% else %}❌{% endif %}</td><td><button class="btn btn-warning btn-sm" onclick="editarMedico({{ m[0] }},'{{ m[1] }}','{{ m[2] }}','{{ m[3] }}','{{ m[4] }}','{{ m[5] }}','{{ m[6] }}','{{ m[7] }}',{{ m[8] }})">✏️</button><form style="display:inline;" method="POST" onsubmit="return confirm('¿Eliminar?')"><input type="hidden" name="accion" value="eliminar"><input type="hidden" name="id_medico" value="{{ m[0] }}"><button class="btn btn-danger btn-sm">🗑️</button></form></td></tr>{% else %}<tr><td colspan="9">Sin médicos</td></tr>{% endfor %}</tbody></table>
        <div id="form_editar_medico" style="display:none;" class="card p-3 mt-3"><h5>Editar médico</h5><form method="POST" class="row g-2"><input type="hidden" name="accion" value="editar"><input type="hidden" name="id_medico" id="edit_id_medico">
            <div class="col-md-3"><label>Nombre</label><input type="text" name="nombre" id="edit_nombre_med" class="form-control" required></div>
            <div class="col-md-3"><label>Apellido</label><input type="text" name="apellido" id="edit_apellido_med" class="form-control" required></div>
            <div class="col-md-3"><label>Especialidad</label><input type="text" name="especialidad" id="edit_especialidad_med" class="form-control"></div>
            <div class="col-md-3"><label>Horario</label><input type="text" name="horario" id="edit_horario_med" class="form-control"></div>
            <div class="col-md-3"><label>Teléfono</label><input type="text" name="telefono" id="edit_telefono_med" class="form-control"></div>
            <div class="col-md-3"><label>Email</label><input type="email" name="email" id="edit_email_med" class="form-control"></div>
            <div class="col-md-3"><label>Licencia</label><input type="text" name="numero_licencia" id="edit_licencia_med" class="form-control"></div>
            <div class="col-md-2"><label>Activo</label><input type="checkbox" name="activo" id="edit_activo_med" checked></div>
            <div class="col-md-3"><button class="btn btn-warning mt-2">Guardar</button><button type="button" onclick="document.getElementById('form_editar_medico').style.display='none'" class="btn btn-secondary mt-2">Cancelar</button></div>
        </form></div>
        <script>function editarMedico(id,nom,ape,esp,hor,tel,email,lic,act){document.getElementById('form_editar_medico').style.display='block';document.getElementById('edit_id_medico').value=id;document.getElementById('edit_nombre_med').value=nom;document.getElementById('edit_apellido_med').value=ape;document.getElementById('edit_especialidad_med').value=esp;document.getElementById('edit_horario_med').value=hor;document.getElementById('edit_telefono_med').value=tel;document.getElementById('edit_email_med').value=email;document.getElementById('edit_licencia_med').value=lic;document.getElementById('edit_activo_med').checked=act===1;}</script>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), medicos=medicos)

    elif tab == 'medicamentos':
        if request.method == 'POST':
            accion = request.form.get('accion')
            if accion == 'agregar':
                codigo = request.form['codigo']
                nombre = request.form['nombre']
                desc = request.form.get('descripcion', '')
                precio = float(request.form.get('precio', 0))
                stock = int(request.form.get('stock', 0))
                unidad = request.form.get('unidad_medida', 'unidad')
                lab = request.form.get('laboratorio', '')
                fv = request.form.get('fecha_vencimiento', '')
                try:
                    if IS_POSTGRES:
                        ejecutar_consulta(cur, "INSERT INTO medicamentos (codigo, nombre, descripcion, precio, stock, unidad_medida, laboratorio, fecha_vencimiento, activo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)", (codigo, nombre, desc, precio, stock, unidad, lab, fv if fv else None))
                    else:
                        ejecutar_consulta(cur, "INSERT OR IGNORE INTO medicamentos (codigo, nombre, descripcion, precio, stock, unidad_medida, laboratorio, fecha_vencimiento, activo) VALUES (?,?,?,?,?,?,?,?,1)", (codigo, nombre, desc, precio, stock, unidad, lab, fv if fv else None))
                    conn.commit()
                    flash('Medicamento agregado.', 'success')
                except Exception as e:
                    flash(f'Error: {str(e)}', 'danger')
            elif accion == 'editar':
                id_med = request.form['id_medicamento']
                codigo = request.form['codigo']
                nombre = request.form['nombre']
                desc = request.form.get('descripcion', '')
                precio = float(request.form.get('precio', 0))
                stock = int(request.form.get('stock', 0))
                unidad = request.form.get('unidad_medida', 'unidad')
                lab = request.form.get('laboratorio', '')
                fv = request.form.get('fecha_vencimiento', '')
                activo = 1 if request.form.get('activo') == 'on' else 0
                try:
                    ejecutar_consulta(cur, """UPDATE medicamentos SET codigo=?, nombre=?, descripcion=?, precio=?, stock=?, unidad_medida=?, laboratorio=?, fecha_vencimiento=?, activo=? WHERE id=?""",
                                (codigo, nombre, desc, precio, stock, unidad, lab, fv if fv else None, activo, id_med))
                    conn.commit()
                    flash('Medicamento actualizado.', 'success')
                except Exception as e:
                    flash(f'Error: {str(e)}', 'danger')
            elif accion == 'eliminar':
                id_med = request.form['id_medicamento']
                try:
                    ejecutar_consulta(cur, "DELETE FROM medicamentos WHERE id=?", (id_med,))
                    conn.commit()
                    flash('Medicamento eliminado.', 'success')
                except Exception as e:
                    flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('configuracion_sistema', tab='medicamentos'))
        ejecutar_consulta(cur, "SELECT id, codigo, nombre, descripcion, precio, stock, unidad_medida, laboratorio, fecha_vencimiento, activo FROM medicamentos ORDER BY nombre")
        medicamentos = cur.fetchall()
        conn.close()
        contenido = """
        <h2>💊 Medicamentos</h2>
        <div class="card p-3 mb-3"><h5>Agregar medicamento</h5><form method="POST" class="row g-2"><input type="hidden" name="accion" value="agregar">
            <div class="col-md-2"><label>Código</label><input type="text" name="codigo" class="form-control" required></div>
            <div class="col-md-3"><label>Nombre</label><input type="text" name="nombre" class="form-control" required></div>
            <div class="col-md-2"><label>Descripción</label><input type="text" name="descripcion" class="form-control"></div>
            <div class="col-md-1"><label>Precio</label><input type="number" step="0.01" name="precio" class="form-control"></div>
            <div class="col-md-1"><label>Stock</label><input type="number" name="stock" class="form-control"></div>
            <div class="col-md-2"><label>Unidad</label><input type="text" name="unidad_medida" class="form-control" value="unidad"></div>
            <div class="col-md-2"><label>Laboratorio</label><input type="text" name="laboratorio" class="form-control"></div>
            <div class="col-md-2"><label>Vencimiento</label><input type="date" name="fecha_vencimiento" class="form-control"></div>
            <div class="col-md-2"><button class="btn btn-success mt-2">Agregar</button></div>
        </form></div>
        <table class="table"><thead><tr><th>Código</th><th>Nombre</th><th>Desc.</th><th>Precio</th><th>Stock</th><th>Unidad</th><th>Laboratorio</th><th>Venc.</th><th>Activo</th><th>Acciones</th></tr></thead><tbody>{% for m in medicamentos %}<tr><td>{{ m[1] }}</td><td>{{ m[2] }}</td><td>{{ m[3] }}</td><td>S/ {{ m[4] }}</td><td>{{ m[5] }}</td><td>{{ m[6] }}</td><td>{{ m[7] }}</td><td>{{ m[8] or 'N/A' }}</td><td>{% if m[9]==1 %}✅{% else %}❌{% endif %}</td><td><button class="btn btn-warning btn-sm" onclick="editarMedicamento({{ m[0] }},'{{ m[1] }}','{{ m[2] }}','{{ m[3] }}',{{ m[4] }},{{ m[5] }},'{{ m[6] }}','{{ m[7] }}','{{ m[8] }}',{{ m[9] }})">✏️</button><form style="display:inline;" method="POST" onsubmit="return confirm('¿Eliminar?')"><input type="hidden" name="accion" value="eliminar"><input type="hidden" name="id_medicamento" value="{{ m[0] }}"><button class="btn btn-danger btn-sm">🗑️</button></form></td></tr>{% else %}<tr><td colspan="10">Sin medicamentos</td></tr>{% endfor %}</tbody></table>
        <div id="form_editar_medicamento" style="display:none;" class="card p-3 mt-3"><h5>Editar medicamento</h5><form method="POST" class="row g-2"><input type="hidden" name="accion" value="editar"><input type="hidden" name="id_medicamento" id="edit_id_medicamento">
            <div class="col-md-2"><label>Código</label><input type="text" name="codigo" id="edit_codigo_med" class="form-control" required></div>
            <div class="col-md-3"><label>Nombre</label><input type="text" name="nombre" id="edit_nombre_med" class="form-control" required></div>
            <div class="col-md-2"><label>Descripción</label><input type="text" name="descripcion" id="edit_desc_med" class="form-control"></div>
            <div class="col-md-1"><label>Precio</label><input type="number" step="0.01" name="precio" id="edit_precio_med" class="form-control"></div>
            <div class="col-md-1"><label>Stock</label><input type="number" name="stock" id="edit_stock_med" class="form-control"></div>
            <div class="col-md-2"><label>Unidad</label><input type="text" name="unidad_medida" id="edit_unidad_med" class="form-control"></div>
            <div class="col-md-2"><label>Laboratorio</label><input type="text" name="laboratorio" id="edit_lab_med" class="form-control"></div>
            <div class="col-md-2"><label>Vencimiento</label><input type="date" name="fecha_vencimiento" id="edit_fv_med" class="form-control"></div>
            <div class="col-md-2"><label>Activo</label><input type="checkbox" name="activo" id="edit_activo_medic" checked></div>
            <div class="col-md-3"><button class="btn btn-warning mt-2">Guardar</button><button type="button" onclick="document.getElementById('form_editar_medicamento').style.display='none'" class="btn btn-secondary mt-2">Cancelar</button></div>
        </form></div>
        <script>function editarMedicamento(id,cod,nom,desc,prec,stock,unidad,lab,fv,act){document.getElementById('form_editar_medicamento').style.display='block';document.getElementById('edit_id_medicamento').value=id;document.getElementById('edit_codigo_med').value=cod;document.getElementById('edit_nombre_med').value=nom;document.getElementById('edit_desc_med').value=desc;document.getElementById('edit_precio_med').value=prec;document.getElementById('edit_stock_med').value=stock;document.getElementById('edit_unidad_med').value=unidad;document.getElementById('edit_lab_med').value=lab;document.getElementById('edit_fv_med').value=fv;document.getElementById('edit_activo_medic').checked=act===1;}</script>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), medicamentos=medicamentos)

    elif tab == 'examenes':
        if request.method == 'POST':
            if request.form.get('accion') == 'agregar':
                cod = request.form.get('codigo', '')
                desc = request.form['descripcion']
                prec = request.form['precio']
                if IS_POSTGRES:
                    ejecutar_consulta(cur, "INSERT INTO examenes_catalogo (codigo, descripcion, precio) VALUES (%s,%s,%s)", (cod, desc, prec))
                else:
                    ejecutar_consulta(cur, "INSERT INTO examenes_catalogo (codigo, descripcion, precio) VALUES (?,?,?)", (cod, desc, prec))
                conn.commit()
                flash('Examen agregado.', 'success')
            elif request.form.get('accion') == 'eliminar':
                id_ex = request.form['id_examen']
                ejecutar_consulta(cur, "DELETE FROM examenes_catalogo WHERE id=?", (id_ex,))
                conn.commit()
                flash('Examen eliminado.', 'success')
            return redirect(url_for('configuracion_sistema', tab='examenes'))
        ejecutar_consulta(cur, "SELECT id, codigo, descripcion, precio FROM examenes_catalogo ORDER BY id")
        examenes = cur.fetchall()
        conn.close()
        contenido = """
        <h2>Exámenes</h2>
        <form method="POST" class="row g-2 mb-3"><input type="hidden" name="accion" value="agregar"><div class="col-md-2"><label>Código</label><input type="text" name="codigo" class="form-control"></div><div class="col-md-4"><label>Descripción</label><input type="text" name="descripcion" class="form-control" required></div><div class="col-md-2"><label>Precio</label><input type="number" step="0.01" name="precio" class="form-control" required></div><div class="col-md-2"><button class="btn btn-primary">Agregar</button></div></form>
        <table class="table"><thead><tr><th>Código</th><th>Descripción</th><th>Precio</th><th>Acción</th></tr></thead><tbody>{% for e in examenes %}<tr><td>{{ e[1] }}</td><td>{{ e[2] }}</td><td>S/ {{ e[3] }}</td><td><form style="display:inline;" method="POST" onsubmit="return confirm('¿Eliminar?')"><input type="hidden" name="accion" value="eliminar"><input type="hidden" name="id_examen" value="{{ e[0] }}"><button class="btn btn-danger btn-sm">🗑️</button></form></td></tr>{% else %}<tr><td colspan="4">Sin exámenes</td></tr>{% endfor %}</tbody></table>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), examenes=examenes)

    elif tab == 'procedimientos':
        if request.method == 'POST':
            if request.form.get('accion') == 'agregar':
                cod = request.form['codigo']
                nom = request.form['nombre']
                tip = request.form['tipo']
                prec = request.form['precio']
                if IS_POSTGRES:
                    ejecutar_consulta(cur, "INSERT INTO procedimientos (codigo, nombre, tipo, precio) VALUES (%s,%s,%s,%s)", (cod, nom, tip, prec))
                else:
                    ejecutar_consulta(cur, "INSERT INTO procedimientos (codigo, nombre, tipo, precio) VALUES (?,?,?,?)", (cod, nom, tip, prec))
                conn.commit()
                flash('Procedimiento agregado.', 'success')
            elif request.form.get('accion') == 'eliminar':
                id_proc = request.form['id_procedimiento']
                ejecutar_consulta(cur, "DELETE FROM procedimientos WHERE id=?", (id_proc,))
                conn.commit()
                flash('Procedimiento eliminado.', 'success')
            return redirect(url_for('configuracion_sistema', tab='procedimientos'))
        ejecutar_consulta(cur, "SELECT id, codigo, nombre, tipo, precio FROM procedimientos ORDER BY id")
        procedimientos = cur.fetchall()
        conn.close()
        contenido = """
        <h2>Procedimientos</h2>
        <form method="POST" class="row g-2 mb-3"><input type="hidden" name="accion" value="agregar"><div class="col-md-2"><label>Código</label><input type="text" name="codigo" class="form-control" required></div><div class="col-md-3"><label>Nombre</label><input type="text" name="nombre" class="form-control" required></div><div class="col-md-2"><label>Tipo</label><select name="tipo" class="form-control"><option value="examen">Examen</option><option value="servicio">Servicio</option><option value="medicamento">Medicamento</option><option value="procedimiento">Procedimiento</option></select></div><div class="col-md-2"><label>Precio</label><input type="number" step="0.01" name="precio" class="form-control" required></div><div class="col-md-2"><button class="btn btn-primary">Agregar</button></div></form>
        <table class="table"><thead><tr><th>Código</th><th>Nombre</th><th>Tipo</th><th>Precio</th><th>Acción</th></tr></thead><tbody>{% for p in procedimientos %}<tr><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td>S/ {{ p[4] }}</td><td><form style="display:inline;" method="POST" onsubmit="return confirm('¿Eliminar?')"><input type="hidden" name="accion" value="eliminar"><input type="hidden" name="id_procedimiento" value="{{ p[0] }}"><button class="btn btn-danger btn-sm">🗑️</button></form></td></tr>{% else %}<tr><td colspan="5">Sin procedimientos</td></tr>{% endfor %}</tbody></table>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), procedimientos=procedimientos)

    elif tab == 'secciones':
        if request.method == 'POST':
            accion = request.form.get('accion')
            if accion == 'agregar':
                nombre = request.form.get('nombre_seccion', '').strip().upper()
                if nombre:
                    try:
                        if IS_POSTGRES:
                            ejecutar_consulta(cur, "INSERT INTO secciones_parametros (nombre) VALUES (%s)", (nombre,))
                        else:
                            ejecutar_consulta(cur, "INSERT OR IGNORE INTO secciones_parametros (nombre) VALUES (?)", (nombre,))
                        conn.commit()
                        flash('Sección agregada correctamente.', 'success')
                    except Exception as e:
                        flash(f'Error: {str(e)}', 'danger')
            elif accion == 'eliminar':
                id_sec = request.form.get('id_seccion')
                if id_sec:
                    ejecutar_consulta(cur, "SELECT COUNT(*) FROM examenes_parametros WHERE id_seccion=?", (id_sec,))
                    count_cat = cur.fetchone()[0]
                    ejecutar_consulta(cur, "SELECT COUNT(*) FROM parametros_extra_orden WHERE id_seccion=?", (id_sec,))
                    count_extra = cur.fetchone()[0]
                    if count_cat > 0 or count_extra > 0:
                        flash('No se puede eliminar: la sección está en uso por parámetros.', 'danger')
                    else:
                        ejecutar_consulta(cur, "DELETE FROM secciones_parametros WHERE id=?", (id_sec,))
                        conn.commit()
                        flash('Sección eliminada.', 'success')
            return redirect(url_for('configuracion_sistema', tab='secciones'))
        
        ejecutar_consulta(cur, "SELECT id, nombre, orden FROM secciones_parametros ORDER BY orden")
        secciones = cur.fetchall()
        conn.close()
        
        contenido = """
        <h2>📂 Secciones de Laboratorio</h2>
        <p>Las secciones permiten agrupar los parámetros de los exámenes (ej. Hematología, Bioquímica).</p>
        <form method="POST" class="row g-2 mb-3">
            <input type="hidden" name="accion" value="agregar">
            <div class="col-md-8">
                <label>Nueva sección</label>
                <input type="text" name="nombre_seccion" class="form-control" required placeholder="Ej. HEMATOLOGIA">
            </div>
            <div class="col-md-4">
                <button class="btn btn-primary mt-2">Agregar</button>
            </div>
        </form>
        <table class="table">
            <thead><tr><th>Nombre</th><th>Orden</th><th>Acción</th></tr></thead>
            <tbody>
                {% for s in secciones %}
                <tr>
                    <td>{{ s[1] }}</td>
                    <td>{{ s[2] }}</td>
                    <td>
                        <form style="display:inline;" method="POST" onsubmit="return confirm('¿Eliminar esta sección?')">
                            <input type="hidden" name="accion" value="eliminar">
                            <input type="hidden" name="id_seccion" value="{{ s[0] }}">
                            <button class="btn btn-danger btn-sm">🗑️</button>
                        </form>
                    </td>
                </tr>
                {% else %}
                <tr><td colspan="3">No hay secciones definidas.</td></tr>
                {% endfor %}
            </tbody>
        </table>
        <a href="{{ url_for('configuracion_sistema', tab='general') }}" class="btn btn-secondary">Volver</a>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), secciones=secciones)

    conn.close()
    return redirect(url_for('configuracion_sistema'))

# ========================== SUBIR LOGO / SELLO (CORREGIDO) ==========================
@app.route('/configuracion/subir_logo', methods=['GET','POST'])
def subir_logo():
    if 'Configuración' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        file = request.files.get('logo_archivo')
        if file and file.filename:
            allowed = {'png','jpg','jpeg','gif'}
            if '.' in file.filename and file.filename.rsplit('.',1)[1].lower() in allowed:
                try:
                    img = Image.open(file)
                    img.verify()
                    file.seek(0)
                except:
                    flash('Imagen inválida.', 'danger')
                    return redirect(url_for('subir_logo'))
                # Asegurar que la carpeta static existe
                if not os.path.exists('static'):
                    os.makedirs('static')
                ext = file.filename.rsplit('.',1)[1].lower()
                filename = f"logo_{uuid.uuid4().hex}.{ext}"
                file.save(os.path.join('static', filename))
                conn = get_db_connection()
                cur = conn.cursor()
                ejecutar_consulta(cur, "UPDATE configuracion_sistema SET logo_path=? WHERE id=1", (filename,))
                conn.commit()
                conn.close()
                flash('Logo subido.', 'success')
                return redirect(url_for('configuracion_sistema'))
            else:
                flash('Formato no permitido.', 'danger')
        else:
            flash('Seleccione un archivo.', 'danger')
        return redirect(url_for('subir_logo'))
    contenido = """
    <h2>Subir Logo</h2>
    <form method="POST" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="logo_archivo" accept="image/*" class="form-control" required></div><button class="btn btn-success">Subir</button><a href="{{ url_for('configuracion_sistema') }}" class="btn btn-secondary">Cancelar</a></form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')))

@app.route('/configuracion/subir_sello', methods=['GET','POST'])
def subir_sello():
    if 'Configuración' not in get_user_modules(session.get('rol')):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        file = request.files.get('sello_archivo')
        if file and file.filename:
            allowed = {'png','jpg','jpeg','gif'}
            if '.' in file.filename and file.filename.rsplit('.',1)[1].lower() in allowed:
                try:
                    img = Image.open(file)
                    img.verify()
                    file.seek(0)
                except:
                    flash('Imagen inválida.', 'danger')
                    return redirect(url_for('subir_sello'))
                if not os.path.exists('static'):
                    os.makedirs('static')
                ext = file.filename.rsplit('.',1)[1].lower()
                filename = f"sello_{uuid.uuid4().hex}.{ext}"
                file.save(os.path.join('static', filename))
                conn = get_db_connection()
                cur = conn.cursor()
                ejecutar_consulta(cur, "UPDATE configuracion_sistema SET sello_path=? WHERE id=1", (filename,))
                conn.commit()
                conn.close()
                flash('Sello subido.', 'success')
                return redirect(url_for('configuracion_sistema'))
            else:
                flash('Formato no permitido.', 'danger')
        else:
            flash('Seleccione un archivo.', 'danger')
        return redirect(url_for('subir_sello'))
    contenido = """
    <h2>Subir Sello</h2>
    <form method="POST" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="sello_archivo" accept="image/*" class="form-control" required></div><button class="btn btn-success">Subir</button><a href="{{ url_for('configuracion_sistema') }}" class="btn btn-secondary">Cancelar</a></form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
