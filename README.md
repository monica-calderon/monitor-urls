# Monitor de URLs con Telegram

Este proyecto revisa paginas web privadas cada 15 minutos, avisa por Telegram si cambia el texto y envia un archivo `.txt` por cada pagina leida correctamente.

Las URLs no estan escritas en el codigo ni en el README. Se guardan en GitHub Secrets para que el repositorio pueda seguir siendo publico sin mostrar las paginas monitorizadas.

## Como funciona

1. cron-job.org lanza el workflow de GitHub Actions cada 15 minutos.
2. GitHub Actions ejecuta `monitor.py`.
3. El script lee las URLs desde el secret `MONITOR_URLS_JSON`.
4. Cada pagina se descarga primero por HTTP normal y, si hace falta, con Chromium mediante Playwright.
5. Si una pagina cambia, el bot envia un resumen por Telegram.
6. Si una pagina se lee correctamente, el bot envia un `.txt` individual con el texto extraido.
7. Si una pagina falla, el bot envia un error por Telegram y continua con las demas.

## Archivos importantes

- `monitor.py`: bot principal.
- `dump_urls_text.py`: prueba local para ver el texto que se extrae de las paginas.
- `requirements.txt`: dependencias de Python.
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
- `summary`: opcional. Texto fijo que se enviara en Telegram cuando `mode` sea `manual_summary`.

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
- Se envia un mensaje de Telegram con el enlace para revisar manualmente.
- Si existe `summary`, tambien se incluye en el mensaje.

La alternativa fiable para automatizar Idealista es solicitar acceso a su Search API oficial.

## Probar manualmente en GitHub

1. Entra en la pestana `Actions`.
2. Abre el workflow `Monitor URLs`.
3. Pulsa `Run workflow`.
4. Espera a que termine.
5. Comprueba Telegram.

La primera ejecucion crea una base inicial. Puede no enviar resumen de cambios, pero si enviara los `.txt` de las paginas que se hayan leido correctamente.

## Configurar cron-job.org cada 15 minutos

GitHub Actions no garantiza que `schedule` se ejecute exactamente cada 15 minutos. Por eso este proyecto usa cron-job.org para lanzar el workflow mediante API.

En cron-job.org crea un cron job:

- Schedule: cada 15 minutos.
- Method: `POST`.
- URL:

```text
https://api.github.com/repos/monica-calderon/monitor-urls/actions/workflows/monitor.yml/dispatches
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
  "ref": "main"
}
```

## Crear el token de GitHub para cron-job.org

Crea un fine-grained personal access token en GitHub:

```text
GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens
```

Configuralo asi:

- Repository access: solo `monica-calderon/monitor-urls`.
- Permissions:
  - `Actions`: `Read and write`.
  - `Contents`: `Read-only`, si GitHub lo pide.
- Caducidad: la que prefieras. Si caduca, cron-job.org dejara de lanzar el workflow.

Copia el token y usalo en cron-job.org en el header:

```text
Authorization: Bearer TU_GITHUB_TOKEN
```

No guardes este token en el repositorio.

## Que recibiras en Telegram

Por cada ejecucion real:

- Si una web cambia: mensaje con resumen de cambios.
- Si una web falla: mensaje de error con la URL para revisar manualmente.
- Si una web se lee correctamente: archivo `.txt` con nombre identificativo.
- Si una web esta en `manual_summary`: mensaje con enlace manual y resumen fijo opcional.

Cada `.txt` incluye:

- Nombre.
- URL.
- Metodo usado (`http` o `browser`).
- Estado (`baseline`, `unchanged` o `changed`).
- Fecha UTC.
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

Prueba sin enviar nada real a Telegram:

```powershell
$env:MONITOR_URLS_JSON = '[{"name":"Nombre pagina","url":"https://ejemplo.com","expected_terms":["ejemplo"]}]'
$env:DRY_RUN = "1"
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
