import os
import sqlite3
import html
import logging

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = {
    126421812,   # @a_never
    8761859,     # Cristo
    5710212742,  # Gus / GuilArenas
    13017110,    # Juan
}

DB_FILE = "events.db"

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
# LOGS
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


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
            photo_file_id TEXT DEFAULT '',
            comments TEXT DEFAULT '',
            channel_id INTEGER,
            published_message_id INTEGER,
            active INTEGER DEFAULT 1,
            created_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Compatibilidad con la base de datos de v2/v3/v4
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(events)").fetchall()
    }

    required_columns = {
        "photo_file_id": "TEXT DEFAULT ''",
        "comments": "TEXT DEFAULT ''",
        "channel_id": "INTEGER",
        "published_message_id": "INTEGER",
        "active": "INTEGER DEFAULT 1",
        "created_by": "INTEGER",
        "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    }

    for column, definition in required_columns.items():
        if column not in columns:
            conn.execute(
                f"ALTER TABLE events ADD COLUMN {column} {definition}"
            )

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
            title TEXT NOT NULL,
            username TEXT DEFAULT '',
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# UTILIDADES
# ============================================================

def clean(value):
    return html.escape(str(value or "").strip())


def authorized(user_id):
    return user_id in ADMIN_IDS


def get_counts(event_id):
    conn = db()

    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
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
        if row["status"] in counts:
            counts[row["status"]] = row["n"]

    return counts


def event_text(event, counts=None):
    if counts is None:
        counts = get_counts(event["id"])

    comments = (
        clean(event["comments"])
        if event["comments"]
        else "Nada"
    )

    return (
        f"📅 <b>{clean(event['name'])}</b>\n\n"
        f"🗓 {clean(event['date'])} · {clean(event['time'])}\n"
        f"📍 {clean(event['place'])}\n\n"
        f"📌 <b>Información:</b>\n"
        f"{comments}\n\n"
        f"👥 <b>Asistencia</b>\n"
        f"✅ {counts['yes']}   "
        f"🤔 {counts['maybe']}   "
        f"❌ {counts['no']}"
    )


def attendance_keyboard(event_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Asistiré",
                callback_data=f"att:yes:{event_id}",
            ),
            InlineKeyboardButton(
                "🤔 Quizás",
                callback_data=f"att:maybe:{event_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ No asistiré",
                callback_data=f"att:no:{event_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Acompañantes",
                callback_data=f"att:comp:{event_id}",
            ),
            InlineKeyboardButton(
                "👀 Ver asistentes",
                callback_data=f"att:list:{event_id}",
            ),
        ],
    ])


# ============================================================
# DETECCIÓN AUTOMÁTICA DE CANALES
# ============================================================

