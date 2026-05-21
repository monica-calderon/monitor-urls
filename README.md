# Monitor de URLs con Telegram

Este proyecto revisa varias paginas web cada 15 minutos, avisa por Telegram si cambia el texto y envia un archivo `.txt` por cada pagina leida correctamente.

Esta pensado para funcionar gratis con GitHub Actions en un repositorio publico. No necesitas tener tu ordenador encendido.

## Que paginas revisa

Las paginas no estan escritas en el codigo para que no sean publicas.

Se configuran en GitHub como un secret llamado `MONITOR_URLS_JSON`.

Idealista puede mostrar verificacion de dispositivo. El script intenta abrir la pagina con navegador real usando Playwright. Si aun asi no puede leer el contenido real, avisa por Telegram y sigue revisando las otras paginas.

## Archivos importantes

- `monitor.py`: el bot que revisa las paginas y envia mensajes.
- `dump_urls_text.py`: archivo de prueba para imprimir el texto que se extrae de las paginas.
- `requirements.txt`: librerias de Python necesarias.
- `.github/workflows/monitor.yml`: workflow de GitHub Actions que se lanza manualmente o desde cron-job.org.
- `.monitor_state/`: carpeta donde se guarda el ultimo texto conocido. No se sube a GitHub.

## Paso 1: crear un repositorio publico en GitHub

1. Entra en <https://github.com>.
2. Pulsa `New repository`.
3. Pon un nombre, por ejemplo `monitor-urls`.
4. Marca `Public`.
5. Crea el repositorio.

## Paso 2: subir estos archivos

Desde esta carpeta, ejecuta:

```powershell
git init
git add .
git commit -m "Crear monitor de URLs con Telegram"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/monitor-urls.git
git push -u origin main
```

Cambia `TU_USUARIO` por tu usuario real de GitHub.

## Paso 3: crear los secretos de Telegram

No pongas el token del bot ni las URLs dentro del codigo.

En GitHub:

1. Abre tu repositorio.
2. Ve a `Settings`.
3. En el menu izquierdo, abre `Secrets and variables`.
4. Entra en `Actions`.
5. Pulsa `New repository secret`.
6. Crea este secreto:
   - Nombre: `TELEGRAM_BOT_TOKEN`
   - Valor: el token de tu bot de Telegram
7. Pulsa otra vez `New repository secret`.
8. Crea este secreto:
   - Nombre: `TELEGRAM_CHAT_ID`
   - Valor: tu chat id de Telegram
9. Pulsa otra vez `New repository secret`.
10. Crea este secreto:
   - Nombre: `MONITOR_URLS_JSON`
   - Valor: una lista JSON con las paginas privadas a revisar.

Ejemplo de formato para `MONITOR_URLS_JSON`:

```json
[
  {
    "name": "Nombre pagina 1",
    "url": "https://ejemplo.com/pagina-1",
    "expected_terms": ["palabra", "importante"]
  },
  {
    "name": "Nombre pagina 2",
    "url": "https://ejemplo.com/pagina-2",
    "expected_terms": ["palabra", "importante"]
  },
  {
    "name": "Nombre pagina dificil",
    "url": "https://ejemplo.com/pagina-3",
    "expected_terms": ["palabra", "importante"],
    "strict_expected_terms": true
  }
]
```

## Paso 4: probar manualmente en GitHub

1. Entra en la pestana `Actions` de tu repositorio.
2. Elige el workflow `Monitor URLs`.
3. Pulsa `Run workflow`.
4. Espera a que termine.
5. Mira los logs para comprobar que revisa las tres paginas.

La primera ejecucion guarda una base inicial. Normalmente no envia aviso de cambio porque todavia no tiene una version anterior con la que comparar.

## Paso 5: funcionamiento automatico

GitHub Actions no garantiza que los workflows programados se ejecuten exactamente cada 15 minutos. Para hacerlo mas fiable, usa cron-job.org para lanzar el workflow por API cada 15 minutos.

El workflow conserva `workflow_dispatch`, que permite lanzarlo desde fuera con una peticion HTTP.

En cron-job.org crea un cron job asi:

- Metodo: `POST`
- URL:

```text
https://api.github.com/repos/monica-calderon/monitor-urls/actions/workflows/monitor.yml/dispatches
```

- Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer TU_GITHUB_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

- Body:

```json
{
  "ref": "main"
}
```

El token de GitHub debe ser un token fino con permiso solo para este repositorio y con `Actions: Read and write`.

Si una pagina cambia, recibiras un mensaje de Telegram con un resumen.

Si una pagina falla, por ejemplo porque Idealista pide verificacion, recibiras un mensaje de error y el bot seguira con las demas.

Despues de cada pagina leida correctamente, recibiras tambien un archivo `.txt` con el nombre de esa web y el texto extraido.

## Control del limite gratuito

El repositorio debe mantenerse como `Public`.

Segun la documentacion oficial de GitHub, GitHub Actions es gratis para repositorios publicos usando runners estandar de GitHub. Por eso se mantiene el escaneo cada 15 minutos.

Calculo aproximado:

- Cada 15 minutos son unas 96 ejecuciones al dia.
- En un repositorio publico, esas ejecuciones no consumen los minutos gratuitos de repositorios privados.
- cron-job.org es gratuito y permite ejecutar cron jobs con intervalos personalizados.
- Si algun dia cambias el repositorio a `Private`, revisa el consumo de minutos de GitHub Actions.

## Probar en tu ordenador

Instala Python 3.12 o superior y ejecuta:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
$env:MONITOR_URLS_JSON = '[{"name":"Nombre pagina","url":"https://ejemplo.com","expected_terms":["ejemplo"]}]'
$env:DRY_RUN = "1"
python monitor.py
```

Con `DRY_RUN=1`, el script no envia mensajes ni archivos reales a Telegram. Solo los muestra por pantalla con una vista previa.

Para probar el envio real localmente:

```powershell
$env:TELEGRAM_BOT_TOKEN = "TU_TOKEN"
$env:TELEGRAM_CHAT_ID = "TU_CHAT_ID"
$env:MONITOR_URLS_JSON = '[{"name":"Nombre pagina","url":"https://ejemplo.com","expected_terms":["ejemplo"]}]'
python monitor.py
```

## Ver el texto extraido de las URLs

Para ver en pantalla el texto que el bot consigue leer de cada pagina, usa:

```powershell
$env:MONITOR_URLS_JSON = '[{"name":"Nombre pagina","url":"https://ejemplo.com","expected_terms":["ejemplo"]}]'
python dump_urls_text.py
```

Este archivo no envia mensajes a Telegram y no compara cambios. Solo muestra el texto extraido.

## Como cambiar o anadir URLs

Cambia el secret `MONITOR_URLS_JSON` en GitHub.

Cada pagina tiene este formato:

```python
{
    "name": "Nombre que veras en Telegram",
    "url": "https://ejemplo.com",
    "expected_terms": ["palabra", "importante"],
}
```

Si una pagina es dificil y quieres exigir que aparezcan esas palabras, anade:

```python
"strict_expected_terms": True
```

## Notas importantes

- GitHub Actions gratis funciona muy bien para este caso si el repositorio es publico.
- GitHub no garantiza que el minuto exacto sea siempre perfecto; por eso el disparador recomendado es cron-job.org.
- El estado se guarda con cache de GitHub Actions. Si GitHub borra la cache, la siguiente ejecucion creara una nueva base inicial.
- El token de Telegram y las URLs deben estar solo en GitHub Secrets o en variables de entorno locales.
- Los logs publicos no muestran las URLs cuando hay error; el mensaje privado de Telegram si incluye la URL.
