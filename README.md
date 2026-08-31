# Telegram Event Bot

Bot para crear eventos y controlar asistentes con:
- ✅ Asistiré
- 🤔 Quizás
- ❌ No asistiré
- 👥 Lista de asistentes
- ➕ Añadir acompañantes (+1, +2, etc.)
- Control del total real de personas
- Solo una respuesta por usuario y evento
- Administración básica para crear/cancelar eventos

## Requisitos
Python 3.10+

## Instalación
```bash
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y pon el token que te dio BotFather:
```env
BOT_TOKEN=PEGA_AQUI_EL_TOKEN
```

## Arranque
```bash
python bot.py
```

## Uso
En el grupo:
- `/crear` — inicia el asistente para crear un evento.
- `/eventos` — muestra los eventos activos.
- `/cancelar ID` — cancela un evento (solo administradores).

Al crear un evento, el bot publica los botones de asistencia.

### Acompañantes
Cada usuario puede pulsar "➕ Acompañantes" y elegir cuántas personas adicionales lleva.
Ejemplo:
`@juan — 1 + 1 acompañante = 2 personas`

El bot contabiliza personas, no solo cuentas de Telegram.
