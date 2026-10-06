import os
import sqlite3
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {126421812, 8761859, 5710212742, 13017110}
DESTINATIONS = {
    -1003290070983: {"name": "Canal de prueba", "thread_id": None, "topic_name": None},
    -1001839833790: {"name": "Maxiscooter Club", "thread_id": 12542, "topic_name": "Rutas"},
}
DB_PATH = "bot.db"
(NAME, DATE, TIME, PLACE, MEETING_MAPS, ROUTE_MAPS, ROUTE_MAPS_2, PHOTO, COMMENTS, CONFIRM) = range(10)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, date TEXT NOT NULL,
        time TEXT NOT NULL, place TEXT NOT NULL, route_maps TEXT, photo_file_id TEXT,
        comments TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        published_chat_id INTEGER, published_message_id INTEGER, active INTEGER DEFAULT 1)''')
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    for column, definition in [("meeting_maps", "TEXT"), ("route_maps_2", "TEXT"), ("created_by_name", "TEXT")]:
        if column not in columns:
            conn.execute(f"ALTER TABLE events ADD COLUMN {column} {definition}")
    conn.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL, status TEXT NOT NULL, companions INTEGER DEFAULT 0,
        UNIQUE(event_id, user_id))''')
    conn.commit(); conn.close()

def is_admin(user_id): return user_id in ADMIN_IDS

def get_event(event_id):
    conn = db(); row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone(); conn.close(); return row

def get_counts(event_id):
    conn = db(); rows = conn.execute("SELECT status, SUM(1+companions) total FROM attendance WHERE event_id=? GROUP BY status", (event_id,)).fetchall(); conn.close()
    result = {"yes": 0}
    for row in rows: result[row["status"]] = row["total"] or 0
    return result

def event_text(event):
    counts = get_counts(event["id"])
    lines = [f"🏍️ {event['name']}", "", f"📅 Fecha: {event['date']}", f"🕐 Hora de quedada: {event['time']}", f"📍 Lugar de encuentro: {event['place']}"]
    if event["comments"]: lines += ["", "ℹ️ Información:", event["comments"]]
    lines += ["", f"👤 Creado por: {event['created_by_name']}", "", f"👥 Asistentes: {counts.get('yes', 0)}"]
    return "\n".join(lines)

def attendance_keyboard(event_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Asistiré", callback_data=f"attend:yes:{event_id}")],
        [InlineKeyboardButton("➕ +1", callback_data=f"compplus:{event_id}"), InlineKeyboardButton("➖ -1", callback_data=f"compminus:{event_id}"), InlineKeyboardButton("👀 Ver asistentes", callback_data=f"list:{event_id}")],
    ])

def event_keyboard(event):
    buttons = []
    if event["meeting_maps"]: buttons.append([InlineKeyboardButton("📍 Lugar de encuentro", url=event["meeting_maps"])])
    if event["route_maps"]: buttons.append([InlineKeyboardButton("🗺️ Ruta 1", url=event["route_maps"])])
    if event["route_maps_2"]: buttons.append([InlineKeyboardButton("🗺️ Ruta 2", url=event["route_maps_2"])])
    buttons.extend(attendance_keyboard(event["id"]).inline_keyboard)
    return InlineKeyboardMarkup(buttons)

async def update_published_event(context, event_id):
    event = get_event(event_id)
    if not event or not event["published_chat_id"] or not event["published_message_id"]: return
    try:
        await context.bot.edit_message_caption(chat_id=event["published_chat_id"], message_id=event["published_message_id"], caption=event_text(event), reply_markup=event_keyboard(event))
    except Exception as exc: print("No se pudo actualizar:", exc)

async def start(update, context): await update.message.reply_text("🏍️ Bot de rutas activo. Usa /crear si eres administrador.")