async def detect_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Este handler recibe TODAS las actualizaciones.

    Su misión principal en v5 es comprobar si Telegram está
    enviando realmente channel_post al bot.
    """

    print("")
    print("========================================")
    print(">>> UPDATE RECIBIDO")
    print("update_id:", update.update_id)

    # --------------------------------------------------------
    # PUBLICACIÓN NUEVA EN UN CANAL
    # --------------------------------------------------------

    if update.channel_post:
        post = update.channel_post
        chat = post.chat

        print(">>> CHANNEL_POST RECIBIDO <<<")
        print("Chat ID:", chat.id)
        print("Título:", chat.title)
        print("Username:", chat.username)
        print("Tipo:", chat.type)
        print(
            "Texto:",
            post.text or post.caption or "[sin texto]"
        )

        conn = db()

        conn.execute(
            """
            INSERT OR REPLACE INTO channels
            (chat_id, title, username)
            VALUES (?, ?, ?)
            """,
            (
                chat.id,
                chat.title or "Sin título",
                chat.username or "",
            ),
        )

        conn.commit()
        conn.close()

        print(">>> CANAL GUARDADO EN SQLITE <<<")

        # Avisamos a los administradores
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "📢 <b>Canal detectado automáticamente</b>\n\n"
                        f"📺 {clean(chat.title)}\n"
                        f"🆔 <code>{chat.id}</code>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                print(
                    f"No se pudo avisar al administrador "
                    f"{admin_id}: {e}"
                )

        print("========================================")
        return

    # --------------------------------------------------------
    # PUBLICACIÓN EDITADA EN UN CANAL
    # --------------------------------------------------------

    if update.edited_channel_post:
        post = update.edited_channel_post
        chat = post.chat

        print(">>> EDITED_CHANNEL_POST RECIBIDO <<<")
        print("Chat ID:", chat.id)
        print("Título:", chat.title)
        print("Username:", chat.username)

        conn = db()

        conn.execute(
            """
            INSERT OR REPLACE INTO channels
            (chat_id, title, username)
            VALUES (?, ?, ?)
            """,
            (
                chat.id,
                chat.title or "Sin título",
                chat.username or "",
            ),
        )

        conn.commit()
        conn.close()

        print(">>> CANAL ACTUALIZADO EN SQLITE <<<")
        print("========================================")
        return

    # --------------------------------------------------------
    # EL BOT HA SIDO AÑADIDO / MODIFICADO EN UN CHAT
    # --------------------------------------------------------

    if update.my_chat_member:
        change = update.my_chat_member
        chat = change.chat

        print(">>> MY_CHAT_MEMBER RECIBIDO <<<")
        print("Chat ID:", chat.id)
        print("Título:", chat.title)
        print("Tipo:", chat.type)
        print(
            "Nuevo estado:",
            change.new_chat_member.status
        )

        if (
            chat.type == "channel"
            and change.new_chat_member.status
            in ("administrator", "member")
        ):
            conn = db()

            conn.execute(
                """
                INSERT OR REPLACE INTO channels
                (chat_id, title, username)
                VALUES (?, ?, ?)
                """,
                (
                    chat.id,
                    chat.title or "Sin título",
                    chat.username or "",
                ),
            )

            conn.commit()
            conn.close()

            print(
                ">>> CANAL GUARDADO POR MY_CHAT_MEMBER <<<"
            )

        print("========================================")
        return

    # --------------------------------------------------------
    # DIAGNÓSTICO DE OTROS UPDATES
    # --------------------------------------------------------

    if update.message:
        print(">>> MESSAGE")

    elif update.edited_message:
        print(">>> EDITED_MESSAGE")

    elif update.callback_query:
        print(">>> CALLBACK_QUERY")

    elif update.chat_member:
        print(">>> CHAT_MEMBER")

    elif update.chat_join_request:
        print(">>> CHAT_JOIN_REQUEST")

    else:
        print(">>> OTRO / DESCONOCIDO")

    print("========================================")


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return

    await update.message.reply_text(
        "👋 Hola.\n\n"
        "Usa /crear para crear una ruta.\n"
        "Usa /canales para ver los canales detectados.\n"
        "Usa /eventos para ver los eventos activos."
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

    conn = db()

    rows = conn.execute(
        """
        SELECT chat_id, title, username
        FROM channels
        ORDER BY title
        """
    ).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 No tengo ningún canal detectado todavía.\n\n"
            "Para detectarlo:\n\n"
            "1️⃣ Abre el canal donde está añadido el bot.\n"
            "2️⃣ Publica un mensaje nuevo en ese canal.\n"
            "3️⃣ Vuelve aquí.\n"
            "4️⃣ Escribe /canales.\n\n"
            "No necesitas conocer ningún ID."
        )
        return

    text = "📢 <b>Canales detectados</b>\n\n"

    for row in rows:
        username = ""

        if row["username"]:
            username = f" @{row['username']}"

        text += (
            f"📺 <b>{clean(row['title'])}</b>{username}\n"
            f"ID interno: <code>{row['chat_id']}</code>\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


def channel_keyboard():
    conn = db()

    rows = conn.execute(
        """
        SELECT chat_id, title, username
        FROM channels
        ORDER BY title
        """
    ).fetchall()

    conn.close()

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"📢 {row['title']}"[:60],
                callback_data=f"publish:{row['chat_id']}",
            )
        ])

    return InlineKeyboardMarkup(buttons)


# ============================================================
# ACTUALIZAR EVENTO PUBLICADO
# ============================================================

async def update_published_event(
    context,
    event_id,
):
    conn = db()

    event = conn.execute(
        "SELECT * FROM events WHERE id=?",
        (event_id,),
    ).fetchone()

    conn.close()

    if not event:
        return

    if (
        not event["channel_id"]
        or not event["published_message_id"]
    ):
        return

    counts = get_counts(event_id)

    # Si fue una foto
    try:
        await context.bot.edit_message_caption(
            chat_id=event["channel_id"],
            message_id=event["published_message_id"],
            caption=event_text(event, counts),
            parse_mode="HTML",
            reply_markup=attendance_keyboard(event_id),
        )
        return

    except Exception as photo_error:
        print(
            "No se pudo actualizar caption:",
            photo_error
        )

    # Si fuera un mensaje de texto
    try:
        await context.bot.edit_message_text(
            chat_id=event["channel_id"],
            message_id=event["published_message_id"],
            text=event_text(event, counts),
            parse_mode="HTML",
            reply_markup=attendance_keyboard(event_id),
        )

    except Exception as text_error:
        print(
            "No se pudo actualizar mensaje:",
            text_error
        )


# ============================================================
# CREAR EVENTO
# ============================================================

async def create_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not authorized(update.effective_user.id):
        return ConversationHandler.END

    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "🔒 La creación de rutas se hace por privado.\n\n"
            "Escríbeme /crear en el chat privado del bot."
        )
        return ConversationHandler.END

    context.user_data["create"] = {}

    await update.message.reply_text(
        "🆕 <b>Nueva ruta</b>\n\n"
        "1️⃣ Escribe el nombre de la ruta/evento.",
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
        "2️⃣ Escribe la fecha.\n"
        "Ejemplo: 15/10/32"
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
        "3️⃣ Escribe la hora.\n"
        "Ejemplo: 09:00"
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
        "4️⃣ Escribe el lugar."
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
        "5️⃣ Ahora envíame la <b>foto</b> de la ruta.",
        parse_mode="HTML",
    )

    return PHOTO


async def create_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message.photo:
        await update.message.reply_text(
            "⚠️ Necesito una foto. "
            "Envíamela directamente aquí."
        )
        return PHOTO

    # No descargamos la foto.
    # Telegram file_id es suficiente.
    context.user_data["create"]["photo_file_id"] = (
        update.message.photo[-1].file_id
    )

    await update.message.reply_text(
        "6️⃣ Escribe la información/comentarios "
        "que quieras añadir.\n\n"
        "Ejemplo:\n"
        "Confirmar reserva antes del día X\n\n"
        "Si no quieres añadir nada, escribe "
        "<b>Nada</b>.",
        parse_mode="HTML",
    )

    return COMMENTS


async def create_comments(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    comments = update.message.text.strip()

    if comments.lower() == "nada":
        comments = "Nada"

    context.user_data["create"]["comments"] = comments

    data = context.user_data["create"]

    preview = (
        "👀 <b>VISTA PREVIA</b>\n\n"
        f"📅 <b>{clean(data['name'])}</b>\n"
        f"🗓 {clean(data['date'])} · "
        f"{clean(data['time'])}\n"
        f"📍 {clean(data['place'])}\n"
        f"📌 <b>Información:</b>\n"
        f"{clean(data['comments'])}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 PUBLICAR",
                callback_data="create:choose",
            ),
            InlineKeyboardButton(
                "❌ CANCELAR",
                callback_data="create:cancel",
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
# ELEGIR CANAL
# ============================================================

async def choose_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not authorized(query.from_user.id):
        return ConversationHandler.END

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM channels"
    ).fetchone()["n"]

    conn.close()

    if not count:
        await query.edit_message_caption(
            caption=(
                "⚠️ No tengo ningún canal detectado todavía.\n\n"
                "Publica primero un mensaje nuevo en el canal "
                "y vuelve a intentarlo."
            )
        )

        return ConversationHandler.END

    await query.edit_message_caption(
        caption="📢 <b>¿En qué canal quieres publicar?</b>",
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

    if not authorized(query.from_user.id):
        return ConversationHandler.END

    try:
        channel_id = int(
            query.data.split(":", 1)[1]
        )
    except Exception:
        await query.edit_message_caption(
            caption="❌ Canal no válido."
        )
        return ConversationHandler.END

    data = context.user_data.get("create")

    if not data:
        await query.edit_message_caption(
            caption=(
                "❌ Se ha perdido la información "
                "de la ruta."
            )
        )
        return ConversationHandler.END

    conn = db()

    cur = conn.execute(
        """
        INSERT INTO events
        (
            name,
            date,
            time,
            place,
            photo_file_id,
            comments,
            channel_id,
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
            data["photo_file_id"],
            data["comments"],
            channel_id,
            query.from_user.id,
        ),
    )

    event_id = cur.lastrowid

    conn.commit()

    event = conn.execute(
        "SELECT * FROM events WHERE id=?",
        (event_id,),
    ).fetchone()

    conn.close()

    try:
        sent = await context.bot.send_photo(
            chat_id=channel_id,
            photo=event["photo_file_id"],
            caption=event_text(event),
            parse_mode="HTML",
            reply_markup=attendance_keyboard(event_id),
        )

        conn = db()

        conn.execute(
            """
            UPDATE events
            SET published_message_id=?
            WHERE id=?
            """,
            (
                sent.message_id,
                event_id,
            ),
        )

        conn.commit()
        conn.close()

        await query.edit_message_caption(
            caption=(
                "✅ <b>Publicado correctamente</b>\n\n"
                f"📅 {clean(event['name'])}\n"
                f"📍 {clean(event['place'])}"
            ),
            parse_mode="HTML",
        )

    except Exception as e:
        print(
            "ERROR PUBLICANDO:",
            repr(e)
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
                "❌ <b>No se pudo publicar.</b>\n\n"
                f"{clean(e)}"
            ),
            parse_mode="HTML",
        )

    context.user_data.pop("create", None)

    return ConversationHandler.END


