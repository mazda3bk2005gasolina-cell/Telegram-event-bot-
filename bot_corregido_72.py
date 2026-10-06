import os
import sqlite3
import html
import logging

from dotenv import load_dotenv

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    TypeHandler,
    filters,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("No se ha encontrado BOT_TOKEN.")


ADMIN_IDS = {
    126421812,   # @a_never
    8761859,     # Cristo
    5710212742,  # Gus / GuilArenas
    13017110,    # Juan
}

DB_FILE = "events.db"


# ============================================================
# DESTINOS
# ============================================================

DESTINATIONS = {
    -1003290070983: {
        "name": "Canal de prueba",
        "thread_id": None,
        "topic_name": None,
    },

    -1001839833790: {
        "name": "Maxiscooter Club",
        "thread_id": None,
        "topic_name": "Rutas",
    },
}


# ============================================================
# ESTADOS CREACIÓN
# ============================================================

(
    NAME,
    DATE,
    TIME,
    PLACE,
    ROUTE_MAPS,
    PHOTO,
    COMMENTS,
    CONFIRM,
) = range(8)


# ============================================================
# BASE DE DATOS
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            place TEXT NOT NULL,
            route_maps TEXT,
            photo_file_id TEXT,
            comments TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            published_chat_id INTEGER,
            published_message_id INTEGER,
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            status TEXT NOT NULL,
            companions INTEGER DEFAULT 0,
            UNIQUE(event_id, user_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            username TEXT,
            chat_type TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_topics (
            chat_id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            topic_name TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # MIGRACIÓN V7.2
    # --------------------------------------------------------
    # Si events.db ya existía desde V7/V7.1, añadimos
    # automáticamente la nueva columna route_maps.
    # --------------------------------------------------------

    columns = conn.execute(
        "PRAGMA table_info(events)"
    ).fetchall()

    column_names = {
        row["name"]
        for row in columns
    }

    if "route_maps" not in column_names:

        conn.execute(
            """
            ALTER TABLE events
            ADD COLUMN route_maps TEXT
            """
        )

    conn.commit()
    conn.close()


# ============================================================
# UTILIDADES
# ============================================================

def clean(value):

    if value is None:
        return ""

    return html.escape(str(value))


def authorized(user_id):

    return user_id in ADMIN_IDS


# ============================================================
# ASISTENCIA
# ============================================================

def get_counts(event_id):

    conn = db()

    rows = conn.execute(
        """
        SELECT
            status,
            SUM(1 + companions) AS total
        FROM attendance
        WHERE event_id=?
        GROUP BY status
        """,
        (event_id,),
    ).fetchall()

    conn.close()

    counts = {
        "yes": 0,
        "maybe": 0,
        "no": 0,
    }

    for row in rows:

        status = row["status"]

        if status in counts:
            counts[status] = row["total"] or 0

    return counts


def attendance_keyboard(event_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Asistiré",
                callback_data=f"attend:yes:{event_id}",
            ),
            InlineKeyboardButton(
                "❓ Quizás",
                callback_data=f"attend:maybe:{event_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ No asistiré",
                callback_data=f"attend:no:{event_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Acompañantes",
                callback_data=f"companions:{event_id}",
            ),
            InlineKeyboardButton(
                "👀 Ver asistentes",
                callback_data=f"list:{event_id}",
            ),
        ],
    ])


# ============================================================
# TEXTO DE EVENTO
# ============================================================

def event_text(event):

    counts = get_counts(event["id"])

    text = (
        f"🏍️ <b>{clean(event['name'])}</b>\n\n"
        f"📅 <b>Fecha:</b> {clean(event['date'])}\n"
        f"⏰ <b>Hora de quedada:</b> "
        f"{clean(event['time'])}\n"
        f"📍 <b>Lugar de encuentro:</b> "
        f"{clean(event['place'])}\n"
    )

    if event["comments"]:

        text += (
            "\n"
            f"ℹ️ <b>Información:</b>\n"
            f"{clean(event['comments'])}\n"
        )

    text += (
        "\n"
        "👥 <b>Asistencia</b>\n"
        f"✅ Asistiré: <b>{counts['yes']}</b>\n"
        f"❓ Quizás: <b>{counts['maybe']}</b>\n"
        f"❌ No asistiré: <b>{counts['no']}</b>"
    )

    return text


# ============================================================
# BOTÓN GOOGLE MAPS
# ============================================================

