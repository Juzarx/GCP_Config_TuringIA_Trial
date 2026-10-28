# GCP_Config_TuringIA_Trial

## Creando un proyecto nuevo en GCP
- Damos click en crear nuevo proyecto y le damos nombre
<img width="781" height="534" alt="image" src="https://github.com/user-attachments/assets/663db681-7337-4ffe-8832-fdf864065ffd" />
- Añadimos las API necesarias, en este caso empezamos con Cloud Storage, Cloud Functions, Cloud Pub/Sub, Cloud Logging y Cloud Build
- Entramos a la pestaña de IAM y administración
- Creamos un usuarion con privilegios mínimos en este caso de visualizador.
<img width="676" height="717" alt="image" src="https://github.com/user-attachments/assets/4e366f3c-0bfc-4635-92ee-81d2ad963ea3" />

## Creando el Bucket de prueba
- Entramos a la pestaña de buckets
- Creamos el bucket y le damos nombre
- Seleccionamos por region y dejamos la default
- Dejamos el modo standard
- El control de acceso queda en Uniforme
- La protección de datos queda en predeterminada
- La configuración del bucket quedaría algo así:
<img width="455" height="1050" alt="image" src="https://github.com/user-attachments/assets/2dcc6b2a-9c8b-4f55-8701-51f590ab70a8" />
- La cuenta con permisos de visualización hereda los permisos al bucket.

## Creando el ciclo de vida del bucket
- Creamos una regla que pase los archivos en estandard por más de 30 días a Nearline
- Creamos una regla que pase los archivos con más de 90 días a Coldline
- Creamos una regla que elimine los archivos que tienen mas de un año.
  <img width="796" height="391" alt="image" src="https://github.com/user-attachments/assets/c820ce43-2142-4ec5-8575-e5f8707d8d61" />

### Justificación
El supuesto es que los archivos se consultan con mucha frecuencia
justo después de subirse, y su uso disminuye con el tiempo:

- **0-30 días (Standard):** acceso frecuente. Es la clase con mayor costo
  de almacenamiento pero sin cargos por lectura.
- **30-90 días (Nearline):** acceso ocasional. Almacenar es más barato,
  pero leer tiene un costo adicional.
- **90-365 días (Coldline):** acceso muy raro. Es aún más barato almacenar.
- **365 días:** los archivos se eliminan para no pagar por datos que ya
  no se necesitan (política de retención de 1 año).
- **Subidas incompletas (7 días):** evita pagar por fragmentos de
  subidas fallidas que nadie usará.

  ### Notas

- Los valores de 30, 90 y 365 días son un ejemplo. En un caso real se
  ajustarían según el patrón de acceso y los requisitos de retención
  (legales, de auditoría, etc.) de cada tipo de dato.
- Nearline y Coldline tienen una duración mínima de almacenamiento
  (30 y 90 días), por eso las reglas se encadenan con esos intervalos.
- GCP evalúa estas reglas de forma asíncrona (aprox. una vez al día),
  por lo que los cambios no son inmediatos.

  ## Creando Clound Function
  - Nos vamos a la pestaña de Cloud Run
  - En el apartado de escribir función elegimos python
  - Le damos nombre al servicio
  - Añadimos el activador de cloud storage y seleccionamos el bucket
  - Verificamos en tipo de evento sea `google.cloud.storage.object.v1.finalized`
  - Seleccionamos la cuenta de servicio default, aunque las mejores p'racticas sería crear una con los permisos mínimos
  - Quedaría de la siguiente manera la configuración:
  <img width="870" height="1100" alt="image" src="https://github.com/user-attachments/assets/7743d5f8-404f-401d-8955-5e23e4f358d5" />
  - Damos click en crear para que abra el editor
