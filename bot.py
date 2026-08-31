import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, ContextTypes, filters,
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB = "events.db"

NAME, DATE, TIME, PLACE, DESCRIPTION = range(5)

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
    con.commit()
    con.close()

def is_admin(update: Update) -> bool:
    member = update.effective_chat.get_member(update.effective_user.id)
    return member.status in ("administrator", "creator")

def event_text(e, counts):
    return (
        f"📅 <b>{e['name']}</b>\n\n"
        f"🗓 {e['event_date']} · {e['event_time']}\n"
        f"📍 {e['place']}\n"
        + (f"\n📝 {e['description']}\n" if e['description'] else "")
        + "\n"
        f"✅ {counts.get('yes', 0)} personas · "
        f"🤔 {counts.get('maybe', 0)} personas · "
        f"❌ {counts.get('no', 0)} personas\n"
    )

def get_counts(event_id):
    con = db()
    rows = con.execute(
        "SELECT status, COALESCE(SUM(1 + plus_ones),0) n "
        "FROM attendance WHERE event_id=? GROUP BY status", (event_id,)
    ).fetchall()
    con.close()
    return {r["status"]: r["n"] for r in rows}

def keyboard(event_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Asistiré", callback_data=f"status:{event_id}:yes"),
            InlineKeyboardButton("🤔 Quizás", callback_data=f"status:{event_id}:maybe"),
            InlineKeyboardButton("❌ No asistiré", callback_data=f"status:{event_id}:no"),
        ],
        [
            InlineKeyboardButton("➕ Acompañantes", callback_data=f"plus:{event_id}"),
            InlineKeyboardButton("👥 Ver asistentes", callback_data=f"list:{event_id}"),
        ],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Soy el bot de eventos.\n\n"
        "Usa /crear para crear un evento."
    )

async def crear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("Solo los administradores pueden crear eventos.")
        return ConversationHandler.END
    await update.message.reply_text("¿Cómo se llama el evento?")
    return NAME

async def get_name(update, context):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("¿Qué fecha? (ej. 15/09/2026)")
    return DATE

async def get_date(update, context):
    context.user_data["date"] = update.message.text.strip()
    await update.message.reply_text("¿A qué hora? (ej. 21:00)")
    return TIME

async def get_time(update, context):
    context.user_data["time"] = update.message.text.strip()
    await update.message.reply_text("¿Dónde?")
    return PLACE

async def get_place(update, context):
    context.user_data["place"] = update.message.text.strip()
    await update.message.reply_text("Descripción (o escribe '-' para dejarla vacía):")
    return DESCRIPTION

