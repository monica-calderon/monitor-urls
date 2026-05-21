# Monitor de URLs con Telegram

Este proyecto revisa paginas web privadas cada 15 minutos, avisa por Telegram si cambia el texto y envia un archivo `.txt` por cada pagina leida correctamente.

Las URLs no estan escritas en el codigo ni en el README. Se guardan en GitHub Secrets para que el repositorio pueda seguir siendo publico sin mostrar las paginas monitorizadas.

## Como funciona

1. cron-job.org lanza el workflow de GitHub Actions cada 15 minutos en modo `normal`.
2. GitHub Actions ejecuta `monitor.py`.
3. El script lee las URLs desde el secret `MONITOR_URLS_JSON`.
4. Cada pagina se descarga primero por HTTP normal y, si hace falta, con Chromium mediante Playwright.
5. Si una pagina cambia, el bot envia un resumen por Telegram.
6. En modo `debug`, si una pagina se lee correctamente, el bot envia un `.txt` individual con el texto extraido.
7. En modo `normal`, el bot solo envia `.txt` si esa pagina ha cambiado.
8. Si una pagina falla, el bot registra el error y continua con las demas.

## Archivos importantes

- `monitor.py`: bot principal.
- `dump_urls_text.py`: prueba local para ver el texto que se extrae de las paginas.
- `requirements.txt`: dependencias de Python.
- `.env.example`: ejemplo de variables locales sin secretos reales.
- `.github/workflows/monitor.yml`: workflow de GitHub Actions lanzado manualmente o desde cron-job.org.
- `.monitor_state/`: estado local de comparacion. No se sube a GitHub.

## Secrets necesarios en GitHub

Entra en tu repositorio:

```text
Settings > Secrets and variables > Actions > New repository secret
```

Crea estos tres secrets:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
MONITOR_URLS_JSON
```

`TELEGRAM_BOT_TOKEN` es el token del bot de Telegram.

`TELEGRAM_CHAT_ID` es el chat al que se enviaran los mensajes.

`MONITOR_URLS_JSON` contiene las paginas privadas a revisar. Formato:

```json
[
  {
    "name": "Nombre pagina 1",
    "url": "https://ejemplo.com/pagina-1",
    "expected_terms": ["palabra", "importante"]
  },
  {
    "name": "Nombre pagina dificil",
    "url": "https://ejemplo.com/pagina-2",
    "expected_terms": ["palabra", "importante"],
    "strict_expected_terms": true
  },
  {
    "name": "Idealista ejemplo",
    "url": "https://ejemplo.com/pagina-manual",
    "mode": "manual_summary",
    "summary": "Nota fija opcional con los datos principales conocidos."
  }
]
```

Campos:

- `name`: nombre que se vera en Telegram y en el nombre del `.txt`.
- `url`: URL privada que se monitoriza.
- `expected_terms`: palabras que deberian aparecer en el texto.
- `strict_expected_terms`: opcional. Si es `true`, la pagina se considera error si no aparecen esas palabras.
- `mode`: opcional. Usa `manual_summary` para webs que no deben abrirse automaticamente.
- `summary`: opcional. Texto fijo de referencia para webs en modo `manual_summary`.

## Webs con revision manual

Algunas webs, como Idealista, pueden bloquear la automatizacion con 403, captcha o verificacion de dispositivo.

Para esas webs puedes usar:

```json
{
  "name": "Idealista Essence Homes II",
  "url": "https://ejemplo.com/url-privada",
  "mode": "manual_summary",
  "summary": "Resumen fijo opcional para recordar los datos importantes."
}
```

En ese modo:

- El bot no intenta abrir la URL automaticamente.
- No se genera `.txt`.
- En `debug`, aparece dentro del resumen final de monitorizacion.
- En `normal`, queda registrado en logs y no genera mensaje propio.

La alternativa fiable para automatizar Idealista es solicitar acceso a su Search API oficial.

## Probar manualmente en GitHub

1. Entra en la pestana `Actions`.
2. Abre el workflow `Monitor URLs`.
3. Pulsa `Run workflow`.
4. Elige la rama `main`.
5. En `Action to run`, elige:
   - `normal`: comportamiento de produccion.
   - `debug`: fuerza la ejecucion aunque sea fuera de horario y envia un resumen final con todas las URLs.
6. Espera a que termine.
7. Comprueba Telegram.

La primera ejecucion crea una base inicial. En `normal` puede no enviar resumen ni `.txt` si todavia no hay cambios. En `debug` enviara un resumen final y los `.txt` de las paginas leidas correctamente.

## Modos de ejecucion

`normal`:

- Es el modo usado por cron-job.org.
- Solo monitoriza entre las 08:00 y las 22:00, hora de Madrid.
- Si una web no cambia, no envia `.txt`.
- Si una web cambia, envia alerta con `Antes` y `Despues`, y adjunta el `.txt`.
- Si hay errores, solo envia recordatorio si la ejecucion ocurre entre las 12:00 y las 12:15.

`debug`:

- Se elige manualmente desde GitHub Actions.
- Ejecuta siempre, tambien fuera del horario 08:00-22:00.
- Al final de la ejecucion, envia un unico `Resumen debug` con todas las URLs, metodo, estado y codigo de respuesta si existe.
- Envia `.txt` de todas las webs leidas correctamente.
- Informa tambien de URLs en `manual_summary`.

## Configurar cron-job.org cada 15 minutos

GitHub Actions no garantiza que `schedule` se ejecute exactamente cada 15 minutos. Por eso este proyecto usa cron-job.org para lanzar el workflow mediante API.

Usa `repository_dispatch`, que es mas simple para cron-job.org que `workflow_dispatch` y evita errores comunes `422 Unprocessable Entity` por un `ref` o body mal formado.

En cron-job.org crea un cron job:

- Schedule: cada 15 minutos.
- Method: `POST`.
- URL:

```text
https://api.github.com/repos/monica-calderon/monitor-urls/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer TU_GITHUB_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Body:

