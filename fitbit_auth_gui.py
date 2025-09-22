"""Interfaz gráfica para gestionar la autenticación OAuth 2.0 de Fitbit."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import tkinter as tk
from flask import Flask, jsonify, request
from requests import exceptions as requests_exceptions
from requests_oauthlib import OAuth2Session
from tkinter import messagebox

OAUTH_SCOPES = [
    "activity",
    "heartrate",
    "sleep",
    "profile",
    "respiratory_rate",
    "oxygen_saturation",
    "weight",
    "settings",
]
TOKEN_URL = "https://api.fitbit.com/oauth2/token"
REDIRECT_URI = "http://localhost:5000/callback"
AUTHORIZATION_BASE_URL = "https://www.fitbit.com/oauth2/authorize"

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKENS_FILE = BASE_DIR / "fitbit_tokens.json"

flask_app = Flask(__name__)

_gui_log_queue: Optional["queue.Queue[Dict[str, str]]"] = None
_active_sessions: Dict[str, Dict[str, Any]] = {}
_state_to_client: Dict[str, str] = {}
_sessions_lock = threading.Lock()
_credentials_lock = threading.Lock()
_tokens_lock = threading.Lock()
_server_thread: Optional[threading.Thread] = None


def set_log_queue(log_queue: "queue.Queue[Dict[str, str]]") -> None:
    """Registra la cola de mensajes para comunicar Flask con la GUI."""

    global _gui_log_queue
    _gui_log_queue = log_queue


def emit_log(message: str, status: bool = False) -> None:
    """Envía un mensaje para que la GUI lo muestre."""

    if _gui_log_queue is None:
        return
    event_type = "status" if status else "log"
    _gui_log_queue.put({"type": event_type, "message": message})


def ensure_json_file(path: Path, default: Any) -> Any:
    """Crea un archivo JSON con un valor por defecto si no existe."""

    if not path.exists():
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        # Si el archivo está corrupto, se reescribe con el valor por defecto.
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        return default


def save_credentials(client_id: str, client_secret: str) -> None:
    """Guarda o actualiza las credenciales en credentials.json."""

    with _credentials_lock:
        data: List[Dict[str, str]] = ensure_json_file(CREDENTIALS_FILE, [])
        for entry in data:
            if entry.get("client_id") == client_id:
                entry["client_secret"] = client_secret
                break
        else:
            data.append({"client_id": client_id, "client_secret": client_secret})
        CREDENTIALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_tokens() -> Dict[str, Any]:
    """Carga el contenido de fitbit_tokens.json."""

    with _tokens_lock:
        return ensure_json_file(TOKENS_FILE, {})


def save_token(client_id: str, token: Dict[str, Any]) -> None:
    """Guarda el token de Fitbit para el client_id indicado."""

    with _tokens_lock:
        tokens: Dict[str, Any] = ensure_json_file(TOKENS_FILE, {})
        tokens[client_id] = token
        TOKENS_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def register_session(client_id: str, client_secret: str) -> None:
    """Almacena temporalmente los datos necesarios para completar el flujo OAuth."""

    with _sessions_lock:
        _active_sessions[client_id] = {"secret": client_secret}
        # Elimina estados previos asociados al mismo client_id para evitar confusiones.
        stale_states = [key for key, value in _state_to_client.items() if value == client_id]
        for state in stale_states:
            _state_to_client.pop(state, None)


def start_flask_server() -> None:
    """Arranca el servidor Flask en un hilo daemon si no está activo."""

    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return

    def run() -> None:
        flask_app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

    _server_thread = threading.Thread(target=run, daemon=True)
    _server_thread.start()
    emit_log("Servidor local iniciado en http://localhost:5000.")


def trigger_authorization(client_id: str) -> str:
    """Solicita al servidor Flask que genere la URL de autorización."""

    params = {"client_id": client_id}
    last_error: Optional[Exception] = None
    for _ in range(10):
        try:
            response = requests.get(
                "http://localhost:5000/auth", params=params, timeout=5
            )
            if "application/json" in response.headers.get("Content-Type", ""):
                payload = response.json()
                return payload.get("message", "Autorización iniciada.")
            return response.text
        except requests_exceptions.ConnectionError as exc:
            last_error = exc
            time.sleep(0.3)
        except requests_exceptions.RequestException as exc:
            raise exc
    if last_error:
        raise last_error
    raise requests_exceptions.ConnectionError("No se pudo conectar al servidor Flask.")


@flask_app.route("/auth", methods=["GET"])
def auth_route():
    """Genera la URL de autorización de Fitbit y abre el navegador."""

    client_id = request.args.get("client_id", "").strip()
    if not client_id:
        emit_log("Solicitud de /auth sin client_id.")
        return jsonify({"error": "Falta el client_id."}), 400

    with _sessions_lock:
        session_info = _active_sessions.get(client_id)
    if not session_info:
        message = "Client ID no registrado. Inicia la autenticación desde la aplicación."
        emit_log(message)
        return jsonify({"error": message}), 400

    oauth = OAuth2Session(client_id, redirect_uri=REDIRECT_URI, scope=OAUTH_SCOPES)
    authorization_url, state = oauth.authorization_url(AUTHORIZATION_BASE_URL)
    with _sessions_lock:
        session_info["state"] = state
        _state_to_client[state] = client_id

    webbrowser.open(authorization_url)
    emit_log(f"Abriendo navegador para autorizar la app {client_id}...")
    return jsonify({"message": "Autorización iniciada. Revisa el navegador."})


@flask_app.route("/callback", methods=["GET"])
def callback_route():
    """Procesa la respuesta de Fitbit y guarda el token."""

    if "error" in request.args:
        error_description = request.args.get("error_description") or request.args.get("error")
        emit_log(f"Error devuelto por Fitbit: {error_description}")
        return (
            "<html><body><h1>Autenticación fallida</h1><p>"
            f"{error_description}</p></body></html>",
            400,
        )

    state = request.args.get("state")
    if not state:
        emit_log("Respuesta de Fitbit sin parámetro state.")
        return (
            "<html><body><h1>Respuesta inválida</h1><p>No se recibió el parámetro state.</p>"
            "</body></html>",
            400,
        )

    with _sessions_lock:
        client_id = _state_to_client.pop(state, None)
        session_info = _active_sessions.get(client_id) if client_id else None

    if not client_id or not session_info:
        emit_log("No se encontró la sesión asociada a la respuesta de Fitbit.")
        return (
            "<html><body><h1>Sesión no encontrada</h1><p>Inicia la autenticación desde la "
            "aplicación nuevamente.</p></body></html>",
            400,
        )

    client_secret = session_info.get("secret")
    oauth = OAuth2Session(
        client_id,
        redirect_uri=REDIRECT_URI,
        scope=OAUTH_SCOPES,
        state=state,
    )

    try:
        token = oauth.fetch_token(
            TOKEN_URL,
            authorization_response=request.url,
            client_secret=client_secret,
        )
    except Exception as exc:  # pylint: disable=broad-except
        emit_log(f"Error al obtener el token: {exc}")
        return (
            "<html><body><h1>Autenticación fallida</h1><p>No se pudo obtener el token."
            "</p></body></html>",
            500,
        )

    save_token(client_id, token)
    emit_log(f"Autenticación exitosa para {client_id}.", status=True)

    with _sessions_lock:
        _active_sessions.pop(client_id, None)

    return (
        "<html><body><h1>Autenticación completada</h1><p>Ya puedes cerrar esta ventana."
        "</p></body></html>",
        200,
    )


class AuthApp(tk.Tk):
    """Ventana principal para gestionar la autenticación."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Autenticación Fitbit")
        self.resizable(False, False)

        self.client_id_var = tk.StringVar()
        self.client_secret_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ingresa tu Client ID y Client Secret.")

        main_frame = tk.Frame(self, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="Client ID:").grid(row=0, column=0, sticky="w")
        client_id_entry = tk.Entry(main_frame, textvariable=self.client_id_var, width=40)
        client_id_entry.grid(row=0, column=1, pady=2)

        tk.Label(main_frame, text="Client Secret:").grid(row=1, column=0, sticky="w")
        client_secret_entry = tk.Entry(
            main_frame, textvariable=self.client_secret_var, show="*", width=40
        )
        client_secret_entry.grid(row=1, column=1, pady=2)

        authenticate_button = tk.Button(
            main_frame, text="Autenticar", command=self.on_authenticate
        )
        authenticate_button.grid(row=2, column=0, columnspan=2, pady=(8, 10))

        status_label = tk.Label(
            main_frame,
            textvariable=self.status_var,
            anchor="w",
            fg="#1b5e20",
        )
        status_label.grid(row=3, column=0, columnspan=2, sticky="we")

        tk.Label(main_frame, text="Registro:").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        self.log_text = tk.Text(main_frame, height=10, width=50, state="disabled")
        self.log_text.grid(row=5, column=0, columnspan=2, sticky="we")

        main_frame.grid_columnconfigure(1, weight=1)

        self.log_queue: "queue.Queue[Dict[str, str]]" = queue.Queue()
        set_log_queue(self.log_queue)
        self.after(200, self.process_log_queue)

        client_id_entry.focus()

    def log_message(self, message: str) -> None:
        """Añade un mensaje al área de registro."""

        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def process_log_queue(self) -> None:
        """Procesa los mensajes enviados desde el servidor Flask."""

        while not self.log_queue.empty():
            record = self.log_queue.get_nowait()
            message = record.get("message", "")
            if record.get("type") == "status":
                self.status_var.set(message)
            self.log_message(message)
        self.after(200, self.process_log_queue)

    def on_authenticate(self) -> None:
        """Acciones que ocurren al pulsar el botón Autenticar."""

        client_id = self.client_id_var.get().strip()
        client_secret = self.client_secret_var.get().strip()
        if not client_id or not client_secret:
            messagebox.showwarning(
                "Datos incompletos",
                "Debes proporcionar el Client ID y el Client Secret.",
            )
            return

        try:
            save_credentials(client_id, client_secret)
        except OSError as exc:
            messagebox.showerror(
                "Error al guardar",
                f"No se pudo guardar credentials.json: {exc}",
            )
            self.log_message(f"Error al guardar credentials.json: {exc}")
            return

        register_session(client_id, client_secret)
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        try:
            start_flask_server()
        except OSError as exc:
            messagebox.showerror("Servidor", f"No se pudo iniciar Flask: {exc}")
            self.log_message(f"No se pudo iniciar Flask: {exc}")
            return

        self.status_var.set("Iniciando autorización...")
        try:
            message = trigger_authorization(client_id)
            self.log_message(message)
        except requests_exceptions.RequestException as exc:
            messagebox.showerror("Autenticación", f"Error al iniciar la autorización: {exc}")
            self.log_message(f"Error al iniciar la autorización: {exc}")
            return

        self.status_var.set("Autoriza el acceso en el navegador.")


if __name__ == "__main__":
    AuthApp().mainloop()