# ============================================================
# CANCELAR PREVIEW
# ============================================================

async def cancel_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if not authorized(query.from_user.id):
        return ConversationHandler.END

    context.user_data.pop("create", None)

    try:
        await query.edit_message_caption(
            caption="❌ Creación cancelada."
        )
    except Exception:
        pass

    return ConversationHandler.END


async def cancel_create(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("create", None)

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

    if not query.data.startswith("att:"):
        return

    parts = query.data.split(":")

    if len(parts) != 3:
        return

    action = parts[1]

    try:
        event_id = int(parts[2])
    except ValueError:
        return

    conn = db()

    event = conn.execute(
        """
        SELECT *
        FROM events
        WHERE id=? AND active=1
        """,
        (event_id,),
    ).fetchone()

    conn.close()

    if not event:
        await query.answer(
            "Este evento ya no está activo.",
            show_alert=True,
        )
        return

    user = query.from_user

    # ----------------------------------------
    # ASISTENCIA
    # ----------------------------------------

    if action in ("yes", "maybe", "no"):

        status = action

        if status == "yes":
            label = "✅ Asistiré"
        elif status == "maybe":
            label = "🤔 Quizás"
        else:
            label = "❌ No asistiré"

        conn = db()

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

            ON CONFLICT(event_id, user_id)
            DO UPDATE SET
                user_name=excluded.user_name,
                status=excluded.status
            """,
            (
                event_id,
                user.id,
                user.full_name,
                status,
            ),
        )

        conn.commit()
        conn.close()

        await query.answer(label)

        await update_published_event(
            context,
            event_id,
        )

        return

    # ----------------------------------------
    # ACOMPAÑANTES
    # ----------------------------------------

    if action == "comp":

        context.user_data["companion_event_id"] = event_id

        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    f"👥 <b>Acompañantes — "
                    f"{clean(event['name'])}</b>\n\n"
                    "Escribe cuántos acompañantes llevarás.\n\n"
                    "Ejemplo: <b>2</b>"
                ),
                parse_mode="HTML",
            )

            await query.answer(
                "Te he enviado la pregunta por privado."
            )

        except Exception:
            await query.answer(
                "Primero abre el chat privado con el bot "
                "y pulsa /start.",
                show_alert=True,
            )

        return

    # ----------------------------------------
    # LISTA DE ASISTENTES
    # ----------------------------------------

    if action == "list":

        conn = db()

        rows = conn.execute(
            """
            SELECT user_name, status, companions
            FROM attendance
            WHERE event_id=?
            ORDER BY status, user_name
            """,
            (event_id,),
        ).fetchall()

        conn.close()

        if not rows:

            text = (
                "👀 <b>Asistentes</b>\n\n"
                "Todavía no hay respuestas."
            )

        else:

            yes = []
            maybe = []
            no = []

            for row in rows:

                line = row["user_name"]

                if row["companions"]:
                    line += (
                        f" (+{row['companions']})"
                    )

                if row["status"] == "yes":
                    yes.append(line)

                elif row["status"] == "maybe":
                    maybe.append(line)

                else:
                    no.append(line)

            text = (
                f"👀 <b>Asistentes — "
                f"{clean(event['name'])}</b>\n\n"
            )

            if yes:
                text += "✅ <b>Asistirán:</b>\n"

                text += "\n".join(
                    f"• {clean(x)}"
                    for x in yes
                )

                text += "\n\n"

            if maybe:
                text += "🤔 <b>Quizás:</b>\n"

                text += "\n".join(
                    f"• {clean(x)}"
                    for x in maybe
                )

                text += "\n\n"

            if no:
                text += "❌ <b>No asistirán:</b>\n"

                text += "\n".join(
                    f"• {clean(x)}"
                    for x in no
                )

        try:

            await context.bot.send_message(
                chat_id=user.id,
                text=text,
                parse_mode="HTML",
            )

            await query.answer(
                "Te he enviado la lista por privado."
            )

        except Exception:

            await query.answer(
                "Primero abre el chat privado con el bot "
                "y pulsa /start.",
                show_alert=True,
            )


# ============================================================
# NÚMERO DE ACOMPAÑANTES
# ============================================================

async def companion_number(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    event_id = context.user_data.get(
        "companion_event_id"
    )

    if not event_id:
        return

    try:
        companions = int(
            update.message.text.strip()
        )

    except ValueError:

        await update.message.reply_text(
            "⚠️ Escribe solamente un número.\n"
            "Ejemplo: 2"
        )

        return

    if companions < 0:
        companions = 0

    if companions > 20:

        await update.message.reply_text(
            "⚠️ Introduce un número entre 0 y 20."
        )

        return

    conn = db()

    conn.execute(
        """
        UPDATE attendance
        SET companions=?
        WHERE event_id=? AND user_id=?
        """,
        (
            companions,
            event_id,
            update.effective_user.id,
        ),
    )

    conn.commit()
    conn.close()

    context.user_data.pop(
        "companion_event_id",
        None,
    )

    await update.message.reply_text(
        f"✅ Acompañantes actualizados: {companions}"
    )

    await update_published_event(
        context,
        event_id,
    )


# ============================================================
# EVENTOS
# ============================================================

async def eventos(
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
        """
    ).fetchall()

    conn.close()

    if not rows:

        await update.message.reply_text(
            "📭 No hay eventos activos."
        )

        return

    text = "📅 <b>Eventos activos</b>\n\n"

    for row in rows:

        text += (
            f"🆔 <b>{row['id']}</b> — "
            f"{clean(row['name'])}\n"
            f"🗓 {clean(row['date'])} · "
            f"{clean(row['time'])}\n"
            f"📍 {clean(row['place'])}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
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

    if not context.args:

        await update.message.reply_text(
            "Uso:\n/cancelar ID"
        )

        return

    try:
        event_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ El ID debe ser numérico."
        )

        return

    conn = db()

    event = conn.execute(
        "SELECT * FROM events WHERE id=?",
        (event_id,),
    ).fetchone()

    if not event:

        conn.close()

        await update.message.reply_text(
            "❌ No existe ese evento."
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
        event["channel_id"]
        and event["published_message_id"]
    ):

        try:

            await context.bot.edit_message_reply_markup(
                chat_id=event["channel_id"],
                message_id=event["published_message_id"],
                reply_markup=None,
            )

        except Exception as e:

            print(
                "No se pudieron quitar los botones:",
                e
            )

    await update.message.reply_text(
        f"✅ Evento {event_id} cancelado."
    )