```json
{
  "event_type": "monitor-urls",
  "client_payload": {
    "action_to_run": "normal"
  }
}
```

`client_payload` es opcional porque el workflow usa `normal` por defecto. Aun asi, se recomienda incluirlo para que la configuracion sea explicita.

Respuesta esperada:

```text
204 No Content
```

Si ves `422 Unprocessable Entity`, revisa que el body sea JSON real, que `event_type` este escrito exactamente como `monitor-urls` y que cron-job.org envie `Content-Type: application/json`.

## Crear el token de GitHub para cron-job.org

Crea un fine-grained personal access token en GitHub:

```text
GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens
```

Configuralo asi:

- Repository access: solo `monica-calderon/monitor-urls`.
- Permissions:
  - `Contents`: `Read and write`.
  - `Actions`: `Read-only`, si GitHub lo pide.
- Caducidad: la que prefieras. Si caduca, cron-job.org dejara de lanzar el workflow.

Copia el token y usalo en cron-job.org en el header:

```text
Authorization: Bearer TU_GITHUB_TOKEN
```

No guardes este token en el repositorio.

## Que recibiras en Telegram

Por cada ejecucion real:

- Los mensajes usan emojis y secciones visuales para distinguir cambios, errores, URLs manuales y archivos.
- Si una web cambia: mensaje de alerta con `Antes` y `Despues`.
- Si una web falla en `debug`: aparece en el `Resumen debug` final con la URL y el detalle seguro.
- Si una web falla en `normal`: recordatorio solo entre las 12:00 y las 12:15.
- Si una web se lee correctamente en `debug`: archivo `.txt` con nombre identificativo.
- Si una web cambia en `normal`: archivo `.txt` con nombre identificativo.
- Si una web esta en `manual_summary`: en `debug`, aparece en el resumen final; en `normal`, solo queda registrado en logs.

Cada `.txt` incluye:

- Nombre.
- URL.
- Metodo usado (`http` o `browser`).
- Estado (`baseline`, `unchanged` o `changed`).
- Fecha Madrid.
- Texto extraido.

Las webs con error no generan `.txt`.

## Probar en local

Instala dependencias:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

Puedes usar `.env.example` como plantilla para crear tu `.env` local. El archivo `.env` real no se sube a GitHub porque puede contener secretos.

Prueba sin enviar nada real a Telegram:

```powershell
$env:MONITOR_URLS_JSON = '[{"name":"Nombre pagina","url":"https://ejemplo.com","expected_terms":["ejemplo"]}]'
$env:DRY_RUN = "1"
$env:ACTION_TO_RUN = "debug"
python monitor.py
```

Con `DRY_RUN=1`, el script imprime los mensajes y una vista previa de los `.txt`, pero no envia nada.

Para probar solo la extraccion de texto:

```powershell
$env:MONITOR_URLS_JSON = '[{"name":"Nombre pagina","url":"https://ejemplo.com","expected_terms":["ejemplo"]}]'
python dump_urls_text.py
```

## Cambiar o anadir URLs

No cambies `monitor.py` para anadir URLs.

Cambia el secret `MONITOR_URLS_JSON` en GitHub:

```text
Settings > Secrets and variables > Actions > MONITOR_URLS_JSON
```

Despues ejecuta `Run workflow` para probarlo.

## Privacidad

- Las URLs no estan en el codigo.
- Las URLs no estan en el README.
- Las URLs estan en `MONITOR_URLS_JSON`.
- Los logs publicos ocultan la URL cuando hay errores.
- Los mensajes privados de Telegram y los `.txt` si incluyen la URL.

## Coste

El repositorio debe mantenerse como `Public`.

En repositorios publicos, GitHub Actions con runners estandar no consume los minutos gratuitos de repositorios privados. cron-job.org tambien es gratuito para este uso.

Si algun dia cambias el repositorio a `Private`, revisa el consumo de minutos de GitHub Actions.

## Notas

- Idealista puede bloquear el acceso automatico con verificacion de dispositivo. Usa `mode: "manual_summary"` o solicita acceso a la Search API oficial.
- El estado se guarda con cache de GitHub Actions. Si GitHub borra la cache, la siguiente ejecucion creara una nueva base inicial.
- Si cron-job.org deja de ejecutar, revisa que el token de GitHub no haya caducado.