def event_keyboard(event):

    buttons = []

    route_maps = event["route_maps"]

    if route_maps:

        buttons.append([
            InlineKeyboardButton(
                "🗺️ Ver ruta en Google Maps",
                url=route_maps,
            )
        ])

    attendance_buttons = attendance_keyboard(
        event["id"]
    ).inline_keyboard

    buttons.extend(attendance_buttons)

    return InlineKeyboardMarkup(buttons)


# ============================================================
# TOPICS
# ============================================================

def save_topic(
    chat_id,
    thread_id,
    topic_name="",
):

    if chat_id not in DESTINATIONS:
        return

    conn = db()

    conn.execute(
        """
        INSERT INTO channel_topics
        (
            chat_id,
            thread_id,
            topic_name,
            updated_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)

        ON CONFLICT(chat_id)
        DO UPDATE SET
            thread_id=excluded.thread_id,
            topic_name=excluded.topic_name,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            chat_id,
            thread_id,
            topic_name or "",
        ),
    )

    conn.commit()
    conn.close()


def get_saved_thread_id(chat_id):

    conn = db()

    row = conn.execute(
        """
        SELECT thread_id
        FROM channel_topics
        WHERE chat_id=?
        """,
        (chat_id,),
    ).fetchone()

    conn.close()

    if row and row["thread_id"] is not None:
        return row["thread_id"]

    return None


def get_destinations():

    result = []

    for chat_id, info in DESTINATIONS.items():

        destination = dict(info)
        destination["chat_id"] = chat_id

        if info["topic_name"]:

            destination["thread_id"] = (
                get_saved_thread_id(chat_id)
            )

        result.append(destination)

    return result


def get_destination(chat_id):

    info = DESTINATIONS.get(chat_id)

    if not info:
        return None

    destination = dict(info)
    destination["chat_id"] = chat_id

    if info["topic_name"]:

        destination["thread_id"] = (
            get_saved_thread_id(chat_id)
        )

    return destination


def channel_keyboard():

    buttons = []

    for destination in get_destinations():

        name = destination["name"]

        if destination["topic_name"]:

            if destination["thread_id"] is not None:

                label = (
                    f"📢 {name} → "
                    f"{destination['topic_name']}"
                )

            else:

                label = (
                    f"⚠️ {name} → "
                    f"{destination['topic_name']} "
                    f"(sin configurar)"
                )

        else:

            label = f"📢 {name}"

        buttons.append([
            InlineKeyboardButton(
                label[:60],
                callback_data=(
                    f"publish:{destination['chat_id']}"
                ),
            )
        ])

    return InlineKeyboardMarkup(buttons)


# ============================================================
# DIAGNÓSTICO DE CHATS
# ============================================================

def save_channel(chat):

    if not chat:
        return

    conn = db()

    conn.execute(
        """
        INSERT INTO channels
        (
            chat_id,
            title,
            username,
            chat_type,
            active,
            updated_at
        )
        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)

        ON CONFLICT(chat_id)
        DO UPDATE SET
            title=excluded.title,
            username=excluded.username,
            chat_type=excluded.chat_type,
            active=1,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            chat.id,
            chat.title or "",
            chat.username or "",
            chat.type,
        ),
    )

    conn.commit()
    conn.close()