# ============================================================
# ID
# ============================================================

async def get_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "Tu ID de Telegram es:\n"
        f"<code>{update.effective_user.id}</code>",
        parse_mode="HTML",
    )


# ============================================================
# ERRORES
# ============================================================

async def error_handler(
    update,
    context,
):
    print("")
    print("========== ERROR ==========")
    print(repr(context.error))
    print("============================")


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "Falta la variable de entorno BOT_TOKEN."
        )

    init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ========================================================
    # PRIMERO:
    # Recibimos TODAS las actualizaciones.
    # ========================================================

    app.add_handler(
        TypeHandler(
            Update,
            detect_channel,
        ),
        group=0,
    )

    # ========================================================
    # CREACIÓN
    # ========================================================

    creation_handler = ConversationHandler(

        entry_points=[
            CommandHandler(
                "crear",
                create_start,
            ),
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
                    pattern=r"^create:choose$",
                ),

                CallbackQueryHandler(
                    cancel_preview,
                    pattern=r"^create:cancel$",
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
            ),
        ],

        allow_reentry=True,
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    app.add_handler(
        creation_handler,
        group=1,
    )

    # ========================================================
    # COMANDOS
    # ========================================================

    app.add_handler(
        CommandHandler(
            "start",
            start,
        ),
        group=1,
    )

    app.add_handler(
        CommandHandler(
            "canales",
            canales,
        ),
        group=1,
    )

    app.add_handler(
        CommandHandler(
            "eventos",
            eventos,
        ),
        group=1,
    )

    app.add_handler(
        CommandHandler(
            "cancelar",
            cancelar,
        ),
        group=1,
    )

    app.add_handler(
        CommandHandler(
            "id",
            get_id,
        ),
        group=1,
    )

    # ========================================================
    # ASISTENCIA
    # ========================================================

    app.add_handler(
        CallbackQueryHandler(
            attendance_callback,
            pattern=r"^att:",
        ),
        group=2,
    )

    # ========================================================
    # ACOMPAÑANTES
    # ========================================================

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            companion_number,
        ),
        group=3,
    )

    app.add_error_handler(
        error_handler
    )

    print("")
    print("==========================================")
    print("       BOT CORREGIDO V5 INICIADO")
    print("==========================================")
    print("Detección automática de canales: ACTIVA")
    print("Diagnóstico de updates: ACTIVO")
    print("allowed_updates: ALL_TYPES")
    print("SQLite: ACTIVO")
    print("==========================================")
    print("")

    # ========================================================
    # MUY IMPORTANTE:
    # Pedimos explícitamente TODOS los tipos de update.
    # ========================================================

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
