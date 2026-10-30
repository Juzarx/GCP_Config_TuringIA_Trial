/**
 * Webhook receptor: recibe los metadatos de la Cloud Function.
 *
 * Flujo:
 *  1. Parsea el cuerpo y exige "payload" y "signature".
 *  2. Verifica la firma HMAC-SHA256 con el secreto compartido.
 *  3. Valida los campos y que el timestamp esté dentro de la ventana.
 *  4. Ignora eventos duplicados (reintentos).
 *  5. Registra una fila en la hoja "Archivos" y notifica por Gmail.
 *
 * Nota: Apps Script no permite leer encabezados HTTP ni devolver códigos
 * distintos de 200, por eso la firma va en el cuerpo y el resultado se
 * devuelve en el JSON de respuesta.
 */

const HOJA_ARCHIVOS = 'Archivos';
const TOLERANCIA_MS = 5 * 60 * 1000;  // ventana anti-replay: 5 minutos
const CAMPOS_TEXTO = ['id_evento', 'bucket', 'nombre', 'tipo_contenido'];

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return responder(400, 'Cuerpo vacío');
    }

    let sobre;
    try {
      sobre = JSON.parse(e.postData.contents);
    } catch (err) {
      return responder(400, 'JSON inválido');
    }
    if (!sobre.payload || !sobre.signature) {
      return responder(400, 'Faltan payload o signature');
    }

    // El secreto vive en las propiedades del script, no en el código
    const secreto = PropertiesService.getScriptProperties().getProperty('WEBHOOK_SECRET');
    if (!secreto) {
      Logger.log('ERROR: falta la propiedad WEBHOOK_SECRET');
      return responder(500, 'Servidor sin configurar');
    }

    // 1) Autenticidad e integridad: la firma se calcula sobre el texto
    //    exacto del payload, así no hay problemas de formato al reserializar
    const firmaEsperada = firmar(String(sobre.payload), secreto);
    if (!compararSeguro(firmaEsperada, String(sobre.signature))) {
      Logger.log('Firma inválida, petición rechazada');
      return responder(401, 'Firma inválida');
    }

    // 2) Validación de datos
    let datos;
    try {
      datos = JSON.parse(sobre.payload);
    } catch (err) {
      return responder(400, 'Payload inválido');
    }
    const errorValidacion = validarDatos(datos);
    if (errorValidacion) {
      Logger.log('Datos inválidos: ' + errorValidacion);
      return responder(400, errorValidacion);
    }

    // 3) Anti-replay: se rechazan mensajes demasiado viejos o del futuro
    if (Math.abs(Date.now() - datos.timestamp) > TOLERANCIA_MS) {
      Logger.log('Timestamp fuera de la ventana permitida');
      return responder(408, 'Mensaje expirado');
    }

    // 4) Duplicados: Eventarc puede reintentar el mismo evento
    const cache = CacheService.getScriptCache();
    const claveCache = 'evt_' + datos.id_evento;
    if (cache.get(claveCache)) {
      Logger.log('Evento duplicado ignorado: ' + datos.id_evento);
      return responder(200, 'Duplicado ignorado');
    }

    // 5) Registrar y notificar
    const resultado = registrarYNotificar(datos);
    cache.put(claveCache, '1', 21600); // se recuerda 6 horas

    return responder(200, resultado);

  } catch (error) {
    // Error inesperado: se registra con detalle, pero la respuesta no
    // expone información interna
    Logger.log('Error inesperado en doPost: ' + error.message);
    return responder(500, 'Error interno');
  }
}

/** HMAC-SHA256 en hexadecimal. */
function firmar(mensaje, secreto) {
  const bytes = Utilities.computeHmacSha256Signature(mensaje, secreto);
  return bytes.map(b => ('0' + (b & 0xff).toString(16)).slice(-2)).join('');
}

