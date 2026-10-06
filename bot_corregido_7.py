import os
import sqlite3
import html
import logging
from datetime import datetime

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
    raise RuntimeError(
        "No se ha encontrado BOT_TOKEN en las variables de entorno."
    )


# Administradores autorizados
ADMIN_IDS = {
    126421812,   # @a_never
    8761859,     # Cristo
    5710212742,  # Gus / GuilArenas
    13017110,    # Juan
}


DB_FILE = "events.db"


# ============================================================
# DESTINOS FIJOS
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
# ESTADOS DE LA CONVERSACIÓN DE CREACIÓN
# ============================================================

(
    NAME,
    DATE,
    TIME,
    PLACE,
    PHOTO,
    COMMENTS,
    CONFIRM,
) = range(7)


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

    # NUEVO EN V7:
    # Guarda el topic/thread de Rutas de Maxiscooter Club.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_topics (
            chat_id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            topic_name TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

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


def get_counts(event_id):

    conn = db()

    rows = conn.execute(
        """
        SELECT status, SUM(1 + companions) AS total
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


def event_text(event):

    counts = get_counts(event["id"])

    text = (
        f"🏍️ <b>{clean(event['name'])}</b>\n\n"
        f"📅 <b>Fecha:</b> {clean(event['date'])}\n"
        f"⏰ <b>Hora:</b> {clean(event['time'])}\n"
        f"📍 <b>Lugar:</b> {clean(event['place'])}\n"
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
# DESTINOS / TOPICS
# ============================================================

def save_topic(chat_id, thread_id, topic_name=""):

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
            destination["thread_id"] = get_saved_thread_id(
                chat_id
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
        destination["thread_id"] = get_saved_thread_id(
            chat_id
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
# REGISTRO / DETECCIÓN DE CANALES
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

        print("\n==============================")
        print("UPDATE RECIBIDO")
        print("Update ID:", update.update_id)
        print("==============================")

        # ----------------------------------------------------
        # CHANNEL POST
        # ----------------------------------------------------

        if update.channel_post:

            message = update.channel_post

            print(">>> CHANNEL POST")
            print("Chat ID:", message.chat.id)
            print("Título:", message.chat.title)
            print(
                "Texto:",
                message.text or message.caption or "",
            )

            save_channel(message.chat)

            return

        # ----------------------------------------------------
        # EDITED CHANNEL POST
        # ----------------------------------------------------

        if update.edited_channel_post:

            message = update.edited_channel_post

            print(">>> EDITED CHANNEL POST")
            print("Chat ID:", message.chat.id)
            print("Título:", message.chat.title)

            save_channel(message.chat)

            return

        # ----------------------------------------------------
        # MY CHAT MEMBER
        # ----------------------------------------------------

        if update.my_chat_member:

            chat = update.my_chat_member.chat

            print(">>> MY CHAT MEMBER")
            print("Chat ID:", chat.id)
            print("Tipo:", chat.type)
            print(
                "Título:",
                getattr(chat, "title", None),
            )

            save_channel(chat)

            return

        # ----------------------------------------------------
        # MENSAJE NORMAL
        # ----------------------------------------------------

        if update.message:

            message = update.message
            chat = message.chat

            print(">>> MESSAGE")
            print("Chat ID:", chat.id)
            print("Tipo:", chat.type)
            print(
                "Título:",
                getattr(chat, "title", None),
            )
            print(
                "Message thread ID:",
                getattr(
                    message,
                    "message_thread_id",
                    None,
                ),
            )
            print(
                "Texto:",
                message.text or message.caption or "",
            )

            # Guardamos los chats que interesen.
            if chat.type in (
                "group",
                "supergroup",
                "channel",
            ):
                save_channel(chat)

            # Información de origen de mensajes reenviados.
            if message.forward_origin:

                origin = message.forward_origin

                print(">>> FORWARD_ORIGIN DETECTADO")
                print("Tipo:", type(origin).__name__)

                origin_chat = getattr(
                    origin,
                    "chat",
                    None,
                )

                if origin_chat:

                    print(
                        "Origin chat ID:",
                        origin_chat.id,
                    )

                    print(
                        "Origin title:",
                        getattr(
                            origin_chat,
                            "title",
                            None,
                        ),
                    )

                    save_channel(origin_chat)

            return

        # ----------------------------------------------------
        # OTROS TIPOS
        # ----------------------------------------------------

        if update.callback_query:
            print(">>> CALLBACK QUERY")
            return

        if update.edited_message:
            print(">>> EDITED MESSAGE")
            return

        if update.chat_member:
            print(">>> CHAT MEMBER")
            return

        if update.chat_join_request:
            print(">>> CHAT JOIN REQUEST")
            return

    except Exception as e:

        print(
            "Error en detect_channel:",
            repr(e),
        )


# ============================================================
# /start
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    text = (
        "🏍️ <b>Bot de rutas</b>\n\n"
        "Comandos disponibles:\n\n"
        "/crear — Crear una nueva ruta\n"
        "/eventos — Ver próximas rutas\n"
        "/cancelar — Cancelar una ruta\n"
        "/id — Ver tu ID / información del chat\n"
        "/canales — Ver destinos configurados\n"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# /canales
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
                    "🔴 Sin configurar\n"
                    "Entra en Rutas y utiliza /id.\n\n"
                )

        else:

            text += (
                "🟢 Listo para publicar\n\n"
            )

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

    text = event_text(event)

    try:

        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=attendance_keyboard(
                event_id
            ),
        )

    except Exception:

        try:

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=attendance_keyboard(
                    event_id
                ),
            )

        except Exception as e:

            print(
                "No se pudo actualizar evento:",
                repr(e),
            )


# ============================================================
# CREACIÓN DE RUTA
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


async def create_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["name"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "📅 ¿Qué fecha tendrá la ruta?\n\n"
        "Por ejemplo: <code>15/10/2026</code>",
        parse_mode="HTML",
    )

    return DATE


async def create_date(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["date"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "⏰ ¿A qué hora?",
    )

    return TIME


async def create_time(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["time"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "📍 ¿Cuál es el lugar de salida?",
    )

    return PLACE


async def create_place(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["create"]["place"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "📸 Ahora envíame <b>la foto de la ruta</b> "
        "directamente aquí.",
        parse_mode="HTML",
    )

    return PHOTO


async def create_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message.photo:

        await update.message.reply_text(
            "⚠️ Necesito que envíes una foto."
        )

        return PHOTO

    # Telegram ya nos da el file_id.
    # No descargamos ni guardamos físicamente la imagen.
    photo = update.message.photo[-1]

    context.user_data["create"]["photo_file_id"] = (
        photo.file_id
    )

    await update.message.reply_text(
        "ℹ️ Puedes añadir información adicional "
        "o comentarios para la ruta.\n\n"
        "Si no quieres añadir nada, escribe "
        "<code>ninguno</code>.",
        parse_mode="HTML",
    )

    return COMMENTS


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
        f"⏰ {clean(data['time'])}\n"
        f"📍 {clean(data['place'])}\n"
    )

    if data["comments"]:
        preview += (
            "\n"
            f"ℹ️ {clean(data['comments'])}\n"
        )

    preview += (
        "\n¿Quieres crear esta ruta?"
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
# CONFIRMAR / CANCELAR PREVIEW
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


async def choose_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = context.user_data.get("create")

    if not data:

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

    callback = query.data

    try:
        channel_id = int(
            callback.split(":", 1)[1]
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

    # --------------------------------------------------------
    # COMPROBAR TOPIC
    # --------------------------------------------------------

    thread_id = destination.get(
        "thread_id"
    )

    if destination["topic_name"]:

        if thread_id is None:

            await query.edit_message_caption(
                caption=(
                    "⚠️ <b>El tema Rutas todavía "
                    "no está configurado.</b>\n\n"
                    "Entra en:\n"
                    "📢 <b>Maxiscooter Club</b> → "
                    "🧵 <b>Rutas</b>\n\n"
                    "Escribe allí:\n"
                    "<code>/id</code>\n\n"
                    "Después vuelve a crear/publicar "
                    "la ruta."
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

    # --------------------------------------------------------
    # GUARDAR EVENTO
    # --------------------------------------------------------

    conn = db()

    cursor = conn.execute(
        """
        INSERT INTO events
        (
            name,
            date,
            time,
            place,
            photo_file_id,
            comments,
            created_by,
            active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            data["name"],
            data["date"],
            data["time"],
            data["place"],
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

    # --------------------------------------------------------
    # PREPARAR ENVÍO
    # --------------------------------------------------------

    send_args = {
        "chat_id": channel_id,
        "photo": event["photo_file_id"],
        "caption": event_text(event),
        "parse_mode": "HTML",
        "reply_markup": attendance_keyboard(
            event_id
        ),
    }

    if thread_id is not None:
        send_args["message_thread_id"] = thread_id

    # --------------------------------------------------------
    # ENVIAR
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # GUARDAR MENSAJE PUBLICADO
    # --------------------------------------------------------

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
# ASISTENCIA
# ============================================================

async def attendance_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    parts = query.data.split(":")

    if len(parts) < 3:
        return

    action = parts[1]

    try:
        event_id = int(parts[2])

    except ValueError:
        return

    user = update.effective_user

    if not user:
        return

    # --------------------------------------------------------
    # VER ASISTENTES
    # --------------------------------------------------------

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
            ORDER BY status, user_name
            """,
            (event_id,),
        ).fetchall()

        conn.close()

        if not rows:

            text = "👀 <b>Asistentes</b>\n\nNadie ha respondido todavía."

        else:

            yes = []
            maybe = []
            no = []

            for row in rows:

                name = clean(row["user_name"])
                companions = row["companions"] or 0

                suffix = ""

                if companions:
                    suffix = (
                        f" + {companions} acompañante"
                        f"{'s' if companions != 1 else ''}"
                    )

                line = f"• {name}{suffix}"

                if row["status"] == "yes":
                    yes.append(line)

                elif row["status"] == "maybe":
                    maybe.append(line)

                else:
                    no.append(line)

            text = "👀 <b>Asistentes</b>\n\n"

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

        await query.answer(
            text=text[:200],
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # ACOMPAÑANTES
    # --------------------------------------------------------

    if action == "companions":

        context.user_data[
            "companion_event_id"
        ] = event_id

        await query.answer()

        try:

            await query.message.reply_text(
                "👥 ¿Cuántos acompañantes llevarás?\n\n"
                "Escribe un número. "
                "Si vas solo, escribe <code>0</code>.",
                parse_mode="HTML",
            )

        except Exception:
            pass

        return

    # --------------------------------------------------------
    # ASISTENCIA
    # --------------------------------------------------------

    if action not in (
        "yes",
        "maybe",
        "no",
    ):
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
                action,
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
                action,
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
        labels[action]
    )


# ============================================================
# ACOMPAÑANTES - TEXTO
# ============================================================

async def companion_number(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if "companion_event_id" not in context.user_data:
        return

    text = update.message.text.strip()

    try:

        number = int(text)

    except ValueError:

        await update.message.reply_text(
            "⚠️ Escribe un número válido."
        )

        return

    if number < 0:
        number = 0

    if number > 20:
        await update.message.reply_text(
            "⚠️ El máximo permitido es 20."
        )
        return

    event_id = context.user_data.pop(
        "companion_event_id"
    )

    user = update.effective_user

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

    user_name = (
        user.full_name
        or user.username
        or str(user.id)
    )

    if existing:

        conn.execute(
            """
            UPDATE attendance
            SET
                companions=?,
                user_name=?
            WHERE event_id=? AND user_id=?
            """,
            (
                number,
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
                number,
            ),
        )

    conn.commit()
    conn.close()

    await update_published_event(
        context,
        event_id,
    )

    await update.message.reply_text(
        f"👥 Acompañantes registrados: <b>{number}</b>",
        parse_mode="HTML",
    )


# ============================================================
# /eventos
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

        keyboard = attendance_keyboard(
            event["id"]
        )

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
# /cancelar
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
                callback_data=f"delete_event:{event['id']}",
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

    # Intentar eliminar la publicación.
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
# /id
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

    # --------------------------------------------------------
    # PRIVADO
    # --------------------------------------------------------

    if chat.type == "private":

        await message.reply_text(
            "Tu ID de Telegram es:\n"
            f"<code>{user.id}</code>",
            parse_mode="HTML",
        )

        return

    # --------------------------------------------------------
    # GRUPO / SUPERGRUPO
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # GUARDAR TOPIC
    # --------------------------------------------------------

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
# ERROR HANDLER
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
    print(" BOT DE RUTAS V7")
    print("==========================================")
    print("Destinos configurados:")

    for chat_id, destination in DESTINATIONS.items():

        print(
            f" - {destination['name']}: "
            f"{chat_id}"
        )

        if destination["topic_name"]:

            thread_id = get_saved_thread_id(
                chat_id
            )

            print(
                f"   Tema: "
                f"{destination['topic_name']}"
            )

            print(
                f"   Thread ID: "
                f"{thread_id}"
            )

    print("==========================================")
    print("")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # DIAGNÓSTICO GENERAL
    # --------------------------------------------------------

    application.add_handler(
        TypeHandler(
            Update,
            detect_channel,
            ),
        group=0,
    )

    # --------------------------------------------------------
    # CONVERSACIÓN /CREAR
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
                    filters.TEXT
                    & ~filters.COMMAND,
                    create_name,
                )
            ],

            DATE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    create_date,
                )
            ],

            TIME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    create_time,
                )
            ],

            PLACE: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    create_place,
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
                    filters.TEXT
                    & ~filters.COMMAND,
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
        CommandHandler(
            "start",
            start,
        ),
        group=2,
    )

    application.add_handler(
        CommandHandler(
            "eventos",
            eventos,
        ),
        group=2,
    )

    application.add_handler(
        CommandHandler(
            "cancelar",
            cancelar,
        ),
        group=2,
    )

    application.add_handler(
        CommandHandler(
            "id",
            get_id,
        ),
        group=2,
    )

    application.add_handler(
        CommandHandler(
            "canales",
            canales,
        ),
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
    # ASISTENCIA
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            attendance_callback,
            pattern=r"^attend:(yes|maybe|no):\d+$",
        ),
        group=4,
    )

    application.add_handler(
        CallbackQueryHandler(
            attendance_callback,
            pattern=r"^list:\d+$",
        ),
        group=4,
    )

    application.add_handler(
        CallbackQueryHandler(
            attendance_callback,
            pattern=r"^companions:\d+$",
        ),
        group=4,
    )

    # --------------------------------------------------------
    # NÚMERO DE ACOMPAÑANTES
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            companion_number,
        ),
        group=5,
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
