# Fitbit Auth GUI

## Qué hace
Esta utilidad crea una pequeña interfaz en Tkinter junto a un servidor Flask para completar únicamente la autenticación OAuth 2.0 de Fitbit. Tras autorizar, genera y actualiza los archivos `credentials.json` y `fitbit_tokens.json` con el formato solicitado, asociando cada par de `client_id` y `client_secret` con los tokens que devuelve Fitbit.

## Prerrequisitos
1. Contar con Python 3.10.11.
2. Crear una aplicación en el panel de desarrolladores de Fitbit.
3. Registrar la URL de redirección `http://localhost:5000/callback` en la configuración de la aplicación.

## Instalación
### Crear y activar un entorno virtual
- **Windows**
  ```bash
  python -m venv .venv
  .venv\Scripts\activate
  ```
- **macOS / Linux**
  ```bash
  python -m venv .venv
  source .venv/bin/activate
  ```

### Instalar dependencias
```bash
pip install -r requirements.txt
```
## Uso
1. Ejecuta la aplicación:
   ```bash
   python fitbit_auth_gui.py
   ```
2. Ingresa el **Client ID** y el **Client Secret** de tu aplicación Fitbit.
3. Haz clic en **Autenticar**. Se abrirá el navegador con la pantalla de consentimiento de Fitbit.
4. Autoriza el acceso. Una vez completado el flujo, revisa los archivos `credentials.json` y `fitbit_tokens.json` en el directorio del proyecto para confirmar que contienen las credenciales y tokens actualizados.

## Notas
- Los alcances solicitados son: `activity`, `heartrate`, `sleep`, `profile`, `respiratory_rate`, `oxygen_saturation`, `weight` y `settings`.
- Puedes repetir la autenticación con un nuevo `client_id`; el archivo `credentials.json` agregará la entrada sin eliminar las existentes.
- Conserva los archivos JSON en un lugar seguro. Contienen secretos y tokens de acceso que otorgan permisos sobre la cuenta de Fitbit.