async def detect_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        if update.channel_post:

            message = update.channel_post

            print(
                "CHANNEL POST:",
                message.chat.id,
                message.chat.title,
            )

            save_channel(message.chat)
            return

        if update.edited_channel_post:

            message = update.edited_channel_post

            save_channel(message.chat)
            return

        if update.my_chat_member:

            save_channel(
                update.my_chat_member.chat
            )

            return

        if update.message:

            message = update.message
            chat = message.chat

            if chat.type in (
                "group",
                "supergroup",
                "channel",
            ):

                save_channel(chat)

            return

    except Exception as e:

        print(
            "Error en detect_channel:",
            repr(e),
        )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = (
        "🏍️ <b>Bot de rutas</b>\n\n"
        "/crear — Crear una nueva ruta\n"
        "/eventos — Ver próximas rutas\n"
        "/cancelar — Cancelar una ruta\n"
        "/id — Ver información del chat\n"
        "/canales — Ver destinos configurados"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# CANALES
# ============================================================

async def canales(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not authorized(update.effective_user.id):
        return

    text = "📢 <b>Destinos configurados</b>\n\n"

    for destination in get_destinations():

        text += (
            f"📺 <b>{clean(destination['name'])}</b>\n"
            f"ID: <code>{destination['chat_id']}</code>\n"
        )

        if destination["topic_name"]:

            thread_id = destination["thread_id"]

            text += (
                f"🧵 Tema: "
                f"<b>{clean(destination['topic_name'])}</b>\n"
            )

            if thread_id is not None:

                text += (
                    f"Thread ID: "
                    f"<code>{thread_id}</code>\n"
                    "🟢 Configurado\n\n"
                )

            else:

                text += (
                    "🔴 Sin configurar\n\n"
                )

        else:

            text += "🟢 Listo para publicar\n\n"

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# ACTUALIZAR PUBLICACIÓN
# ============================================================

async def update_published_event(
    context,
    event_id,
):

    conn = db()

    event = conn.execute(
        """
        SELECT *
        FROM events
        WHERE id=?
        """,
        (event_id,),
    ).fetchone()

    conn.close()

    if not event:
        return

    chat_id = event["published_chat_id"]
    message_id = event["published_message_id"]

    if not chat_id or not message_id:
        return

    try:

        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=event_text(event),
            parse_mode="HTML",
            reply_markup=event_keyboard(event),
        )

    except Exception as e:

        print(
            "No se pudo actualizar publicación:",
            repr(e),
        )


# ============================================================
# CREAR — INICIO
# ============================================================

async def create_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not authorized(update.effective_user.id):

        await update.message.reply_text(
            "❌ No estás autorizado para crear rutas."
        )

        return ConversationHandler.END

    context.user_data["create"] = {}

    await update.message.reply_text(
        "🏍️ <b>Crear nueva ruta</b>\n\n"
        "¿Cuál es el nombre de la ruta?",
        parse_mode="HTML",
    )

    return NAME


# ============================================================
# NOMBRE
# ============================================================

async def create_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["name"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "📅 ¿Qué fecha tendrá la ruta?\n\n"
        "Ejemplo: <code>15/10/2026</code>",
        parse_mode="HTML",
    )

    return DATE


# ============================================================
# FECHA
# ============================================================

async def create_date(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["date"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "⏰ ¿A qué hora será la <b>quedada</b>?",
        parse_mode="HTML",
    )

    return TIME


# ============================================================
# HORA
# ============================================================

async def create_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["time"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "📍 ¿Cuál es el <b>lugar de encuentro / quedada</b>?",
        parse_mode="HTML",
    )

    return PLACE


# ============================================================
# LUGAR
# ============================================================

async def create_place(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["place"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "🗺️ Ahora pega el <b>enlace de Google Maps "
        "de la ruta</b>.\n\n"
        "Si no quieres añadirlo, escribe "
        "<code>ninguno</code>.",
        parse_mode="HTML",
    )

    return ROUTE_MAPS


# ============================================================
# GOOGLE MAPS
# ============================================================

async def create_route_maps(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    maps_url = update.message.text.strip()

    if maps_url.lower() in (
        "ninguno",
        "no",
        "nada",
        "-",
    ):

        maps_url = ""

    elif not (
        maps_url.startswith("http://")
        or maps_url.startswith("https://")
    ):

        await update.message.reply_text(
            "⚠️ Ese enlace no parece válido.\n\n"
            "Pega el enlace completo de Google Maps "
            "o escribe <code>ninguno</code>.",
            parse_mode="HTML",
        )

        return ROUTE_MAPS

    context.user_data["create"]["route_maps"] = (
        maps_url
    )

    await update.message.reply_text(
        "📸 Ahora envíame <b>la foto de la ruta</b>.",
        parse_mode="HTML",
    )

    return PHOTO


# ============================================================
# FOTO
# ============================================================

async def create_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message.photo:

        await update.message.reply_text(
            "⚠️ Necesito que envíes una foto."
        )

        return PHOTO

    photo = update.message.photo[-1]

    context.user_data["create"]["photo_file_id"] = (
        photo.file_id
    )

    await update.message.reply_text(
        "ℹ️ Añade información o comentarios "
        "para la ruta.\n\n"
        "Si no quieres añadir nada, escribe "
        "<code>ninguno</code>.",
        parse_mode="HTML",
    )

    return COMMENTS


# ============================================================
# COMENTARIOS
# ============================================================

async def create_comments(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    comments = update.message.text.strip()

    if comments.lower() in (
        "ninguno",
        "no",
        "nada",
        "-",
    ):

        comments = ""

    context.user_data["create"]["comments"] = (
        comments
    )

    data = context.user_data["create"]

    preview = (
        "🏍️ <b>VISTA PREVIA</b>\n\n"
        f"📌 <b>{clean(data['name'])}</b>\n\n"
        f"📅 {clean(data['date'])}\n"
        f"⏰ Hora de quedada: "
        f"{clean(data['time'])}\n"
        f"📍 Lugar de encuentro: "
        f"{clean(data['place'])}\n"
    )

    if data.get("route_maps"):

        preview += (
            "\n🗺️ <b>Ruta Google Maps:</b>\n"
            f"{clean(data['route_maps'])}\n"
        )

    if data.get("comments"):

        preview += (
            "\nℹ️ "
            f"{clean(data['comments'])}\n"
        )

    preview += (
        "\n\n¿Quieres crear esta ruta?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Confirmar",
                callback_data="create_confirm",
            ),
            InlineKeyboardButton(
                "❌ Cancelar",
                callback_data="create_cancel",
            ),
        ]
    ])

    await update.message.reply_photo(
        photo=data["photo_file_id"],
        caption=preview,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    return CONFIRM


# ============================================================
# CANCELAR PREVIEW
# ============================================================

async def cancel_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "create",
        None,
    )

    await query.edit_message_caption(
        caption="❌ Creación de ruta cancelada."
    )

    return ConversationHandler.END


# ============================================================
# ELEGIR DESTINO
# ============================================================

async def choose_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not context.user_data.get("create"):

        await query.edit_message_caption(
            caption="❌ No hay ninguna ruta en creación."
        )

        return ConversationHandler.END

    await query.edit_message_caption(
        caption=(
            "📢 <b>¿Dónde quieres publicar la ruta?</b>\n\n"
            "Elige un destino:"
        ),
        parse_mode="HTML",
        reply_markup=channel_keyboard(),
    )

    return CONFIRM


# ============================================================
# PUBLICAR
# ============================================================

async def publish_selected_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    try:

        channel_id = int(
            query.data.split(":", 1)[1]
        )

    except Exception:

        await query.edit_message_caption(
            caption="❌ Destino no válido."
        )

        return ConversationHandler.END

    destination = get_destination(channel_id)

    if not destination:

        await query.edit_message_caption(
            caption="❌ Destino no configurado."
        )

        return ConversationHandler.END

    thread_id = destination.get(
        "thread_id"
    )

    if destination["topic_name"]:

        if thread_id is None:

            await query.edit_message_caption(
                caption=(
                    "⚠️ <b>El tema Rutas no está "
                    "configurado.</b>\n\n"
                    "Entra en Maxiscooter Club → "
                    "Rutas y escribe /id."
                ),
                parse_mode="HTML",
            )

            return CONFIRM

    data = context.user_data.get("create")

    if not data:

        await query.edit_message_caption(
            caption="❌ No hay datos de la ruta."
        )

        return ConversationHandler.END

    conn = db()

    cursor = conn.execute(
        """
        INSERT INTO events
        (
            name,
            date,
            time,
            place,
            route_maps,
            photo_file_id,
            comments,
            created_by,
            active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            data["name"],
            data["date"],
            data["time"],
            data["place"],
            data.get("route_maps", ""),
            data["photo_file_id"],
            data.get("comments", ""),
            update.effective_user.id,
        ),
    )

    event_id = cursor.lastrowid

    conn.commit()

    event = conn.execute(
        """
        SELECT *
        FROM events
        WHERE id=?
        """,
        (event_id,),
    ).fetchone()

    conn.close()

    send_args = {
        "chat_id": channel_id,
        "photo": event["photo_file_id"],
        "caption": event_text(event),
        "parse_mode": "HTML",
        "reply_markup": event_keyboard(event),
    }

    if thread_id is not None:

        send_args["message_thread_id"] = thread_id

    try:

        sent = await context.bot.send_photo(
            **send_args
        )

    except Exception as e:

        print(
            "ERROR PUBLICANDO:",
            repr(e),
        )

        conn = db()

        conn.execute(
            """
            UPDATE events
            SET active=0
            WHERE id=?
            """,
            (event_id,),
        )

        conn.commit()
        conn.close()

        await query.edit_message_caption(
            caption=(
                "❌ <b>No se pudo publicar la ruta.</b>\n\n"
                f"<code>{clean(e)}</code>"
            ),
            parse_mode="HTML",
        )

        return ConversationHandler.END

    conn = db()

    conn.execute(
        """
        UPDATE events
        SET
            published_chat_id=?,
            published_message_id=?
        WHERE id=?
        """,
        (
            channel_id,
            sent.message_id,
            event_id,
        ),
    )

    conn.commit()
    conn.close()

    context.user_data.pop(
        "create",
        None,
    )

    await query.edit_message_caption(
        caption=(
            "✅ <b>Ruta publicada correctamente.</b>\n\n"
            f"📢 {clean(destination['name'])}"
        ),
        parse_mode="HTML",
    )

    return ConversationHandler.END


# ============================================================
# CANCELAR CREACIÓN
# ============================================================

async def cancel_create(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data.pop(
        "create",
        None,
    )

    await update.message.reply_text(
        "❌ Creación cancelada."
    )

    return ConversationHandler.END


# ============================================================
# SELECTOR DE ACOMPAÑANTES
# ============================================================

def companions_keyboard(event_id, number):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➖",
                callback_data=f"compminus:{event_id}",
            ),
            InlineKeyboardButton(
                f"👥 {number}",
                callback_data=f"compinfo:{event_id}",
            ),
            InlineKeyboardButton(
                "➕",
                callback_data=f"compplus:{event_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "✅ Listo",
                callback_data=f"compdone:{event_id}",
            ),
        ],
    ])


async def show_companion_selector(
    query,
    event_id,
    number,
):

    await query.edit_message_text(
        (
            "👥 <b>Acompañantes</b>\n\n"
            "Indica cuántos acompañantes llevarás.\n\n"
            f"<b>{number}</b> acompañante"
            f"{'s' if number != 1 else ''}"
        ),
        parse_mode="HTML",
        reply_markup=companions_keyboard(
            event_id,
            number,
        ),
    )


# ============================================================
# CALLBACKS DE ASISTENCIA
# ============================================================

async def attendance_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    data = query.data or ""
    parts = data.split(":")

    action = parts[0]

    # --------------------------------------------------------
    # AQUÍ ESTÁ LA CORRECCIÓN IMPORTANTE DEL V7.1
    # --------------------------------------------------------
    #
    # Algunos callbacks tienen:
    #
    # attend:yes:123
    #
    # y otros:
    #
    # list:123
    # companions:123
    # compplus:123
    #
    # Por eso NO podemos usar siempre parts[2].
    # --------------------------------------------------------

    try:

        if action == "attend":

            if len(parts) != 3:
                await query.answer()
                return

            status = parts[1]
            event_id = int(parts[2])

        else:

            if len(parts) != 2:
                await query.answer()
                return

            status = None
            event_id = int(parts[1])

    except (ValueError, IndexError):

        await query.answer(
            "❌ Acción no válida.",
            show_alert=True,
        )

        return

    user = update.effective_user

    if not user:

        await query.answer()
        return

    # ========================================================
    # VER ASISTENTES
    # ========================================================

    if action == "list":

        conn = db()

        rows = conn.execute(
            """
            SELECT
                user_name,
                status,
                companions
            FROM attendance
            WHERE event_id=?
            ORDER BY
                CASE status
                    WHEN 'yes' THEN 1
                    WHEN 'maybe' THEN 2
                    WHEN 'no' THEN 3
                    ELSE 4
                END,
                user_name
            """,
            (event_id,),
        ).fetchall()

        conn.close()

        if not rows:

            text = (
                "👀 <b>ASISTENTES</b>\n\n"
                "Todavía nadie ha respondido."
            )

        else:

            yes = []
            maybe = []
            no = []

            for row in rows:

                name = clean(
                    row["user_name"]
                )

                companions = (
                    row["companions"] or 0
                )

                if companions == 1:

                    suffix = " + 1 acompañante"

                elif companions > 1:

                    suffix = (
                        f" + {companions} acompañantes"
                    )

                else:

                    suffix = ""

                line = (
                    f"• {name}{suffix}"
                )

                if row["status"] == "yes":

                    yes.append(line)

                elif row["status"] == "maybe":

                    maybe.append(line)

                elif row["status"] == "no":

                    no.append(line)

            text = "👀 <b>ASISTENTES</b>\n\n"

            if yes:

                text += (
                    "✅ <b>Asistirán</b>\n"
                    + "\n".join(yes)
                    + "\n\n"
                )

            if maybe:

                text += (
                    "❓ <b>Quizás</b>\n"
                    + "\n".join(maybe)
                    + "\n\n"
                )

            if no:

                text += (
                    "❌ <b>No asistirán</b>\n"
                    + "\n".join(no)
                )

        # Telegram limita los alertas.
        await query.answer(
            text=text[:190],
            show_alert=True,
        )

        return

    # ========================================================
    # ABRIR ACOMPAÑANTES
    # ========================================================

    if action == "companions":

        conn = db()

        row = conn.execute(
            """
            SELECT companions
            FROM attendance
            WHERE event_id=? AND user_id=?
            """,
            (
                event_id,
                user.id,
            ),
        ).fetchone()

        conn.close()

        number = (
            row["companions"]
            if row
            else 0
        )

        await query.answer()

        # ----------------------------------------------------
        # IMPORTANTE:
        # No enviamos mensaje privado.
        #
        # Se sustituye temporalmente el contenido del mensaje
        # de la ruta por el selector.
        # Al pulsar LISTO se recupera la ruta.
        # ----------------------------------------------------

        await show_companion_selector(
            query,
            event_id,
            number,
        )

        return

    # ========================================================
    # RESTAR
    # ========================================================

    if action == "compminus":

        conn = db()

        row = conn.execute(
            """
            SELECT companions
            FROM attendance
            WHERE event_id=? AND user_id=?
            """,
            (
                event_id,
                user.id,
            ),
        ).fetchone()

        current = (
            row["companions"]
            if row
            else 0
        )

        current = max(
            0,
            current - 1,
        )

        conn.close()

        user_name = (
            user.full_name
            or user.username
            or str(user.id)
        )

        conn = db()

        if row:

            conn.execute(
                """
                UPDATE attendance
                SET
                    companions=?,
                    user_name=?
                WHERE event_id=? AND user_id=?
                """,
                (
                    current,
                    user_name,
                    event_id,
                    user.id,
                ),
            )

        else:

            conn.execute(
                """
                INSERT INTO attendance
                (
                    event_id,
                    user_id,
                    user_name,
                    status,
                    companions
                )
                VALUES (?, ?, ?, 'yes', ?)
                """,
                (
                    event_id,
                    user.id,
                    user_name,
                    current,
                ),
            )

        conn.commit()
        conn.close()

        await query.answer()

        await show_companion_selector(
            query,
            event_id,
            current,
        )

        await update_published_event(
            context,
            event_id,
        )

        return

    # ========================================================
    # SUMAR
    # ========================================================

    if action == "compplus":

        conn = db()

        row = conn.execute(
            """
            SELECT companions
            FROM attendance
            WHERE event_id=? AND user_id=?
            """,
            (
                event_id,
                user.id,
            ),
        ).fetchone()

        current = (
            row["companions"]
            if row
            else 0
        )

        if current >= 10:

            conn.close()

            await query.answer(
                "Máximo 10 acompañantes.",
                show_alert=True,
            )

            return

        current += 1

        conn.close()

        user_name = (
            user.full_name
            or user.username
            or str(user.id)
        )

        conn = db()

        if row:

            conn.execute(
                """
                UPDATE attendance
                SET
                    companions=?,
                    user_name=?
                WHERE event_id=? AND user_id=?
                """,
                (
                    current,
                    user_name,
                    event_id,
                    user.id,
                ),
            )

        else:

            conn.execute(
                """
                INSERT INTO attendance
                (
                    event_id,
                    user_id,
                    user_name,
                    status,
                    companions
                )
                VALUES (?, ?, ?, 'yes', ?)
                """,
                (
                    event_id,
                    user.id,
                    user_name,
                    current,
                ),
            )

        conn.commit()
        conn.close()

        await query.answer()

        await show_companion_selector(
            query,
            event_id,
            current,
        )

        await update_published_event(
            context,
            event_id,
        )

        return

    # ========================================================
    # BOTÓN CENTRAL
    # ========================================================

    if action == "compinfo":

        await query.answer(
            "Pulsa ➕ para añadir o ➖ para quitar.",
            show_alert=True,
        )

        return

    # ========================================================
    # LISTO
    # ========================================================

    if action == "compdone":

        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM attendance
            WHERE event_id=? AND user_id=?
            """,
            (
                event_id,
                user.id,
            ),
        ).fetchone()

        conn.close()

        number = (
            row["companions"]
            if row
            else 0
        )

        # ----------------------------------------------------
        # Recuperamos el evento y restauramos el mensaje.
        # ----------------------------------------------------

        conn = db()

        event = conn.execute(
            """
            SELECT *
            FROM events
            WHERE id=?
            """,
            (event_id,),
        ).fetchone()

        conn.close()

        await query.answer(
            "Acompañantes guardados."
        )

        if event:

            try:

                await query.edit_message_caption(
                    caption=event_text(event),
                    parse_mode="HTML",
                    reply_markup=event_keyboard(event),
                )

            except Exception as e:

                print(
                    "Error restaurando evento:",
                    repr(e),
                )

        await update_published_event(
            context,
            event_id,
        )

        return

    # ========================================================
    # ASISTENCIA
    # ========================================================

    if action != "attend":

        await query.answer()
        return

    if status not in (
        "yes",
        "maybe",
        "no",
    ):

        await query.answer()
        return

    user_name = (
        user.full_name
        or user.username
        or str(user.id)
    )

    conn = db()

    existing = conn.execute(
        """
        SELECT *
        FROM attendance
        WHERE event_id=? AND user_id=?
        """,
        (
            event_id,
            user.id,
        ),
    ).fetchone()

    if existing:

        conn.execute(
            """
            UPDATE attendance
            SET
                user_name=?,
                status=?
            WHERE event_id=? AND user_id=?
            """,
            (
                user_name,
                status,
                event_id,
                user.id,
            ),
        )

    else:

        conn.execute(
            """
            INSERT INTO attendance
            (
                event_id,
                user_id,
                user_name,
                status,
                companions
            )
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                event_id,
                user.id,
                user_name,
                status,
            ),
        )

    conn.commit()
    conn.close()

    await update_published_event(
        context,
        event_id,
    )

    labels = {
        "yes": "✅ Has indicado que asistirás.",
        "maybe": "❓ Has indicado que quizás asistirás.",
        "no": "❌ Has indicado que no asistirás.",
    }

    await query.answer(
        labels[status]
    )


# ============================================================
# EVENTOS
# ============================================================

async def eventos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM events
        WHERE active=1
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()

    conn.close()

    if not rows:

        await update.message.reply_text(
            "🏍️ No hay rutas activas."
        )

        return

    for event in rows:

        text = event_text(event)

        keyboard = event_keyboard(event)

        if event["photo_file_id"]:

            try:

                await update.message.reply_photo(
                    photo=event["photo_file_id"],
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )

            except Exception as e:

                print(
                    "Error mostrando evento:",
                    repr(e),
                )

                await update.message.reply_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )

        else:

            await update.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )


# ============================================================
# CANCELAR EVENTO
# ============================================================

async def cancelar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not authorized(update.effective_user.id):
        return

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM events
        WHERE active=1
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()

    conn.close()

    if not rows:

        await update.message.reply_text(
            "No hay rutas activas para cancelar."
        )

        return

    buttons = []

    for event in rows:

        buttons.append([
            InlineKeyboardButton(
                f"❌ {event['name']}",
                callback_data=(
                    f"delete_event:{event['id']}"
                ),
            )
        ])

    await update.message.reply_text(
        "Selecciona la ruta que quieres cancelar:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def delete_event_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    if not authorized(update.effective_user.id):
        return

    try:

        event_id = int(
            query.data.split(":")[1]
        )

    except Exception:

        return

    conn = db()

    event = conn.execute(
        """
        SELECT *
        FROM events
        WHERE id=?
        """,
        (event_id,),
    ).fetchone()

    if not event:

        conn.close()

        await query.edit_message_text(
            "❌ Ruta no encontrada."
        )

        return

    conn.execute(
        """
        UPDATE events
        SET active=0
        WHERE id=?
        """,
        (event_id,),
    )

    conn.commit()
    conn.close()

    if (
        event["published_chat_id"]
        and event["published_message_id"]
    ):

        try:

            await context.bot.delete_message(
                chat_id=event["published_chat_id"],
                message_id=event["published_message_id"],
            )

        except Exception as e:

            print(
                "No se pudo eliminar publicación:",
                repr(e),
            )

    await query.edit_message_text(
        f"✅ Ruta <b>{clean(event['name'])}</b> cancelada.",
        parse_mode="HTML",
    )


# ============================================================
# /ID
# ============================================================

async def get_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    message = update.message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user or not chat:
        return

    if chat.type == "private":

        await message.reply_text(
            "Tu ID de Telegram es:\n"
            f"<code>{user.id}</code>",
            parse_mode="HTML",
        )

        return

    thread_id = getattr(
        message,
        "message_thread_id",
        None,
    )

    text = (
        "🔎 <b>Información del chat</b>\n\n"
        f"👤 Tu ID: <code>{user.id}</code>\n"
        f"💬 Chat ID: <code>{chat.id}</code>\n"
        f"🏷 Tipo: <code>{clean(chat.type)}</code>\n"
        f"📋 Nombre: "
        f"<b>{clean(chat.title or 'Sin título')}</b>\n"
        f"🧵 Thread ID: "
        f"<code>"
        f"{thread_id if thread_id is not None else 'Ninguno'}"
        f"</code>"
    )

    if (
        chat.id in DESTINATIONS
        and thread_id is not None
    ):

        destination = DESTINATIONS[chat.id]

        topic_name = destination.get(
            "topic_name"
        )

        if topic_name:

            save_topic(
                chat.id,
                thread_id,
                topic_name,
            )

            text += (
                "\n\n"
                f"✅ <b>Tema guardado para "
                f"{clean(topic_name)}</b>\n"
                "Las próximas rutas se publicarán aquí."
            )

    await message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    print(
        "ERROR DEL BOT:",
        repr(context.error),
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logging.basicConfig(
        format=(
            "%(asctime)s - "
            "%(name)s - "
            "%(levelname)s - "
            "%(message)s"
        ),
        level=logging.INFO,
    )

    init_db()

    print("")
    print("==========================================")
    print(" BOT DE RUTAS V7.2")
    print("==========================================")

    for chat_id, destination in DESTINATIONS.items():

        print(
            f"{destination['name']}: {chat_id}"
        )

        if destination["topic_name"]:

            print(
                "Tema:",
                destination["topic_name"],
            )

            print(
                "Thread ID:",
                get_saved_thread_id(chat_id),
            )

    print("==========================================")
    print("")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # DIAGNÓSTICO
    # --------------------------------------------------------

    application.add_handler(
        TypeHandler(
            Update,
            detect_channel,
        ),
        group=0,
    )

    # --------------------------------------------------------
    # CREACIÓN
    # --------------------------------------------------------

    create_conversation = ConversationHandler(

        entry_points=[
            CommandHandler(
                "crear",
                create_start,
            )
        ],

        states={

            NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    create_name,
                )
            ],

            DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    create_date,
                )
            ],

            TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    create_time,
                )
            ],

            PLACE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    create_place,
                )
            ],

            ROUTE_MAPS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    create_route_maps,
                )
            ],

            PHOTO: [
                MessageHandler(
                    filters.PHOTO,
                    create_photo,
                )
            ],

            COMMENTS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    create_comments,
                )
            ],

            CONFIRM: [

                CallbackQueryHandler(
                    choose_channel,
                    pattern=r"^create_confirm$",
                ),

                CallbackQueryHandler(
                    cancel_preview,
                    pattern=r"^create_cancel$",
                ),

                CallbackQueryHandler(
                    publish_selected_channel,
                    pattern=r"^publish:-?\d+$",
                ),
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancelar",
                cancel_create,
            )
        ],

        per_message=False,
    )

    application.add_handler(
        create_conversation,
        group=1,
    )

    # --------------------------------------------------------
    # COMANDOS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler("start", start),
        group=2,
    )

    application.add_handler(
        CommandHandler("eventos", eventos),
        group=2,
    )

    application.add_handler(
        CommandHandler("cancelar", cancelar),
        group=2,
    )

    application.add_handler(
        CommandHandler("id", get_id),
        group=2,
    )

    application.add_handler(
        CommandHandler("canales", canales),
        group=2,
    )

    # --------------------------------------------------------
    # CANCELAR EVENTO
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            delete_event_callback,
            pattern=r"^delete_event:\d+$",
        ),
        group=3,
    )

    # --------------------------------------------------------
    # ASISTENCIA Y ACOMPAÑANTES
    #
    # UN SOLO HANDLER para todos los callbacks.
    # Esto evita que una combinación de grupos/patrones
    # deje alguno sin procesar.
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            attendance_callback,
            pattern=(
                r"^(attend:(yes|maybe|no):\d+"
                r"|list:\d+"
                r"|companions:\d+"
                r"|compminus:\d+"
                r"|compplus:\d+"
                r"|compinfo:\d+"
                r"|compdone:\d+)$"
            ),
        ),
        group=4,
    )

    # --------------------------------------------------------
    # ERRORES
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    # --------------------------------------------------------
    # POLLING
    # --------------------------------------------------------

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


# ============================================================
# ARRANQUE
# ============================================================

if __name__ == "__main__":
    main()
