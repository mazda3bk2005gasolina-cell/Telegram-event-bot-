import os
import sqlite3
import html

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


load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DB = "events.db"


# =========================================================
# ADMINISTRADORES
# =========================================================

ADMIN_IDS = {
    126421812,
    8761859,
    5710212742,
    13017110,
}


# =========================================================
# ESTADOS DE CREACIÓN
# =========================================================

NAME, DATE, TIME, PLACE, PHOTO, COMMENTS, CONFIRM = range(7)


# =========================================================
# BASE DE DATOS
# =========================================================

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():

    con = db()

    con.executescript("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        event_date TEXT NOT NULL,
        event_time TEXT NOT NULL,
        place TEXT NOT NULL,
        description TEXT DEFAULT '',
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS attendance (
        event_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL,
        plus_ones INTEGER DEFAULT 0,
        PRIMARY KEY(event_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS channels (
        chat_id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        username TEXT DEFAULT '',
        added_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Compatibilidad con las versiones anteriores
    columns = {
        "photo_file_id": "TEXT DEFAULT ''",
        "comments": "TEXT DEFAULT ''",
        "channel_id": "TEXT DEFAULT ''",
        "published_message_id": "INTEGER",
    }

    existing = {
        row["name"]
        for row in con.execute(
            "PRAGMA table_info(events)"
        ).fetchall()
    }

    for column, definition in columns.items():

        if column not in existing:

            con.execute(
                f"ALTER TABLE events ADD COLUMN "
                f"{column} {definition}"
            )

    con.commit()
    con.close()


# =========================================================
# UTILIDADES
# =========================================================

def clean(value):

    return html.escape(
        str(value or "")
    )


def authorized(user_id):

    return user_id in ADMIN_IDS


def get_counts(event_id):

    con = db()

    rows = con.execute(
        """
        SELECT
            status,
            COALESCE(SUM(1 + plus_ones), 0) AS n
        FROM attendance
        WHERE event_id=?
        GROUP BY status
        """,
        (event_id,),
    ).fetchall()

    con.close()

    return {
        row["status"]: row["n"]
        for row in rows
    }


def event_text(event):

    c = get_counts(
        event["id"]
    )

    text = (
        f"📅 <b>{clean(event['name'])}</b>\n\n"
        f"🗓 {clean(event['event_date'])} · "
        f"{clean(event['event_time'])}\n"
        f"📍 {clean(event['place'])}\n"
    )

    if event["description"]:

        text += (
            f"\n📝 "
            f"{clean(event['description'])}\n"
        )

    if event["comments"]:

        text += (
            f"\n📌 <b>Información:</b>\n"
            f"{clean(event['comments'])}\n"
        )

    text += (
        "\n"
        f"✅ {c.get('yes', 0)} personas · "
        f"🤔 {c.get('maybe', 0)} personas · "
        f"❌ {c.get('no', 0)} personas"
    )

    return text


# =========================================================
# BOTONES DE ASISTENCIA
# =========================================================

def attendance_keyboard(event_id):

    return InlineKeyboardMarkup([

        [

            InlineKeyboardButton(
                "✅ Asistiré",
                callback_data=(
                    f"status:{event_id}:yes"
                ),
            ),

            InlineKeyboardButton(
                "🤔 Quizás",
                callback_data=(
                    f"status:{event_id}:maybe"
                ),
            ),

            InlineKeyboardButton(
                "❌ No asistiré",
                callback_data=(
                    f"status:{event_id}:no"
                ),
            ),

        ],

        [

            InlineKeyboardButton(
                "➕ Acompañantes",
                callback_data=(
                    f"plus:{event_id}"
                ),
            ),

            InlineKeyboardButton(
                "👥 Ver asistentes",
                callback_data=(
                    f"list:{event_id}"
                ),
            ),

        ],

    ])


# =========================================================
# DETECCIÓN DE CANALES
# =========================================================

async def detect_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    """
    Recibe el Update completo.

    Si contiene channel_post, guarda automáticamente
    el canal en SQLite.
    """

    if not update.channel_post:
        return

    post = update.channel_post

    chat = post.chat

    con = db()

    existing = con.execute(
        """
        SELECT chat_id
        FROM channels
        WHERE chat_id=?
        """,
        (chat.id,),
    ).fetchone()

    con.execute(
        """
        INSERT INTO channels(
            chat_id,
            title,
            username
        )
        VALUES (?, ?, ?)

        ON CONFLICT(chat_id)
        DO UPDATE SET
            title=excluded.title,
            username=excluded.username
        """,
        (
            chat.id,
            chat.title or "Canal sin nombre",
            chat.username or "",
        ),
    )

    con.commit()
    con.close()

    # Avisamos únicamente la primera vez
    if not existing:

        for admin_id in ADMIN_IDS:

            try:

                await context.bot.send_message(

                    chat_id=admin_id,

                    text=(
                        "📢 <b>Canal detectado</b>\n\n"
                        f"<b>{clean(chat.title)}</b>\n\n"
                        "Ya puedes seleccionarlo "
                        "cuando publiques una ruta."
                    ),

                    parse_mode="HTML",
                )

            except Exception:

                pass


# =========================================================
# START
# =========================================================

async def start(
    update,
    context
):

    if update.effective_chat.type != "private":
        return

    await update.message.reply_text(

        "👋 <b>Soy el bot de RUTAS Y QUEDADAS.</b>\n\n"

        "🆕 /crear — Crear una ruta\n"
        "📋 /eventos — Ver rutas activas\n"
        "📢 /canales — Ver canales detectados\n"
        "❌ /cancelar ID — Cancelar una ruta",

        parse_mode="HTML",
    )


# =========================================================
# CANALES DETECTADOS
# =========================================================

async def canales(
    update,
    context
):

    if update.effective_chat.type != "private":
        return

    if not authorized(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ No estás autorizado."
        )

        return

    con = db()

    rows = con.execute(
        """
        SELECT *
        FROM channels
        ORDER BY title
        """
    ).fetchall()

    con.close()

    if not rows:

        await update.message.reply_text(

            "📭 <b>No tengo ningún canal detectado todavía.</b>\n\n"

            "Para detectarlo:\n\n"

            "1️⃣ Abre el canal donde está añadido el bot.\n"
            "2️⃣ Publica un mensaje nuevo en ese canal.\n"
            "3️⃣ Vuelve aquí.\n"
            "4️⃣ Escribe /canales.\n\n"

            "No necesitas conocer ningún ID.",

            parse_mode="HTML",
        )

        return

    lines = [
        "📢 <b>Canales detectados</b>\n"
    ]

    for row in rows:

        if row["username"]:

            lines.append(
                f"📢 <b>{clean(row['title'])}</b> "
                f"(@{clean(row['username'])})"
            )

        else:

            lines.append(
                f"📢 <b>{clean(row['title'])}</b>"
            )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


def channel_keyboard():

    con = db()

    rows = con.execute(
        """
        SELECT
            chat_id,
            title,
            username
        FROM channels
        ORDER BY title
        """
    ).fetchall()

    con.close()

    buttons = []

    for row in rows:

        label = (
            f"📢 {row['title']}"
        )

        if row["username"]:

            label += (
                f" (@{row['username']})"
            )

        buttons.append([

            InlineKeyboardButton(

                label,

                callback_data=(
                    f"publish:{row['chat_id']}"
                ),

            )

        ])

    buttons.append([

        InlineKeyboardButton(
            "❌ Cancelar",
            callback_data="create:cancel",
        )

    ])

    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# ACTUALIZAR PUBLICACIÓN DEL CANAL
# =========================================================

async def update_published_event(
    bot,
    event_id
):

    con = db()

    event = con.execute(
        """
        SELECT *
        FROM events
        WHERE id=?
        """,
        (event_id,),
    ).fetchone()

    con.close()

    if not event:
        return

    if not event["channel_id"]:
        return

    if not event["published_message_id"]:
        return

    try:

        if event["photo_file_id"]:

            await bot.edit_message_caption(

                chat_id=int(
                    event["channel_id"]
                ),

                message_id=int(
                    event["published_message_id"]
                ),

                caption=event_text(event),

                parse_mode="HTML",

                reply_markup=attendance_keyboard(
                    event_id
                ),
            )

        else:

            await bot.edit_message_text(

                chat_id=int(
                    event["channel_id"]
                ),

                message_id=int(
                    event["published_message_id"]
                ),

                text=event_text(event),

                parse_mode="HTML",

                reply_markup=attendance_keyboard(
                    event_id
                ),
            )

    except Exception:

        pass


# =========================================================
# CREAR
# =========================================================

async def crear(
    update,
    context
):

    if update.effective_chat.type != "private":
        return ConversationHandler.END

    if not authorized(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ No estás autorizado para crear rutas."
        )

        return ConversationHandler.END

    context.user_data.clear()

    await update.message.reply_text(

        "🆕 <b>Vamos a crear una ruta.</b>\n\n"
        "¿Cómo se llama?",

        parse_mode="HTML",
    )

    return NAME


async def get_name(
    update,
    context
):

    context.user_data["name"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(

        "¿Qué fecha?\n\n"
        "Ejemplo: 15/10/2026"
    )

    return DATE


async def get_date(
    update,
    context
):

    context.user_data["date"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(

        "¿A qué hora?\n\n"
        "Ejemplo: 09:00"
    )

    return TIME


async def get_time(
    update,
    context
):

    context.user_data["time"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(
        "📍 ¿Dónde es?"
    )

    return PLACE


async def get_place(
    update,
    context
):

    context.user_data["place"] = (
        update.message.text.strip()
    )

    await update.message.reply_text(

        "📷 Envíame ahora la foto de la ruta.\n\n"
        "Si no quieres poner foto, escribe <b>-</b>.",

        parse_mode="HTML",
    )

    return PHOTO


async def get_photo(
    update,
    context
):

    if update.message.photo:

        photo = (
            update.message.photo[-1]
        )

        context.user_data[
            "photo_file_id"
        ] = photo.file_id

        await update.message.reply_text(

            "📌 <b>Perfecto.</b>\n\n"

            "Ahora escribe la información "
            "adicional.\n\n"

            "Ejemplo:\n"
            "<i>Confirmar reserva antes del día 10.</i>\n\n"

            "Si no quieres añadir nada, "
            "escribe <b>-</b>.",

            parse_mode="HTML",
        )

        return COMMENTS

    if (
        update.message.text
        and update.message.text.strip() == "-"
    ):

        context.user_data[
            "photo_file_id"
        ] = ""

        await update.message.reply_text(

            "📌 Escribe ahora la información adicional.\n\n"

            "Ejemplo:\n"
            "<i>Confirmar reserva antes del día 10.</i>\n\n"

            "Si no quieres añadir nada, "
            "escribe <b>-</b>.",

            parse_mode="HTML",
        )

        return COMMENTS

    await update.message.reply_text(

        "📷 Necesito una foto.\n\n"
        "Si quieres continuar sin foto, "
        "escribe <b>-</b>.",

        parse_mode="HTML",
    )

    return PHOTO


async def get_comments(
    update,
    context
):

    comments = (
        update.message.text.strip()
    )

    if comments == "-":
        comments = ""

    # Evitamos captions demasiado largos
    comments = comments[:700]

    context.user_data[
        "comments"
    ] = comments

    context.user_data[
        "description"
    ] = ""

    text = (

        "👀 <b>VISTA PREVIA</b>\n\n"

        f"📅 <b>{clean(context.user_data['name'])}</b>\n\n"

        f"🗓 "
        f"{clean(context.user_data['date'])}"
        f" · "
        f"{clean(context.user_data['time'])}\n"

        f"📍 "
        f"{clean(context.user_data['place'])}\n"

    )

    if comments:

        text += (

            "\n📌 <b>Información:</b>\n"
            f"{clean(comments)}\n"

        )

    keyboard = InlineKeyboardMarkup([

        [

            InlineKeyboardButton(
                "📢 PUBLICAR",
                callback_data="create:choose",
            )

        ],

        [

            InlineKeyboardButton(
                "❌ CANCELAR",
                callback_data="create:cancel",
            )

        ],

    ])

    if context.user_data.get(
        "photo_file_id"
    ):

        await update.message.reply_photo(

            photo=context.user_data[
                "photo_file_id"
            ],

            caption=text,

            parse_mode="HTML",

            reply_markup=keyboard,
        )

    else:

        await update.message.reply_text(

            text,

            parse_mode="HTML",

            reply_markup=keyboard,
        )

    return CONFIRM


# =========================================================
# ELEGIR CANAL
# =========================================================

async def choose_channel(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not authorized(
        query.from_user.id
    ):

        await query.message.reply_text(
            "⛔ No estás autorizado."
        )

        return CONFIRM

    con = db()

    count = con.execute(
        """
        SELECT COUNT(*) AS n
        FROM channels
        """
    ).fetchone()["n"]

    con.close()

    if count == 0:

        await query.message.reply_text(

            "⚠️ <b>No tengo ningún canal detectado todavía.</b>\n\n"

            "Haz esto:\n\n"

            "1️⃣ Abre el canal donde está añadido el bot.\n"
            "2️⃣ Publica un mensaje nuevo en ese canal.\n"
            "3️⃣ Vuelve aquí.\n"
            "4️⃣ Escribe /canales.\n\n"

            "No necesitas conocer ni introducir "
            "ningún ID.",

            parse_mode="HTML",
        )

        return CONFIRM

    await query.message.reply_text(

        "📢 <b>¿Dónde quieres publicar esta ruta?</b>\n\n"
        "Selecciona un canal:",

        parse_mode="HTML",

        reply_markup=channel_keyboard(),
    )

    return CONFIRM


# =========================================================
# PUBLICAR EN CANAL ELEGIDO
# =========================================================

async def publish_selected_channel(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not authorized(
        query.from_user.id
    ):

        await query.message.reply_text(
            "⛔ No estás autorizado."
        )

        return ConversationHandler.END

    try:

        channel_id = int(
            query.data.split(
                ":",
                1
            )[1]
        )

    except Exception:

        await query.message.reply_text(
            "❌ Canal no válido."
        )

        return ConversationHandler.END

    con = db()

    channel = con.execute(

        """
        SELECT *
        FROM channels
        WHERE chat_id=?
        """,

        (channel_id,),

    ).fetchone()

    con.close()

    if not channel:

        await query.message.reply_text(
            "❌ Ese canal ya no está disponible."
        )

        return ConversationHandler.END

    data = context.user_data

    con = db()

    cursor = con.execute(

        """
        INSERT INTO events (

            chat_id,
            name,
            event_date,
            event_time,
            place,
            description,
            photo_file_id,
            comments,
            channel_id,
            active

        )

        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, 1
        )
        """,

        (

            channel_id,

            data["name"],

            data["date"],

            data["time"],

            data["place"],

            data.get(
                "description",
                ""
            ),

            data.get(
                "photo_file_id",
                ""
            ),

            data.get(
                "comments",
                ""
            ),

            str(channel_id),

        ),
    )

    event_id = cursor.lastrowid

    con.commit()

    event = con.execute(

        """
        SELECT *
        FROM events
        WHERE id=?
        """,

        (event_id,),

    ).fetchone()

    con.close()

    try:

        if event["photo_file_id"]:

            sent = await context.bot.send_photo(

                chat_id=channel_id,

                photo=event[
                    "photo_file_id"
                ],

                caption=event_text(
                    event
                ),

                parse_mode="HTML",

                reply_markup=attendance_keyboard(
                    event_id
                ),
            )

        else:

            sent = await context.bot.send_message(

                chat_id=channel_id,

                text=event_text(
                    event
                ),

                parse_mode="HTML",

                reply_markup=attendance_keyboard(
                    event_id
                ),
            )

    except Exception as exc:

        con = db()

        con.execute(

            """
            UPDATE events
            SET active=0
            WHERE id=?
            """,

            (event_id,),
        )

        con.commit()
        con.close()

        await query.message.reply_text(

            "❌ <b>No he podido publicar la ruta.</b>\n\n"

            f"Error: {clean(exc)}",

            parse_mode="HTML",
        )

        return ConversationHandler.END

    con = db()

    con.execute(

        """
        UPDATE events
        SET published_message_id=?
        WHERE id=?
        """,

        (
            sent.message_id,
            event_id
        ),
    )

    con.commit()
    con.close()

    try:

        await query.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:

        pass

    await query.message.reply_text(

        "✅ <b>Ruta publicada correctamente.</b>\n\n"

        f"📢 Canal: "
        f"<b>{clean(channel['title'])}</b>\n"

        f"🆔 Ruta: "
        f"<b>{event_id}</b>",

        parse_mode="HTML",
    )

    context.user_data.clear()

    return ConversationHandler.END


# =========================================================
# CANCELAR CREACIÓN
# =========================================================

async def cancel_preview(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    if not authorized(
        query.from_user.id
    ):

        return ConversationHandler.END

    context.user_data.clear()

    try:

        await query.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:

        pass

    await query.message.reply_text(
        "❌ Creación cancelada."
    )

    return ConversationHandler.END


async def cancel_create(
    update,
    context
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Creación cancelada."
    )

    return ConversationHandler.END


# =========================================================
# ASISTENCIA
# =========================================================

async def callback(
    update,
    context
):

    query = update.callback_query

    parts = query.data.split(":")

    action = parts[0]

    try:

        event_id = int(
            parts[1]
        )

    except Exception:

        await query.answer(
            "❌ Error.",
            show_alert=True
        )

        return

    con = db()

    event = con.execute(

        """
        SELECT *
        FROM events
        WHERE id=?
        AND active=1
        """,

        (event_id,),

    ).fetchone()

    con.close()

    if not event:

        await query.answer(
            "Este evento ya no está activo.",
            show_alert=True
        )

        return

    user = query.from_user

    username = (

        f"@{user.username}"

        if user.username

        else None

    )

    display_name = user.full_name


    # -----------------------------------------------------
    # ASISTENCIA
    # -----------------------------------------------------

    if action == "status":

        status = parts[2]

        con = db()

        con.execute(

            """
            INSERT INTO attendance(

                event_id,
                user_id,
                username,
                display_name,
                status,
                plus_ones

            )

            VALUES (
                ?, ?, ?, ?, ?, 0
            )

            ON CONFLICT(
                event_id,
                user_id
            )

            DO UPDATE SET

                username=excluded.username,

                display_name=excluded.display_name,

                status=excluded.status,

                plus_ones=0
            """,

            (

                event_id,

                user.id,

                username,

                display_name,

                status,

            ),
        )

        con.commit()
        con.close()

        await update_published_event(
            context.bot,
            event_id
        )

        labels = {

            "yes":
                "✅ Asistiré",

            "maybe":
                "🤔 Quizás",

            "no":
                "❌ No asistiré",

        }

        await query.answer(

            f"Registrado: "
            f"{labels.get(status, status)}"

        )

        return


    # -----------------------------------------------------
    # ACOMPAÑANTES
    # -----------------------------------------------------

    if action == "plus":

        con = db()

        attendance = con.execute(

            """
            SELECT *
            FROM attendance
            WHERE event_id=?
            AND user_id=?
            """,

            (
                event_id,
                user.id
            ),

        ).fetchone()

        con.close()

        if not attendance:

            await query.answer(

                "Primero selecciona "
                "tu asistencia.",

                show_alert=True

            )

            return

        keyboard = InlineKeyboardMarkup([

            [

                InlineKeyboardButton(
                    "0",
                    callback_data=(
                        f"setplus:{event_id}:0"
                    )
                ),

                InlineKeyboardButton(
                    "+1",
                    callback_data=(
                        f"setplus:{event_id}:1"
                    )
                ),

                InlineKeyboardButton(
                    "+2",
                    callback_data=(
                        f"setplus:{event_id}:2"
                    )
                ),

            ],

            [

                InlineKeyboardButton(
                    "+3",
                    callback_data=(
                        f"setplus:{event_id}:3"
                    )
                ),

                InlineKeyboardButton(
                    "+4",
                    callback_data=(
                        f"setplus:{event_id}:4"
                    )
                ),

                InlineKeyboardButton(
                    "+5",
                    callback_data=(
                        f"setplus:{event_id}:5"
                    )
                ),

            ],

        ])

        try:

            await context.bot.send_message(

                chat_id=user.id,

                text=(
                    "👥 <b>¿Cuántos acompañantes "
                    "llevas?</b>"
                ),

                parse_mode="HTML",

                reply_markup=keyboard,

            )

            await query.answer(
                "Te he enviado las opciones por privado."
            )

        except Exception:

            await query.answer(

                "Primero abre el bot "
                "y pulsa /start.",

                show_alert=True

            )

        return


    # -----------------------------------------------------
    # GUARDAR ACOMPAÑANTES
    # -----------------------------------------------------

    if action == "setplus":

        plus = int(
            parts[2]
        )

        con = db()

        attendance = con.execute(

            """
            SELECT *
            FROM attendance
            WHERE event_id=?
            AND user_id=?
            """,

            (
                event_id,
                user.id
            ),

        ).fetchone()

        if not attendance:

            con.close()

            await query.answer(

                "Primero indica tu asistencia.",

                show_alert=True

            )

            return

        con.execute(

            """
            UPDATE attendance
            SET plus_ones=?
            WHERE event_id=?
            AND user_id=?
            """,

            (
                plus,
                event_id,
                user.id
            ),
        )

        con.commit()
        con.close()

        await update_published_event(
            context.bot,
            event_id
        )

        await query.edit_message_text(

            f"✅ Registrado: tú + "
            f"{plus} acompañante(s)."

        )

        return


    # -----------------------------------------------------
    # VER ASISTENTES
    # -----------------------------------------------------

    if action == "list":

        con = db()

        rows = con.execute(

            """
            SELECT
                display_name,
                username,
                status,
                plus_ones

            FROM attendance

            WHERE event_id=?

            ORDER BY
                status,
                display_name
            """,

            (event_id,),

        ).fetchall()

        con.close()

        labels = {

            "yes": "✅",
            "maybe": "🤔",
            "no": "❌",

        }

        if not rows:

            text = (
                "Todavía no hay respuestas."
            )

        else:

            lines = []

            for row in rows:

                who = (
                    row["username"]
                    or row["display_name"]
                )

                total = (
                    1 + row["plus_ones"]
                )

                lines.append(

                    f"{labels.get(row['status'], '•')} "
                    f"{clean(who)} — "
                    f"{total} persona(s)"

                )

            text = (

                "<b>👥 Asistentes</b>\n\n"

                + "\n".join(lines)

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

                "Primero abre el bot "
                "y pulsa /start.",

                show_alert=True

            )


# =========================================================
# EVENTOS
# =========================================================

async def eventos(
    update,
    context
):

    if update.effective_chat.type != "private":
        return

    if not authorized(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ No estás autorizado."
        )

        return

    con = db()

    rows = con.execute(

        """
        SELECT *
        FROM events
        WHERE active=1
        ORDER BY id DESC
        """

    ).fetchall()

    con.close()

    if not rows:

        await update.message.reply_text(
            "No hay eventos activos."
        )

        return

    for event in rows:

        text = event_text(
            event
        )

        if event["photo_file_id"]:

            await update.message.reply_photo(

                photo=event[
                    "photo_file_id"
                ],

                caption=text,

                parse_mode="HTML",

                reply_markup=attendance_keyboard(
                    event["id"]
                ),

            )

        else:

            await update.message.reply_text(

                text,

                parse_mode="HTML",

                reply_markup=attendance_keyboard(
                    event["id"]
                ),

            )


# =========================================================
# CANCELAR EVENTO
# =========================================================

async def cancelar(
    update,
    context
):

    if update.effective_chat.type != "private":
        return

    if not authorized(
        update.effective_user.id
    ):

        await update.message.reply_text(
            "⛔ No estás autorizado."
        )

        return

    if (
        not context.args
        or not context.args[0].isdigit()
    ):

        await update.message.reply_text(
            "Uso: /cancelar ID"
        )

        return

    event_id = int(
        context.args[0]
    )

    con = db()

    event = con.execute(

        """
        SELECT *
        FROM events
        WHERE id=?
        AND active=1
        """,

        (event_id,),

    ).fetchone()

    if not event:

        con.close()

        await update.message.reply_text(
            "❌ No encuentro esa ruta activa."
        )

        return

    con.execute(

        """
        UPDATE events
        SET active=0
        WHERE id=?
        """,

        (event_id,),

    )

    con.commit()
    con.close()

    try:

        await context.bot.edit_message_reply_markup(

            chat_id=int(
                event["channel_id"]
            ),

            message_id=int(
                event["published_message_id"]
            ),

            reply_markup=None,

        )

    except Exception:

        pass

    await update.message.reply_text(

        f"✅ Ruta {event_id} cancelada."

    )


# =========================================================
# COMANDO ID
# =========================================================

async def obtener_id(
    update,
    context
):

    await update.message.reply_text(

        f"🆔 ID de este chat:\n"
        f"{update.effective_chat.id}"

    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "Falta BOT_TOKEN en las variables de entorno."
        )

    init_db()

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )


    # =====================================================
    # MUY IMPORTANTE
    #
    # Capturamos el Update completo para detectar
    # channel_post.
    # =====================================================

    app.add_handler(

        TypeHandler(
            Update,
            detect_channel
        ),

        group=0

    )


    # =====================================================
    # CONVERSACIÓN CREAR
    # =====================================================

    conv = ConversationHandler(

        entry_points=[

            CommandHandler(

                "crear",

                crear,

                filters=filters.ChatType.PRIVATE

            )

        ],

        states={

            NAME: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    get_name

                )

            ],

            DATE: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    get_date

                )

            ],

            TIME: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    get_time

                )

            ],

            PLACE: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    get_place

                )

            ],

            PHOTO: [

                MessageHandler(

                    filters.PHOTO,

                    get_photo

                ),

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    get_photo

                ),

            ],

            COMMENTS: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    get_comments

                )

            ],

            CONFIRM: [

                CallbackQueryHandler(

                    choose_channel,

                    pattern=r"^create:choose$"

                ),

                CallbackQueryHandler(

                    cancel_preview,

                    pattern=r"^create:cancel$"

                ),

                CallbackQueryHandler(

                    publish_selected_channel,

                    pattern=r"^publish:-?\d+$"

                ),

            ],

        },

        fallbacks=[

            CommandHandler(

                "cancelar_creacion",

                cancel_create

            )

        ],

    )


    # =====================================================
    # HANDLERS NORMALES
    # =====================================================

    app.add_handler(

        CommandHandler(
            "start",
            start
        ),

        group=1

    )

    app.add_handler(
        conv,
        group=1
    )

    app.add_handler(

        CommandHandler(

            "eventos",

            eventos,

            filters=filters.ChatType.PRIVATE

        ),

        group=1

    )

    app.add_handler(

        CommandHandler(

            "canales",

            canales,

            filters=filters.ChatType.PRIVATE

        ),

        group=1

    )

    app.add_handler(

        CommandHandler(

            "cancelar",

            cancelar,

            filters=filters.ChatType.PRIVATE

        ),

        group=1

    )

    app.add_handler(

        CommandHandler(
            "id",
            obtener_id
        ),

        group=1

    )


    # =====================================================
    # BOTONES DE ASISTENCIA
    # =====================================================

    app.add_handler(

        CallbackQueryHandler(

            callback,

            pattern=r"^(status|plus|setplus|list):"

        ),

        group=1

    )


    print(
        "Bot iniciado..."
    )

    app.run_polling()


if __name__ == "__main__":

    main()
