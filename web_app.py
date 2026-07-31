@app.route('/resultados', methods=['GET'])
def resultados():
    if 'usuario' not in session or session['rol'] not in ['administrador', 'laboratorista', 'tecnologo']:
        return redirect(url_for('login'))
    
    dni_busqueda = request.args.get('dni', '').strip()
    conn = get_db_connection(); cursor = conn.cursor()
    
    # Añadimos el filtro por DNI a la consulta SQL
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
    
    <!-- BARRA DE BÚSQUEDA POR DNI -->
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