async def get_description(update, context):
    description = update.message.text.strip()
    if description == "-":
        description = ""
    context.user_data["description"] = description

    con = db()
    cur = con.execute(
        "INSERT INTO events(chat_id,name,event_date,event_time,place,description) "
        "VALUES(?,?,?,?,?,?)",
        (
            update.effective_chat.id,
            context.user_data["name"],
            context.user_data["date"],
            context.user_data["time"],
            context.user_data["place"],
            description,
        ),
    )
    event_id = cur.lastrowid
    con.commit()
    e = con.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    con.close()

    await update.message.reply_text(
        event_text(e, get_counts(event_id)) +
        "\nPulsa una opción para confirmar tu asistencia.",
        parse_mode="HTML",
        reply_markup=keyboard(event_id),
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_create(update, context):
    context.user_data.clear()
    await update.message.reply_text("Creación cancelada.")
    return ConversationHandler.END

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    action, event_id = parts[0], int(parts[1])

    con = db()
    e = con.execute("SELECT * FROM events WHERE id=? AND active=1", (event_id,)).fetchone()
    if not e:
        con.close()
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text("Este evento ya no está activo.")
        return

    user = q.from_user
    username = f"@{user.username}" if user.username else None
    display = user.full_name

    if action == "status":
        status = parts[2]
        con.execute(
            "INSERT INTO attendance(event_id,user_id,username,display_name,status,plus_ones) "
            "VALUES(?,?,?,?,?,0) "
            "ON CONFLICT(event_id,user_id) DO UPDATE SET "
            "username=excluded.username, display_name=excluded.display_name, "
            "status=excluded.status, plus_ones=0",
            (event_id, user.id, username, display, status),
        )
        con.commit()
        counts = get_counts(event_id)
        con.close()
        await q.edit_message_text(
            event_text(e, counts) + f"\n<b>Tu respuesta:</b> {status}",
            parse_mode="HTML", reply_markup=keyboard(event_id)
        )
        return

    if action == "plus":
        con.close()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("0", callback_data=f"setplus:{event_id}:0"),
             InlineKeyboardButton("+1", callback_data=f"setplus:{event_id}:1"),
             InlineKeyboardButton("+2", callback_data=f"setplus:{event_id}:2")],
            [InlineKeyboardButton("+3", callback_data=f"setplus:{event_id}:3"),
             InlineKeyboardButton("+4", callback_data=f"setplus:{event_id}:4"),
             InlineKeyboardButton("+5", callback_data=f"setplus:{event_id}:5")],
        ])
        await q.message.reply_text(
            "¿Cuántos acompañantes llevas?\n\n"
            "Ejemplo: si eres tú + 1 persona, selecciona <b>+1</b>.",
            parse_mode="HTML", reply_markup=kb
        )
        return

    if action == "setplus":
        plus = int(parts[2])
        con.execute(
            "UPDATE attendance SET plus_ones=? WHERE event_id=? AND user_id=?",
            (plus, event_id, user.id),
        )
        con.commit()
        con.close()
        await q.message.reply_text(
            f"✅ Registrado: {display} + {plus} acompañante(s)."
        )
        return

    if action == "list":
        rows = con.execute(
            "SELECT display_name, username, status, plus_ones "
            "FROM attendance WHERE event_id=? ORDER BY status, display_name",
            (event_id,),
        ).fetchall()
        con.close()
        labels = {"yes": "✅", "maybe": "🤔", "no": "❌"}
        if not rows:
            text = "Todavía no hay respuestas."
        else:
            lines = []
            for r in rows:
                who = r["username"] or r["display_name"]
                total = 1 + r["plus_ones"]
                lines.append(f"{labels.get(r['status'], '•')} {who} — {total} persona(s)")
            text = "<b>Asistentes</b>\n\n" + "\n".join(lines)
        await q.message.reply_text(text, parse_mode="HTML")

async def eventos(update, context):
    con = db()
    rows = con.execute(
        "SELECT * FROM events WHERE chat_id=? AND active=1 ORDER BY id DESC",
        (update.effective_chat.id,),
    ).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("No hay eventos activos.")
        return
    for e in rows:
        await update.message.reply_text(
            event_text(e, get_counts(e["id"])),
            parse_mode="HTML", reply_markup=keyboard(e["id"])
        )

async def cancelar(update, context):
    if not is_admin(update):
        await update.message.reply_text("Solo los administradores pueden cancelar eventos.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Uso: /cancelar ID")
        return
    event_id = int(context.args[0])
    con = db()
    con.execute("UPDATE events SET active=0 WHERE id=? AND chat_id=?", (event_id, update.effective_chat.id))
    con.commit()
    con.close()
    await update.message.reply_text(f"Evento {event_id} cancelado.")

def main():
    if not TOKEN:
        raise RuntimeError("Falta BOT_TOKEN en el archivo .env")
    init_db()
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("crear", crear)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
            PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_place)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
        },
        fallbacks=[CommandHandler("cancelar_creacion", cancel_create)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("eventos", eventos))
    app.add_handler(CommandHandler("cancelar", cancelar))
    app.add_handler(CallbackQueryHandler(callback))
    print("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