/** Comparación en tiempo constante (evita ataques de temporización). */
function compararSeguro(a, b) {
  if (a.length !== b.length) return false;
  let diferencia = 0;
  for (let i = 0; i < a.length; i++) {
    diferencia |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diferencia === 0;
}

/** Devuelve un mensaje de error si los datos no son válidos, o null. */
function validarDatos(d) {
  if (!d || typeof d !== 'object') return 'Los datos no son un objeto';

  for (const campo of CAMPOS_TEXTO) {
    if (typeof d[campo] !== 'string' || !d[campo].trim()) {
      return `Campo obligatorio inválido: ${campo}`;
    }
    if (d[campo].length > 1024) return `Campo demasiado largo: ${campo}`;
  }
  if (!Number.isInteger(d.tamano_bytes) || d.tamano_bytes < 0) {
    return 'tamano_bytes inválido';
  }
  if (!Number.isInteger(d.timestamp)) return 'timestamp inválido';
  return null;
}

/**
 * Evita la inyección de fórmulas en Sheets: si un texto empieza con
 * = + - o @, Sheets podría ejecutarlo como fórmula. Se antepone una comilla.
 */
function limpiarTexto(valor) {
  const texto = String(valor == null ? '' : valor);
  return /^[=+\-@]/.test(texto) ? "'" + texto : texto;
}

function registrarYNotificar(d) {
  // LockService evita que dos peticiones simultáneas pisen la misma fila
  const candado = LockService.getScriptLock();
  if (!candado.tryLock(15000)) {
    throw new Error('No se pudo obtener el candado');
  }

  let fila;
  try {
    const libro = SpreadsheetApp.getActiveSpreadsheet();
    let hoja = libro.getSheetByName(HOJA_ARCHIVOS);
    if (!hoja) {
      hoja = libro.insertSheet(HOJA_ARCHIVOS);
      hoja.appendRow(['Fecha recepción', 'ID evento', 'Bucket', 'Nombre',
        'Tamaño (bytes)', 'Tipo', 'Clase almacenamiento', 'Fecha creación',
        'Notificación']);
    }
    hoja.appendRow([
      new Date(), limpiarTexto(d.id_evento), limpiarTexto(d.bucket),
      limpiarTexto(d.nombre), d.tamano_bytes, limpiarTexto(d.tipo_contenido),
      limpiarTexto(d.clase_almacenamiento), limpiarTexto(d.fecha_creacion),
      'Pendiente'
    ]);
    fila = hoja.getLastRow();
  } finally {
    candado.releaseLock();
  }

  // La notificación va fuera del candado: si falla, el registro ya quedó
  const hoja = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(HOJA_ARCHIVOS);
  try {
    const destinatarios = PropertiesService.getScriptProperties().getProperty('DESTINATARIOS');
    if (!destinatarios) throw new Error('Falta la propiedad DESTINATARIOS');

    GmailApp.sendEmail(
      destinatarios,
      `Nuevo archivo en el bucket: ${d.nombre}`,
      `Se subió un archivo nuevo.\n\n` +
      `Bucket: ${d.bucket}\n` +
      `Nombre: ${d.nombre}\n` +
      `Tamaño: ${d.tamano_bytes} bytes\n` +
      `Tipo: ${d.tipo_contenido}\n\n` +
      `Mensaje generado automáticamente.`
    );
    hoja.getRange(fila, 9).setValue('Enviada');
    return 'Registrado y notificado';

  } catch (error) {
    Logger.log('Error al notificar: ' + error.message);
    hoja.getRange(fila, 9).setValue('Error: ' + error.message);
    return 'Registrado, pero falló la notificación';
  }
}

function responder(codigo, mensaje) {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: codigo < 300, codigo: codigo, mensaje: mensaje }))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Prueba local: simula una petición válida sin salir de Apps Script.
 * Ejecútala desde el editor y revisa la hoja "Archivos" y el registro.
 */
function probarWebhook() {
  const secreto = PropertiesService.getScriptProperties().getProperty('WEBHOOK_SECRET');
  const payload = JSON.stringify({
    id_evento: 'prueba-' + Date.now(),
    bucket: 'bucket-demo',
    nombre: 'archivo-prueba.txt',
    tamano_bytes: 1234,
    tipo_contenido: 'text/plain',
    clase_almacenamiento: 'STANDARD',
    fecha_creacion: new Date().toISOString(),
    timestamp: Date.now()
  });
  const e = { postData: { contents: JSON.stringify({
    payload: payload, signature: firmar(payload, secreto)
  }) } };

  Logger.log(doPost(e).getContent());
}