async def crear(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ No estás autorizado."); return ConversationHandler.END
    context.user_data.clear(); user = update.effective_user
    context.user_data["created_by"] = user.id; context.user_data["created_by_name"] = user.full_name or user.username or str(user.id)
    await update.message.reply_text("🏍️ ¿Cuál es el nombre de la ruta?"); return NAME
async def got_name(update, context): context.user_data["name"] = update.message.text.strip(); await update.message.reply_text("📅 ¿Qué fecha tiene la ruta?"); return DATE
async def got_date(update, context): context.user_data["date"] = update.message.text.strip(); await update.message.reply_text("🕐 ¿A qué hora es la quedada?"); return TIME
async def got_time(update, context): context.user_data["time"] = update.message.text.strip(); await update.message.reply_text("📍 ¿Cuál es el lugar de encuentro?"); return PLACE

def optional_url(value):
    value = value.strip()
    if value.lower() in {"ninguno", "ninguna", "no", "-", "nada"}: return ""
    return value if re.match(r"^https?://\S+$", value, re.I) else None

async def got_place(update, context):
    context.user_data["place"] = update.message.text.strip()
    await update.message.reply_text("📍 Pega el enlace de Google Maps del lugar de encuentro.\n\nSi no quieres añadirlo, escribe ninguno."); return MEETING_MAPS
async def got_meeting_maps(update, context):
    value = optional_url(update.message.text)
    if value is None: await update.message.reply_text("⚠️ Enlace no válido. Usa http://, https:// o escribe ninguno."); return MEETING_MAPS
    context.user_data["meeting_maps"] = value
    await update.message.reply_text("🗺️ Pega el enlace de Google Maps de la Ruta 1.\n\nSi no quieres añadirlo, escribe ninguno."); return ROUTE_MAPS
async def got_route_maps(update, context):
    value = optional_url(update.message.text)
    if value is None: await update.message.reply_text("⚠️ Enlace no válido. Usa http://, https:// o escribe ninguno."); return ROUTE_MAPS
    context.user_data["route_maps"] = value
    await update.message.reply_text("🗺️ ¿Quieres añadir un segundo enlace? Puede ser el lugar de almuerzo, destino, parada, etc.\n\nPega el enlace o escribe ninguno."); return ROUTE_MAPS_2
async def got_route_maps_2(update, context):
    value = optional_url(update.message.text)
    if value is None: await update.message.reply_text("⚠️ Enlace no válido. Usa http://, https:// o escribe ninguno."); return ROUTE_MAPS_2
    context.user_data["route_maps_2"] = value; await update.message.reply_text("📸 Ahora envíame la foto de la ruta."); return PHOTO
async def got_photo(update, context):
    if not update.message.photo: await update.message.reply_text("⚠️ Envíame una foto, por favor."); return PHOTO
    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    await update.message.reply_text("ℹ️ Escribe la información o comentarios.\n\nSi no quieres añadir nada, escribe ninguno."); return COMMENTS
async def got_comments(update, context):
    text = update.message.text.strip()
    if text.lower() in {"ninguno", "ninguna", "no", "-", "nada"}: text = ""
    context.user_data["comments"] = text; d = context.user_data
    preview = (f"👀 PREVISUALIZACIÓN\n\n🏍️ {d['name']}\n📅 Fecha: {d['date']}\n🕐 Hora de quedada: {d['time']}\n📍 Lugar de encuentro: {d['place']}\n👤 Creado por: {d['created_by_name']}\n📍 Enlace lugar: {'Sí' if d.get('meeting_maps') else 'No'}\n🗺️ Ruta 1: {'Sí' if d.get('route_maps') else 'No'}\n🗺️ Ruta 2: {'Sí' if d.get('route_maps_2') else 'No'}")
    if text: preview += f"\n\nℹ️ Información:\n{text}"
    await update.message.reply_text(preview, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Confirmar", callback_data="create:confirm"), InlineKeyboardButton("❌ Cancelar", callback_data="create:cancel")]])); return CONFIRM

async def confirm_create(update, context):
    query = update.callback_query; await query.answer()
    if query.data == "create:cancel": context.user_data.clear(); await query.edit_message_text("❌ Creación cancelada."); return ConversationHandler.END
    d = context.user_data; conn = db()
    cursor = conn.execute("""INSERT INTO events (name,date,time,place,route_maps,meeting_maps,route_maps_2,photo_file_id,comments,created_by,created_by_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (d["name"],d["date"],d["time"],d["place"],d.get("route_maps",""),d.get("meeting_maps",""),d.get("route_maps_2",""),d["photo_file_id"],d.get("comments",""),d["created_by"],d["created_by_name"]))
    event_id = cursor.lastrowid; conn.commit(); conn.close()
    buttons = [[InlineKeyboardButton(destination["name"], callback_data=f"publish:{chat_id}:{event_id}")] for chat_id,destination in DESTINATIONS.items()]
    await query.edit_message_text("📢 ¿Dónde quieres publicar esta ruta?", reply_markup=InlineKeyboardMarkup(buttons)); context.user_data.clear(); return ConversationHandler.END

async def publish_callback(update, context):
    query = update.callback_query; await query.answer(); _,chat_id_text,event_id_text = query.data.split(":"); chat_id=int(chat_id_text); event_id=int(event_id_text)
    if not is_admin(query.from_user.id): await query.edit_message_text("⛔ No estás autorizado."); return
    destination=DESTINATIONS.get(chat_id); event=get_event(event_id)
    if not destination or not event: await query.edit_message_text("⚠️ No se encontró la ruta o destino."); return
    try:
        sent=await context.bot.send_photo(chat_id=chat_id,photo=event["photo_file_id"],caption=event_text(event),reply_markup=event_keyboard(event),message_thread_id=destination["thread_id"])
        conn=db(); conn.execute("UPDATE events SET published_chat_id=?, published_message_id=? WHERE id=?",(chat_id,sent.message_id,event_id)); conn.commit(); conn.close()
        await query.edit_message_text(f"✅ Ruta publicada en {destination['name']}.")
    except Exception as exc: print("Error publicando:",exc); await query.edit_message_text("❌ No se pudo publicar la ruta.")

async def attendance_callback(update, context):
    query=update.callback_query; parts=query.data.split(":"); action=parts[0]
    try:
        if action == "attend":
            event_id=int(parts[2]); user=query.from_user; name=user.full_name or user.username or str(user.id); conn=db()
            row=conn.execute("SELECT companions FROM attendance WHERE event_id=? AND user_id=?",(event_id,user.id)).fetchone(); companions=row["companions"] if row else 0
            conn.execute("""INSERT INTO attendance(event_id,user_id,user_name,status,companions) VALUES(?,?,?,?,?) ON CONFLICT(event_id,user_id) DO UPDATE SET user_name=excluded.user_name,status=excluded.status""",(event_id,user.id,name,"yes",companions)); conn.commit(); conn.close(); await query.answer("✅ Has marcado: Asistiré"); await update_published_event(context,event_id); return
        event_id=int(parts[1]); user=query.from_user; conn=db(); row=conn.execute("SELECT companions FROM attendance WHERE event_id=? AND user_id=?",(event_id,user.id)).fetchone()
        if action == "compplus":
            if row and (row["companions"] or 0)>=10: conn.close(); await query.answer("Máximo 10 acompañantes.",show_alert=True); return
            if row: conn.execute("UPDATE attendance SET companions=companions+1 WHERE event_id=? AND user_id=?",(event_id,user.id))
            else:
                name=user.full_name or user.username or str(user.id); conn.execute("INSERT INTO attendance(event_id,user_id,user_name,status,companions) VALUES(?,?,?,?,?)",(event_id,user.id,name,"yes",1))
            conn.commit(); conn.close(); await query.answer("➕ Acompañante añadido."); await update_published_event(context,event_id); return
        if action == "compminus":
            if not row or (row["companions"] or 0)<=0: conn.close(); await query.answer("No tienes acompañantes."); return
            conn.execute("UPDATE attendance SET companions=companions-1 WHERE event_id=? AND user_id=?",(event_id,user.id)); conn.commit(); conn.close(); await query.answer("➖ Acompañante eliminado."); await update_published_event(context,event_id); return
        if action == "list":
            conn.close(); conn=db(); rows=conn.execute("SELECT user_name,companions FROM attendance WHERE event_id=? ORDER BY id",(event_id,)).fetchall(); conn.close()
            text="👀 Todavía no hay asistentes." if not rows else "\n".join(["👀 ASISTENTES",""]+[f"✅ {r['user_name']}"+(f" + {r['companions']}" if r['companions'] else "") for r in rows])
            await query.answer(text[:190],show_alert=True)
    except Exception as exc: print("Error en asistencia:",exc); await query.answer("⚠️ No se pudo actualizar.",show_alert=True)

async def eventos(update, context):
    conn=db(); rows=conn.execute("SELECT * FROM events WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall(); conn.close()
    if not rows: await update.message.reply_text("No hay rutas activas."); return
    for event in rows: await update.message.reply_text(event_text(event),reply_markup=event_keyboard(event))

async def cancelar(update, context):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ No estás autorizado."); return
    conn=db(); rows=conn.execute("SELECT id,name,date FROM events WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall(); conn.close()
    if not rows: await update.message.reply_text("No hay rutas activas."); return
    await update.message.reply_text("¿Qué ruta quieres cancelar?",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"❌ {r['name']} ({r['date']})",callback_data=f"cancel:{r['id']}")] for r in rows]))

async def cancel_callback(update, context):
    query=update.callback_query; await query.answer()
    if not is_admin(query.from_user.id): await query.edit_message_text("⛔ No estás autorizado."); return
    event_id=int(query.data.split(":")[1]); conn=db(); conn.execute("UPDATE events SET active=0 WHERE id=?",(event_id,)); conn.commit(); conn.close(); await query.edit_message_text("✅ Ruta cancelada.")

async def id_command(update, context):
    chat=update.effective_chat; user=update.effective_user; lines=[f"User ID: {user.id}",f"Chat ID: {chat.id}",f"Type: {chat.type}",f"Name: {chat.title or chat.full_name}"]
    if chat.type in {"group","supergroup"}:
        thread_id=update.effective_message.message_thread_id
        if thread_id: lines += [f"Thread ID: {thread_id}","Tema guardado para Rutas"]
    await update.message.reply_text("\n".join(lines))

async def canales(update, context):
    lines=["📢 DESTINOS CONFIGURADOS",""]
    for chat_id,d in DESTINATIONS.items():
        lines.append(f"{d['name']}: {chat_id}")
        if d["thread_id"]: lines += [f"  Tema: {d['topic_name']}",f"  Thread ID: {d['thread_id']}"]
        else: lines.append("  Listo")
        lines.append("")
    await update.message.reply_text("\n".join(lines))

async def cancel_creation(update, context): context.user_data.clear(); await update.message.reply_text("❌ Creación cancelada."); return ConversationHandler.END

def main():
    init_db(); application=Application.builder().token(BOT_TOKEN).build()
    creation=ConversationHandler(entry_points=[CommandHandler("crear",crear)],states={
        NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_name)], DATE:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_date)], TIME:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_time)], PLACE:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_place)], MEETING_MAPS:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_meeting_maps)], ROUTE_MAPS:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_route_maps)], ROUTE_MAPS_2:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_route_maps_2)], PHOTO:[MessageHandler(filters.PHOTO,got_photo)], COMMENTS:[MessageHandler(filters.TEXT & ~filters.COMMAND,got_comments)], CONFIRM:[CallbackQueryHandler(confirm_create,pattern=r"^create:(confirm|cancel)$")]},fallbacks=[CommandHandler("cancelar",cancel_creation)],allow_reentry=True)
    application.add_handler(CommandHandler("start",start)); application.add_handler(creation)
    application.add_handler(CallbackQueryHandler(publish_callback,pattern=r"^publish:-?\d+:\d+$"),group=2)
    application.add_handler(CallbackQueryHandler(cancel_callback,pattern=r"^cancel:\d+$"),group=3)
    application.add_handler(CallbackQueryHandler(attendance_callback,pattern=r"^(attend:yes:\d+|compplus:\d+|compminus:\d+|list:\d+)$"),group=4)
    application.add_handler(CommandHandler("eventos",eventos)); application.add_handler(CommandHandler("cancelar",cancelar)); application.add_handler(CommandHandler("id",id_command)); application.add_handler(CommandHandler("canales",canales))
    print("Bot V7.3 iniciado."); application.run_polling()

if __name__ == "__main__": main()
