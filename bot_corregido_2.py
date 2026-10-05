import os
import sqlite3
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
    filters,
)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

DB = "events.db"

# Administradores autorizados
ADMIN_IDS = {
    126421812,    # @a_never
    8761859,      # Cristo
    5710212742,   # Gus
    13017110,     # Juan
}

NAME, DATE, TIME, PLACE, PHOTO, COMMENTS, CONFIRM = range(7)


# ---------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------

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
    """)

    # Añadimos las nuevas columnas si todavía no existen.
    columns = {
        "photo_file_id": "TEXT DEFAULT ''",
        "comments": "TEXT DEFAULT ''",
        "channel_id": "TEXT DEFAULT ''",
        "published_message_id": "INTEGER",
    }

    existing = {
        row["name"]
        for row in con.execute("PRAGMA table_info(events)").fetchall()
    }

    for column, definition in columns.items():
        if column not in existing:
            con.execute(
                f"ALTER TABLE events ADD COLUMN {column} {definition}"
            )

    con.commit()
    con.close()


# ---------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------

def is_authorized(user_id):
    return user_id in ADMIN_IDS


def get_counts(event_id):
    con = db()

    rows = con.execute(
        """
        SELECT status, COALESCE(SUM(1 + plus_ones), 0) AS n
        FROM attendance
        WHERE event_id=?
        GROUP BY status
        """,
        (event_id,),
    ).fetchall()

    con.close()

    return {r["status"]: r["n"] for r in rows}


def event_text(e, counts):
    text = (
        f"📅 <b>{e['name']}</b>\n\n"
        f"🗓 {e['event_date']} · {e['event_time']}\n"
        f"📍 {e['place']}\n"
    )

    if e["description"]:
        text += f"\n📝 {e['description']}\n"

    if e["comments"]:
        text += f"\n📌 <b>Información:</b>\n{e['comments']}\n"

    text += (
        "\n"
        f"✅ {counts.get('yes', 0)} personas · "
        f"🤔 {counts.get('maybe', 0)} personas · "
        f"❌ {counts.get('no', 0)} personas\n"
    )

    return text


def keyboard(event_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Asistiré",
                callback_data=f"status:{event_id}:yes"
            ),
            InlineKeyboardButton(
                "🤔 Quizás",
                callback_data=f"status:{event_id}:maybe"
            ),
            InlineKeyboardButton(
                "❌ No asistiré",
                callback_data=f"status:{event_id}:no"
            ),
        ],
        [
            InlineKeyboardButton(
                "➕ Acompañantes",
                callback_data=f"plus:{event_id}"
            ),
            InlineKeyboardButton(
                "👥 Ver asistentes",
                callback_data=f"list:{event_id}"
            ),
        ],
    ])


def preview_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ PUBLICAR",
                callback_data="create:publish"
            ),
            InlineKeyboardButton(
                "❌ CANCELAR",
                callback_data="create:cancel"
            ),
        ]
    ])


# ---------------------------------------------------------
# START
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    await update.message.reply_text(
        "👋 Soy el bot de RUTAS Y QUEDADAS.\n\n"
        "Los administradores pueden usar /crear para preparar una ruta."
    )


# ---------------------------------------------------------
# CREACIÓN DE EVENTOS
# ---------------------------------------------------------

async def crear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return ConversationHandler.END

    if not is_authorized(update.effective_user.id):
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


async def get_name(update, context):
    context.user_data["name"] = update.message.text.strip()

    await update.message.reply_text(
        "¿Qué fecha?\n\n"
        "Ejemplo: 15/10/2026"
    )

    return DATE


async def get_date(update, context):
    context.user_data["date"] = update.message.text.strip()

    await update.message.reply_text(
        "¿A qué hora?\n\n"
        "Ejemplo: 09:00"
    )

    return TIME


async def get_time(update, context):
    context.user_data["time"] = update.message.text.strip()

    await update.message.reply_text(
        "📍 ¿Dónde es?"
    )

    return PLACE


async def get_place(update, context):
    context.user_data["place"] = update.message.text.strip()

    await update.message.reply_text(
        "📷 Envíame ahora la foto de la ruta.\n\n"
        "Si no quieres poner foto, escribe <b>-</b>.",
        parse_mode="HTML",
    )

    return PHOTO


async def get_photo(update, context):
    if update.message.photo:
        # Guardamos solamente el file_id de Telegram.
        photo = update.message.photo[-1]
        context.user_data["photo_file_id"] = photo.file_id

        await update.message.reply_text(
            "📌 Perfecto.\n\n"
            "Ahora escribe la información adicional que quieras poner "
            "en la ruta.\n\n"
            "Por ejemplo:\n"
            "<i>Confirmar reserva antes del día 10.</i>\n\n"
            "Si no quieres añadir nada, escribe <b>-</b>.",
            parse_mode="HTML",
        )

        return COMMENTS

    if update.message.text:
        text = update.message.text.strip()

        if text == "-":
            context.user_data["photo_file_id"] = ""

            await update.message.reply_text(
                "📌 Escribe ahora la información adicional.\n\n"
                "Por ejemplo: confirmar reserva antes del día 10.\n\n"
                "Si no quieres añadir nada, escribe <b>-</b>.",
                parse_mode="HTML",
            )

            return COMMENTS

    await update.message.reply_text(
        "📷 Necesito que me envíes una foto.\n\n"
        "Si quieres continuar sin foto, escribe <b>-</b>.",
        parse_mode="HTML",
    )

    return PHOTO


async def get_comments(update, context):
    comments = update.message.text.strip()

    if comments == "-":
        comments = ""

    context.user_data["comments"] = comments

    # Descripción antigua: mantenemos un campo vacío para compatibilidad.
    context.user_data["description"] = ""

    text = (
        "👀 <b>VISTA PREVIA</b>\n\n"
        f"📅 <b>{context.user_data['name']}</b>\n\n"
        f"🗓 {context.user_data['date']} · "
        f"{context.user_data['time']}\n"
        f"📍 {context.user_data['place']}\n"
    )

    if comments:
        text += f"\n📌 <b>Información:</b>\n{comments}\n"

    if context.user_data.get("photo_file_id"):
        await update.message.reply_photo(
            photo=context.user_data["photo_file_id"],
            caption=text,
            parse_mode="HTML",
            reply_markup=preview_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=preview_keyboard(),
        )

    return CONFIRM


# ---------------------------------------------------------
# PUBLICACIÓN
# ---------------------------------------------------------

async def publish_event(update, context):
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.from_user.id):
        await query.message.reply_text(
            "⛔ No estás autorizado."
        )
        return

    if not CHANNEL_ID:
        await query.message.reply_text(
            "⚠️ Falta configurar CHANNEL_ID en Railway.\n\n"
            "El evento no se ha publicado."
        )
        return

    data = context.user_data

    con = db()

    cur = con.execute(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            query.message.chat.id,
            data["name"],
            data["date"],
            data["time"],
            data["place"],
            data.get("description", ""),
            data.get("photo_file_id", ""),
            data.get("comments", ""),
            str(CHANNEL_ID),
        ),
    )

    event_id = cur.lastrowid

    con.commit()

    e = con.execute(
        "SELECT * FROM events WHERE id=?",
        (event_id,)
    ).fetchone()

    con.close()

    counts = get_counts(event_id)

    text = event_text(e, counts)

    try:
        if e["photo_file_id"]:
            sent = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=e["photo_file_id"],
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard(event_id),
            )
        else:
            sent = await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard(event_id),
            )

    except Exception as exc:
        await query.message.reply_text(
            "❌ No he podido publicar la ruta.\n\n"
            f"Error: {exc}"
        )

        con = db()
        con.execute(
            "UPDATE events SET active=0 WHERE id=?",
            (event_id,),
        )
        con.commit()
        con.close()

        return ConversationHandler.END

    con = db()

    con.execute(
        """
        UPDATE events
        SET published_message_id=?
        WHERE id=?
        """,
        (sent.message_id, event_id),
    )

    con.commit()
    con.close()

    await query.message.edit_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "✅ <b>Ruta publicada correctamente.</b>\n\n"
        f"ID de ruta: <b>{event_id}</b>",
        parse_mode="HTML",
    )

    context.user_data.clear()

    return ConversationHandler.END


async def cancel_preview(update, context):
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.from_user.id):
        return ConversationHandler.END

    context.user_data.clear()

    await query.message.edit_reply_markup(reply_markup=None)

    await query.message.reply_text(
        "❌ Creación cancelada."
    )

    return ConversationHandler.END


async def cancel_create(update, context):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Creación cancelada."
    )

    return ConversationHandler.END


# ---------------------------------------------------------
# BOTONES DE ASISTENCIA
# ---------------------------------------------------------

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split(":")

    action = parts[0]
    event_id = int(parts[1])

    con = db()

    e = con.execute(
        "SELECT * FROM events WHERE id=? AND active=1",
        (event_id,),
    ).fetchone()

    if not e:
        con.close()

        try:
            await q.edit_message_reply_markup(
                reply_markup=None
            )
        except Exception:
            pass

        return

    user = q.from_user
    username = f"@{user.username}" if user.username else None
    display = user.full_name

    if action == "status":
        status = parts[2]

        con.execute(
            """
            INSERT INTO attendance (
                event_id,
                user_id,
                username,
                display_name,
                status,
                plus_ones
            )
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(event_id,user_id)
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
                display,
                status,
            ),
        )

        con.commit()
        con.close()

        await refresh_event_message(
            q,
            e,
            event_id,
        )

        return

    if action == "plus":
        con.close()

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "0",
                    callback_data=f"setplus:{event_id}:0"
                ),
                InlineKeyboardButton(
                    "+1",
                    callback_data=f"setplus:{event_id}:1"
                ),
                InlineKeyboardButton(
                    "+2",
                    callback_data=f"setplus:{event_id}:2"
                ),
            ],
            [
                InlineKeyboardButton(
                    "+3",
                    callback_data=f"setplus:{event_id}:3"
                ),
                InlineKeyboardButton(
                    "+4",
                    callback_data=f"setplus:{event_id}:4"
                ),
                InlineKeyboardButton(
                    "+5",
                    callback_data=f"setplus:{event_id}:5"
                ),
            ],
        ])

        await q.message.reply_text(
            "¿Cuántos acompañantes llevas?\n\n"
            "Ejemplo: si eres tú + 1 persona, selecciona +1.",
            reply_markup=kb,
        )

        return

    if action == "setplus":
        plus = int(parts[2])

        con.execute(
            """
            UPDATE attendance
            SET plus_ones=?
            WHERE event_id=? AND user_id=?
            """,
            (
                plus,
                event_id,
                user.id,
            ),
        )

        con.commit()
        con.close()

        await q.message.reply_text(
            f"✅ Registrado: {display} + {plus} acompañante(s)."
        )

        return

    if action == "list":
        rows = con.execute(
            """
            SELECT display_name,
                   username,
                   status,
                   plus_ones
            FROM attendance
            WHERE event_id=?
            ORDER BY status, display_name
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
            text = "Todavía no hay respuestas."
        else:
            lines = []

            for r in rows:
                who = r["username"] or r["display_name"]
                total = 1 + r["plus_ones"]

                lines.append(
                    f"{labels.get(r['status'], '•')} "
                    f"{who} — {total} persona(s)"
                )

            text = (
                "<b>Asistentes</b>\n\n"
                + "\n".join(lines)
            )

        await q.message.reply_text(
            text,
            parse_mode="HTML",
        )


async def refresh_event_message(q, e, event_id):
    counts = get_counts(event_id)

    text = event_text(e, counts)

    try:
        if e["photo_file_id"]:
            await q.edit_message_caption(
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard(event_id),
            )
        else:
            await q.edit_message_text(
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard(event_id),
            )

    except Exception:
        pass


# ---------------------------------------------------------
# EVENTOS
# ---------------------------------------------------------

async def eventos(update, context):
    if update.effective_chat.type != "private":
        return

    if not is_authorized(update.effective_user.id):
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

    for e in rows:
        counts = get_counts(e["id"])

        text = event_text(e, counts)

        if e["photo_file_id"]:
            await update.message.reply_photo(
                photo=e["photo_file_id"],
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard(e["id"]),
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=keyboard(e["id"]),
            )


# ---------------------------------------------------------
# CANCELAR EVENTO
# ---------------------------------------------------------

async def cancelar(update, context):
    if update.effective_chat.type != "private":
        return

    if not is_authorized(update.effective_user.id):
        await update.message.reply_text(
            "⛔ No estás autorizado."
        )
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Uso: /cancelar ID"
        )
        return

    event_id = int(context.args[0])

    con = db()

    e = con.execute(
        "SELECT * FROM events WHERE id=? AND active=1",
        (event_id,),
    ).fetchone()

    if not e:
        con.close()

        await update.message.reply_text(
            "No encuentro esa ruta activa."
        )

        return

    con.execute(
        "UPDATE events SET active=0 WHERE id=?",
        (event_id,),
    )

    con.commit()
    con.close()

    await update.message.reply_text(
        f"✅ Ruta {event_id} cancelada."
    )


# ---------------------------------------------------------
# ID DEL CHAT
# ---------------------------------------------------------

async def obtener_id(update, context):
    await update.message.reply_text(
        f"🆔 ID de este chat:\n{update.effective_chat.id}"
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    if not TOKEN:
        raise RuntimeError(
            "Falta BOT_TOKEN en las variables de entorno."
        )

    init_db()

    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler(
                "crear",
                crear,
                filters=filters.ChatType.PRIVATE,
            )
        ],

        states={
            NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_name,
                )
            ],

            DATE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_date,
                )
            ],

            TIME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_time,
                )
            ],

            PLACE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_place,
                )
            ],

            PHOTO: [
                MessageHandler(
                    filters.PHOTO,
                    get_photo,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_photo,
                ),
            ],

            COMMENTS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_comments,
                )
            ],

            CONFIRM: [
                CallbackQueryHandler(
                    publish_event,
                    pattern=r"^create:publish$",
                ),
                CallbackQueryHandler(
                    cancel_preview,
                    pattern=r"^create:cancel$",
                ),
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancelar_creacion",
                cancel_create,
            )
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    app.add_handler(
        CommandHandler(
            "eventos",
            eventos,
            filters=filters.ChatType.PRIVATE,
        )
    )

    app.add_handler(
        CommandHandler(
            "cancelar",
            cancelar,
            filters=filters.ChatType.PRIVATE,
        )
    )

    app.add_handler(
        CommandHandler(
            "id",
            obtener_id,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            callback,
            pattern=r"^(status|plus|setplus|list):",
        )
    )

    print("Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()
